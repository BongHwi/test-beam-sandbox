# SPDX-License-Identifier: LGPL-3.0-or-later
"""EUDAQ producer for the public synthetic device."""
import argparse
import json
import os
import socket
import time
import threading
import pyeudaq
from msensor.codec import encode
from msensor.device import MSensorDevice
from msensor.source import SyntheticSource
from msensor.session_source import SessionSource
from msensor.stall import Stall

def send_immediately():
    """Disable delayed coalescing of small writes on this process's TCP sockets.

    Each event is a small write. With coalescing, an event waits for the
    acknowledgement of the previous one, which delayed this producer's events
    by up to tens of milliseconds behind the other producers. The data and
    the EUDAQ protocol are unchanged.
    """
    for name in os.listdir('/proc/self/fd'):
        try:
            if not os.readlink('/proc/self/fd/'+name).startswith('socket:'): continue
            handle=socket.socket(fileno=os.dup(int(name)))
            try:
                if handle.family in (socket.AF_INET,socket.AF_INET6) and handle.type==socket.SOCK_STREAM:
                    handle.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            finally: handle.close()
        except (OSError,ValueError): pass

SOURCE_KEYS={'seed','trigger_rate_hz','trigger_period_ns','trigger_first_ns','trigger_times_ns','beam_center_col','beam_center_row','beam_sigma_px','sim_end_ns'}
STRINGS={'readout_mode','stream_dir','clock_mode','source','response_file','EUDAQ_DC','EUDAQ_ID'}

def parse_config(items):
    parsed={}
    for key,value in items.items():
        if key in STRINGS: parsed[key]=value; continue
        try: parsed[key]=json.loads(value)
        except (TypeError,ValueError) as exc: raise ValueError(f'invalid {key}') from exc
    return parsed

