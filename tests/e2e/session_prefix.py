# SPDX-License-Identifier: LGPL-3.0-or-later
"""Compare a stopped run with the complete run before its final guards."""
import json
from pathlib import Path
import re
import sys
from msensor.stream import read

root=Path('/tmp/session-e2e')
files=sorted(root.glob('comparison-*.json'))
truth=[json.loads(p.read_text()) for p in (root/'truth').glob('run-*.json')]
if len(files)!=2:raise SystemExit('expected two comparisons')
runs=[]
for path in files:
    number=int(re.search(r'comparison-(\d+)\.json',path.name).group(1))
    record=json.loads(path.read_text())
    entry=next(x for x in truth if number in x['eudaq_runs'])
    runs.append((number,record,entry))
short,long=sorted(runs,key=lambda x:len(x[1]['events']['msensor']))
if len(short[1]['events']['msensor'])>=len(long[1]['events']['msensor']):raise SystemExit('the early run was not shorter')
cutoff=short[2]['end_ps']//1000
window=1_000_000
eligible={number for number,payload in short[1]['events']['msensor']
          if (payload['trigger_time_ns']//window+2)*window<=cutoff}
if not eligible:raise SystemExit('no complete early events to compare')
checks={}
for name in ('ALPIDE_plane_0','ALPIDE_plane_1','msensor'):
    a=[x for x in short[1]['events'][name] if x[0] in eligible]
    b=[x for x in long[1]['events'][name] if x[0] in eligible]
    checks[name]=a==b and len(a)==len(eligible)

def normalized_stream(run):
    path=next(root.glob(f'*_run{run}.msws'))
    result=read(path)
    if result['status']!='closed_clean':raise ValueError('stream did not close cleanly')
    output=[]
    for item in result['records']:
        kind=item['kind'];data=dict(item['data'])
        if kind=='HEADER':
            data['stream_id']='RUN_NORMALIZED';data['run_number']=0
            output.append((kind,data))
        elif kind=='WINDOW':
            if not data['truncated'] and data['t_end_ns']<=cutoff:output.append((kind,data))
        elif kind=='EMPTY_RUN':
            for index in range(data['first_index'],data['first_index']+data['count']):
                if (index+1)*window<=cutoff:output.append(('EMPTY_WINDOW',index))
        elif kind=='TRIGGER':
            if data['trigger_number'] in eligible:output.append((kind,data))
    return output
checks['stream']=normalized_stream(short[0])==normalized_stream(long[0])
# BORE is independent of end time. EORE counts describe each different run,
# so check them against that run's actual raw payload count rather than against
# the other run; compare all remaining end-independent EORE tags directly.
a_status,b_status=short[1]['status'],long[1]['status']
checks['bore_status']=set(a_status)==set(b_status) and all(a_status[name][0]==b_status[name][0] for name in a_status)
end_dependent={'Event','TRGMON_EVENTS','frames_total','hits_total','last_window_index',
               'last_window_truncated','triggers_total','windows_nonempty','windows_total'}
def eore_static(row):
    return {key:value for key,value in row['tags'].items() if key not in end_dependent}
checks['eore_static_status']=set(a_status)==set(b_status) and all(eore_static(a_status[name][1])==eore_static(b_status[name][1]) for name in a_status)
def counts_match(run):
    record=run[1]
    for producer,events in record['events'].items():
        name='MSENSOR_0_status' if producer=='msensor' else producer+'_status'
        counter='triggers_total' if producer=='msensor' else 'Event'
        if int(record['status'][name][1]['tags'][counter])!=len(events):return False
    return True
checks['eore_counts_match_raw']=counts_match(short) and counts_match(long)
out=Path('/tmp/session-results');out.mkdir(exist_ok=True)
result={'status':'pass' if all(checks.values()) else 'fail','measurement':{'checks':checks,'early_events':len(short[1]['events']['msensor']),'full_events':len(long[1]['events']['msensor']),'complete_events_compared':len(eligible),'end_ns':cutoff,'excluded_intermediate_status_counts':{'short':short[1]['excluded_intermediate_status_counts'],'long':long[1]['excluded_intermediate_status_counts']}}}
(out/'C-E5.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));sys.exit(0 if result['status']=='pass' else 1)
