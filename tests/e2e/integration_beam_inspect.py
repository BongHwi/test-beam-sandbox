# SPDX-License-Identifier: LGPL-3.0-or-later
"""Check seven-device off/manual/replay runs and normalize real DAQ output."""
import copy
import json
from pathlib import Path
import re
import sys
import pyeudaq
from msensor.codec import decode
from msensor.stream import read
from status_compare import StatusProjection

mode=sys.argv[1];root=Path(sys.argv[2] if len(sys.argv)>2 else '/tmp/integration-e2e')
out=Path('/tmp/integration-results');out.mkdir(exist_ok=True)
def emit(name,ok,**measurement):
    (out/(name+'.json')).write_text(json.dumps({'status':'pass' if ok else 'fail','measurement':measurement},indent=2)+'\n')
    print(name,'pass' if ok else 'fail',flush=True)
raws=list(root.glob('*.raw'));streams=list(root.glob('*.msws'))
if len(raws)!=1 or len(streams)!=1:raise RuntimeError('expected one raw and one stream')
raw=raws[0];run=int(re.search(r'run(\d+)\.raw$',raw.name).group(1));reader=pyeudaq.FileReader('native',str(raw))
names=[f'ALPIDE_plane_{i}' for i in range(6)]+['msensor'];events={x:[] for x in names};projection=StatusProjection();groups=[];bore=set();eore=set();times={}
while True:
    event=reader.GetNextEvent()
    if event is None:break
    if event.IsBORE():bore.add(event.GetDescription())
    if event.IsEORE():eore.add(event.GetDescription())
    projection.observe(event)
    if event.GetDescription()=='ITS3global':
        subs=event.GetSubEvents();groups.append([(s.GetDescription(),s.GetTriggerN()) for s in subs])
        for sub in subs:
            name=sub.GetDescription()
            if name not in events:continue
            payload=bytes(sub.GetBlock(0))
            if name=='msensor':
                payload=decode(payload);payload['stream_id']='RUN_NORMALIZED';times[sub.GetTriggerN()]=sub.GetTimestampBegin()
            else:payload=payload.hex()
            events[name].append([sub.GetTriggerN(),payload])
stream=read(streams[0]);records=[]
for item in stream['records']:
    data=copy.deepcopy(item['data'])
    if item['kind']=='HEADER':data['run_number']=0;data['stream_id']='RUN_NORMALIZED'
    if item['kind']=='FOOTER':data['body_sha256']='RUN_NORMALIZED'
    records.append([item['kind'],data])
status,excluded=projection.finish(events)
(root/'comparison.json').write_text(json.dumps({'events':events,'stream':records,'status':status,'excluded_intermediate_status_counts':excluded},sort_keys=True))
truths=[json.loads(p.read_text()) for p in (root/'truth').glob('run-*.json')];truth=next(x for x in truths if run in x['eudaq_runs'])
expected_status={*(f'ALPIDE_plane_{i}_status' for i in range(6)),'MSENSOR_0_status'}
closed=expected_status<=bore and expected_status<=eore and stream['status']=='closed_clean'
aligned=all(len(g)==7 and {x[0] for x in g}==set(names) and len({x[1] for x in g})==1 for g in groups)
if mode=='replay' and (not closed or not aligned):raise RuntimeError('replay raw or stream did not close or align')
if mode=='off':
    emit('I-E8',closed and not groups and not truth['crossings'] and stream['counts']['triggers_total']==0,
         closed=closed,data_events=len(groups),tracks=0,truth_crossings=len(truth['crossings']),stream_counts=stream['counts'])
if mode=='manual':
    import ROOT
    ROOT.gSystem.Load('/opt/corry/lib/libCorryvreckanObjects.so')
    f=ROOT.TFile.Open(str(root/'analysis/output/events.root'));et=f.Get('Event');tt=f.Get('Track');track_ids=[]
    for i in range(et.GetEntries()):
        et.GetEntry(i);tt.GetEntry(i);ids=[int(x.first) for x in getattr(et,'global').triggerList()]
        if len(ids)!=1:raise RuntimeError('analysis trigger count invalid')
        if getattr(tt,'global').size():track_ids.append(ids[0])
    f.Close()
    beam=[json.loads(x) for x in (root/'source.sock.beam.log').read_text().splitlines()]
    start=next(i for i,x in enumerate(beam) if x['origin']=='run_start' and x['applied_run_ps']==0)
    actions=[x for x in beam[start+1:] if x['origin']=='command' and x['applied_run_ps'] is not None]
    valid=len(actions)==2 and [x['beam_on'] for x in actions]==[False,True] and beam[start]['beam_on'] is True
    off,on=[x['applied_run_ps'] for x in actions] if valid else (0,0)
    by_trigger={x['trigger']:x['time_ps'] for x in truth['crossings']};track_times=[by_trigger[x] for x in track_ids]
    before=[x for x in times.values() if x<off];after=[x for x in times.values() if x>=on]
    tolerance=10/237.654321987*1e12
    gap_left=off-max(before) if before else None;gap_right=min(after)-on if after else None
    checks={'actions':valid,'normal_close':closed,'aligned':aligned,'both_sides_tracks':any(x<off for x in track_times) and any(x>=on for x in track_times),
            'no_off_tracks':not any(off<=x<on for x in track_times),
            'gap_agrees':bool(before and after and 0<=gap_left<=tolerance and 0<=gap_right<=tolerance),
            'truth_no_off_trigger':not any(off<=x['time_ps']<on for x in truth['crossings'])}
    emit('I-E8a',all(checks.values()),checks=checks,track_count=len(track_ids),
         off_ps=off,on_ps=on,gap_left_ps=gap_left,gap_right_ps=gap_right,tolerance_ps=tolerance)
    schedule=[{'time_ps':0,'on':True}]+[{'time_ps':x['applied_run_ps'],'on':x['beam_on']} for x in actions]
    (root/'replay-schedule.json').write_text(json.dumps(schedule))
print(json.dumps({'mode':mode,'closed':closed,'aligned':aligned,'events':len(groups)}))
