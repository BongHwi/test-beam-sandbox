# SPDX-License-Identifier: LGPL-3.0-or-later
"""Inspect actual native raw files, public decoder output and private truth."""
import json
import configparser
import math
from pathlib import Path
import re
import subprocess
import pyeudaq

root = Path('/tmp/session-e2e')
out = Path('/tmp/session-results')
out.mkdir(exist_ok=True)


def emit(name, status, **details):
    (out / (name + '.json')).write_text(json.dumps({'status':status, 'measurement':details}, indent=2) + '\n')

journal=[json.loads(x) for x in (root/'published.jsonl').read_text().splitlines()]
received=[json.loads(x) for x in (root/'truth/received.jsonl').read_text().splitlines()]
truths=[json.loads(p.read_text()) for p in sorted((root/'truth').glob('run-*.json'))]
raws=sorted(root.glob('*.raw'))
compiled=subprocess.run(['c++','-std=c++17','-I/opt/eudaq/include','/tmp/alpide_convert.cc','-L/opt/eudaq/lib','-leudaq_core','-ldl','-Wl,-rpath,/opt/eudaq/lib','-o','/tmp/alpide_convert'],capture_output=True,text=True)
if compiled.returncode:raise RuntimeError('public converter checker did not build')
results=[]
for index,path in enumerate(raws):
    number=int(re.search(r'run(\d+)\.raw$',path.name).group(1))
    reader=pyeudaq.FileReader('native',str(path));groups=[];opened=set();closed=set();last_trigger_ps=0
    while True:
        e=reader.GetNextEvent()
        if e is None:break
        desc=e.GetDescription()
        if e.IsBORE():opened.add(desc)
        if e.IsEORE():closed.add(desc)
        if desc=='ITS3global':
            subs=e.GetSubEvents()
            groups.append([(x.GetDescription(),x.GetTriggerN()) for x in subs])
            for sub in subs:
                if sub.GetDescription()=='msensor':last_trigger_ps=max(last_trigger_ps,sub.GetTimestampBegin())
    required={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'}
    structure=bool(groups) and all(len(g)==3 and {name for name,_ in g}==required for g in groups)
    aligned=bool(groups) and all(len({trigger for _,trigger in g})==1 for g in groups)
    running=[x['seq'] for x in journal if x['config']['index']==index and x['rc_state']=='RUNNING']
    stop=[x for x in journal if x['config']['index']==index and x['rc_state']=='STOPPED' and running and x['seq']>min(running)]
    statuses=[{c['name']:c['data_events'] for c in x['connections'] if c['role']=='producer'} for x in stop]
    expected={name:len(groups) for name in ('ALPIDE_plane_0','ALPIDE_plane_1','MSENSOR_0')}
    status_ok=expected in statuses
    tags={x+'_status' for x in ('ALPIDE_plane_0','ALPIDE_plane_1','MSENSOR_0')}
    closed_ok=tags<=opened and tags<=closed
    truth=next((x for x in truths if number in x['eudaq_runs']),None)
    ini=configparser.ConfigParser();ini.read(root/'init.ini')
    configs=[x.strip() for x in ini.get('RunControl','configs').split(',')]
    conf=configparser.ConfigParser();conf.read(configs[index])
    gates=[conf.getint('Producer.ALPIDE_plane_0','STROBE_LENGTH')*25000,
           conf.getint('Producer.ALPIDE_plane_1','STROBE_LENGTH')*25000]
    if conf.get('Producer.MSENSOR_0','readout_mode')=='triggered':
        guards=(conf.getint('Producer.MSENSOR_0','strobe_delay_ns')+conf.getint('Producer.MSENSOR_0','strobe_length_ns'))*1000
    else:
        guards=(conf.getint('Producer.MSENSOR_0','window_guard_after')+1)*conf.getint('Producer.MSENSOR_0','window_length_ns')*1000
    required_drain=max(*gates,guards)
    boundary_ok=truth is not None and truth['end_ps']>=last_trigger_ps+required_drain
    converter=subprocess.run(['/tmp/alpide_convert',str(path)],capture_output=True,text=True)
    decoded={}
    for line in converter.stdout.splitlines():
        words=line.split()
        if not words or words[0] not in ('ALPIDE_plane_0','ALPIDE_plane_1'):continue
        decoded[(words[0],int(words[1]))]=[tuple(map(int,x.split(',')[:2])) for x in words[4:]]
    check_truth=truth is not None and converter.returncode==0 and 'WARN' not in converter.stderr and 'WARN' not in converter.stdout
    crossing_seen=0;pixel_seen=0
    got={(x['run'],x['trigger']):x for x in received}
    if truth:
        for row in truth['crossings']:
            trigger=row['trigger']
            for plane,producer in [('P0','ALPIDE_plane_0'),('P1','ALPIDE_plane_1')]:
                mcs=row['planes'][plane]['mc']
                points=[(mc['local_mm'][0]*1000/30,mc['local_mm'][1]*1000/30) for mc in mcs]
                pixels=decoded.get((producer,trigger),[])
                crossing_seen+=len(points);pixel_seen+=len(pixels)
                if any(not any(math.dist(point,pixel)<=2 for pixel in pixels) for point in points):check_truth=False
                if any(not any(math.dist(point,pixel)<=2 for point in points) for pixel in pixels):check_truth=False
            mcs=row['planes']['D0']['mc']
            sample=got.get((number,trigger))
            if len(mcs)!=1 or sample is None:check_truth=False
            elif abs(sample['col']-(mcs[0]['local_mm'][0]*1000/30+0.5))>1e-9 or abs(sample['row']-(mcs[0]['local_mm'][1]*1000/30+0.5))>1e-9:check_truth=False
    check_truth=check_truth and crossing_seen>0 and pixel_seen>0
    results.append({'run':number,'groups':len(groups),'structure':structure,'trigger_match':aligned,
                    'producer_counts_match':status_ok,'bore_eore':closed_ok,'boundary_covers_gate':boundary_ok,'truth_match':check_truth,
                    'truth_crossings':crossing_seen,'decoded_pixels':pixel_seen})
emit('C-E1','pass' if len(results)==2 and all(r['structure'] and r['producer_counts_match'] and r['bore_eore'] and r['boundary_covers_gate'] for r in results) else 'fail',runs=results)
emit('C-E2','pass' if len(results)==2 and all(r['trigger_match'] for r in results) else 'fail',runs=results)
emit('C-E3','pass' if len(results)==2 and all(r['truth_match'] for r in results) else 'fail',runs=results,distance_limit_pixels=2,position_tolerance_pixels=1e-9)
