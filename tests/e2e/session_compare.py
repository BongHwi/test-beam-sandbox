# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare actual event bytes, decoded streams and status tags across runs."""
import copy
import json
from pathlib import Path
import re
import sys
import pyeudaq
from msensor.codec import decode
from msensor.stream import read
from status_compare import StatusProjection, comparable

def collect(path):
    root=path.parent
    run=int(re.search(r'run(\d+)\.raw$',path.name).group(1))
    reader=pyeudaq.FileReader('native',str(path))
    events={'ALPIDE_plane_0':[],'ALPIDE_plane_1':[],'msensor':[]}
    projection=StatusProjection()
    while True:
        event=reader.GetNextEvent()
        if event is None:break
        projection.observe(event)
        for sub in event.GetSubEvents():
            name=sub.GetDescription()
            if name not in events:continue
            payload=bytes(sub.GetBlock(0))
            if name=='msensor':
                payload=decode(payload)
                payload['stream_id']='RUN_NORMALIZED'
            else: payload=payload.hex()
            events[name].append([sub.GetTriggerN(),payload])
    streams=list(root.glob(f'*_run{run}.msws'))
    records=[]
    if streams:
        stream=read(streams[0])
        if len(streams)!=1 or stream['status']!='closed_clean':raise ValueError('stream is not closed cleanly')
        for item in stream['records']:
            data=copy.deepcopy(item['data'])
            if item['kind']=='HEADER':
                data['run_number']=0
                data['stream_id']='RUN_NORMALIZED'
            if item['kind']=='FOOTER':data['body_sha256']='RUN_NORMALIZED'
            records.append([item['kind'],data])
    status,excluded=projection.finish(events)
    return {'events':events,'stream':records,'status':status,'excluded_intermediate_status_counts':excluded}

def main():
    root=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/session-e2e')
    raws=sorted(root.glob('*.raw'))
    for path in raws:
        run=re.search(r'run(\d+)\.raw$',path.name).group(1)
        (root/('comparison-'+run+'.json')).write_text(json.dumps(collect(path),sort_keys=True))
    if len(raws)==2:
        a=collect(raws[0]);b=collect(raws[1])
        result={key:a[key]==b[key] for key in comparable(a)}
        result['excluded_intermediate_status_counts']=[a['excluded_intermediate_status_counts'],b['excluded_intermediate_status_counts']]
        print(json.dumps(result))
        return 0 if all(result[key] for key in ('events','stream','status')) else 1
    raise SystemExit('expected exactly two raw files')


if __name__=='__main__':
    sys.exit(main())
