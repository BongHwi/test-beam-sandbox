#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise session limits and device failure with actual public EUDAQ."""
import json,platform,subprocess,sys,tempfile,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'artifacts/robustness';OUT.mkdir(parents=True,exist_ok=True)
image='local/session-core:native'
rev=(ROOT/'SOURCE_REVISION').read_text().strip()
image_id=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()

def command(*args,allow=False,timeout=180):
 p=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
 if p.returncode and not allow:raise RuntimeError(args[0]+' exit '+str(p.returncode)+': '+(p.stdout+p.stderr)[-3000:])
 return p

def write(id,status,measurement,reason=None):
 d={'id':id,'status':status,'input_conditions':{'architecture':platform.machine()},'measurement':measurement,'criterion':'documented end-to-end acceptance criteria','repository_commit':rev,'image_id':image_id}
 if reason:d['reason']=reason
 (OUT/(id+'.json')).write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print(id,status,flush=True)
 return status=='pass'

def scenario(case):
 name='robust-session-'+uuid.uuid4().hex[:10]
 with tempfile.TemporaryDirectory(prefix='robust-session-') as td:
  try:
   command('docker','create','--name',name,'--network','none',image,'sleep','600')
   for f in ('robust_session_scenario.py','robust_source_observer.py','robust_raw_inspect.py',
             'robust_gate_inspect.py','session_compare.py','status_compare.py','alpide_convert.cc'):
    command('docker','cp',str(ROOT/'tests/e2e'/f),name+':/tmp/'+f)
   for d in ('robust_session_observer','runcontrol_observer'):
    target='session_observer' if d=='robust_session_observer' else d
    command('docker','cp',str(ROOT/'tests/e2e'/d),name+':/tmp/'+target)
   command('docker','start',name)
   p=command('docker','exec','-e','ROBUST_CASE='+case,name,'python','/tmp/robust_session_scenario.py',allow=True,timeout=150)
   command('docker','cp',name+':/tmp/session-e2e/.',td,allow=True)
   root=Path(td);out=root/'robust-observation.json'
   obs=json.loads(out.read_text()) if out.exists() else {}
   audit=root/'truth/protocol.jsonl';rows=[json.loads(x) for x in audit.read_text().splitlines()] if audit.exists() else []
   journal=root/'published.jsonl';published=[json.loads(x) for x in journal.read_text().splitlines()] if journal.exists() else []
   events=root/'session.sock.session.log';eventlog=[json.loads(x) for x in events.read_text().splitlines()] if events.exists() else []
   collector_file=root/'truth/collector-output.jsonl';collector_events=[json.loads(x) for x in collector_file.read_text().splitlines()] if collector_file.exists() else []
   truth=[json.loads(p.read_text()) for p in (root/'truth').glob('run-*.json')]
   inspect=command('docker','exec',name,'python','/tmp/robust_raw_inspect.py','/tmp/session-e2e',allow=True)
   try:raw=json.loads(inspect.stdout.splitlines()[-1])
   except (ValueError,IndexError):raw=[]
   gate=None
   if case in ('c8a','c8b'):
    p_gate=command('docker','exec',name,'python','/tmp/robust_gate_inspect.py',case,allow=True)
    try:gate=json.loads(p_gate.stdout.splitlines()[-1])
    except (ValueError,IndexError):gate={'error':(p_gate.stdout+p_gate.stderr)[-1500:],'exit_code':p_gate.returncode}
   return {'exit_code':p.returncode,'error_tail':(p.stdout+p.stderr)[-1800:] if p.returncode else '',
           'obs':obs,'rows':rows,'journal':published,'eventlog':eventlog,'collector_events':collector_events,'truth':truth,'raw':raw,'raw_inspection_exit':inspect.returncode,'gate':gate}
  finally:command('docker','rm','-f',name,allow=True)

def aligned(raw):
 return all(len({x['trigger'] for x in group})==1 and len({x['name'] for x in group})==len(group) for file in raw for group in file['groups'])

