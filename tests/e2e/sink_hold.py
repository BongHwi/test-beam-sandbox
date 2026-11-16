#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise a real accepted stop while collector progress is withheld."""
import gzip, json, os, platform, subprocess, sys
from pathlib import Path
import accepted_driver as driver

root=Path.cwd();out=root/'artifacts/timing-observations/sink-hold';out.mkdir(parents=True,exist_ok=True)
commit=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
os.environ['ACCEPTED_SINK_HOLD_SECONDS']='3'
driver.TRIALS=out/'trials';driver.TRIALS.mkdir(exist_ok=True)
row={'id':'sink-hold','status':'fail','repository_commit':commit,'image_id':driver.IMAGE_ID,
     'input_conditions':{'architecture':platform.machine(),'stop_delay_s':0.1,'induced_sink_hold_s':3},
     'criterion':'accepted stop remains connected until actual sink progress is released and closed raw is complete'}
try:
    trial=driver.trial('running',4)
    with gzip.open(driver.TRIALS/'running/004/timeline.json.gz','rt') as stream: events=json.load(stream)
    begin=[x for x in events if x['event']=='sink_hold_begin']
    held=[x for x in events if x['event']=='sink_hold']
    end=[x for x in events if x['event']=='sink_hold_end']
    releases=[x for x in events if x['event']=='release']
    ordering=bool(begin and held and end and releases and all(x['monotonic_ns']>=end[0]['monotonic_ns'] for x in releases))
    row['measurement']={'trial_status':trial['status'],'checks':trial.get('checks'),'raw':trial.get('raw'),
                        'hold_begin':begin,'held_updates':len(held),'hold_end':end,'releases':releases,
                        'all_releases_after_hold':ordering}
    if trial['status']=='pass' and trial['checks'].get('sink_release_confirmed') and ordering:
        row['status']='pass'
except Exception as exc:row['reason']=str(exc)
(out/'result.json').write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(row,ensure_ascii=False));sys.exit(0 if row['status']=='pass' else 1)
