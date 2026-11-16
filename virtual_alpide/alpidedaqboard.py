# SPDX-License-Identifier: LGPL-3.0-or-later
"""Virtual interface derived solely from the pinned public EUDAQ producer/decoder."""
import datetime
import json
import os
import pathlib
import socket
import threading
import time

_PARAMS=json.loads(pathlib.Path(__file__).with_name('response.json').read_text())

class _Value:
    def __init__(self, value=0, changed=None): self.value=value; self.changed=changed
    def write(self, value):
        self.value=int(value)
        if self.changed: self.changed(self.value)
    def read(self): return self.value

class _Pulse:
    def __init__(self, changed=None): self.changed=changed
    def issue(self):
        if self.changed: self.changed()

class _Block: pass

def encode_frame(trigger, time_ps, pixels, chipid=0x10):
    if not isinstance(trigger,int) or not 0 <= trigger <= 0xffffffff: raise ValueError('invalid trigger')
    if not isinstance(time_ps,int) or not 0 <= time_ps//12500 <= 0xffffffffffffffff: raise ValueError('invalid time')
    ordered=sorted({tuple(p) for p in pixels})
    for p in ordered:
        if len(p)!=2 or any(not isinstance(v,int) or isinstance(v,bool) for v in p) or not 0<=p[0]<1024 or not 0<=p[1]<512: raise ValueError('invalid pixel')
    if not ordered: payload=bytes([0xe0|(chipid&15),0,0,0])
    else:
        data=bytearray([0xa0|(chipid&15),0]); region=None
        for x,y in ordered:
            address=((x&~1)<<9)|(y<<1)|((x&1)^(y&1)); r=address>>14
            if r!=region: data.append(0xc0|r);region=r
            local=address&0x3fff; data.extend([0x40|(local>>8),local&255])
        data.append(0xb0)
        while len(data)%4: data.append(0xff)
        payload=bytes(data)
    return b'\xaa'*4+trigger.to_bytes(4,'little')+(time_ps//12500).to_bytes(8,'little')+payload+b'\xbb'*4

class ALPIDEDAQBoard:
    def __init__(self, serial):
        if not isinstance(serial,str) or not serial: raise ValueError('serial required')
        self.serial=serial;self.registers={};self._lock=threading.RLock();self._connection=None;self._reader=None
        self._armed=False;self._stopping=False;self._finished=False;self._ack=None;self._event_count=0;self._stop_started=None
        self.trg=_Block();self.trg.ctrl=_Value(changed=self._trigger_control);self.trg.opcode=_Value();self.trg.rstrdobsy=_Pulse();self.trg.minspacing=_Value();self.trg.fixedbusy=_Value()
        self.trgseq=_Block();self.trgseq.dt_set=_Value();self.trgseq.ntrg_set=_Value();self.trgseq.stop=_Pulse()
        self.trgmon=_Block();self.trgmon.clr=_Pulse(changed=self._clear_counter);self.trgmon.lat=_Pulse();self.trgmon.regs={'events':_Counter(self)}
        self.rdoctrl=_Block();self.rdoctrl.rst=_Pulse();self.rdoctrl.delay_set=_Value();self.rdoctrl.chipid=_Value();self.rdoctrl.ctrl=_Value()
        self.rdopar=_Block();self.rdopar.rst=_Pulse();self.rdopar.clr=_Pulse();self.rdopar.ctrl=_Value()
        self.evtpkr=_Block();self.evtpkr.rst=_Pulse();self.evtbld=_Block();self.evtbld.rst=_Pulse();self.rdomux=_Block();self.rdomux.ctrl=_Value();self.xonxoff=_Block();self.xonxoff.rst=_Pulse();self.xonxoff.ctrl=_Value()
    def _clear_counter(self):
        with self._lock:
            self._event_count=0
    def _params(self):
        ithr=next((v for (chip,addr),v in reversed(list(self.registers.items())) if addr==0x60e),128); fraction=max(0.0,min(1.0,ithr/255.0))
        strobe=next((v for (chip,addr),v in self.registers.items() if addr==5),None)
        if strobe is None or strobe<0: raise ValueError('strobe not configured')
        return {'efficiency':1.0-fraction,'cluster_mean':2.0-fraction,'gate_ps':strobe*_PARAMS['strobe_tick_ps']}
    def _request(self, request):
        if self._connection is None: raise ConnectionError('stimulus source is not connected')
        self._connection.sendall((json.dumps(request,separators=(',',':'))+'\n').encode())
        line=self._reader.readline()
        if not line: raise ConnectionError('stimulus source disconnected')
        reply=json.loads(line)
        if not isinstance(reply,dict) or reply.get('error'): raise RuntimeError('stimulus source rejected request')
        return reply
    def _trigger_control(self,value):
        with self._lock:
            if value&1:
                if self._armed and not self._stopping:
                    self._stop_started=time.monotonic()
                    self._request({'op':'stop','serial':self.serial}); self._stopping=True
            else:
                if self._armed: raise RuntimeError('already armed')
                path=os.environ.get('VIRTUAL_ALPIDE_SOURCE_SOCKET')
                if not path: raise RuntimeError('stimulus source unavailable')
                connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);connection.settimeout(3)
                try:
                    connection.connect(path);self._connection=connection;self._reader=connection.makefile('rb')
                    self._request({'op':'join','serial':self.serial,'pixels':[1024,512],'response':self._params(),'sink_required':True})
                except BaseException:
                    if self._reader: self._reader.close()
                    connection.close();self._connection=None;self._reader=None;raise
                self._armed=True;self._stopping=False;self._finished=False;self._ack=None;self._event_count=0
    def event_read(self):
        while True:
            with self._lock:
                if not self._armed:
                    if self._finished:
                        # The public producer can keep polling until it receives
                        # StopRun even after the finite source has finished.
                        time.sleep(.05)
                        return b''
                    raise RuntimeError('board is not armed')
                r=self._request({'op':'next','serial':self.serial,'ack':self._ack})
                self._ack=None
                if r.get('kind')=='event':
                    trigger=r['trigger']; raw=encode_frame(trigger,r['time_ps'],r['pixels'],r.get('chipid',0x10));self._ack=trigger;self._event_count+=1;return raw
                if r.get('kind')=='finished':
                    self._armed=False;self._finished=True;self._reader.close();self._connection.close();self._reader=None;self._connection=None;return b''
                if r.get('kind')!='wait':raise RuntimeError('invalid stimulus response')
                stopping=self._stopping
                if stopping and time.monotonic()-self._stop_started>12: raise TimeoutError('stimulus drain deadline exceeded')
                # Wake for the next scheduled trigger instead of a fixed 50 ms period.
                delay=min(.05,max(.001,r.get('retry_ps',50_000_000_000)/1e12))
            time.sleep(delay)
            if not stopping:return b''
    def power_on(self): pass
    def power_status(self): return (0.0,0.0,True)
    def carrier_temp(self): return None
    def purge_events(self):
        with self._lock:
            if self._armed: raise RuntimeError('cannot purge an armed board')
            self._ack=None;self._finished=False
    def alpide_cmd_issue(self, cmd): pass
    def alpide_reg_write(self,addr,value,chipid=0x10): self.registers[(int(chipid),int(addr))]=int(value)
    def alpide_reg_read(self,addr,chipid=0x10): return self.registers.get((int(chipid),int(addr)),0)
    def get_fpga_git(self): return 'virtual-alpide-firmware'
    def get_fpga_tcompile(self): return datetime.datetime(2000,1,1)
    def get_software_git(self): return 'virtual-alpide-software'
    def get_software_diff(self): return ''

class _Counter:
    def __init__(self,board): self.board=board
    def read(self): return self.board._event_count
