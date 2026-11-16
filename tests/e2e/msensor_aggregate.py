#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Aggregate independently acquired contract fixtures without changing criteria."""
import json, os
from pathlib import Path
out=Path('artifacts/msensor'); commit=os.environ['TEST_COMMIT']; image=os.environ['IMAGE_ID']

def load(name): return json.loads((out/(name+'.json')).read_text())

def result(name, ok, inputs, measurement, criterion, error=None):
    data={'id':name,'level':'eudaq','status':'pass' if ok else 'fail','input_conditions':inputs,'measurement':measurement,'criterion':criterion,'image_id':image,'repository_commit':commit,'corryvreckan_source':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'}
    if error: data['reason']=error
    (out/(name+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')

failed=False
for result_name, fixture in [('A1','T_thr'),('A3','C_thr')]:
    try:
        lo,hi=load(fixture+'_100'),load(fixture+'_200'); a,b=lo['measurement'],hi['measurement']; n=2000
        ok=lo['status']==hi['status']=='pass' and a['trigger_times_ns']==b['trigger_times_ns'] and len(a['trigger_numbers'])==len(b['trigger_numbers'])==n and a['converted_hits']>=.9*n and b['converted_hits']<=.1*n
        result(result_name,ok,{'fixture':fixture,'thresholds':[100,200]},{'events':[len(a['trigger_numbers']),len(b['trigger_numbers'])],'hits':[a['converted_hits'],b['converted_hits']]},'N=2000; identical trigger times; hits(th100)>=1800 and hits(th200)<=200')
        failed |= not ok
    except Exception as e: result(result_name,False,{'fixture':fixture},{},'threshold criterion',str(e)); failed=True
try:
    zero,late=load('T_strobe_0'),load('T_strobe_5000'); a,b=zero['measurement'],late['measurement']; n=2000
    ok=zero['status']==late['status']=='pass' and a['trigger_times_ns']==b['trigger_times_ns'] and len(a['trigger_numbers'])==len(b['trigger_numbers'])==n and a['converted_hits']>=.9*n and b['converted_hits']==0
    result('A2',ok,{'fixture':'T_strobe','delays_ns':[0,5000]},{'hits':[a['converted_hits'],b['converted_hits']]},'N=2000; identical trigger times; hits(delay0)>=1800 and hits(delay5000)=0')
    failed |= not ok
except Exception as e: result('A2',False,{'fixture':'T_strobe'},{},'strobe criterion',str(e)); failed=True
try:
    names=['T_thr_100','T_thr_200','C_thr_100','C_thr_200']; runs=[load(n) for n in names]
    ok=all(x['status']=='pass' and x['measurement']['converted_events']==2000 and x['measurement']['converted_hits']==x['measurement']['device_hits'] for x in runs)
    result('E1',ok,{'fixtures':names},{n:{k:v for k,v in x['measurement'].items() if k in ('converted_events','converted_hits','device_hits')} for n,x in zip(names,runs)},'each raw converter hit count equals device payload count')
    failed |= not ok
except Exception as e: result('E1',False,{'fixtures':['T_thr','C_thr']},{},'raw converter agrees with device',str(e)); failed=True
try:
    triggered=load('T_thr_100'); m=triggered['measurement']; ok=triggered['status']=='pass' and m['stream_files']==0
    result('S8',ok,{'fixture':'T_thr'},{'stream_files':m['stream_files']},'triggered mode creates no stream file')
    failed |= not ok
except Exception as e: result('S8',False,{'fixture':'T_thr'},{},'no stream in triggered mode',str(e)); failed=True
try:
    offset=load('C_offset_eudaq'); m=offset['measurement']; o=m['offsets']
    ok=(offset['status']=='pass' and len(m['trigger_numbers'])==2000 and o['0']['hits']>=1800 and o['0']['rejected']==0 and o['-1']['hits']==0 and o['-1']['rejected']==1 and o['1']['hits']<=40 and o['1']['rejected']==0)
    result('A4',ok,{'fixture':'C_offset'},o,'offset 0 >=1800 hits; offset -1 zero hits with first missing guard rejected; offset +1 <=40 hits')
    failed |= not ok
except Exception as e: result('A4',False,{'fixture':'C_offset'},{},'offset criterion',str(e)); failed=True
try:
    rate0=load('C_rate0_eudaq'); m=rate0['measurement']; counts=m['stream_counts']; tags=m['eore']; hits=counts['hits_total']
    checks={'Z1':rate0['status']=='pass' and m['converted_events']==0,
            'Z2-eudaq':rate0['status']=='pass' and counts['windows_total']==2000 and not counts['last_window_truncated'],
            'Z3-eudaq':rate0['status']=='pass' and 7000<=hits<=10800 and int(tags['hits_total'])==hits,
            'Z4-eudaq':rate0['status']=='pass' and m['stream_status']=='closed_clean' and bool(m.get('stream_projection_and_summary_match'))}
    for name,ok in checks.items():
        result(name,ok,{'fixture':'C_rate0'},{'events':m['converted_events'],'stream_counts':counts,'stream_status':m['stream_status'],'tags_match':m.get('stream_projection_and_summary_match')},'zero events, 2000 full windows, 7000..10800 hits, matching closed stream and tags')
        failed |= not ok
except Exception as e:
    for name in ('Z1','Z2-eudaq','Z3-eudaq','Z4-eudaq'): result(name,False,{'fixture':'C_rate0'},{},'zero-trigger run conditions',str(e))
    failed=True
try:
    names=('T_thr_corry_final','C_thr_corry_final','T_thr_corry_200','C_thr_corry_200'); runs=[load(x) for x in names]
    measures={name:run['measurement'].get('corry') for name,run in zip(names,runs)}
    ok=all(run['status']=='pass' and run['measurement']['corry']['status']=='pass' for run in runs)
    result('E3',ok,{'fixtures':['T_thr','C_thr']},measures,'Corryvreckan hitmap entries equal converted hit counts')
    failed |= not ok
except Exception as e: result('E3',False,{'fixtures':['T_thr','C_thr']},{},'Corryvreckan hitmap agrees',str(e)); failed=True
try:
    device=load('SECRET-device'); names=('eudaq-triggered-small','eudaq-continuous-small','E2'); runs=[load(n) for n in names]
    ok=device['status']=='pass' and all(r['status']=='pass' and not r['measurement']['seed_or_source_leak'] for r in runs)
    result('SECRET',ok,{'fixtures':['C_same_window','triggered-small','continuous-small','C_stream']},{'device':device['status'],'eudaq':{n:r['measurement'].get('seed_or_source_leak') for n,r in zip(names,runs)}},'seed and source internal markers absent from stream, payloads, and run tags')
    failed |= not ok
except Exception as e: result('SECRET',False,{}, {},'no seed or source internal values in output',str(e)); failed=True
raise SystemExit(1 if failed else 0)