def normal(raw, *, empty=False):
 required={'ALPIDE_plane_0_status','ALPIDE_plane_1_status','MSENSOR_0_status'}
 names={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'}
 return bool(raw) and all(required<=set(f['bore']) and required<=set(f['eore']) and (empty or bool(f['groups'])) and all({e['name'] for e in g}==names for g in f['groups']) for f in raw) and aligned(raw)

def transitions(journal):
 changes=[];last={}
 for row in journal:
  for c in row.get('connections',[]):
   name=c.get('name');key=(c.get('state'),c.get('connected'))
   if last.get(name)!=key:
    changes.append({'name':name,'state':key[0],'connected':key[1],'data_events':c.get('data_events'),'monotonic_ns':row.get('updated_monotonic_ns')});last[name]=key
 return changes

def observation_docs_match(case):
 base=ROOT/'release/docs/docs' if (ROOT/'release/docs/docs').is_dir() else ROOT/'docs'
 paths=('session-core.md','ko/session-core.md','ja/session-core.md')
 docs=[(base/p).read_text() for p in paths]
 expected={
  'c10a': [('appears disconnected','continues waiting','raw can remain open and readable'),
           ('\uc5f0\uacb0 \ub04a\uae40\uc73c\ub85c \ud45c\uc2dc\ub429\ub2c8\ub2e4','\uacc4\uc18d \uae30\ub2e4\ub9b4 \uc218 \uc788\uc73c\uba70','raw\ub294 \uc5f4\ub9b0 \ucc44 \uc77d\uc744 \uc218'),
           ('\u5207\u65ad\u3068\u3057\u3066\u8868\u793a\u3055\u308c\u307e\u3059','\u5f85\u3061\u7d9a\u3051\u308b\u5834\u5408','raw \u306f\u958b\u3044\u305f\u307e\u307e\u8aad\u307f\u53d6\u308c\u308b\u5834\u5408')],
  'c10b': [('status can remain RUNNING','collector can resume assembling','run can still remain waiting'),
           ('\uc0c1\ud0dc\uac00 RUNNING\uc73c\ub85c \ub0a8\uace0','event \uc870\ub9bd\uc744 \ub2e4\uc2dc \uc2dc\uc791\ud560 \uc218','run\uc740 \uacc4\uc18d \uae30\ub2e4\ub9b4 \uc218'),
           ('\u72b6\u614b\u306f RUNNING \u306e\u307e\u307e','\u7d44\u307f\u7acb\u3066\u3092\u518d\u958b\u3067\u304d\u3066\u3082','run \u304c\u5f85\u6a5f\u3057\u7d9a\u3051\u308b\u5834\u5408')],
  'c10resume': [('can enter `ERROR`','stop request can be rejected'),
                ('RunControl\ub3c4 `ERROR`\uac00 \ub420 \uc218','\uba48\ucda4 \uc694\uccad\uc774 \uac70\ubd80\ub418\uace0'),
                ('RunControl \u3082 `ERROR` \u306b\u306a\u3063\u3066','\u505c\u6b62\u8981\u6c42\u304c\u62d2\u5426\u3055\u308c')],
 }
 return all(all(x in doc for x in terms) for doc,terms in zip(docs,expected[case]))

def loss_observation(id,x):
 o=x['obs'];files=x['raw'];journal=x['journal'];kill=o.get('kill_at');stop=o.get('stop_at')
 collectors=[c.get('data_events',0) for j in journal if kill and j.get('updated_monotonic_ns',0)>kill for c in j.get('connections',[]) if c.get('name')=='dc']
 raw_readable=x['raw_inspection_exit']==0 and bool(files)
 exercised=bool(o.get('resumed_at') and o.get('stop_attempted')) if o.get('case')=='c10resume' else bool(kill and o.get('stop_attempted'))
 obs={'id':id+'-observation','status':'observed' if exercised else 'not_run','repository_commit':rev,'image_id':image_id,
      'input_conditions':{'architecture':platform.machine(),'scenario':o.get('case')},
      'measurement':{'scenario_exit':x['exit_code'],'runcontrol_state':o.get('state'),'status_transitions':transitions(journal),
       'collector_restarted':bool(collectors and o.get('collector_at_kill') is not None and max(collectors)>o['collector_at_kill']),
       'collector_before_disconnect':o.get('collector_at_kill'),'collector_max_after_disconnect':max(collectors) if collectors else None,
       'stop_attempted':o.get('stop_attempted'),'stop_requested':bool(stop),'stop_request_error':o.get('stop_error'),'raw_readable':raw_readable,
       'raw_closed':(bool(raw_readable and all(f['eore'] for f in files) and not o['raw_open_fds']) if 'raw_open_fds' in o else None),
       'raw_open_fds':o.get('raw_open_fds'),
       'global_events_in_raw':sum(len(f['groups']) for f in files) if raw_readable else None,
       'resumed_at':o.get('resumed_at'),'documentation_matches':observation_docs_match(o['case']),'source_events':x['eventlog']},
      'criterion':'observation only; public RunControl and collector determine the result'}
 if not exercised:obs['reason']=x['error_tail'] or 'the requested observation scenario did not reach the required action'
 (OUT/(id+'-observation.json')).write_text(json.dumps(obs,indent=2,ensure_ascii=False)+'\n')
 return obs

def assess_loss(case):
 x=scenario(case);o=x['obs'];files=x['raw'];groups=[g for f in files for g in f['groups']]
 loss_kind='disconnected' if case=='c10a' else 'response_timeout'
 loss=next((e for e in x['eventlog'] if e.get('kind')==loss_kind and e.get('serial')=='V1'),None)
 loss_at=loss.get('monotonic_ns') if loss else None;stop=o.get('stop_at')
 before=[j for j in x['journal'] if loss_at and j.get('updated_monotonic_ns',0)<=loss_at]
 after=[j for j in x['journal'] if loss_at and j.get('updated_monotonic_ns',0)>loss_at and (not stop or j.get('updated_monotonic_ns',0)<stop)]
 def value(j,name):return next((c.get('data_events',0) for c in j.get('connections',[]) if c.get('name')==name),0)
 names={'ALPIDE_plane_0':'V0','MSENSOR_0':'MSENSOR_0'}
 counts={name:{'before':value(before[-1],name) if before else None,
               'after':max((value(j,name) for j in after),default=None),
               'triggers_after_loss':sum(1 for r in x['rows'] if loss_at and r.get('monotonic_ns',0)>loss_at and (not stop or r.get('monotonic_ns',0)<stop) and r.get('serial')==serial and r.get('op')=='next' and r.get('kind')=='event' and 'trigger' in r)} for name,serial in names.items()}
 grows=all(c['before'] is not None and c['after'] is not None and c['after']>c['before'] and c['triggers_after_loss']>0 for c in counts.values())
 disconnected=any(c.get('name')=='ALPIDE_plane_1' and not c.get('connected') for j in x['journal'] for c in j.get('connections',[]))
 pre_stop=[j for j in x['journal'] if not stop or j.get('updated_monotonic_ns',0)<stop]
 errors=[{'name':c['name'],'state':c.get('state'),'rc_state':j.get('rc_state')} for j in pre_stop for c in j.get('connections',[]) if c.get('name') in names and c.get('state')=='ERROR']
 rc_error_at=next((j.get('updated_monotonic_ns') for j in pre_stop if j.get('rc_state')=='ERROR'),None)
 source_errors=[r for r in x['rows'] if r.get('serial') in names.values() and r.get('kind')=='rejected' and (not stop or r.get('monotonic_ns',0)<stop) and (not rc_error_at or r.get('monotonic_ns',0)<rc_error_at)]
 # Once public RunControl itself reports ERROR, its later producer states are
 # observation, not evidence that the added source caused that response.
 added_errors=[e for e in errors if e['rc_state']!='ERROR']
 # A killed process is gone when SIGKILL is sent. The collector may remove its
 # connection before the source notices the disconnect and writes its record.
 raw_loss_at=min(loss_at,o['kill_at']) if case=='c10a' and loss_at and o.get('kill_at') else loss_at
 collector_before=[e for e in x['collector_events'] if raw_loss_at and e['monotonic_ns']<raw_loss_at]
 valid_names={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'};survivors={'ALPIDE_plane_0','msensor'}
 shape=all({e['name'] for e in g} in (valid_names,survivors) for g in groups)
 prefix=groups[:len(collector_before)]
 prior_complete=all({e['name'] for e in g}==valid_names for g in prefix)
 observed_prefix=[e['group'] for e in collector_before[:len(prefix)]]
 before_agrees=prefix==observed_prefix
 collector_before_record=[e for e in x['collector_events'] if loss_at and e['monotonic_ns']<loss_at]
 source_record_prefix=groups[:len(collector_before_record)]
 raw_readable=x['raw_inspection_exit']==0 and bool(files)
 raw_ok=not raw_readable or (shape and prior_complete and before_agrees and aligned(files))
 ok=bool(loss) and bool(collector_before) and grows and disconnected and not added_errors and not source_errors and raw_ok
 measurement={'loss_recorded':bool(loss),'loss_kind':loss_kind,'survivor_progress':counts,'disconnected_status':disconnected,
              'survivor_errors_before_stop':errors,'added_path_errors_before_stop':added_errors,'source_errors_before_stop':source_errors,'raw_readable':raw_readable,'raw_valid_when_readable':raw_ok,
              'actual_loss_monotonic_ns':raw_loss_at,'source_loss_record_monotonic_ns':loss_at,
              'collector_groups_before_loss':len(collector_before),'raw_groups_before_loss':len(prefix),'collector_raw_prefix_matches':before_agrees,
              'prior_groups_missing_participants':[i for i,g in enumerate(prefix) if {e['name'] for e in g}!=valid_names],
              'groups_missing_before_source_record':[i for i,g in enumerate(source_record_prefix) if {e['name'] for e in g}!=valid_names],
              'unexpected_group_count':sum({e['name'] for e in g} not in (valid_names,survivors) for g in groups),
              'raw_groups':len(groups),'raw_aligned':aligned(files) if raw_readable else None}
 return ok,measurement,x

def losses(id,case):
 ok,measurement,x=assess_loss(case);obs=loss_observation(id,x)
 verdict=write(id,'pass' if ok else 'fail',measurement,None if ok else x['error_tail'] or 'loss, producer progress or raw check failed')
 return verdict and obs['status']=='observed' and obs['measurement']['documentation_matches']

def resume_recovery():
 x=scenario('c10resume');obs=loss_observation('G1-resume-recovery',x)
 result=obs.copy();result['id']='G1-resume-recovery'
 (OUT/'G1-resume-recovery.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
 print('G1-resume-recovery observed',flush=True)
 return obs['status']=='observed' and obs['measurement']['documentation_matches']

def barrier():
 x=scenario('c7');r=x['rows'];events=[q for q in r if q['op']=='next' and q['kind'] in ('event','crossing_tail')]
 early=[q for q in events if q['joined']<q['expected']]
 ok=x['exit_code']==0 and x['obs'].get('delay_exercised') and bool(events) and not early and normal(x['raw'])
 return write('C-E7','pass' if ok else 'fail',{'exit_code':x['exit_code'],'delayed_join_observed':x['obs'].get('delay_exercised'),'delivered_events':len(events),'early_events':len(early)},x['error_tail'] or None)

def core_loss():
 x=scenario('c11');kill=x['obs'].get('kill_at');times={}
 for row in x['journal']:
  for c in row.get('connections',[]):
   if c.get('role')=='producer' and c.get('state')=='ERROR':times.setdefault(c['name'],row['updated_monotonic_ns'])
 elapsed={k:(v-kill)/1e9 for k,v in times.items()} if kill is not None else {}
 ok=x['exit_code']==0 and len(elapsed)==3 and all(0<=t<=5 for t in elapsed.values())
 return write('C-E11','pass' if ok else 'fail',{'exit_code':x['exit_code'],'producer_error_delays_s':elapsed,'limit_s':5},x['error_tail'] or None)

def missing():
 x=scenario('c12');u=scenario('unknown')
 event=next((e for e in x['eventlog'] if e.get('kind')=='join_timeout' and e.get('serial')=='ABSENT'),None)
 delivered=[r for r in x['rows'] if r['op']=='next' and r['kind']=='event']
 unknown_events=[r for r in u['rows'] if r['op']=='next' and r['kind']=='event']
 early=[r for r in delivered if event and r['monotonic_ns']<event['monotonic_ns']]
 unknown_error=u['obs'].get('state')=='ERROR'
 join_rejected=any(r['op']=='join' and r['serial']=='UNREGISTERED' and r['kind']=='rejected' for r in u['rows'])
 ok=x['exit_code']==0 and event is not None and bool(delivered) and not early and normal(x['raw']) and u['exit_code']==0 and unknown_error and join_rejected and not unknown_events
 return write('C-E12','pass' if ok else 'fail',{'missing_exit':x['exit_code'],'join_timeout_recorded':bool(event),'events_after_timeout':len(delivered),'events_before_timeout':len(early),'unknown_exit':u['exit_code'],'unknown_state':u['obs'].get('state'),'unknown_join_rejected':join_rejected,'unknown_stimuli':len(unknown_events)},x['error_tail'] or u['error_tail'] or None)

def limits():
 observations=[]
 for case in ('events','intersections','pixels'):
  x=scenario(case)
  observed=sorted({c.get('source_limit') for j in x['journal'] for c in j.get('connections',[]) if c.get('name')=='MSENSOR_0' and c.get('message')=='source_finished' and c.get('source_limit')})
  observations.append({'case':case,'exit_code':x['exit_code'],'state':x['obs'].get('state'),'reported_limits':observed,'raw_normal':normal(x['raw'])})
 ok=all(r['exit_code']==0 and r['state']=='TERMINATED' and r['reported_limits']==[r['case']] and r['raw_normal'] for r in observations)
 return write('C-E13','pass' if ok else 'fail',{'limits':observations})

def beam_off():
 x=scenario('beam_off');observed=sorted({c.get('source_limit') for j in x['journal'] for c in j.get('connections',[]) if c.get('name')=='MSENSOR_0' and c.get('message')=='source_finished' and c.get('source_limit')})
 groups=sum(len(f['groups']) for f in x['raw'])
 ok=x['exit_code']==0 and x['obs'].get('state')=='TERMINATED' and observed==['events'] and groups==0 and normal(x['raw'],empty=True)
 return write('M-E14','pass' if ok else 'fail',{'exit_code':x['exit_code'],'state':x['obs'].get('state'),'reported_limits':observed,'global_events':groups,'raw_normal':normal(x['raw'],empty=True)},x['error_tail'] or None)

def truth_mapping():
 x=scenario('c16');raw=[f['run'] for f in x['raw']];truth=[r for t in x['truth'] for r in t.get('eudaq_runs',[])]
 ok=x['exit_code']==0 and len(raw)>=2 and len(raw)==len(set(raw)) and len(truth)==len(set(truth)) and sorted(raw)==sorted(truth)
 return write('C-E16','pass' if ok else 'fail',{'exit_code':x['exit_code'],'raw_runs':raw,'truth_eudaq_runs':truth,'session_runs':[t['session_run'] for t in x['truth']]},x['error_tail'] or None)

def gate_setting(id,case):
 x=scenario(case);g=x['gate'] or {};o=x['obs']
 ok=(x['exit_code']==0 and normal(x['raw']) and g.get('all_small_subsets') is True and
     g.get('strictly_larger_frames',0)>0 and g.get('other_devices_unchanged') is True)
 if case=='c8b':ok=ok and o.get('config_changed') and o.get('changed_before_finish') and g.get('current_run_unchanged') is True
 return write(id,'pass' if ok else 'fail',{'exit_code':x['exit_code'],'gate_comparison':g,'changed_during_run':o.get('config_changed'),'changed_before_source_finished':o.get('changed_before_finish')},x['error_tail'] or g.get('error'))

def lead():
 x=scenario('c15');o=x['obs'];paused=o.get('paused_at');stop=o.get('stop_at')
 leads=[(r['committed_ps']-r['slowest_ps']) for r in x['rows'] if r.get('slowest_ps') is not None]
 near=[r for r in x['rows'] if paused and r['monotonic_ns']>=paused and r.get('slowest_ps') is not None and r['committed_ps']-r['slowest_ps']>=180_000_000_000]
 stopped={}
 for row in x['journal']:
  for c in row.get('connections',[]):
   if stop and c.get('role')=='producer' and c.get('state')=='STOPPED':stopped.setdefault(c['name'],row['updated_monotonic_ns'])
 times={k:(v-stop)/1e9 for k,v in stopped.items()} if stop else {}
 recorded=any(e.get('kind')=='lead' for e in x['eventlog'])
 by={c['name']:c for c in o.get('status',{}).get('connections',[])}
 counts={'ALPIDE_plane_0':sum(e['name']=='ALPIDE_plane_0' for f in x['raw'] for g in f['groups'] for e in g),
         'ALPIDE_plane_1':sum(e['name']=='ALPIDE_plane_1' for f in x['raw'] for g in f['groups'] for e in g),
         'MSENSOR_0':sum(e['name']=='msensor' for f in x['raw'] for g in f['groups'] for e in g)}
 reported={n:by.get(n,{}).get('data_events') for n in counts}
 ok=x['exit_code']==0 and bool(near) and bool(leads) and max(leads)<=200_000_000_000 and len(times)==3 and all(0<=t<=15 for t in times.values()) and normal(x['raw']) and counts==reported and recorded
 return write('C-E15','pass' if ok else 'fail',{'exit_code':x['exit_code'],'maximum_ahead_ps':max(leads) if leads else None,'near_limit_samples':len(near),'stop_delays_s':times,'source_lead_recorded':recorded,'raw_normal':normal(x['raw']),'raw_counts':counts,'reported_counts':reported},x['error_tail'] or None)

def observe_stop():
 x=scenario('c10c');files=x['raw'];status=x['obs'].get('status',{});groups=sum(len(f['groups']) for f in files)
 changes=[];last={}
 for row in x['journal']:
  for connection in row.get('connections',[]):
   name=connection.get('name');key=(connection.get('state'),connection.get('connected'))
   if last.get(name)!=key:
    changes.append({'name':name,'state':key[0],'connected':key[1],
                    'data_events':connection.get('data_events'),'monotonic_ns':row.get('updated_monotonic_ns')})
    last[name]=key
 docs_base=ROOT/'release/docs/docs' if (ROOT/'release/docs/docs').is_dir() else ROOT/'docs'
 docs=[(docs_base/'session-core.md').read_text(),(docs_base/'ko/session-core.md').read_text(),(docs_base/'ja/session-core.md').read_text()]
 phrases=[('Before stopping the run, terminate the stalled producer','unassembled events can be absent'),
          ('run\uc744 \uba48\ucd94\uae30 \uc804\uc5d0 \uba48\ucd98 producer\ub97c \uc885\ub8cc','\uc544\uc9c1 \uc870\ub9bd\ub418\uc9c0 \uc54a\uc740 event\uac00 raw\uc5d0\uc11c \ube60\uc9c8 \uc218'),
          ('run \u3092\u505c\u6b62\u3059\u308b\u524d\u306b\u505c\u6b62\u3057\u305f producer \u3092\u7d42\u4e86','\u672a\u7d44\u307f\u7acb\u3066\u306e event \u304c raw \u306b\u542b\u307e\u308c\u306a\u3044\u5834\u5408')]
 docs_match=all('Stop Error' in doc and all(p in doc for p in expected) for doc,expected in zip(docs,phrases))
 result={'id':'C-E10c','status':'observed' if x['obs'].get('stop_at') else 'not_run','repository_commit':rev,'image_id':image_id,'input_conditions':{'architecture':platform.machine(),'scenario':'paused producer; StopRun before producer termination'},'measurement':{'scenario_exit':x['exit_code'],'stop_monotonic_ns':x['obs'].get('stop_at'),'runcontrol_state':x['obs'].get('state'),'connections':status.get('connections',[]),'status_transitions':changes,'global_events_in_raw':groups,'raw_inspection_exit':x['raw_inspection_exit'],'raw_closed':(bool(files and all(f['eore'] for f in files) and not x['obs']['raw_open_fds']) if 'raw_open_fds' in x['obs'] else None),'raw_open_fds':x['obs'].get('raw_open_fds'),'source_events':x['eventlog'],'documentation_matches':docs_match},'criterion':'observation only; no pass/fail or fault injection'}
 if result['status']=='not_run':result['reason']=x['error_tail'] or 'StopRun was not reached'
 (OUT/'C-E10c.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print('C-E10c',result['status'],flush=True)
 return result['status']=='observed' and docs_match

checks=[('C-E7',barrier),('C-E8a',lambda:gate_setting('C-E8a','c8a')),('C-E8b',lambda:gate_setting('C-E8b','c8b')),('C-E10a',lambda:losses('C-E10a','c10a')),('C-E10b',lambda:losses('C-E10b','c10b')),('G1-resume-recovery',resume_recovery),('C-E10c',observe_stop),('C-E11',core_loss),('C-E12',missing),('C-E13',limits),('M-E14',beam_off),('C-E15',lead),('C-E16',truth_mapping)]
all_ok=True
for id,fn in checks:
 if len(sys.argv)>1 and id not in sys.argv[1:]:continue
 try:all_ok=fn() and all_ok
 except Exception as exc:
  write(id,'fail',{},str(exc));all_ok=False
sys.exit(0 if all_ok else 1)
