# SPDX-License-Identifier: LGPL-3.0-or-later
"""Inspect a real beam run using raw, stream, control log and private truth."""
import json
import math
from pathlib import Path
import re
import sys
import subprocess
import pyeudaq
from msensor.stream import read
from session_compare import collect

case=sys.argv[1]
root=Path(sys.argv[2] if len(sys.argv)>2 else '/tmp/beam-acquire')
out=Path('/tmp/beam-results');out.mkdir(exist_ok=True)

def emit(name,status,**details):
    (out/(name+'.json')).write_text(json.dumps({'status':status,'measurement':details},indent=2)+'\n')

raws=sorted(root.glob('*.raw'));streams=sorted(root.glob('*.msws'))
if len(raws)!=1 or len(streams)!=1:raise RuntimeError('expected one raw and one stream')
reader=pyeudaq.FileReader('native',str(raws[0]));groups=[];raw_times=[];bore=set();eore=set()
while True:
    event=reader.GetNextEvent()
    if event is None:break
    if event.IsBORE():bore.add(event.GetDescription())
    if event.IsEORE():eore.add(event.GetDescription())
    if event.GetDescription()=='ITS3global':
        subs=event.GetSubEvents()
        groups.append([(x.GetDescription(),x.GetTriggerN()) for x in subs])
        raw_times.extend((x.GetTriggerN(),x.GetTimestampBegin()) for x in subs if x.GetDescription()=='msensor')
