# SPDX-License-Identifier: LGPL-3.0-or-later
"""Isolated real EUDAQ scenarios for virtual board boundary, errors and resources."""
import json,os,pathlib,signal,subprocess,sys,threading,time,traceback
import pyeudaq
from alpide_test_source import Source
case=sys.argv[1];root=pathlib.Path('/tmp/alpide-'+case);root.mkdir(exist_ok=True);out=pathlib.Path('/tmp/alpide-results');out.mkdir(exist_ok=True);os.chdir(root)
class RC(pyeudaq.RunControl):
    def __init__(self):super().__init__('44000');self.states={};self.counts={}
    def DoStatus(self,c,s):self.states[c.GetName()]=s.GetState();self.counts[c.GetName()]=s.GetTag('DataEventN','0')
def wait(fn,sec=30):
    end=time.monotonic()+sec
    while time.monotonic()<end:
        v=fn()
        if v:return v
        time.sleep(.1)
    raise TimeoutError('state or data not observed')
def emit(n,status,**measurement):(out/f'V-E{n}.json').write_text(json.dumps({'status':status,'measurement':measurement},indent=2)+'\n')
def cpu(pid):
    words=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split();return (int(words[11])+int(words[12]))/os.sysconf('SC_CLK_TCK')
def launch(n=1,events=6,source_kind='normal',cwd='/opt/virtual-producer-cwd',delay=0):
    serials=[f'VIRTUAL{i}' for i in range(n)];names=[f'ALPIDE_plane_{i}' for i in range(n)]
    frames=[{'time_ps':i*1000000,'pixels':[(i%100,10)]} for i in range(events)]
    source=Source(str(root/'source.sock'),serials,frames,delay,boundary_delay=2 if case=='boundary' else 0) if source_kind!='absent' else None
    init='[RunControl]\n[DataCollector.dc]\ndataproducers = '+','.join(names)+'\n'
    for i,name in enumerate(names):init+=f'[Producer.{name}]\nserial = '+('UNKNOWN' if source_kind=='unknown' else serials[i])+f'\nplane = {i}\ntriggermode = '+('primary' if i==0 else 'replica')+'\n'
    (root/'init.ini').write_text(init);conf='[RunControl]\n'
    for i,name in enumerate(names):conf+=f'[Producer.{name}]\nEUDAQ_DC = dc\nEUDAQ_ID = {i}\nITHR = 100\nSTROBE_LENGTH = 20\n'
    conf+='[DataCollector.dc]\nEUDAQ_ID = 0\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = '+str(root)+'/run$3R$X\nDISABLE_PRINT = 1\n';(root/'run.conf').write_text(conf)
    rc=RC();threading.Thread(target=rc.Exec,daemon=True).start();time.sleep(1)
    env=dict(os.environ,PYTHONPATH='/tmp/alpide-observer:'+os.environ.get('PYTHONPATH',''))
    if source:env['VIRTUAL_ALPIDE_SOURCE_SOCKET']=str(root/'source.sock')
    else:env.pop('VIRTUAL_ALPIDE_SOURCE_SOCKET',None)
    procs=[]
    collector=['python','/opt/eudaq-src/user/ITS3/python/ITS3DataCollector.py','--name','dc','--rc','tcp://127.0.0.1:44000']
    procs.append(subprocess.Popen(collector,env=env,stdout=open(root/'collector.log','w'),stderr=subprocess.STDOUT,cwd=cwd))
    for name in names:
        p=subprocess.Popen(['python','/opt/eudaq-src/user/ITS3/python/ALPIDEProducer.py','--name',name,'-r','tcp://127.0.0.1:44000'],env=env,stdout=open(root/(name+'.log'),'w'),stderr=subprocess.STDOUT,cwd=cwd);procs.append(p)
    wait(lambda:all(rc.states.get(x)==pyeudaq.Status.STATE_UNINIT for x in names+['dc']))
    rc.ReadInitialiseFile(str(root/'init.ini'));rc.Initialise();wait(lambda:all(rc.states.get(x)==pyeudaq.Status.STATE_UNCONF for x in names+['dc']))
    rc.ReadConfigureFile(str(root/'run.conf'));rc.Configure();wait(lambda:all(rc.states.get(x)==pyeudaq.Status.STATE_CONF for x in names+['dc']))
    rc.StartRun()
    return rc,source,procs,names,frames
