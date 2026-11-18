# SPDX-License-Identifier: LGPL-3.0-or-later
"""msensor runner with a single-client Unix JSON-lines control socket.

The status file and status reply describe the run, file, frame and record
counts, last tick, TCP delivery counters, errors and update time. TCP sends
header, complete frames and END as the exact file bytes. Drops count frames
not fully handed to the sender kernel; they do not count subsequent losses
at the receiver. Overflow closes normally, discards incomplete queued units,
and reconnects periodically with a new header while running. During drain,
END gets one immediate connection attempt if needed, and TCP failure never
changes the result of a successfully completed core drain.
"""
import argparse
from collections import deque
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import sys
import threading
import time

from .clock import Clock
from .device import MSensorDevice
from .rawstream import RawWriter, TICK
from .session_source import SessionSource
from .source import Crossing, Trigger

SOURCE_KEYS = {'seed', 'trigger_rate_hz', 'trigger_period_ns', 'trigger_first_ns',
               'trigger_times_ns', 'beam_center_col', 'beam_center_row', 'beam_sigma_px', 'sim_end_ns'}
KEYS = {'format', 'name', 'device', 'frame_ns', 'anchor_windows', 'output_dir',
        'output', 'socket_port', 'send_buffer_bytes', 'socket_sndbuf_bytes'}


