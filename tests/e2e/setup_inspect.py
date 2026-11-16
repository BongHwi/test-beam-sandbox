# SPDX-License-Identifier: LGPL-3.0-or-later
"""Inspect real setup session raw using public converters and status events."""
import json, subprocess
from pathlib import Path
import pyeudaq
from msensor.stream import read
root=Path('/tmp/session-run')
layout=json.loads((root/'mapping.json').read_text());expected={x['plane'] for x in layout}
names={x['producer']+'_status' for x in layout};raws=list(root.glob('*.raw'))
if len(raws)!=1:raise SystemExit('expected exactly one raw')
r=pyeudaq.FileReader('native',str(raws[0]));bore=set();eore=set();groups=[]
while True:
 e=r.GetNextEvent()
 if e is None:break
 if e.IsBORE():bore.add(e.GetDescription())
 if e.IsEORE():eore.add(e.GetDescription())
 if e.GetDescription()=='ITS3global':groups.append([(s.GetDeviceN(),s.GetTriggerN()) for s in e.GetSubEvents()])
aligned=bool(groups) and all(len(g)==len(expected) and {x[0] for x in g}==expected and len({x[1] for x in g})==1 for g in groups)
p=subprocess.run(['/tmp/layout-convert',str(raws[0])],text=True,capture_output=True)
rows=p.stdout.splitlines();converted=len(rows)==len(groups)*len(expected) and p.returncode==0
streams=list(root.glob('*.msws'));closed_streams=all(read(x)['status']=='closed_clean' for x in streams)
expected_streams=sum(x['producer'].startswith('MSENSOR_') for x in layout)
status=json.loads((root/'status.json').read_text());errors=any(c.get('state')=='ERROR' for c in status.get('connections',[]))
result={'closed':names<=bore and names<=eore and status['rc_state']=='TERMINATED',
        'aligned':aligned,'events':len(groups),'converted':converted,'converted_sub_events':len(rows),
        'stream_count':len(streams),'expected_streams':expected_streams,'streams_closed':closed_streams,'errors':errors}
result['common_pass']=result['closed'] and aligned and converted and closed_streams and len(streams)==expected_streams and not errors
(root/'setup-inspection.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));raise SystemExit(0 if result['common_pass'] else 1)