rc=source=None;procs=[]
try:
    if case in ('boundary','six','idle'):
        n=6 if case=='six' else 1;events=0 if case=='idle' else 100 if case=='boundary' else 6
        rc,source,procs,names,frames=launch(n,events,delay=.015 if case=='boundary' else 0)
        wait(lambda:all(rc.states.get(x)==pyeudaq.Status.STATE_RUNNING for x in names+['dc']))
        if case=='idle':
            pid=procs[1].pid;os.kill(pid,signal.SIGUSR1);wait(lambda:pathlib.Path('/tmp/alpide-process-inspection.json').exists());before=cpu(pid);time.sleep(.2);elapsed=cpu(pid)-before
            inspect=json.loads(pathlib.Path('/tmp/alpide-process-inspection.json').read_text());badmods=[x for x in inspect['modules'] if x in ('usb','usb.core','serial','pyftdi','ftd2xx','libusb1')];badfds=[v for v in inspect['fds'].values() if v.startswith('/dev/') and not (v in ('/dev/null','/dev/urandom') or v.startswith('/dev/pts/'))]; endpoints=[]
            for table in ('tcp','tcp6'):
                for row in pathlib.Path(f'/proc/{pid}/net/{table}').read_text().splitlines()[1:]:
                    remote=row.split()[2].split(':')[0]
                    if remote not in ('00000000','0100007F','00000000000000000000000000000000','00000000000000000000000001000000'): endpoints.append(remote)
        else:wait(lambda:sum(x['op']=='event' for x in source.audit)>= (4 if case=='boundary' else n*events))
        started=time.monotonic();rc.StopRun();wait(lambda:all(rc.states.get(x)==pyeudaq.Status.STATE_STOPPED for x in names+['dc']),15);stopped=time.monotonic()-started
        counts={x:int(rc.counts.get(x,'0')) for x in names};rc.Terminate();time.sleep(2)
        files=list(root.glob('*.raw'));raw=[]
        for f in files:
            reader=pyeudaq.FileReader('native',str(f))
            while True:
                event=reader.GetNextEvent()
                if event is None:break
                if event.GetDescription()=='ITS3global':raw.append([{'name':e.GetDescription(),'trigger':e.GetTriggerN()} for e in event.GetSubEvents()])
            converted=subprocess.run(['/tmp/alpide_convert',str(f)],capture_output=True,text=True,timeout=20)
            if converted.returncode:raise RuntimeError('public converter failed '+converted.stderr[-500:])
        if case=='boundary':
            boundary=source.boundary;triggers=[e['trigger'] for row in raw for e in row];ok=boundary is not None and triggers==list(range(boundary+1)) and counts[names[0]]==len(triggers) and rc.states.get(names[0])!=pyeudaq.Status.STATE_ERROR
            emit(7,'pass' if ok else 'fail',boundary=boundary,triggers=triggers,producer_counts=counts,source_audit=source.audit)
            emit(12,'not_run',reason='deferred; not checked')
        elif case=='six':
            ok=bool(raw) and all(len(row)==6 and len({e['trigger'] for e in row})==1 for row in raw) and all(counts[n]==sum(e['name']==n for row in raw for e in row) for n in names)
            emit(11,'pass' if ok else 'fail',global_events=raw,producer_counts=counts)
        else:
            emit(9,'not_run',reason='deferred; not checked')
            emit(10,'pass' if not badmods and not badfds and not endpoints else 'fail',forbidden_modules=badmods,forbidden_fds=badfds,nonlocal_endpoints=endpoints,inspected_fds=inspect['fds'])
    elif case in ('absent','unknown','outside'):
        kind=case if case in ('absent','unknown') else 'normal';cwd='/tmp' if case=='outside' else '/opt/virtual-producer-cwd'
        rc,source,procs,names,_=launch(source_kind=kind,cwd=cwd)
        wait(lambda:rc.states.get(names[0])==pyeudaq.Status.STATE_ERROR,20)
        state=rc.states.get(names[0]);rc.Terminate();time.sleep(1)
        (out/(case+'-observation.json')).write_text(json.dumps({'status':'pass' if state==pyeudaq.Status.STATE_ERROR else 'fail','state':str(state),'producer_log':(root/(names[0]+'.log')).read_text(errors='replace')[-4000:]},indent=2)+'\n')
except Exception as e:
    (out/(case+'-error.json')).write_text(json.dumps({'error':str(e),'traceback':traceback.format_exc(),'logs':{f.name:f.read_text(errors='replace')[-4000:] for f in root.glob('*.log')}},indent=2)+'\n')
finally:
    if source:source.close()
    for p in procs:
        if p.poll() is None:p.terminate()
    # pyeudaq RC's background thread is process-scoped; no destructor changes upstream behavior.
    os._exit(0)
