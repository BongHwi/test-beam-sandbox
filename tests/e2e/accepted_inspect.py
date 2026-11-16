# SPDX-License-Identifier: LGPL-3.0-or-later
"""Summarize actual collector/source observations and closed native raw."""
import collections, json, os, sys, time
from pathlib import Path
import pyeudaq
root=Path('/tmp/session-e2e');trace=root/'accepted-observations'
def read(path):
 try:return [json.loads(x) for x in path.read_text().splitlines() if x]
 except FileNotFoundError:return []
core=read(trace/'core.jsonl');collector=read(trace/'collector.jsonl');rc=read(trace/'runcontrol.jsonl');journal=read(root/'published.jsonl')
try:request=json.loads((trace/'request.json').read_text())
except (OSError,ValueError):request=None
try:terminal_observation=json.loads((trace/'terminal.json').read_text())
except (OSError,ValueError):terminal_observation=None
raws=list(root.glob('*.raw'));groups=[];bore=set();eore=set();raw_error=None
# Inspect actual open descriptors before this process opens the files for reading.
def raw_writers(paths):
 targets={str(path.resolve()) for path in paths};writers=[]
 for proc in Path('/proc').iterdir():
  if not proc.name.isdigit():continue
  try: descriptors=list((proc/'fd').iterdir())
  except FileNotFoundError:continue
  except PermissionError:
   writers.append({'pid':proc.name,'error':'cannot inspect descriptors'});continue
  for fd in descriptors:
   try:
    target=os.readlink(fd)
    if target not in targets:continue
    fields=(proc/'fdinfo'/fd.name).read_text().splitlines()
    flags=int(next(row.split()[1] for row in fields if row.startswith('flags:')),8)
    if flags & os.O_ACCMODE in (os.O_WRONLY,os.O_RDWR):writers.append({'pid':proc.name,'fd':fd.name,'path':target})
   except (FileNotFoundError,ProcessLookupError):continue
   except (PermissionError,StopIteration,ValueError) as exc:writers.append({'pid':proc.name,'fd':fd.name,'error':str(exc)})
 return writers
until=time.monotonic()+5
while True:
 writers=raw_writers(raws)
 if not writers or time.monotonic()>=until:break
 time.sleep(.05)
try:
 if len(raws)!=1:raise RuntimeError('expected exactly one raw')
 reader=pyeudaq.FileReader('native',str(raws[0]))
 while True:
  event=reader.GetNextEvent()
  if event is None:break
  if event.IsBORE():bore.add(event.GetDescription())
  if event.IsEORE():eore.add(event.GetDescription())
  if event.GetDescription()=='ITS3global':groups.append([(s.GetDescription(),s.GetTriggerN()) for s in event.GetSubEvents()])
except Exception as e:raw_error=str(e)
expected={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'};names={'ALPIDE_plane_0','ALPIDE_plane_1','MSENSOR_0'}
invalid=[g for g in groups if len(g)!=3 or {x[0] for x in g}!=expected or len({x[1] for x in g})!=1]
connections=[x for x in collector if x['event']=='DoConnect_after']
disconnections=[x for x in collector if x['event']=='DoDisconnect_after']
received=[x for x in collector if x['event']=='DoReceive_after' and x['is_data']]
counts=collections.Counter(x['producer'] for x in received)
joins=[x for x in core if x['event']=='join_done'];stops=[x for x in core if x['event']=='stop_done'];releases=[x for x in core if x['event']=='release']
producer_errors=[{'name':c['name'],'state':c['state'],'message':c.get('message')} for row in journal for c in row.get('connections',[]) if c.get('state')=='ERROR']
terminal=any(x.get('rc_state')=='TERMINATED' for x in journal)
joined_at_send=[x['serial'] for x in joins if request and x['monotonic_ns'] <= request['send_ns']]
joined_early=request is not None and request['kind']=='joining' and 0 < len(joined_at_send) < 3
running_delay=(request['send_ns']-request['anchor_ns'])/1e9 if request and request['kind']=='running' else None
checks={'request_accepted':bool(request and request['response'].get('accepted')),
 'raw_readable':raw_error is None,'raw_writer_closed':bool(raws) and not writers,'all_global_events_complete':not invalid,
 'all_bore_eore':{x+'_status' for x in names}<=bore and {x+'_status' for x in names}<=eore,
 'terminated':terminal,'no_producer_error':not producer_errors,
 'collector_connected':names<={x['producer'] for x in connections},
 'collector_disconnected':names<={x['producer'] for x in disconnections},
 'source_joins_recorded':names=={('ALPIDE_plane_'+x['serial'][1:]) if x['serial'].startswith('V') else x['serial'] for x in joins},
 'source_boundary_recorded':bool(stops), 'all_releases_recorded':len({x['serial'] for x in releases})==3,
 'sink_release_confirmed':bool(releases) and all(x.get('sink_required') and x.get('sink_counts') and all(v is not None and v >= x['boundary_index']+1 for v in x['sink_counts'].values()) for x in releases),
 'during_join_verified':joined_early if request and request['kind']=='joining' else True,
 'running_delay_in_range':0<=running_delay<=.5 if running_delay is not None else True,
 'within_deadline':bool(request and terminal_observation and 0 <= terminal_observation['observed_ns']-request['send_ns'] <= 15_000_000_000)}
accepted=bool(request and request['response'].get('accepted'))
rejected_with_reason=bool(request and not accepted and request['response'].get('reason'))
if request and request['kind']=='joining' and rejected_with_reason:
 required=['during_join_verified']
else:
 required=['request_accepted','raw_readable','raw_writer_closed','all_global_events_complete','all_bore_eore','terminated','no_producer_error','sink_release_confirmed','during_join_verified','running_delay_in_range','within_deadline']
result={'status':'pass' if request and all(checks[x] for x in required) and (accepted or rejected_with_reason) else 'fail','checks':checks,'required_checks':required,'request':request,'terminal_observation':terminal_observation,
 'raw':{'files':[p.name for p in raws],'open_writers':writers,'event_count':len(groups),'invalid_count':len(invalid),'invalid_examples':invalid[:8],'read_error':raw_error},
 'collector_data_counts':dict(counts),'producer_errors':producer_errors[:8],
 'collector_connect_count':len(connections),'collector_disconnect_count':len(disconnections),
 'source_join_count':len(joins),'joined_at_send':joined_at_send,'source_stop_count':len(stops),'source_release_count':len(releases),
 'collector_unobserved_disconnects':sorted(names-{x['producer'] for x in disconnections}),
 'collector_exit_observed':any(x['event']=='collector_exit' for x in collector),
 'runcontrol_disconnects':sorted({x.get('producer') for x in rc if x.get('event')=='runcontrol_disconnect'}),
 'timeline':sorted(core+collector+rc,key=lambda x:x['monotonic_ns'])}
(trace/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'checks':checks,'raw':result['raw']}));sys.exit(0 if result['status']=='pass' else 1)
