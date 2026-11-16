# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare the real unmodified and patched ITS3 run-control lifecycle."""
import json, os, pathlib, socket, subprocess, time, traceback
root=pathlib.Path('/tmp/runcontrol-e2e'); out=pathlib.Path('/tmp/runcontrol-results'); root.mkdir(exist_ok=True); out.mkdir(exist_ok=True); os.chdir(root)
(root/'init.ini').write_text('[RunControl]\ndataproducers = TEST_0\nmoreproducers =\nloggers =\ncollectors = direct\nconfigs = /tmp/runcontrol-e2e/a.conf,/tmp/runcontrol-e2e/b.conf\n[DataCollector.direct]\n[Producer.TEST_0]\n')
def emit(n,status,**data): (out/f'H-E{n}.json').write_text(json.dumps({'status':status,'measurement':data},indent=2)+'\n')
def calls():
    p=root/'calls.jsonl'
    return [json.loads(x) for x in p.read_text().splitlines() if x] if p.exists() else []
def wait(pred,timeout=25):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if pred(): return True
        time.sleep(.1)
    raise RuntimeError('timed out waiting for lifecycle or socket')
def scenario(original=False, commands=('S','T'), socket_mode=False, invalid=False, write_error=False, edit_repeat=False):
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
    for pattern in ('*.raw','calls.jsonl','status.json','control.sock','exit','ready'):
        for p in root.glob(pattern): p.unlink()
    for name,value in [('a','first'),('b','second')]:
        (root/(name+'.conf')).write_text(f'[RunControl]\nNEVENTS = 1000000\n[Producer.TEST_0]\nVALUE = {value}\nEUDAQ_DC = direct\n[DataCollector.direct]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/runcontrol-e2e/run$3R$X\n')
    binary='ITS3RunControl.original.py' if original else 'ITS3RunControl.py'
    opt=''
    if socket_mode or invalid: opt=' --status-file /tmp/runcontrol-e2e/status.json --control-socket /tmp/runcontrol-e2e/control.sock'
    if write_error: opt=' --status-file /proc/no-such-directory/status.json'
    cmd=f'python -u /opt/eudaq-src/user/ITS3/python/{binary} --address 44003 --ini /tmp/runcontrol-e2e/init.ini{opt}; echo $? > /tmp/runcontrol-e2e/exit'
    observer_env=dict(os.environ, PYTHONPATH='/tmp/runcontrol_ready_observer:'+os.environ.get('PYTHONPATH',''), RUNCONTROL_READY_TRACE=str(root/'ready'))
    subprocess.run(['tmux','new-session','-d','-s','compare',cmd],check=True,env=observer_env); time.sleep(1)
    children=[]
    try:
        for name,args in [('collector',['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44003']),('producer',['python','/tmp/runcontrol_producer.py'])]:
            children.append(subprocess.Popen(args,stdout=open(name+'.log','w'),stderr=subprocess.STDOUT))
        if socket_mode or invalid: wait(lambda:(root/'control.sock').exists())
        if invalid:
            replies=[]
            for data in (b'{bad\n',b'{"id":"x","intent":"bogus"}\n'):
                client=socket.socket(socket.AF_UNIX); client.settimeout(3);client.connect(str(root/'control.sock'));client.sendall(data);replies.append(json.loads(client.recv(4096)));client.close()
        else: replies=[]
        for idx,key in enumerate(commands):
            wait(lambda:sum(c['call']=='StartRun' for c in calls())>=idx+1)
            wait(lambda: (root/'ready').exists() and len((root/'ready').read_text().splitlines()) >= idx+1)
            if edit_repeat and idx==0:
                conf=root/'a.conf'; conf.write_text(conf.read_text().replace('VALUE = first','VALUE = edited'))
            if socket_mode:
                client=socket.socket(socket.AF_UNIX);client.settimeout(3);client.connect(str(root/'control.sock'));client.sendall((json.dumps({'id':str(idx),'intent':{'S':'stop_next','R':'repeat','T':'terminate'}[key]})+'\n').encode());reply=json.loads(client.recv(4096));client.close()
                if not reply.get('accepted'): raise RuntimeError('intent rejected')
            else: subprocess.run(['tmux','send-keys','-t','compare',key],check=True)
        wait(lambda:any(c['call']=='Terminate' for c in calls()))
        data=[{k:v for k,v in c.items() if k!='count'} for c in calls() if c['call'] not in ('Event','Status')]
        return {'lifecycle':data,'replies':replies}
    finally:
        for child in children:
            if child.poll() is None: child.terminate()
        subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
        time.sleep(.5)
target=os.environ.get('RUNCONTROL_TEST_TARGET')
try:
    patched=None
    if target in (None,'1','9','10'):
        base=scenario(original=True) if target in (None,'1') else None
        patched=scenario()
        if target in (None,'1'):
            emit(1,'pass' if base['lifecycle']==patched['lifecycle'] else 'fail',original=base['lifecycle'],patched=patched['lifecycle'])
    if target in (None,'4'):
        keyboard=scenario(commands=('R','S','T'))
        remote=scenario(commands=('R','S','T'),socket_mode=True)
        emit(4,'pass' if keyboard['lifecycle']==remote['lifecycle'] else 'fail',keyboard=keyboard['lifecycle'],socket=remote['lifecycle'])
    if target in (None,'5'):
        edited=scenario(commands=('R','T'), edit_repeat=True)
        values=[c.get('value') for c in edited['lifecycle'] if c['call']=='Configure']
        emit(5,'pass' if values==['first','edited'] else 'fail',lifecycle=edited['lifecycle'],configured_values=values)
    if target in (None,'9'):
        invalid=scenario(invalid=True)
        emit(9,'pass' if all(not r.get('accepted') for r in invalid['replies']) and invalid['lifecycle']==patched['lifecycle'] else 'fail',responses=invalid['replies'],control=patched['lifecycle'],invalid=invalid['lifecycle'])
    if target in (None,'10'):
        try:
            error=scenario(write_error=True)
            emit(10,'pass' if error['lifecycle']==patched['lifecycle'] else 'fail',control=patched['lifecycle'],unwritable_path=error['lifecycle'])
        except Exception as exc:
            emit(10,'fail',control=patched['lifecycle'],error=str(exc),reason='unwritable status interrupted the lifecycle')
except Exception as exc:
    (out/'comparison-error.json').write_text(json.dumps({'error':str(exc),'traceback':traceback.format_exc(),'calls':calls(),'logs':{p.name:p.read_text(errors='replace')[-5000:] for p in root.glob('*.log')}},indent=2)+'\n')
