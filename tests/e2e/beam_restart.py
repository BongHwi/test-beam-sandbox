# SPDX-License-Identifier: LGPL-3.0-or-later
"""Keep the real source alive through two separate launcher tmux sessions."""
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
import pyeudaq

root=Path('/tmp/beam-m6');root.mkdir(exist_ok=True)
out=Path('/tmp/beam-results');out.mkdir(exist_ok=True)
sock=str(root/'source.sock');truth=str(root/'truth')
private={'seed':1857092461,'device_seed':1857092460,
         'beam':{'position_mm':[1.234567891,-2.345678912,-20],'direction':[0.00123456789,0,1],
                 'phase_space_covariance':[[0.00345678912,0,0,0,0]]+[[0]*5 for _ in range(4)]},
         'rate_hz':237.654321987}
env=dict(os.environ,SESSION_SOURCE_CONFIG_JSON=json.dumps(private))
process=subprocess.Popen(['python','-m','session_core.server','--geometry','/tmp/session_example_geometry.json',
                          '--socket',sock,'--truth-dir',truth],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    deadline=time.monotonic()+10
    while not Path(sock+'.beam.json').exists():
        if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('source startup failed')
        time.sleep(.01)
    def control(action):
        p=subprocess.run(['python','-m','session_core.beamctl','--socket',sock,action],capture_output=True,text=True,timeout=10)
        if p.returncode:raise RuntimeError('beam control failed')
        return json.loads(p.stdout)['status']
    first=control('on');pid=process.pid;pidfile=int((Path(truth)/'session.pid').read_text())
    data=[];states=[];tmux_gone=[];pids=[]
    for number in (1,2):
        runroot=root/('run'+str(number));child_env=dict(os.environ,BEAM_E2E_CASE='existing',BEAM_E2E_ROOT=str(runroot),
                                                        BEAM_E2E_SOCKET=sock,BEAM_E2E_TRUTH=truth)
        p=subprocess.run(['python','/tmp/beam_acquire.py'],env=child_env,capture_output=True,text=True,timeout=100)
        (root/('run%d.log'%number)).write_text(p.stdout+p.stderr)
        if p.returncode:raise RuntimeError('launcher run %d failed'%number)
        raw=list(runroot.glob('*.raw'))
        if len(raw)!=1:raise RuntimeError('expected one raw')
        reader=pyeudaq.FileReader('native',str(raw[0]));count=0
        while True:
            event=reader.GetNextEvent()
            if event is None:break
            if event.GetDescription()=='ITS3global':count+=1
        data.append(count);states.append(control('status')['beam_on'])
        tmux_gone.append(subprocess.run(['tmux','has-session','-t','ITS3'],capture_output=True).returncode!=0)
        pids.append(int((Path(truth)/'session.pid').read_text()))
    ok=(first['beam_on'] is True and pid==pidfile and process.poll() is None and
        pids==[pid,pid] and states==[True,True] and all(tmux_gone) and all(x>0 for x in data))
    result={'status':'pass' if ok else 'fail','measurement':{'initial_pid':pid,'observed_pids':pids,
            'beam_on_after_runs':states,'events':data,'tmux_closed_after_runs':tmux_gone}}
    (out/'M-E6.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
except Exception:
    (out/'M-E6.json').write_text(json.dumps({'status':'fail','reason':traceback.format_exc()},indent=2)+'\n')
    raise
finally:
    process.terminate()
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.wait()
