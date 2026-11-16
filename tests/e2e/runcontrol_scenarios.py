# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run isolated real-tmux EUDAQ scenarios; results are written on every path."""
import json, os, pathlib, socket, subprocess, time, traceback
root=pathlib.Path('/tmp/runcontrol-e2e'); root.mkdir(exist_ok=True); os.chdir(root)
result=pathlib.Path('/tmp/runcontrol-results'); result.mkdir(exist_ok=True)
def emit(n,status,**details): (result/f'H-E{n}.json').write_text(json.dumps({'status':status,'measurement':details},indent=2)+'\n')
def calls():
    p=root/'calls.jsonl'
    if not p.exists(): return []
    return [json.loads(x) for x in p.read_text().splitlines() if x]
def request(data):
    s=socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(str(root/'control.sock')); s.sendall(data+b'\n'); out=s.recv(65536); s.close(); return json.loads(out)
def wait(pred,seconds=25):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if pred(): return True
        time.sleep(.05)
    return False
(root/'init.ini').write_text('[RunControl]\ndataproducers = TEST_0\nmoreproducers =\nloggers =\ncollectors = direct\nconfigs = /tmp/runcontrol-e2e/a.conf,/tmp/runcontrol-e2e/b.conf\n[DataCollector.direct]\n[Producer.TEST_0]\n')
for name,val in [('a','first'),('b','second')]:
    (root/(name+'.conf')).write_text(f'[RunControl]\nNEVENTS = 15\n[Producer.TEST_0]\nVALUE = {val}\nEUDAQ_DC = direct\n[DataCollector.direct]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/runcontrol-e2e/run$3R$X\n')
