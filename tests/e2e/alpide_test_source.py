# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only stimulus server; real virtual boards use its public socket protocol."""
import json,os,socket,threading,time

class Source:
    def __init__(self,path,serials,frames,delay=0,boundary_delay=0):
        self.path=path;self.serials=set(serials);self.frames=frames;self.delay=delay;self.boundary_delay=boundary_delay;self.lock=threading.RLock();self.wave=0;self.members={};self.stopped=False;self.boundary=None;self.audit=[];self.closed=False
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.server.bind(path);self.server.listen(16);self.server.settimeout(.2)
        threading.Thread(target=self._accept,daemon=True).start()
    def _accept(self):
        while not self.closed:
            try:c,_=self.server.accept()
            except socket.timeout:continue
            except OSError:break
            threading.Thread(target=self._client,args=(c,),daemon=True).start()
    def _client(self,c):
        with c:
            f=c.makefile('rb')
            for line in f:
                try:r=self._handle(json.loads(line))
                except Exception as e:r={'error':str(e)}
                try:c.sendall((json.dumps(r)+'\n').encode())
                except OSError:break
    def _handle(self,r):
        with self.lock:
            op=r['op'];serial=r['serial']
            if op=='join':
                if serial not in self.serials:return {'error':'unknown serial'}
                if self.members and all(v['finished'] for v in self.members.values()) and len(self.members)==len(self.serials):
                    self.wave+=1;self.members={};self.stopped=False;self.boundary=None
                if serial in self.members:return {'error':'duplicate join'}
                self.members[serial]={'next':0,'ack':-1,'finished':False};self.audit.append({'wave':self.wave,'op':'join','serial':serial,'response':r['response'],'pixels':r['pixels']});return {'ok':True}
            if serial not in self.members:return {'error':'not joined'}
            m=self.members[serial]
            if op=='stop':
                if not self.stopped:
                    self.stopped=True;self.boundary=max((v['next']-1 for v in self.members.values()),default=-1);self.audit.append({'wave':self.wave,'op':'boundary','trigger':self.boundary});time.sleep(self.boundary_delay)
                return {'ok':True,'boundary':self.boundary}
            if op!='next':return {'error':'bad operation'}
            if r.get('ack') is not None:
                if r['ack']<m['ack']:return {'error':'invalid ack'}
                m['ack']=r['ack'];self.audit.append({'wave':self.wave,'op':'ack','serial':serial,'trigger':r['ack']})
            if len(self.members)!=len(self.serials):return {'kind':'wait'}
            if self.stopped and m['next']>self.boundary:
                if all(v['ack']>=self.boundary for v in self.members.values()):
                    m['finished']=True;return {'kind':'finished'}
                return {'kind':'wait'}
            if m['next']>=len(self.frames):return {'kind':'wait'}
            if self.delay:time.sleep(self.delay)
            n=m['next'];m['next']+=1;frame=self.frames[n];self.audit.append({'wave':self.wave,'op':'event','serial':serial,'trigger':n,'time_ps':frame['time_ps'],'pixels':frame['pixels']})
            return {'kind':'event','trigger':n,'time_ps':frame['time_ps'],'pixels':frame['pixels']}
    def close(self):
        self.closed=True;self.server.close()
        try:os.unlink(self.path)
        except FileNotFoundError:pass
