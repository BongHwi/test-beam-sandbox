# SPDX-License-Identifier: LGPL-3.0-or-later
"""Inspect one real-time StopRun against C-E1/C-E2 and producer errors."""
import json
from collections import Counter
from pathlib import Path
import sys
import pyeudaq
root=Path('/tmp/session-e2e')
raws=list(root.glob('*.raw'))
if len(raws)!=1:raise SystemExit('expected one raw file')
reader=pyeudaq.FileReader('native',str(raws[0])); groups=[];opened=set();closed=set()
while True:
    e=reader.GetNextEvent()
    if e is None:break
    if e.IsBORE():opened.add(e.GetDescription())
    if e.IsEORE():closed.add(e.GetDescription())
    if e.GetDescription()=='ITS3global':groups.append([(x.GetDescription(),x.GetTriggerN()) for x in e.GetSubEvents()])
journal=[json.loads(x) for x in (root/'published.jsonl').read_text().splitlines()]
stop=[x for x in journal if x['rc_state']=='STOPPED']
counts=[{c['name']:c['data_events'] for c in x['connections'] if c['role']=='producer'} for x in stop]
expected={x:len(groups) for x in ('ALPIDE_plane_0','ALPIDE_plane_1','MSENSOR_0')}
flags={'all_producers':bool(groups) and all(len(g)==3 and {x[0] for x in g}=={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'} for g in groups),
       'matching_triggers':bool(groups) and all(len({x[1] for x in g})==1 for g in groups),
       'matching_counts':expected in counts,
       'closed':{x+'_status' for x in expected}<=opened and {x+'_status' for x in expected}<=closed,
       'no_error':not any(c['state']=='ERROR' for x in journal for c in x['connections'] if c['role']=='producer')}
timing=json.loads((root/'stop-times.jsonl').read_text().strip())
flags['timing']=0<=timing['elapsed_s']-timing['requested_s']<=.05
signatures=Counter(','.join(sorted(name for name,_ in group)) for group in groups)
examples=[group for group in groups if len(group)!=3 or {x[0] for x in group}!={'ALPIDE_plane_0','ALPIDE_plane_1','msensor'}][:5]
result={'status':'pass' if all(flags.values()) else 'fail','measurement':{'groups':len(groups),'checks':flags,'timing':timing,'producer_signatures':dict(signatures),'invalid_examples':examples}}

out=Path('/tmp/session-results');out.mkdir(exist_ok=True)
(out/'stop.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));sys.exit(0 if result['status']=='pass' else 1)