procs=[]; samples=[]; malformed=0
try:
    cmd='python -u /opt/eudaq-src/user/ITS3/python/ITS3RunControl.py --address 44003 --ini /tmp/runcontrol-e2e/init.ini --status-file /tmp/runcontrol-e2e/status.json --control-socket /tmp/runcontrol-e2e/control.sock --one-pass; echo $? > /tmp/runcontrol-e2e/exit'
    observer_env=dict(os.environ, PYTHONPATH='/tmp/runcontrol_observer:'+os.environ.get('PYTHONPATH',''), RUNCONTROL_TEST_STATUS_PATH=str(root/'status.json'), RUNCONTROL_TEST_JOURNAL=str(root/'published.jsonl'), RUNCONTROL_TEST_RECEIVED=str(root/'received.jsonl'))
    subprocess.run(['tmux','new-session','-d','-s','e2e',cmd],check=True,env=observer_env)
    time.sleep(1)
    for name,args in [('collector',['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44003']),('producer',['python','/tmp/runcontrol_producer.py'])]:
        procs.append(subprocess.Popen(args,stdout=open(name+'.log','w'),stderr=subprocess.STDOUT))
    if not wait(lambda:(root/'control.sock').exists()): raise RuntimeError('control socket unavailable')
    mode=(root/'control.sock').stat().st_mode & 0o777
    bad1=request(b'{not-json'); bad2=request(b'{"id":"unknown","intent":"bogus"}')
    idle=socket.socket(socket.AF_UNIX); idle.connect(str(root/'control.sock'))
    key_sent=False; end=time.monotonic()+50
    while time.monotonic()<end:
        try:
            doc=json.loads((root/'status.json').read_text()); samples.append(doc)
            if doc['rc_state']=='RUNNING' and not key_sent:
                subprocess.run(['tmux','send-keys','-t','e2e','S']); key_sent=True
        except FileNotFoundError: pass
        except json.JSONDecodeError: malformed+=1
        if any(x['call']=='Terminate' for x in calls()): break
        if any(p.poll() is not None for p in procs): break
        time.sleep(.01)
    idle.close()
    cs=calls(); lifecycle=[c for c in cs if c['call'] not in ('Event','Status')]; states=[d['rc_state'] for d in samples]; seq=[d['seq'] for d in samples]
    terminated=any(c['call']=='Terminate' for c in cs); configs=[c.get('value') for c in cs if c['call']=='Configure']
    emit(6,'pass' if terminated and configs==['first','second'] else 'fail',configs=configs,lifecycle=lifecycle)
    emit(3,'pass' if samples and malformed==0 else 'fail',reads=len(samples),parse_errors=malformed)
    keyboard=any(d.get('last_intent') and d['last_intent'].get('source')=='keyboard' for d in samples)
    emit(7,'pass' if key_sent and keyboard and len(configs)>=2 else 'fail',key_sent=key_sent,keyboard_observed=keyboard,configs=configs)
    emit(9,'not_run' if bad1.get('accepted') is False and bad2.get('accepted') is False and terminated else 'fail',reason='lifecycle was not compared with a control run',responses=[bad1,bad2],terminated=terminated)
    # Q is accepted only after upstream has terminated.
    gone = False
    if terminated:
        for _ in range(30):
            subprocess.run(['tmux','send-keys','-t','e2e','Q'], stderr=subprocess.DEVNULL)
            if wait(lambda:not (root/'control.sock').exists(),.2): gone=True; break
    emit(11,'pass' if mode==0o600 and gone else 'fail',mode=oct(mode),removed=gone)
    # The observer records every successfully replaced status file, not sampled approximations.
    journal=root/'published.jsonl'
    published=[json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    independent={c['count'] for c in cs if c['call']=='Event'} | {0}
    observations=[{'timestamp_ns':d['updated_monotonic_ns'],'value':c['data_events'],'rc_state':d['rc_state'],'connection_state':c['state'],'config_index':d['config']['index']} for d in published for c in d['connections'] if c['name']=='TEST_0']
    producer_status=[c for c in cs if c['call']=='Status']
    received_path=root/'received.jsonl'
    received=[json.loads(line) for line in received_path.read_text().splitlines()] if received_path.exists() else []
    mismatches=[]
    per_run={0:[],1:[]}
    for item in observations:
        value=item['value']; t=item['timestamp_ns']; index=item['config_index']
        active=(item['rc_state']=='RUNNING' and item['connection_state']=='RUNNING' and index in per_run)
        if active: per_run[index].append(value)
        candidate=[c for c in producer_status if c['count']==value and c['monotonic_ns']<=t and (not active or c['run_index']==index+1)]
        actual_received=any(x['value']==value and x['monotonic_ns']<=t for x in received)
        if value not in independent or (value and (not candidate or not actual_received)): mismatches.append(item)
    counter_monotonic=all(all(a<=b for a,b in zip(values,values[1:])) for values in per_run.values())
    states=[d['rc_state'] for d in published]
    transitions=[x for i,x in enumerate(states) if i==0 or x!=states[i-1]]
    expected=['CONWAIT','UNCONF','CONF','RUNNING','STOPPED','CONF','RUNNING','STOPPED','TERMINATED']
    journal_seq=[d['seq'] for d in published]
    monotonic=all(a<b for a,b in zip(journal_seq,journal_seq[1:]))
    states_ok=transitions==expected
    ok=bool(observations) and bool(received) and all(per_run.values()) and not mismatches and counter_monotonic and monotonic and states_ok
    emit(2,'pass' if ok else 'fail',observed_values=sorted({x['value'] for x in observations}),producer_event_max=max(independent),producer_status_records=len(producer_status),producer_reports=producer_status,runcontrol_received=received,unmatched_samples=mismatches[:20],published_count=len(published),monotonic_seq=monotonic,counter_monotonic_per_run=counter_monotonic,running_counts_by_config=per_run,states=transitions,expected_states=expected)

except Exception as exc:
    (result/'harness-error.json').write_text(json.dumps({'error':str(exc),'traceback':traceback.format_exc(),'calls':calls(),'logs':{p.name:p.read_text(errors='replace')[-5000:] for p in root.glob('*.log')}},indent=2)+'\n')
finally:
    for n in range(1,15):
        if not (result/f'H-E{n}.json').exists(): emit(n,'not_run',reason='scenario was not implemented or did not reach its criterion; see harness-error.json if present')
    for p in procs:
        if p.poll() is None: p.terminate()
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