stream=read(streams[0]);run=int(re.search(r'run(\d+)\.raw$',raws[0].name).group(1))
truths=[json.loads(p.read_text()) for p in (root/'truth').glob('run-*.json')]
truth=next((x for x in truths if run in x['eudaq_runs']),None)
received=[json.loads(x) for x in (root/'truth/received.jsonl').read_text().splitlines()] if (root/'truth/received.jsonl').exists() else []
received=[x for x in received if x['run']==run]
journal=[json.loads(x) for x in (root/'published.jsonl').read_text().splitlines()]
stopped=[x for x in journal if x['rc_state']=='STOPPED']
expected={x:len(groups) for x in ('ALPIDE_plane_0','ALPIDE_plane_1','MSENSOR_0')}
counts=[{c['name']:c['data_events'] for c in x['connections'] if c['role']=='producer'} for x in stopped]
status_names={x+'_status' for x in expected}
closed=status_names<=bore and status_names<=eore and stream['status']=='closed_clean'
aligned=all(len(g)==3 and {x[0] for x in g}=={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'} and len({x[1] for x in g})==1 for g in groups)
counts_ok=expected in counts
records=[json.loads(x) for x in (root/'source.sock.beam.log').read_text().splitlines()]
start=next(i for i,x in enumerate(records) if x['origin']=='run_start' and x['applied_run_ps']==0) if case not in ('replay','edge') else next(i for i,x in enumerate(records) if x['origin']=='schedule' and x['applied_run_ps']==0)
changes=[x for x in records[start+1:] if x['origin'] in ('command','schedule') and x['applied_run_ps'] is not None]
if case=='off':
    checks={'no_global_events':len(groups)==0,'no_trigger_records':stream['counts']['triggers_total']==0,
            'normal_close':closed,'counts_match':counts_ok,'no_truth_crossings':truth is not None and not truth['crossings'],
            'no_signal_hits':stream['counts']['hits_total']==0,'windows_advance':stream['counts']['windows_total']>0}
    emit('M-E2','pass' if all(checks.values()) else 'fail',checks=checks,stream_counts=stream['counts'])
if case=='manual':
    commands=[x for x in changes if x['origin']=='command']
    valid=len(commands)==3 and [x['beam_on'] for x in commands]==[True,False,True]
    times=[x['applied_run_ps'] for x in commands] if valid else []
    truth_rows=truth['crossings'] if truth else []
    if valid:
        on1,off,on2=times;end=truth['end_ps'] if truth else 0
        first=[x for x in truth_rows if on1<=x['time_ps']<off]
        second=[x for x in truth_rows if on2<=x['time_ps']<end]
        outside=[x['trigger'] for x in truth_rows if not (on1<=x['time_ps']<off or on2<=x['time_ps']<end)]
        outside_crossings=[]
        for row in truth_rows:
            for plane in row['planes'].values():
                for crossing in plane['mc']:
                    at=row['time_ps']+int(crossing['global_time_ns']*1000)
                    if not (on1<=at<off or on2<=at<end):outside_crossings.append([row['trigger'],at])
        required_ps=20/237.654321987*1e12
        lengths=(off-on1>=required_ps and end-on2>=required_ps)
    else:first=[];second=[];outside=[];outside_crossings=[];lengths=False;off=on2=0
    truth_pairs=[(x['trigger'],(x['time_ps']//1000)*1000) for x in truth_rows]
    checks={'three_actions':valid,'two_on_intervals':lengths and bool(first) and bool(second),
            'off_has_no_triggers':not outside,'off_has_no_crossings':not outside_crossings,'truth_matches_raw':raw_times==truth_pairs,
            'three_producers_and_triggers':bool(groups) and aligned,'counts_match':counts_ok,'normal_close':closed}
    emit('M-E3','pass' if all(checks.values()) else 'fail',checks=checks,
         applied_run_ps=times,on_trigger_counts=[len(first),len(second)],global_events=len(groups))
    windows=[x['data'] for x in stream['records'] if x['kind']=='WINDOW' and x['data']['t_begin_ns']*1000>=off and x['data']['t_end_ns']*1000<=on2]
    empty_count=0
    for item in stream['records']:
        if item['kind']=='EMPTY_RUN':
            data=item['data'];width=10000000
            empty_count+=sum(1 for i in range(data['first_index'],data['first_index']+data['count']) if i*width*1000>=off and (i+1)*width*1000<=on2)
    unexpected=[];delayed_hits=0
    for window in windows:
        allowed={(math.floor(x['col']),math.floor(x['row'])) for x in received
                 if x['time_ns']*1000<off and window['t_begin_ns']<=x['time_ns']+50000000<window['t_end_ns']}
        for pixel in window['hits']:
            if tuple(pixel) not in allowed:unexpected.append([window['window_index'],pixel])
            else:delayed_hits+=1
    checked=len(windows)+empty_count
    ok=valid and checked>0 and delayed_hits>0 and not unexpected
    emit('M-E4','pass' if ok else 'fail',off_windows=checked,nonempty_off_windows=len(windows),delayed_hits=delayed_hits,unexpected_count=len(unexpected),noise_fixture='zero')
    schedule=[{'time_ps':0,'on':records[start]['beam_on']}]+[{'time_ps':x['applied_run_ps'],'on':x['beam_on']} for x in commands]
    (root/'replay-schedule.json').write_text(json.dumps(schedule))
if case=='edge':
    compiled=subprocess.run(['c++','-std=c++17','-I/opt/eudaq/include','/tmp/alpide_convert.cc',
                             '-L/opt/eudaq/lib','-leudaq_core','-ldl','-Wl,-rpath,/opt/eudaq/lib',
                             '-o','/tmp/beam_alpide_convert'],capture_output=True,text=True)
    converted=subprocess.run(['/tmp/beam_alpide_convert',str(raws[0])],capture_output=True,text=True) if compiled.returncode==0 else compiled
    pixels={}
    for line in converted.stdout.splitlines():
        fields=line.split()
        if fields and fields[0] in ('ALPIDE_plane_0','ALPIDE_plane_1'):
            pixels[(fields[0],int(fields[1]))]=int(fields[3])
    crossings=truth['crossings'] if truth else []
    plane_times=[]
    for row in crossings:
        for name,plane in row['planes'].items():
            for crossing in plane['mc']:
                plane_times.append((name,row['time_ps']+int(crossing['global_time_ns']*1000)))
    checks={'one_aligned_trigger':len(groups)==1 and aligned and groups[0][0][1]==0,
            'p0_crosses_before_off':any(name=='P0' and at<150 for name,at in plane_times),
            'no_crossing_after_off':all(at<150 for _name,at in plane_times),
            'early_frame_has_hit':pixels.get(('ALPIDE_plane_0',0),0)>0,
            'late_frame_has_no_hit':pixels.get(('ALPIDE_plane_1',0))==0,
            'normal_close':closed,'counts_match':counts_ok,'public_conversion':converted.returncode==0}
    emit('M-E3-edge','pass' if all(checks.values()) else 'fail',checks=checks,plane_times=plane_times,pixel_counts={str(k):v for k,v in pixels.items()})
comparison=collect(raws[0]);(root/'comparison.json').write_text(json.dumps(comparison,sort_keys=True))
print(json.dumps({'case':case,'groups':len(groups),'aligned':aligned,'closed':closed,'counts_match':counts_ok}))
