# SPDX-License-Identifier: LGPL-3.0-or-later
"""Check actual raw and public Corryvreckan output against isolated truth."""
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import pyeudaq
import ROOT
from msensor.codec import decode

ROOT.gSystem.Load('/opt/corry/lib/libCorryvreckanObjects.so')
root=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/integration-e2e')
out=Path('/tmp/integration-results');out.mkdir(exist_ok=True)
expected={*(f'ALPIDE_plane_{i}' for i in range(6)),'msensor'}
def emit(name,ok,**measurement):
    data={'status':'pass' if ok else 'fail','measurement':measurement}
    (out/(name+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    print(name,data['status'],flush=True)

def parse_raw(path):
    reader=pyeudaq.FileReader('native',str(path));groups=[];bore=set();eore=set();hits={};times={}
    while True:
        ev=reader.GetNextEvent()
        if ev is None:break
        if ev.IsBORE():bore.add(ev.GetDescription())
        if ev.IsEORE():eore.add(ev.GetDescription())
        if ev.GetDescription()=='ITS3global':
            subs=ev.GetSubEvents();group=[(s.GetDescription(),s.GetTriggerN()) for s in subs];groups.append(group)
            for sub in subs:
                if sub.GetDescription()=='msensor':
                    d=decode(bytes(sub.GetBlock(0)));hits[sub.GetTriggerN()]=any(frame['hits'] for frame in d['frames']);times[sub.GetTriggerN()]=sub.GetTimestampBegin()
    names={*(f'ALPIDE_plane_{i}_status' for i in range(6)),'MSENSOR_0_status'}
    return {'groups':groups,'closed':names<=bore and names<=eore,'hits':hits,'times':times}

def source_efficiency(trigger,threshold):
    # Independent transcription of the published counter-based response law.
    key=f'86753091234:response:{trigger}'.encode('ascii')
    u=((int.from_bytes(hashlib.sha256(key).digest()[:8],'big')>>12)+0.5)/2**52
    return u < .99/(1+math.exp((threshold-160)/12))

def analyze(path,truth,raw,threshold):
    f=ROOT.TFile.Open(str(path/'output/events.root'));hist=ROOT.TFile.Open(str(path/'output/histograms.root'))
    if not f or f.IsZombie() or not hist or hist.IsZombie():raise RuntimeError('invalid Corryvreckan ROOT output')
    events=f.Get('Event');tracks=f.Get('Track');clusters=f.Get('Cluster')
    if not events or not clusters:raise RuntimeError('missing event or cluster analysis tree')
    if events.GetEntries()!=clusters.GetEntries() or (tracks and events.GetEntries()!=tracks.GetEntries()):raise RuntimeError('analysis tree lengths differ')
    truth_by_id={x['trigger']:x for x in truth['crossings']};selected=[];sizes=[[] for _ in range(6)];seen=[];badtruth=0
    for i in range(events.GetEntries()):
        events.GetEntry(i);clusters.GetEntry(i)
        if tracks:tracks.GetEntry(i)
        event=getattr(events,'global');ids=[int(x.first) for x in event.triggerList()]
        if len(ids)!=1:raise RuntimeError('Corry event does not have exactly one raw trigger')
        trigger=ids[0];seen.append(trigger)
        for j in range(6):
            for cl in getattr(clusters,'ALPIDE_'+str(j)):sizes[j].append(int(cl.size()))
        if not tracks:continue
        track_list=getattr(tracks,'global')
        if track_list.size()>1:raise RuntimeError('multiple tracks for one trigger')
        if not track_list.size():continue
        track=track_list[0];position=track.getIntercept(60.)
        if abs(position.X()-1)>1.5 or abs(position.Y()+2)>1.5:continue
        row=truth_by_id.get(trigger);mc=row['planes']['D0']['mc'] if row else []
        if len(mc)!=1:badtruth+=1;continue
        source=source_efficiency(trigger,threshold)
        associated=bool(track.getAssociatedClusters('MSENSOR_6').size())
        selected.append((trigger,associated,source,raw['hits'].get(trigger,False)))
    residual={}
    for axis in ('X','Y'):
        h=hist.Get('AnalysisDUT/MSENSOR_6/local_residuals/residuals'+axis)
        if not h:raise RuntimeError('missing residual histogram')
        residual[axis]={'entries':int(h.GetEntries()),'mean_mm':h.GetMean()/1000.,'rms_mm':h.GetRMS()/1000.}
    n=len(selected);counts={'associated':sum(x[1] for x in selected),'truth_response':sum(x[2] for x in selected),'raw_hit':sum(x[3] for x in selected)}
    ratios={k:v/n if n else None for k,v in counts.items()}
    result={'raw_events':len(raw['groups']),'corry_events':len(seen),'corry_trigger_matches_raw':seen==[g[0][1] for g in raw['groups']],
            'track_count':n,'track_ratio':n/len(raw['groups']) if raw['groups'] else 0,'missing_truth':badtruth,
            'counts':counts,'ratios':ratios,'residual':residual,'cluster_counts':[len(x) for x in sizes],
            'cluster_mean':[sum(x)/len(x) if x else 0 for x in sizes]}
    f.Close();hist.Close();return result

def check_i1(root,raw):
    journal=[json.loads(line) for line in (root/'published.jsonl').read_text().splitlines()]
    stopped={}
    for row in journal:
        if row['rc_state']=='STOPPED':stopped[row['config']['index']]={x['name']:x['data_events'] for x in row['connections'] if x['role']=='producer'}
    counts=[];alignment=[]
    for i,r in enumerate(raw):
        alignment.append(all(len(g)==7 and {name for name,_ in g}==expected and len({n for _,n in g})==1 for g in r['groups']))
        target={*(f'ALPIDE_plane_{j}' for j in range(6)),'MSENSOR_0'}
        observed=stopped.get(i,{})
        counts.append(set(observed)==target and all(v==len(r['groups']) for v in observed.values()))
    ok=all(r['closed'] and len(r['groups'])>=300 for r in raw) and all(alignment) and all(counts)
    return ok,{'event_counts':[len(r['groups']) for r in raw],'closed':[r['closed'] for r in raw],
               'alignment':alignment,'status_counts_match':counts}

def main():
    raws=sorted(root.glob('*.raw'))
    if len(raws)!=2:raise RuntimeError('expected two raw runs')
    raw=[parse_raw(p) for p in raws]
    ok,measurement=check_i1(root,raw);emit('I-E1',ok,**measurement)
    truths=[json.loads(p.read_text()) for p in (root/'truth').glob('run-*.json')]
    truth=[]
    for p in raws:
        run=int(re.search(r'run(\d+)\.raw$',p.name).group(1));truth.append(next(x for x in truths if run in x['eudaq_runs']))
    logs=[(root/('analysis-'+x)/'corry.log').read_text(errors='replace') for x in ('a','b')]
    known='Warning in <TH1::TH1>: nbins is <=0 - set to nbins = 1'
    errors=[[line for line in log.splitlines() if ('(WARNING)' in line or '(ERROR)' in line or '(FATAL)' in line or 'Warning in <' in line or 'Error in <' in line or 'Unknown class ID' in line) and line.strip()!=known] for log in logs]
    known_count=[sum(line.strip()==known for line in log.splitlines()) for log in logs]
    a,b=[analyze(root/('analysis-'+x),t,r,threshold) for x,t,r,threshold in zip(('a','b'),truth,raw,(100,200))]
    emit('I-E2',not any(errors) and known_count==[1,1] and a['corry_trigger_matches_raw'] and b['corry_trigger_matches_raw'],warnings=errors,allowed_root_warning_count=known_count,
         corry_events=[a['corry_events'],b['corry_events']],corryvreckan_source='official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe')
    emit('I-E3',.90<=a['track_ratio']<=1 and .35<=b['track_ratio']<=.71 and min(a['track_count'],b['track_count'])>=100,
         track_counts=[a['track_count'],b['track_count']],track_ratios=[a['track_ratio'],b['track_ratio']])
    r=a['residual'];emit('I-E4',all(x['entries']>=30 and abs(x['mean_mm'])<=.06 and x['rms_mm']<=.06 for x in r.values()),residual=r)
    def efficiency_ok(x,low,high):
        vals=list(x['ratios'].values());n=x['track_count'];tol=max(.06,5*math.sqrt(.25/n)) if n else 0
        return n>=100 and x['missing_truth']==0 and all(low<=v<=high for v in vals) and max(vals)-min(vals)<=tol
    emit('I-E5',efficiency_ok(a,.9,1) and efficiency_ok(b,0,.12),denominators=[a['track_count'],b['track_count']],counts=[a['counts'],b['counts']],ratios=[a['ratios'],b['ratios']],missing_truth=[a['missing_truth'],b['missing_truth']])
    emit('I-E6',a['ratios']['associated']-b['ratios']['associated']>=.5,efficiencies=[a['ratios']['associated'],b['ratios']['associated']])
    means=[sum(x['cluster_mean'])/6 for x in (a,b)]
    emit('I-E7',min(a['cluster_counts']+b['cluster_counts'])>=100 and 1.6<=means[0]<=2.3 and 1.3<=means[1]<=2.1 and means[0]-means[1]>=.08,means=means,per_plane=[a['cluster_mean'],b['cluster_mean']],cluster_counts=[a['cluster_counts'],b['cluster_counts']])

if __name__=='__main__':main()
