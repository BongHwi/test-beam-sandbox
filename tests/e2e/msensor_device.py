#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Public device E2E checks; writes one JSON per requested result."""
import copy, hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from msensor.device import MSensorDevice
from msensor.source import SyntheticSource
from msensor.codec import encode
from msensor.stream import read

fixture=json.loads(Path('/work/tests/e2e/msensor_fixtures/test_fixtures.json').read_text())
outdir=Path('/out'); outdir.mkdir(parents=True,exist_ok=True)
image=os.environ.get('IMAGE_ID','unknown'); commit=os.environ.get('TEST_COMMIT','unknown')
common=fixture['common']
base={k:common[k] for k in ('columns','rows','regions','seed','deterministic_ids','beam_center_col','beam_center_row','beam_sigma_px')}
base.update(plane=1,stream_dir='/tmp',clock_mode='virtual')

def record(name,status,conditions,measurement,criterion,reason=None):
    obj={'id':name,'status':status,'level':'device','input_conditions':conditions,'measurement':measurement,'criterion':criterion,'image_id':image,'repository_commit':commit,'corryvreckan_source':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'}
    if reason: obj['reason']=reason
    (outdir/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')

def run(name, overrides=None, step=None, pause=False, flush=False):
    f=fixture['fixtures'][name]; c=dict(base)
    c.update({k:v for k,v in f.items() if k not in ('noise','compare','comment','level','run_number','advance_partitions_ns','extra')})
    c.update(overrides or {})
    td=tempfile.mkdtemp(); c['stream_dir']=td
    if f.get('noise')=='off':
        p=json.loads(Path('/work/msensor/response_default.json').read_text()); p['r0']=0
        rp=Path(td)/'response.json'; rp.write_text(json.dumps(p)); c['response_file']=str(rp)
    device=MSensorDevice('test'); device.configure(c); source=SyntheticSource(device.config)
    device.arm(1,source); end=c['sim_end_ns']; position=0; events=[]
    turns=0
    while position<end:
        increment = ([177, 91003, 1200003, 531, 701117][turns % 5] if step == 'irregular' else (step or end))
        position=min(end,position+increment); events+=device.advance_to(position); turns+=1
        if flush and device.stream: device.stream.flush()
        if pause: time.sleep(.00001)
    summary=device.disarm(); events+=summary.pop('remaining_records')
    path=Path(td)/'test_run1.msws'; stream=read(path) if path.exists() else None
    digest=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return c,events,summary,stream,digest,td

if len(sys.argv)>1 and sys.argv[1]=='--partition-child':
    x=run('D_partition',step=1000000)
    print(json.dumps({'sha256':x[4], 'payloads':[encode(r).hex() for r in x[1]]}))
    raise SystemExit(0)

failures=[]
def execute(name, fn, criterion):
    try:
        ok, inputs, measurement=fn(); status='pass' if ok else 'fail'; record(name,status,inputs,measurement,criterion)
    except Exception as e:
        status='fail'; record(name,status,{}, {'error':repr(e)},criterion)
    if status!='pass': failures.append(name)

runs=[]
try:
    for step,pause in [(7919,False),(1000000,False),(50000000,False),('irregular',False),(1000000,True)]: runs.append(run('D_partition',step=step,pause=pause))
    hashes=[x[4] for x in runs]; payloads=[[encode(r).hex() for r in x[1]] for x in runs]
    child=subprocess.run([sys.executable,__file__,'--partition-child'],capture_output=True,text=True,timeout=60,check=True)
    fresh=json.loads(child.stdout)
    for name, ok in [('D1',len(set(hashes[:4]))==1),('D2',all(payloads[0]==p for p in payloads[1:4])),('D3',hashes[1]==hashes[4] and payloads[1]==payloads[4]),('D4',fresh['sha256']==hashes[1] and fresh['payloads']==payloads[1])]:
        record(name,'pass' if ok else 'fail',{'fixture':'D_partition'}, {'stream_sha256':hashes,'event_counts':[len(x) for x in payloads]},'all partitioned runs produce identical stream and trigger records')
        if not ok: failures.append(name)
except Exception as e:
    for name in ('D1','D2','D3','D4'): record(name,'fail',{'fixture':'D_partition'},{'error':repr(e)},'same output across partitions'); failures.append(name)

try:
    one=run('D_partition',step=1000000,flush=False); many=run('D_partition',step=1000000,flush=True)
    ok=one[4]==many[4] and [encode(e) for e in one[1]]==[encode(e) for e in many[1]]
    record('FLUSH','pass' if ok else 'fail',{'fixture':'D_partition','flush':['at close','each advance']},{'stream_sha256':[one[4],many[4]]},'flush schedule does not change stream bytes or events')
    if not ok: failures.append('FLUSH')
except Exception as e: record('FLUSH','fail',{'fixture':'D_partition'},{'error':repr(e)},'flush does not change output'); failures.append('FLUSH')

try:
    trial=run('C_same_window'); path=Path(trial[5])/'test_run1.msws'; encoded=b''.join(encode(r) for r in trial[1]); raw=path.read_bytes()
    forbidden=(b'20261110',b'"seed"',b'"trigger_times_ns"',b'"beam_center_col"')
    present=[x.decode() for x in forbidden if x in encoded or x in raw]
    ok=not present; record('SECRET-device','pass' if ok else 'fail',{'fixture':'C_same_window'}, {'forbidden_markers_present':present},'seed and source internals absent in event payloads and stream')
    if not ok: failures.append('SECRET-device')
except Exception as e: record('SECRET-device','fail',{}, {'error':repr(e)},'no source internals in public outputs'); failures.append('SECRET-device')

try:
    z=run('C_rate0'); _,events,summary,stream,digest,_=z
    checks={'Z2':summary['windows_total']==2000 and not summary['last_window_truncated'],
            'Z3':7000<=summary['hits_total']<=10800 and stream['counts']['hits_total']==summary['hits_total'],
            'Z4':stream['status']=='closed_clean' and summary['stream_file_sha256']==digest}
    for name,ok in checks.items():
        record(name,'pass' if ok else 'fail',{'fixture':'C_rate0'}, {'event_count':len(events),'summary':summary,'stream_status':stream['status'],'file_sha256':digest}, {'Z2':'2000 full windows','Z3':'7000..10800 hits and matching footer','Z4':'closed_clean and matching full digest'}[name]);
        if not ok: failures.append(name)
except Exception as e:
    for name in ('Z2','Z3','Z4'): record(name,'fail',{'fixture':'C_rate0'},{'error':repr(e)},'zero trigger stream requirements'); failures.append(name)

try:
    low=run('C_noise_dir',{'threshold_dac':80}); high=run('C_noise_dir',{'threshold_dac':120})
    a,b=low[2]['hits_total'],high[2]['hits_total']; ok=7000<=a<=10800 and 80<=b<=260 and a>=10*b
    record('A5','pass' if ok else 'fail',{'fixture':'C_noise_dir','thresholds':[80,120]},{'hits':[a,b]},'7000..10800; 80..260; ratio >= 10')
    if not ok: failures.append('A5')
except Exception as e: record('A5','fail',{'fixture':'C_noise_dir'},{'error':repr(e)},'noise direction'); failures.append('A5')

for name,fix in [('S3','C_same_window'),('S4a','C_truncated_end'),('S4b','C_guard_before')]:
    try:
        _,events,summary,stream,digest,_=run(fix)
        if name=='S3':
            coverage=sum(1 for r in stream['records'] if (r['kind']=='WINDOW' and r['data']['window_index']==2) or (r['kind']=='EMPTY_RUN' and r['data']['first_index']<=2<r['data']['first_index']+r['data']['count']))
            ok=len(events)==2 and all(e['window_index']==2 for e in events) and coverage==1 and len([x for x in stream['records'] if x['kind']=='TRIGGER'])==2
        elif name=='S4a': ok=summary['windows_total']==11 and summary['last_window_truncated'] and events[0]['guard_incomplete_after'] and [x['window_index'] for x in events[0]['frames']]==[9,10] and events[0]['frames'][-1]['t_end_ns']==1050000 and stream['status']=='closed_clean'
        else: ok=events[0]['guard_incomplete_before'] and [x['window_index'] for x in events[0]['frames']]==[0,1]
        record(name,'pass' if ok else 'fail',{'fixture':fix},{'summary':summary,'events':events,'stream_status':stream['status']},'fixture boundary and guard conditions from test-conditions.md')
        if not ok: failures.append(name)
    except Exception as e: record(name,'fail',{'fixture':fix},{'error':repr(e)},'fixture boundary and guard conditions'); failures.append(name)

try:
    _,events,summary,stream,digest,td=run('C_stream')
    recs=stream['records']; windows={}; triggers=[]
    for r in recs:
        if r['kind']=='WINDOW': windows[r['data']['window_index']]=r['data']
        elif r['kind']=='EMPTY_RUN':
            for k in range(r['data']['first_index'],r['data']['first_index']+r['data']['count']): windows[k]={'window_index':k,'hits':[]}
        elif r['kind']=='TRIGGER': triggers.append(r['data'])
    matches=all(any(t['trigger_number']==e['trigger_number'] and t['trigger_time_ns']==e['trigger_time_ns'] and t['window_index']==e['window_index'] for t in triggers) and all(sorted(map(tuple,f['hits']))==sorted(map(tuple,windows[f['window_index']]['hits'])) for f in e['frames']) for e in events)
    ok=(stream['status']=='closed_clean' and len(events)==len(triggers)==summary['triggers_total'] and matches and len(windows)==summary['windows_total'] and all(stream['counts'][k]==summary[k] for k in stream['counts']) and digest==summary['stream_file_sha256'])
    record('S1','pass' if ok else 'fail',{'fixture':'C_stream'},{'events':len(events),'triggers':len(triggers),'windows':len(windows),'projection_matches':matches,'summary':summary,'stream_status':stream['status']},'stream coverage, projections, counts and full digest agree')
    if not ok: failures.append('S1')
    path=Path(td)/'test_run1.msws'; data=path.read_bytes(); wr=next(r for r in recs if r['kind']=='WINDOW'); footer=next(r for r in recs if r['kind']=='FOOTER'); wi=recs.index(wr); fi=recs.index(footer)
    wstart=wr['offset']; wend=recs[wi+1]['offset']; fstart=footer['offset']
    cases={
      'S5a':(data[:wstart],'truncated',wi),
      'S5b':(data[:wstart+3],'truncated',wi),
      'S5c':(data[:wstart+6],'truncated',wi),
      'S5d':(data[:fstart+7],'truncated',fi),
      'S6a':(data[:wstart+5]+bytes([data[wstart+5]^1])+data[wstart+6:],'corrupt',None),
      'S6b':(data[:wstart+1]+(2**32-1).to_bytes(4,'big')+data[wstart+5:],'corrupt',None),
      'S6c':(data[:wstart]+data[wend:],'corrupt',None),
      'S6d':(data+b'!', 'corrupt',None),
      'S6e':(bytes([data[0]^1])+data[1:],'corrupt',None),
    }
    for name,(changed,want,count) in cases.items():
        p=Path(td)/(name+'.msws'); p.write_bytes(changed); recovered=read(p)
        ok=recovered['status']==want and (count is None or len(recovered['records'])==count) and (want!='corrupt' or ('reason' in recovered and 'offset' in recovered))
        if name=='S6a': ok=ok and len(recovered['records'])==wi
        record(name,'pass' if ok else 'fail',{'fixture':'C_stream','mutation':name},{'status':recovered['status'],'reason':recovered.get('reason'),'offset':recovered.get('offset'),'valid_records':len(recovered['records'])},'expected truncation or corruption classification and valid-prefix length')
        if not ok: failures.append(name)
except Exception as e:
    for name in ('S1','S5a','S5b','S5c','S5d','S6a','S6b','S6c','S6d','S6e'):
        record(name,'fail',{'fixture':'C_stream'},{'error':repr(e)},'stream consistency or corruption handling'); failures.append(name)

try:
    valid=dict(base,readout_mode='continuous',threshold_dac=100,window_length_ns=100000,trigger_rate_hz=0,sim_end_ns=1000000)
    device=MSensorDevice('config'); device.configure(valid)
    previous=copy.deepcopy(device.config); previous_hash=device.config_hash
    mutations=[{'unknown_key':1},{'plane':-1},{'columns':0},{'columns':4097},{'rows':0},{'rows':4097},
      {'regions':0},{'regions':65},{'regions':3},{'seed':-1},{'seed':2**63},{'clock_mode':'invalid'},
      {'sim_step_ns':0},{'pace':0},{'source':'unknown'},{'deterministic_ids':2},{'threshold_dac':256},
      {'threshold_dac':-1},{'threshold_dac':[1,2]},{'readout_mode':'invalid'},{'window_length_ns':999},
      {'window_length_ns':10000001},{'window_guard_before':9},{'window_guard_after':-1},
      {'strobe_delay_ns':0},{'trigger_rate_hz':-1},{'trigger_period_ns':100,'trigger_first_ns':1},
      {'trigger_times_ns':[3,2]}, {'beam_sigma_px':-1},{'sim_end_ns':-1},{'plane':2**32}, {'stream_dir':[]}, {'threshold_dac':True},{'clock_mode':True},{'beam_center_col':float('inf')},{'window_guard_before':-1},{'window_guard_after':9}]
    rejected=[]; failures_detail=[]
    for changes in mutations:
        invalid=dict(valid,**changes)
        try: device.configure(invalid); failures_detail.append({'accepted':changes})
        except (ValueError,OSError,KeyError,TypeError): rejected.append(changes)
        if device.config!=previous or device.config_hash!=previous_hash: failures_detail.append({'state_changed_after':changes})
    special=[]
    for key in ('plane','columns','rows','seed','readout_mode','threshold_dac','window_length_ns','stream_dir'):
        broken=dict(valid); broken.pop(key); special.append(('missing_'+key,broken))
    no_rate=dict(valid); no_rate.pop('trigger_rate_hz')
    special.extend([('no_trigger_source',no_rate),('unsorted_times',dict(no_rate,trigger_times_ns=[3,2])),('negative_time',dict(no_rate,trigger_times_ns=[-1])),('period_without_first',dict(no_rate,trigger_period_ns=1)),('zero_period',dict(no_rate,trigger_period_ns=0,trigger_first_ns=1)),('zero_first',dict(no_rate,trigger_period_ns=1,trigger_first_ns=0))])
    triggered={k:v for k,v in valid.items() if k not in ('stream_dir','window_length_ns')}; triggered.update(readout_mode='triggered',strobe_delay_ns=0,strobe_length_ns=1)
    for changes in ({'strobe_delay_ns':-1},{'strobe_delay_ns':1000001},{'strobe_length_ns':0},{'strobe_length_ns':1000001},{'window_length_ns':100000},{'window_guard_after':1},{'stream_dir':'/tmp'}): special.append(('triggered_invalid',dict(triggered,**changes)))
    for name,invalid in special:
        try: device.configure(invalid); failures_detail.append({'accepted':name})
        except (ValueError,OSError,KeyError,TypeError): rejected.append(name)
        if device.config!=previous or device.config_hash!=previous_hash: failures_detail.append({'state_changed_after':name})
    running_dir=tempfile.mkdtemp(); armed=MSensorDevice('armed'); active=dict(valid,stream_dir=running_dir); armed.configure(active); armed.arm(1,SyntheticSource(armed.config)); before=copy.deepcopy(armed.config)
    try: armed.configure(active); failures_detail.append({'accepted':'configure_during_run'})
    except RuntimeError: rejected.append('configure_during_run')
    if armed.config!=before: failures_detail.append({'state_changed_after':'configure_during_run'})
    armed.disarm()
    total=len(mutations)+len(special)+1; ok=not failures_detail and len(rejected)==total
    record('CFG','pass' if ok else 'fail',{'valid_fixture':'continuous','boundary_and_combination_cases':total},{'rejected_count':len(rejected),'case_count':total,'violations':failures_detail},'required keys, boundary values, invalid mode combinations and run-time configure rejected without state change')
    if not ok: failures.append('CFG')
except Exception as e: record('CFG','fail',{}, {'error':repr(e)},'invalid configure rejected without changing state'); failures.append('CFG')

for name in ('A1','A2','A3','A4','Z1','Z2-eudaq','Z3-eudaq','Z4-eudaq','S1-eudaq','S7','S8','E1','E2','E3'):
    record(name,'not_run',{},None,'contract test pending','E2E not implemented or not executed')
print(json.dumps({'failed':failures,'not_run':14}))
sys.exit(1 if failures else 0)  # the outer runner checks all cross-component results
