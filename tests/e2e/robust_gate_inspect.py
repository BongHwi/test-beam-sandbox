# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare actual public-converter pixels across strobe settings."""
import json,subprocess,sys
from pathlib import Path
from session_compare import collect
root=Path('/tmp/session-e2e');raws=sorted(root.glob('*.raw'));case=sys.argv[1]
p=subprocess.run(['c++','-std=c++17','-I/opt/eudaq/include','/tmp/alpide_convert.cc','-L/opt/eudaq/lib','-leudaq_core','-ldl','-Wl,-rpath,/opt/eudaq/lib','-o','/tmp/robust-convert'],text=True,capture_output=True)
if p.returncode:raise RuntimeError('converter compilation failed: '+p.stderr[-1000:])
converted=[];payload=[]
for raw in raws:
 p=subprocess.run(['/tmp/robust-convert',str(raw)],text=True,capture_output=True)
 if p.returncode or 'WARN' in p.stdout or 'WARN' in p.stderr:raise RuntimeError('public converter failed')
 planes={}
 for line in p.stdout.splitlines():
  w=line.split()
  if not w or w[0] not in ('ALPIDE_plane_0','ALPIDE_plane_1'):continue
  planes.setdefault(w[0],{})[int(w[1])]=sorted(set(tuple(map(int,x.split(',')[:2])) for x in w[4:]))
 converted.append(planes);payload.append(collect(raw))
small=0;large=1 if case=='c8a' else 2
expected_runs=2 if case=='c8a' else 3
if len(converted)!=expected_runs:raise RuntimeError('wrong number of runs')
a=converted[small].get('ALPIDE_plane_0',{});b=converted[large].get('ALPIDE_plane_0',{})
triggers=sorted(set(a)&set(b));monotone=bool(triggers) and set(a)==set(b) and all(set(a[t])<=set(b[t]) for t in triggers)
strict=sum(set(a[t])<set(b[t]) for t in triggers)
unchanged_parts={'reference_plane':converted[small].get('ALPIDE_plane_1')==converted[large].get('ALPIDE_plane_1'),
                 'device_events':payload[small]['events']['msensor']==payload[large]['events']['msensor'],
                 'device_stream':payload[small]['stream']==payload[large]['stream']}
current_parts={'events':payload[0]['events']==payload[1]['events'],
               'stream':payload[0]['stream']==payload[1]['stream']} if case=='c8b' else None
print(json.dumps({'runs':len(raws),'triggers':len(triggers),'all_small_subsets':monotone,'strictly_larger_frames':strict,'other_devices_unchanged':all(unchanged_parts.values()),'other_comparisons':unchanged_parts,'current_run_unchanged':all(current_parts.values()) if current_parts else None,'current_comparisons':current_parts}))