def integer(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def configuration(path):
    try:
        c = json.loads(Path(path).read_text())
        if not isinstance(c, dict) or not KEYS <= set(c) or set(c) - KEYS - {'clock', 'missed_triggers', 'noise'}: raise ValueError('invalid configuration keys')
        if c['format'] != 'msensor-runner-config-1': raise ValueError('invalid format')
        if not isinstance(c['name'], str) or not re.fullmatch(r'MSENSOR_[0-9]+', c['name']): raise ValueError('invalid name')
    except SyntaxError:
        raise
    except (OSError, ValueError, TypeError):
        raise ValueError('invalid configuration') from None
    clock = c.get('clock', {'offset_ns': 0, 'drift_ppb': 0})
    if (not isinstance(clock, dict) or set(clock) != {'offset_ns', 'drift_ppb'}
            or not integer(clock['offset_ns'], 0, 1000000000)
            or not integer(clock['drift_ppb'], -1000000, 1000000)):
        raise ValueError('invalid clock')
    if 'missed_triggers' in c:
        missed = c['missed_triggers']
        if (not isinstance(missed, list) or not 1 <= len(missed) <= 64
                or any(not integer(n, 0, 1000000000) for n in missed)
                or any(a >= b for a, b in zip(missed, missed[1:]))):
            raise ValueError('invalid missed triggers')
    if 'noise' in c and c['noise'] != 'fast': raise ValueError('invalid noise')
    d = c['device']
    if not isinstance(d, dict): raise ValueError('invalid device')
    if set(d) & (SOURCE_KEYS | {'stream_dir', 'EUDAQ_DC', 'EUDAQ_ID'}): raise ValueError('forbidden device setting')
    mode=d.get('readout_mode')
    if d.get('source') != 'session' or d.get('clock_mode') != 'virtual' or mode not in ('continuous','triggered'): raise ValueError('invalid device mode')
    if not integer(c['frame_ns'], 10_000_000, 1_000_000_000): raise ValueError('invalid frame settings')
    if mode == 'continuous':
        w = d.get('window_length_ns')
        if not integer(c['anchor_windows'],16,32768) or not integer(w,1000,10_000_000) or c['frame_ns'] % w or c['frame_ns']//w > 65536: raise ValueError('invalid frame settings')
    elif c['anchor_windows'] is not None or set(d) & {'window_length_ns','window_guard_before','window_guard_after'} or not {'strobe_delay_ns','strobe_length_ns'} <= set(d):
        raise ValueError('invalid frame settings')
    if not isinstance(c['output_dir'],str) or not os.path.isabs(c['output_dir']) or not os.path.isdir(c['output_dir']): raise ValueError('invalid output directory')
    if c['output'] == 'file':
        if any(c[k] is not None for k in ('socket_port','send_buffer_bytes','socket_sndbuf_bytes')): raise ValueError('invalid socket settings')
    elif c['output'] == 'socket':
        if not integer(c['socket_port'],1024,65535) or not integer(c['send_buffer_bytes'],4096,1073741824) or (c['socket_sndbuf_bytes'] is not None and not integer(c['socket_sndbuf_bytes'],4096,4194304)): raise ValueError('invalid socket settings')
    else: raise ValueError('invalid output')
    try:
        private=json.loads(os.environ['MSENSOR_SOURCE_CONFIG_JSON'])
        if not isinstance(private,dict) or set(private)!={'seed'}: raise ValueError()
        device=MSensorDevice(c['name'])
        device_settings=dict(d, seed=private['seed'])
        if mode == 'continuous': device_settings['stream_dir']=c['output_dir']
        device.configure(device_settings)
        if not os.environ.get('SESSION_SOURCE_SOCKET'): raise ValueError()
    except Exception: raise ValueError('invalid device or environment') from None
    return c,device


class RunnerSource(SessionSource):
    def __init__(self, config, serial, clock=None, missed_triggers=None):
        super().__init__(config, serial)
        self.clock = Clock(**(clock or {}))
        self.missed = missed_triggers or []
        self.missed_pending = deque()
        self.outstanding = deque()
        self.local_to_session = {}
        self.dropped = 0

    def start(self, run_number):
        self.dropped = 0
        self.missed_pending.clear()
        self.outstanding.clear()
        self.local_to_session.clear()
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            self.sock.settimeout(.8)
            self.sock.connect(os.environ['SESSION_SOURCE_SOCKET'])
            self.reader=self.sock.makefile('rb')
            c=self.config
            drain=((c['window_guard_after']+1)*c['window_length_ns'] if c['readout_mode']=='continuous' else c['strobe_delay_ns']+c['strobe_length_ns'])
            reply=self._request('join',pixels=[c['columns'],c['rows']],form='crossing',response={},drain_ps=(drain if self.clock.identity else self.clock.session_span(drain))*1000,run=run_number,sink_required=False)
            self.bounded_history=reply.get('continuous') is True
        except Exception:
            if self.reader: self.reader.close()
            self.sock.close()
            raise


    def watermark_ns(self):
        value = super().watermark_ns()
        return value if self.clock.identity else self.clock.local(value)

    def end_ns(self):
        value = super().end_ns()
        return value if self.clock.identity or value is None else self.clock.local(value)

    def take_until(self, x):
        if self.clock.identity:
            ready = super().take_until(x)
        else:
            ready = [v for v in self.pending if self.clock.local(v.time_ns) < x]
            self.pending = [v for v in self.pending if self.clock.local(v.time_ns) >= x]
            ready = [replace(v, time_ns=self.clock.local(v.time_ns)) for v in ready]
            ready = sorted(ready, key=lambda v: (v.time_ns, 0 if isinstance(v, Crossing) else 1,
                                                 v.crossing_id if isinstance(v, Crossing) else v.trigger_number))
        if not self.missed:
            return ready
        result = []
        for v in ready:
            if isinstance(v, Trigger):
                session = v.trigger_number
                if session in self.missed:
                    self.missed_pending.append(session)
                    self.dropped += 1
                    continue
                local = session - self.dropped
                self.local_to_session[local] = session
                self.outstanding.append(session)
                v = replace(v, trigger_number=local)
            result.append(v)
        self._flush()
        return result

    def mark_sent(self, number):
        if not self.missed:
            return super().mark_sent(number)
        session = self.local_to_session.pop(number)
        if not self.outstanding or self.outstanding[0] != session:
            raise RuntimeError('out-of-order trigger acknowledgement')
        self.outstanding.popleft()
        while self.missed_pending and self.missed_pending[0] < session:
            super().mark_sent(self.missed_pending.popleft())
        super().mark_sent(session)
        self._flush()

    def _flush(self):
        while self.missed_pending and (not self.outstanding or self.missed_pending[0] < self.outstanding[0]):
            super().mark_sent(self.missed_pending.popleft())

    def mark_processed(self, x):
        super().mark_processed(x if self.clock.identity else self.clock.session_time(x))


class Adapter:
    def __init__(self, raw): self.raw=raw
    def window(self, f):
        method = self.raw.window if isinstance(f['hits'], list) else self.raw.window_array
        method(f['window_index'],f['t_begin_ns'],f['t_end_ns'],f['truncated'],f['hits'])
    def trigger(self, t): self.raw.trigger(t['trigger_number'],t['trigger_time_ns'])
    def close(self, counts): return {}


class RunnerDevice(MSensorDevice):
    fast_noise = False
    noise_on = False

    def _fake(self, key, duration):
        if self.noise_on:
            key = f'{self.name}:{self.run_number}:{key}'
        return super()._fake(key, duration)

    def _window(self, index, end, truncated):
        if not self.fast_noise:
            return super()._window(index, end, truncated)
        import numpy as np
        c = self.config
        if not hasattr(self, '_noise_zones'):
            p = self.response
            zones = [z for z in self._zones() if z[0] not in c['tiles_off']]
            lam = []
            base = []
            for region, col, width, row, height in zones:
                th = c['threshold_dac'][region]
                rate = min(p['fake_rate_max'], p['r0'] * math.exp(max(-700, min(700, -(th-p['t_n'])/p['w_n']))))
                lam.append(width * height * rate / p['reference_duration_ns'])
                base.append(row * c['columns'] + col)
            self._noise_zones = (np.asarray(lam, dtype=float), np.asarray(base, dtype=np.int64),
                                 zones[0][2] if zones else 0, zones[0][4] if zones else 0)
        lam, base, width, height = self._noise_zones
        begin = index * c['window_length_ns']
        signal = np.asarray([row*c['columns']+col for t,col,row in self.signals.pop(index, []) if t < end], dtype=np.int64)
        key = int.from_bytes(hashlib.sha256(f"{c['seed']}:fast-noise:{self.name}:{self.run_number}:{index}".encode('ascii')).digest()[:16], 'big')
        g = np.random.Generator(np.random.Philox(key=key))
        n = g.poisson(lam * (end-begin)); total = int(n.sum())
        noise = np.repeat(base, n)
        if total:
            noise += g.integers(0, height, total) * c['columns'] + g.integers(0, width, total)
        keys = np.unique(np.concatenate((signal, noise)))
        hits = np.empty((len(keys), 2), dtype='<u2')
        hits[:, 0] = keys % c['columns']; hits[:, 1] = keys // c['columns']
        frame = {'window_index': index, 't_begin_ns': begin, 't_end_ns': end, 'truncated': truncated, 'hits': hits}
        self.frames[index] = frame
        self.windows_total += 1; self.windows_nonempty += int(len(hits) > 0); self.hits_total += len(hits); self.last_truncated = truncated
        self.stream.window(frame)

    def arm(self, run_number, source, stream_path=None):
        # State initialization must remain identical to MSensorDevice.arm.
        if self.running or self.config is None: raise RuntimeError('device not ready')
        c=self.config
        self.__dict__.pop('_noise_zones', None)
        self.time=0; self.source=source; source.start(run_number); self.run_number=run_number
        self.bounded_history=getattr(source,'bounded_history',False)
        self.signals={}; self.all_hits=[]; self.frames={}; self.pending=[]; self.trigger_records=[]
        self.records_frames_total=0; self.records_hits_total=0
        self.windows_total=0; self.windows_nonempty=0; self.hits_total=0; self.triggers_total=0
        self.stream=None; self.stream_id=0; self.last_truncated=False
        if c['readout_mode']=='continuous':
            identity=f'{run_number}:{self.name}:{self.config_hash}'.encode()
            self.stream_id=(hashlib.sha256(identity).digest()[:16] if c['deterministic_ids'] else secrets.token_bytes(16)).hex()
            self.stream=Adapter(stream_path)
        elif stream_path is not None: raise ValueError('stream only in continuous mode')
        self.running=True


class Output:
    def __init__(self, runner, file): self.runner=runner; self.file=file; self.pending=bytearray()
    def write(self,data):
        self.file.write(data); self.pending.extend(data); return len(data)
    def flush(self):
        self.file.flush()
        if self.pending:
            data=bytes(self.pending); self.pending.clear()
            kind='end' if data[0] == 5 else 'frame'
            self.runner.enqueue(kind,data)
    def header(self):
        data=bytes(self.pending); self.pending.clear(); self.runner.header=data; self.runner.enqueue('header',data)


class Runner:
    device_class = RunnerDevice

    def make_source(self):
        return RunnerSource(self.device.config,self.c['name'],self.c.get('clock'),self.c.get('missed_triggers'))

    def __init__(self,c,device):
        self.c=c; self.device=self.device_class(c['name']); self.device.__dict__.update(device.__dict__)
        self.device.noise_on = c.get('noise') == 'fast'
        self.device.fast_noise = self.device.noise_on and device.config['readout_mode'] == 'continuous'
        self.state='idle'; self.run=None; self.last_error=None; self.last_run_result=None
        self.raw=None; self.file=None; self.source=None; self.sock=None; self.header=b''; self.queue=deque(); self.queued=0
        self.sent=0; self.dropped=0; self.dropped_bytes=0; self.reconnects=0; self.end_sent=False; self.retry=0
        self.path=None; self.lock=threading.RLock(); self.stop_event=threading.Event(); self.client=None; self.last_status=0
        self.ctl_path=Path(c['output_dir'])/(c['name']+'.ctl'); self.status_path=Path(c['output_dir'])/(c['name']+'.status.json')
    def status(self):
        raw=self.raw; counts=raw.counts if raw else {}
        sock=None if self.c['output']=='file' else dict(port=self.c['socket_port'],connected=self.sock is not None,sent_bytes=self.sent,dropped_frames=self.dropped,dropped_bytes=self.dropped_bytes,reconnects=self.reconnects,end_sent=self.end_sent)
        return dict(name=self.c['name'],state=self.state,run=self.run,pid=os.getpid(),time_ns=getattr(self.device,'time',0),frames_written=counts.get('ticks',0),last_tick=({'frame':raw.frame-1,'time_ns':min(raw.frame*self.c['frame_ns'],self.device.time)} if raw and raw.frame else None),records={k:counts.get(k,0) for k in ('tile_data','triggers','tile_status')},hits=counts.get('hits_total',0),file=({'path':str(self.path),'bytes':self.path.stat().st_size} if self.path and self.path.exists() else None),socket=sock,last_error=self.last_error,last_run_result=self.last_run_result,updated_ns=time.time_ns())
    def publish(self):
        data=self.status(); tmp=self.status_path.with_name(self.status_path.name+'.tmp')
        tmp.write_text(json.dumps(data,separators=(',',':'))+'\n'); os.replace(tmp,self.status_path); self.last_status=time.monotonic(); return data
    def transition(self,state):
        self.state=state; self.publish(); print('state '+state,file=sys.stderr,flush=True)
    def connect(self):
        s=None
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            if self.c['socket_sndbuf_bytes'] is not None: s.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,self.c['socket_sndbuf_bytes'])
            s.settimeout(.1)
            s.connect(('127.0.0.1',self.c['socket_port'])); s.setblocking(False); self.sock=s; return True
        except OSError:
            if s is not None: s.close()
            return False
    def discard(self):
        if self.sock:
            self.sock.close(); self.sock=None
        for kind,data,offset in self.queue:
            if kind=='frame': self.dropped+=1; self.dropped_bytes+=len(data)
        self.queue.clear(); self.queued=0; self.retry=time.monotonic()+1
    def enqueue(self,kind,data):
        if self.c['output']=='file': return
        if self.sock is None:
            if kind=='frame':
                self.dropped+=1; self.dropped_bytes+=len(data); return
            if kind=='end' and self.state=='draining' and self.connect():
                self.reconnects+=1; self.enqueue('header',self.header)
            if self.sock is None: return
        if kind=='header' and self.queue: self.queue.appendleft([kind,data,0])
        else: self.queue.append([kind,data,0])
        self.queued+=len(data)
        if self.queued>self.c['send_buffer_bytes']: self.discard()
    def send(self):
        if self.c['output']=='file': return
        if self.sock is None and self.state=='running' and time.monotonic()>=self.retry:
            if self.connect(): self.reconnects+=1; self.enqueue('header',self.header)
            else: self.retry=time.monotonic()+1
        deadline=time.monotonic()+.01; sent_now=0
        while self.sock and self.queue and time.monotonic()<deadline and sent_now<262144:
            kind,data,offset=self.queue[0]
            try: n=self.sock.send(memoryview(data)[offset:offset+262144-sent_now])
            except BlockingIOError: break
            except OSError: self.discard(); break
            if not n: self.discard(); break
            self.sent+=n; sent_now+=n; self.queued-=n; offset+=n
            if offset==len(data):
                self.queue.popleft()
                if kind=='end': self.end_sent=True
            else: self.queue[0][2]=offset; break
    def close_source(self):
        if self.source:
            try:
                if self.source.reader: self.source.reader.close()
                if self.source.sock: self.source.sock.close()
            except OSError: pass
        self.source=None
    def cleanup(self,remove=False):
        self.close_source()
        if self.sock: self.sock.close(); self.sock=None
        self.queue.clear(); self.queued=0
        if self.file: self.file.close(); self.file=None
        if remove and self.path: self.path.unlink(missing_ok=True); self.path=None
        self.device.running=False
    def abort(self):
        if self.state in ('starting','running','draining'):
            self.cleanup(); self.last_run_result='aborted'; self.transition('aborted')
    def metadata(self,n):
        d=self.device.config
        return dict(format='msraw-metadata-1',device=self.c['name'],run_number=n,readout_mode=d['readout_mode'],columns=d['columns'],rows=d['rows'],plane=d['plane'],tiles_x=d['tiles_x'],tiles_y=d['tiles_y'],tiles_off=sorted(d['tiles_off']),tile_columns=d['columns']//d['tiles_x'],tile_rows=d['rows']//d['tiles_y'],window_length_ns=d.get('window_length_ns') if d['readout_mode']=='continuous' else None,strobe_delay_ns=d.get('strobe_delay_ns') if d['readout_mode']=='triggered' else None,strobe_length_ns=d.get('strobe_length_ns') if d['readout_mode']=='triggered' else None,frame_ns=self.c['frame_ns'],anchor_windows=self.c['anchor_windows'],config_hash=self.device.config_hash,software='msensor.rawstream 1')
    def alive(self):
        if self.stop_event.is_set(): return False
        try:
            return not (select.select([self.client],[],[],0)[0] and not self.client.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT))
        except OSError: return False
    def start(self,n):
        if isinstance(n,bool) or not isinstance(n,int) or n<0: return {'ok':False,'error':'invalid run'}
        if self.state not in ('idle','drained','aborted'): return {'ok':False,'error':'busy'}
        self.run=n; self.last_error=None; self.transition('starting')
        self.path=Path(self.c['output_dir'])/f"{self.c['name']}_run{n}.msraw"; created=False
        try:
            try: self.file=self.path.open('xb',buffering=0); created=True
            except FileExistsError: raise RuntimeError('output exists') from None
            self.raw=None; self.sent=self.dropped=self.dropped_bytes=self.reconnects=0; self.end_sent=False; self.queue.clear(); self.queued=0
            if self.c['output']=='socket':
                deadline=time.monotonic()+5
                while not self.connect():
                    if not self.alive(): raise RuntimeError('control disconnected')
                    if time.monotonic()>=deadline: raise RuntimeError('socket connect failed')
                    time.sleep(.2)
            out=Output(self,self.file); self.raw=RawWriter(out,self.metadata(n),bool(self.device.config['deterministic_ids'])); out.header()
            deadline=time.monotonic()+15
            while True:
                if not self.alive(): raise RuntimeError('control disconnected')
                self.source=self.make_source()
                try:
                    self.device.arm(n,self.source,self.raw if self.device.config['readout_mode']=='continuous' else None)
                    if not self.alive(): raise RuntimeError('control disconnected')
                    break
                except (OSError,RuntimeError) as exc:
                    self.close_source()
                    if str(exc)=='control disconnected': raise
                    if time.monotonic()>=deadline: raise RuntimeError('join timeout') from None
                    time.sleep(min(.5,max(0,deadline-time.monotonic())))
            self.transition('running'); return {'ok':True}
        except Exception as exc:
            reason=str(exc) if str(exc) in ('output exists','socket connect failed','join timeout','control disconnected') else 'start failed'
            self.cleanup(remove=created); self.last_error=reason
            self.transition('idle')
            return {'ok':False,'error':reason}
    def write_records(self,records):
        if self.device.config['readout_mode'] != 'triggered': return
        for r in records:
            f=r['frames'][0]
            self.raw.trigger(r['trigger_number'],r['trigger_time_ns'])
            self.raw.gate(r['trigger_number'],r['trigger_time_ns'],min(f['t_begin_ns'],f['t_end_ns']),f['t_end_ns'],f['truncated'],f['hits'])
    def ack(self,records):
        for r in records: self.source.mark_sent(r['trigger_number'])
    def step(self):
        target=self.source.watermark_ns(); records=[]
        if target>self.device.time:
            records=self.device.advance_to(target); self.source.mark_processed(self.device.time)
        self.write_records(records); self.raw.advance(self.device.time); self.ack(records); self.send()
        if time.monotonic()-self.last_status>=.5: self.publish()
    def drain(self):
        if self.state!='running': return {'ok':False,'error':'busy'}
        deadline=time.monotonic()+15; self.transition('draining')
        try:
            self.source.stop(); self.source.poll()
            if time.monotonic()>=deadline: raise TimeoutError()
            if self.source.end_ns()>self.device.time:
                records=self.device.advance_to(self.source.end_ns()); self.source.mark_processed(self.device.time); self.write_records(records); self.ack(records)
            summary=self.device.disarm(); self.write_records(summary['remaining_records']); self.ack(summary['remaining_records']); end=self.raw.finish(self.device.time)
            while not self.source.finished:
                if time.monotonic()>=deadline: raise TimeoutError()
                if not self.alive(): raise ConnectionError()
                self.source.poll()
                if time.monotonic()>=deadline: raise TimeoutError()
                self.send(); time.sleep(.001)
            self.close_source()
            while self.queue:
                if not self.alive(): raise ConnectionError()
                if self.sock is None or time.monotonic()>=deadline:
                    self.discard(); break
                self.send(); time.sleep(.001)
            result={'ok':True,'end_bytes':self.file.tell(),'frames':end['frames_total'],'end_ns':end['end_ns'],'dropped_frames':self.dropped if self.c['output']=='socket' else None,'end_sent':self.end_sent if self.c['output']=='socket' else None}
            self.cleanup(); self.last_error=None; self.last_run_result='drained'; self.transition('drained'); return result
        except Exception as exc:
            self.last_error='drain timeout' if isinstance(exc,TimeoutError) else 'drain failed'; self.abort(); return {'ok':False,'error':self.last_error}
    def command(self,req):
        if not isinstance(req,dict): return {'ok':False,'error':'invalid request'}
        op=req.get('op')
        if op=='status': return {'ok':True,'status':self.publish()}
        if op=='start': return self.start(req.get('run'))
        if op=='drain': return self.drain()
        if op=='abort': self.abort(); return {'ok':True}
        return {'ok':False,'error':'invalid request'}
    def serve_client(self,conn):
        try:
            f=conn.makefile('rb')
            while not self.stop_event.is_set():
                line=f.readline(1024*1024)
                if not line: break
                try: req=json.loads(line)
                except (ValueError,UnicodeError): req=None
                with self.lock: response=self.command(req)
                conn.sendall((json.dumps(response,separators=(',',':'))+'\n').encode())
        except (OSError,ValueError): pass
        finally:
            with self.lock:
                self.abort(); self.client=None
            conn.close()
    def serve(self):
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); bound=False
        try:
            listener.bind(str(self.ctl_path)); bound=True; os.chmod(self.ctl_path,0o600); listener.listen(); listener.setblocking(False); self.publish()
            while not self.stop_event.is_set():
                ready,_,_=select.select([listener],[],[],.001)
                if ready:
                    conn,_=listener.accept()
                    if self.client is not None:
                        conn.sendall(b'{"ok":false,"error":"control busy"}\n'); conn.close()
                    else:
                        self.client=conn; threading.Thread(target=self.serve_client,args=(conn,),daemon=True).start()
                if self.lock.acquire(blocking=False):
                    try:
                        if self.state=='running':
                            try: self.step()
                            except Exception: self.last_error='run failed'; self.abort()
                        elif time.monotonic()-self.last_status>=.5: self.publish()
                    finally: self.lock.release()
        finally:
            with self.lock: self.abort()
            listener.close()
            if bound: self.ctl_path.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--config',required=True); args=parser.parse_args()
    try:
        c,device=configuration(args.config); runner=Runner(c,device)
        if os.path.lexists(runner.ctl_path): raise ValueError('control socket exists')
    except Exception as exc:
        print(str(exc).splitlines()[0],file=sys.stderr); return 2
    def stop(signum,frame): runner.stop_event.set()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try: runner.serve()
    except OSError: print('control socket error',file=sys.stderr); return 2
    return 0

if __name__=='__main__': sys.exit(main())
