# SPDX-License-Identifier: LGPL-3.0-or-later
"""End-to-end launcher run with the real public ITS3 collector and msensor."""
import json, os, pathlib, subprocess, time, traceback
import pyeudaq
from msensor.stream import read as read_stream
root=pathlib.Path('/tmp/launcher-e2e'); root.mkdir(exist_ok=True); os.chdir(root)
out=pathlib.Path('/tmp/runcontrol-results')
def emit(n,status,**values): (out/f'H-E{n}.json').write_text(json.dumps({'status':status,'measurement':values},indent=2)+'\n')
try:
    (root/'init.ini').write_text('''[RunControl]\ndataproducers = MSENSOR_0\nmoreproducers =\nloggers =\ncollectors = dc\nconfigs = /tmp/launcher-e2e/a.conf,/tmp/launcher-e2e/b.conf\n[DataCollector.dc]\ndataproducers = MSENSOR_0\n[Producer.MSENSOR_0]\nplane = 1\ncolumns = 32\nrows = 32\nregions = 1\ndeterministic_ids = 1\nclock_mode = virtual\nsim_step_ns = 100000\n''')
    for name,mode in [('a','triggered'),('b','continuous')]:
        extra='strobe_delay_ns = 0\nstrobe_length_ns = 2000' if mode=='triggered' else 'stream_dir = /tmp/launcher-e2e\nwindow_length_ns = 100000\nwindow_guard_before = 1\nwindow_guard_after = 1'
        (root/(name+'.conf')).write_text(f'''[RunControl]\nNEVENTS = 3\n[Producer.MSENSOR_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 1\nreadout_mode = {mode}\nthreshold_dac = 100\n{extra}\n[DataCollector.dc]\nEUDAQ_ID = 1\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/launcher-e2e/run$3R$X\nDISABLE_PRINT = 1\n''')
    source={'seed':86753091234,'trigger_times_ns':[50000,250000,750000],'sim_end_ns':1000000,'beam_center_col':16,'beam_center_row':16,'beam_sigma_px':0}
    env=dict(os.environ,MSENSOR_SOURCE_CONFIG_JSON=json.dumps(source), PYTHONPATH='/tmp/runcontrol_observer:'+os.environ.get('PYTHONPATH',''), RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'), RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'), RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
    cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),'--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py','--rc-address','44007','--producer-env','MSENSOR_SOURCE_CONFIG_JSON','--no-attach','--rc-args',f'--one-pass --status-file {root}/status.json --control-socket {root}/control.sock']
    launched=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20)
    (root/'launcher.log').write_text(launched.stdout+launched.stderr)
    if launched.returncode: raise RuntimeError(f'launcher exited {launched.returncode}')
    listing=subprocess.check_output(['tmux','list-windows','-t','ITS3','-F','#{window_name} #{pane_title}'],text=True)
    windows={line.split(' ',1)[0] for line in listing.splitlines()}; expected={'rc','rp','dp','sp','dc','log','perf'}
    emit(12,'pass' if windows==expected and 'dp Producer MSENSOR 0' in listing else 'fail',windows=listing.splitlines())
    status={}; deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        if (root/'status.json').exists():
            status=json.loads((root/'status.json').read_text())
            if status['rc_state']=='TERMINATED': break
        time.sleep(.1)
    # Terminate callback follows the state transition. Wait for files and counters to settle.
    time.sleep(2)
    status=json.loads((root/'status.json').read_text()) if (root/'status.json').exists() else {}
    if not status:
        panels={name:subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+name,'-S','-100'],capture_output=True,text=True).stdout for name in ('rc','dp','dc')}
        (out/'launcher-debug.json').write_text(json.dumps({'panes':panels,'logs':{p.name:p.read_text(errors='replace')[-5000:] for p in root.glob('*.log')}},indent=2)+'\n')
    raws=sorted(root.glob('*.raw')); streams=sorted(root.glob('*.msws')); readings=[]; all_tags=[]
    for raw in raws:
        reader=pyeudaq.FileReader('native',str(raw)); count=0; bore=eore=False
        while True:
            ev=reader.GetNextEvent()
            if ev is None: break
            all_tags.append(ev.GetTags())
            all_tags.extend(sub.GetTags() for sub in ev.GetSubEvents())
            if ev.IsBORE(): bore=True
            if ev.IsEORE(): eore=True
            if ev.GetDescription()=='ITS3global' and not ev.IsBORE() and not ev.IsEORE(): count+=1
        readings.append({'file':raw.name,'data_events':count,'bore':bore,'eore':eore})
    stream_data=[{'file':p.name,'status':read_stream(p)['status']} for p in streams]
    journal=root/'published.jsonl'
    published=[json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    stopped=[]
    for index in (0,1):
        matches=[d for d in published if d['rc_state']=='STOPPED' and d['config']['index']==index]
        conn=[x for x in matches[0]['connections'] if x['name']=='MSENSOR_0'] if matches else []
        stopped.append(conn[0]['data_events'] if conn else None)
    match=len(readings)==2 and stopped==[x['data_events'] for x in readings]
    ok=status.get('rc_state')=='TERMINATED' and len(readings)==2 and all(x['bore'] and x['eore'] for x in readings) and len(stream_data)==1 and stream_data[0]['status']=='closed_clean' and match
    emit(13,'pass' if ok else 'fail',producer='msensor.msensor_producer (direct)',rc_state=status.get('rc_state'),raws=readings,streams=stream_data,stopped_status_events=stopped,status_matches_each_raw=match,published_count=len(published))
    titles=subprocess.check_output(['tmux','list-panes','-a','-F','#{window_name} #{pane_title}'],text=True)
    logs=''.join(p.read_text(errors='replace') for p in root.glob('*.log'))
    logs+=''.join(subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+name,'-S','-10000'],capture_output=True,text=True).stdout for name in ('rc','dp','dc','log'))
    surfaces=json.dumps([status,listing,titles,all_tags])+logs
    markers=['86753091234','trigger_times_ns','beam_center_col','beam_center_row','beam_sigma_px','sim_end_ns']
    leaked=[x for x in markers if x in surfaces]
    emit(14,'fail' if leaked else 'pass' if ok else 'not_run',source_markers_found=leaked,surfaces=['status','logs','pane titles','all raw event and subevent tags'],h_e13_passed=ok)
except Exception as exc:
    data={'error':str(exc),'traceback':traceback.format_exc(),'logs':{p.name:p.read_text(errors='replace')[-3000:] for p in root.glob('*.log')}}
    (out/'launcher-error.json').write_text(json.dumps(data,indent=2)+'\n')
finally:
    for n in (12,13,14):
        if not (out/f'H-E{n}.json').exists(): emit(n,'not_run',reason='launcher scenario did not reach criterion; see launcher-error.json')
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