class MSensorProducer(pyeudaq.Producer):
    def __init__(self,name,runcontrol):
        super().__init__(name,runcontrol); self.name=name; self.device=MSensorDevice(name); self.running=False; self.init={}
        self._data_events=0; self._data_events_lock=threading.Lock()

    def DoInitialise(self):
        self.init=parse_config(self.GetInitConfiguration().as_dict())

    def DoConfigure(self):
        run_config=parse_config(self.GetConfiguration().as_dict())
        settings=dict(self.init); settings.update(run_config)
        if SOURCE_KEYS & settings.keys(): raise ValueError('source settings must be passed via MSENSOR_SOURCE_CONFIG_JSON to prevent EUDAQ tag leakage')
        source=json.loads(os.environ.get('MSENSOR_SOURCE_CONFIG_JSON','{}'))
        if not isinstance(source,dict) or set(source)-SOURCE_KEYS: raise ValueError('invalid source environment settings')
        settings.update(source)
        if 'EUDAQ_ID' not in run_config or int(run_config['EUDAQ_ID']) != settings.get('plane'):
            raise ValueError('EUDAQ_ID must equal plane; EUDAQ overwrites event DeviceN')
        self.device.configure(settings)

    def _status_event(self,bore=False,eore=False,values=None):
        ev=pyeudaq.Event('RawEvent',self.name+'_status')
        if bore: ev.SetBORE()
        if eore: ev.SetEORE()
        for key,value in (values or {}).items():
            ev.SetTag(key,json.dumps(value,sort_keys=True,separators=(',',':')) if not isinstance(value,str) else value)
        self.SendEvent(ev)

    def DoStartRun(self):
        with self._data_events_lock:
            self._data_events=0
            self.SetStatusTag('DataEventN','0')
            self.SetStatusTag('source_limit','')
        source = (SessionSource(self.device.config, self.name) if self.device.config['source'] == 'session'
                  else SyntheticSource(self.device.config))
        self.device.arm(self.GetRunNumber(), source)
        self._run = self.GetRunNumber()
        self._stall = None
        if isinstance(source, SessionSource):
            self._stall = Stall(os.path.dirname(os.environ['SESSION_SOURCE_SOCKET']), self.name)
            self._stall.reset()
            self.SetStatusTag('stalled', '0')
        begin={'readout_mode':self.device.config['readout_mode'],'settings':self.device.detector,'config_hash':self.device.config_hash}
        if self.device.stream:
            begin.update(stream_id=self.device.stream_id,stream_file=self.device.stream.path.name,window_length_ns=self.device.config['window_length_ns'])
        self._status_event(bore=True,values=begin); self.running=True

    def _send(self,records):
        for record in records:
            event=pyeudaq.Event('RawEvent','msensor')
            event.SetTriggerN(record['trigger_number']); event.SetDeviceN(self.device.config['plane'])
            event.SetTimestamp(record['trigger_time_ns']*1000,record['trigger_time_ns']*1000)
            payload=dict(record,columns=self.device.config['columns'],rows=self.device.config['rows'])
            event.AddBlock(0,encode(payload))
            with self._data_events_lock:
                self.SendEvent(event)
                self._data_events+=1
                self.SetStatusTag('DataEventN',str(self._data_events))
            if isinstance(self.device.source, SessionSource): self.device.source.mark_sent(record['trigger_number'])

    def RunLoop(self):
        # The data connection to the collector exists once the run has started.
        send_immediately()
        start=time.monotonic_ns()
        try:
            if isinstance(self.device.source, SessionSource):
                return self._session_runloop()
            while self.running:
                c=self.device.config; limit=self.device.source.end_ns()
                if c['clock_mode']=='virtual': target=self.device.time+c['sim_step_ns']
                else: target=int((time.monotonic_ns()-start)*c['pace'])
                if limit is not None: target=min(target,limit)
                if target>self.device.time: self._send(self.device.advance_to(target))
                if limit is not None and target>=limit:
                    self.SetStatusTag('source_finished','1'); time.sleep(.01)
                elif c['clock_mode']=='paced': time.sleep(.001)
            summary=self.device.disarm(); self._send(summary.pop('remaining_records'))
            self._status_event(eore=True,values=summary)
        except Exception as exc:
            self.running=False
            self.SetStatusMsg('ERROR: '+str(exc))
            if self.device.stream is not None:
                try: self._status_event(eore=True,values={'stream_closed':0})
                except Exception: pass
            raise

    def _session_runloop(self):
        source = self.device.source
        stalled = False
        while self.running:
            # A timed stall keeps answering the source (so the producer stays in the run)
            # but neither processes nor sends events; they are sent after the stall.
            if self._stall.active(self._run):
                if not stalled: stalled = True; self.SetStatusTag('stalled', '1')
                source.poll()
                time.sleep(.001)
                continue
            if stalled: stalled = False; self.SetStatusTag('stalled', '0')
            target = source.watermark_ns()
            if target > self.device.time:
                self._send(self.device.advance_to(target))
                source.mark_processed(self.device.time)
            if source.finished:
                self.SetStatusTag('source_finished', '1')
                self.SetStatusTag('source_limit', source.limit_reason or 'end_time')
                self.SetStatusMsg('source_finished')
            time.sleep(.001)
        self._stall.finish()
        source.stop()
        source.poll()
        if source.end_ns() > self.device.time:
            self._send(self.device.advance_to(source.end_ns()))
            source.mark_processed(self.device.time)
        summary = self.device.disarm()
        self._send(summary.pop('remaining_records'))
        source.wait_barrier()
        self._status_event(eore=True, values=summary)

    def DoStopRun(self): self.running=False
    def DoReset(self): self.running=False
    def DoTerminate(self): self.running=False
    def DoStatus(self):
        with self._data_events_lock: self.SetStatusTag('DataEventN',str(self._data_events))
        self.SetStatusTag('time_ns',str(self.device.status()['time_ns']))
        self.SetStatusTag('source_finished','1' if self.device.status()['source_finished'] else '0')

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('-n','--name',default='MSENSOR_0'); parser.add_argument('-r','--run-control',default='tcp://localhost:44000'); args=parser.parse_args()
    producer=MSensorProducer(args.name,args.run_control); producer.Connect()
    while producer.IsConnected(): time.sleep(.1)
