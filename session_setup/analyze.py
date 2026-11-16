# SPDX-License-Identifier: LGPL-3.0-or-later
"""Analyze an independent snapshot of a current or completed run."""
import argparse
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

root=Path('/tmp/session-run')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',help='EUDAQ run number to analyze; defaults to the current run')
parser.add_argument('--list',action='store_true',help='list the run numbers of the raw files and exit')
parser.add_argument('--from-page',action='store_true',help='QA repeated by the result page; only its newest results are kept')
args=parser.parse_args()
if args.list:
    found=sorted(root.glob('run*.raw'),key=lambda p:p.stat().st_mtime_ns)
    if not found:print('No raw file was found.');raise SystemExit(0)
    print('Run numbers (oldest first; the last one is the current or latest run):')
    for p in found:print('  '+p.stem[3:].lstrip('0')+'  '+str(p.stat().st_size)+' bytes  '+p.name)
    raise SystemExit(0)

# The result page reads a structured copy of what QA prints: qa/latest.json and qa/history.jsonl.
STARTED=datetime.now(timezone.utc).isoformat()
# format: the version of the documented items of both files; it changes only when their meaning changes.
RESULT={'format':1,'status':None,'started_utc':STARTED,'settings_changed':None}
KEEP_PAGE_RESULTS=3

def write_result(status,**values):
    qa=root/'qa';qa.mkdir(exist_ok=True)
    RESULT.update(values,status=status,finished_utc=datetime.now(timezone.utc).isoformat())
    temporary=qa/'.latest.json.tmp';temporary.write_text(json.dumps(RESULT,indent=2)+'\n');temporary.replace(qa/'latest.json')
    if status=='ok':
        keep=('format','id','origin','started_utc','finished_utc','run','data_events','tracks','efficiency','settings_changed')
        with (qa/'history.jsonl').open('a') as out:out.write(json.dumps({k:RESULT.get(k) for k in keep})+'\n')

def parsed(analysis):
    """Numbers of the public analysis log, as QA prints them."""
    log=Path(analysis)/'corry.log'
    text=log.read_text(errors='replace') if log.is_file() else ''
    progress=re.findall(r'Ev: ([0-9.]+)([kM]?) .*?\(([0-9.]+)/ev\)',text)
    tracks=re.findall(r'Number of tracks with at least one associated cluster: (\d+) vs total number of tracks: (\d+)',text)
    efficiency=[{'dut':m[0],'percent':float(m[1]),'plus':float(m[2]),'minus':float(m[3]),'matched':int(m[4]),'total':int(m[5])}
                for m in re.findall(r'Total efficiency of detector (\S+): ([0-9.]+)\(\+([0-9.e-]+) -([0-9.e-]+)\)%, measured with (\d+)/(\d+)',text)]
    return {'tracks':int(tracks[-1][1]) if tracks else None,'efficiency':efficiency,
            'progress_events':round(float(progress[-1][0])*{'k':1000,'M':1000000}.get(progress[-1][1],1)) if progress else None,
            'progress_tracks_per_event':float(progress[-1][2]) if progress else None}

def summary(analysis):
    """Repeat the values the public analysis already wrote to its log."""
    log=Path(analysis)/'corry.log'
    if not log.is_file():return
    lines=[re.sub(r'^\|[0-9:.]+\|\s*\([A-Z]+\)\s*','',x).rstrip() for x in log.read_text(errors='replace').splitlines()]
    progress=[x for x in lines if x.startswith('Ev: ')]
    tracks=[x for x in lines if 'Number of tracks with at least one associated cluster' in x]
    efficiency=[x for x in lines if 'Total efficiency of detector' in x]
    print('Analysis summary from '+str(log)+':')
    print('  Events: '+(progress[-1] if progress else 'no progress line in the log'))
    if not tracks and not efficiency:
        # Without a DUT the public analysis prints no track count; read it from
        # the track histogram the same analysis wrote.
        counted=track_count(Path(analysis)/'output'/'histograms.root')
        if counted is not None:
            tracks=[f'{counted} tracks reconstructed (Tracking4D, no DUT in this setup)']
            efficiency=['Efficiency: none; this setup has no msensor DUT']
    for x in tracks or ['no track line in the log']:print('  Tracks: '+x)
    for x in tiles_off(Path(analysis).parent/'analysis-geometry.conf'):print('  '+x)
    for x in efficiency or ['no efficiency line in the log']:print('  '+x)
def tiles_off(geometry):
    """One line per DUT whose switched-off tiles the efficiency leaves out."""
    if not geometry.is_file():return []
    lines=[];name=None
    for line in geometry.read_text().splitlines():
        if line.startswith('[') and line.endswith(']'):name=line[1:-1]
        elif line.startswith('# tiles off:') and name:
            lines.append(f'Tiles off in {name} (not counted in its efficiency): {line.split(":",1)[1].strip()}')
    return lines

def track_count(histograms):
    if not histograms.is_file():return None
    code=('import ROOT,sys\nf=ROOT.TFile.Open(sys.argv[1])\nh=f.Get("Tracking4D/clustersPerTrack")\n'
          'print(int(h.GetEntries()) if h else "")')
    p=subprocess.run(['python','-c',code,str(histograms)],text=True,capture_output=True)
    value=p.stdout.strip().splitlines()[-1] if p.returncode==0 and p.stdout.strip() else ''
    return int(value) if value.isdecimal() else None

def reference_planes(geometry):
    """Count the non-DUT planes of the session's analysis geometry."""
    sections=[];roles={}
    for line in geometry.read_text().splitlines():
        line=line.strip()
        if line.startswith('[') and line.endswith(']'):sections.append(line[1:-1]);roles[sections[-1]]=''
        elif line.replace(' ','').startswith('role=') and sections:roles[sections[-1]]=line.split('=',1)[1].strip().strip('"').lower()
    return sum(roles[n]!='dut' for n in sections)

# One QA at a time per session: a terminal run and a page refresh wait for each other.
(root/'qa').mkdir(exist_ok=True)
lock=(root/'qa'/'.lock').open('w')
fcntl.flock(lock,fcntl.LOCK_EX)
RESULT['started_utc']=datetime.now(timezone.utc).isoformat()

if shutil.which('corry') is None:
    print('Analysis tool Corryvreckan is not installed in this acquisition-only image.',file=sys.stderr)
    raise SystemExit(2)
if not (root/'analysis-geometry.conf').is_file():
    print('Analysis geometry is missing. Generate the layout with --analysis-geometry.',file=sys.stderr)
    raise SystemExit(2)
references=reference_planes(root/'analysis-geometry.conf')
if references<3:
    print(f'QA not possible: this session has {references} reference plane(s); tracks need at least 3. '
          'No result was produced.',file=sys.stderr)
    raise SystemExit(2)
# Positions: with hidden offsets, QA needs an alignment that fits this session's devices.
from session_setup import aligned
nominal_text=(root/'analysis-geometry.conf').read_text()
setup=json.loads((root/'setup.json').read_text()) if (root/'setup.json').is_file() else {}
offset_max=setup.get('positions',{}).get('offset_max_mm',0)
alignment=aligned.load(root)
if alignment:
    reason=aligned.matches(alignment,nominal_text)
    if reason:
        print(f'The alignment in this session cannot be used: {reason}.')
        alignment=None
RESULT['alignment']=None
if alignment:
    RESULT['alignment']={k:alignment.get(k) for k in ('id','run','data_events','corrections_um','tracks_per_event_before',
                                                      'tracks_per_event_after','reference_planes','duts')}
    geometry_text=aligned.compose(nominal_text,alignment['positions'])
    print(f'Using alignment {alignment["id"]} (made from {alignment["run"]} with {alignment["data_events"]} data events).',flush=True)
elif offset_max:
    print(f'NOT ALIGNED: the actual device positions differ from the setup by up to {offset_max} mm in x and y. '
          'Align first with: scripts/align_session.sh CONTAINER')
    print('QA stops here; no numbers were produced.')
    write_result('not_aligned',offset_max_mm=offset_max)
    raise SystemExit(3)
else:
    geometry_text=nominal_text

def position_check(analysis):
    """After an aligned analysis, say if the residuals or the track rate suggest that positions changed."""
    if not alignment:return
    histograms=Path(analysis)/'output'/'histograms.root'
    if not histograms.is_file():return
    p=subprocess.run(['python','-m','session_setup.histograms',str(histograms),','.join(alignment['reference_planes']),
                      ','.join(alignment['duts'])],text=True,capture_output=True)
    if p.returncode:print('  Residual check not possible: '+p.stderr.strip()[-300:]);return
    data=json.loads(p.stdout.splitlines()[-1])
    means=[];moved=[]
    for name,axes in data['residuals'].items():
        values=[]
        for axis in ('x','y'):
            r=axes.get(axis)
            if r and r['entries']>=100:
                values.append(f'{axis} {r["mean_um"]:+.1f}')
                if abs(r['mean_um'])>RESIDUAL_LIMIT_UM:moved.append(f'{name} {axis}')
            else:values.append(f'{axis} -')
        means.append(f'{name} '+' '.join(values))
    print('  Residual means (um): '+'; '.join(means))
    RESULT['residual_means_um']={name:{axis:(r['mean_um'] if r and r['entries']>=100 else None) for axis,r in axes.items()}
                                 for name,axes in data['residuals'].items()}
    RESULT['tracks_per_event']=data['tracks_per_event']
    low=data['tracks_per_event']<0.5*alignment['tracks_per_event_after']
    if moved or low:
        why=[]
        if moved:why.append('residual mean above '+str(RESIDUAL_LIMIT_UM)+' um for '+', '.join(moved))
        if low:why.append(f'{data["tracks_per_event"]:.3f} tracks per event against {alignment["tracks_per_event_after"]:.3f} when aligned')
        RESULT['position_warning']='; '.join(why)
        print('  POSITIONS CHANGED? '+'; '.join(why)+'. The devices seem to have moved since this alignment; '
              'align again with: scripts/align_session.sh CONTAINER')

RESIDUAL_LIMIT_UM=20
raws=list(root.glob('run*.raw'))
if args.run is not None:
    if not args.run.isdecimal():raise SystemExit('run number must contain digits only')
    raws=[p for p in raws if re.fullmatch(r'run0*'+re.escape(str(int(args.run)))+r'\.raw',p.name)]
if not raws:raise SystemExit('No matching raw file was found.')
raw=max(raws,key=lambda p:p.stat().st_mtime_ns)
settings_changed=None
if raw.with_suffix('.conf').is_file() and (root/'setup.json').is_file():
    from session_setup.run_settings import setup as original_setup, read_conf, changes
    initial=original_setup(root)
    settings_changed=changes(initial,read_conf(initial,raw.with_suffix('.conf')))
RESULT['settings_changed']=settings_changed

def settings_summary():
    if settings_changed is None: return
    if not settings_changed:
        print('Settings of this run: as in the setup file')
    else:
        print('Settings of this run, changed from the setup file: '+'; '.join(
            f"{r['section']} {r['item']} {r['value']} (setup: {r['setup']})" for r in settings_changed))
subprocess.run(['bash','/opt/public-work/integration/build_modules.sh','/opt/public-work/integration','/tmp'],check=True)
example=(root/'session-mode').is_file() and (root/'session-mode').read_text().strip()=='example' and args.run is None
if example:
    command=['python','/opt/public-work/session_setup/integration_analysis.py',str(raw),str(root/'analysis')]
    if alignment:
        (root/'qa-geometry.conf').write_text(geometry_text);command.append(str(root/'qa-geometry.conf'))
    code=subprocess.run(command,env=dict(os.environ,QA_CORRELATIONS='1')).returncode
    print('Corryvreckan configuration: '+str(root/'analysis'/'corry.conf')+' (in the container)',flush=True)
    summary(root/'analysis');position_check(root/'analysis')
    numbers=parsed(root/'analysis')
    write_result('ok' if code==0 else 'failed',id='example-'+raw.stem,origin='page' if args.from_page else 'command',run=raw.name,mode='example',
                 data_events=numbers['progress_events'],data_events_from='analysis progress line',
                 tracks=numbers['tracks'],efficiency=numbers['efficiency'],tiles_off=tiles_off(root/'analysis-geometry.conf'),
                 histograms='analysis/output/histograms.root')
    settings_summary()
    raise SystemExit(code)
work=root/'qa'/('run-'+raw.stem+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
work.mkdir(parents=True)
(work/'analysis-geometry.conf').write_text(geometry_text)
# Mask files of switched-off tiles are named relative to the analysis geometry.
for mask in re.findall(r'^mask_file\s*=\s*"([^"]+)"',(root/'analysis-geometry.conf').read_text(),re.M):
    shutil.copyfile(root/mask,work/mask)
capture=work/'capture.raw';snapshot=work/'snapshot.raw'
with raw.open('rb') as source, capture.open('xb') as output:
    remaining=os.fstat(source.fileno()).st_size
    while remaining:
        block=source.read(min(1024*1024,remaining))
        if not block:raise RuntimeError('raw file changed while it was copied')
        output.write(block);remaining-=len(block)
result=subprocess.run(['python','-m','session_setup.snapshot',str(capture),str(snapshot)],text=True,capture_output=True)
if result.returncode:raise SystemExit('EUDAQ could not make a complete-event snapshot: '+(result.stdout+result.stderr)[-2000:])
metadata=json.loads(result.stdout.splitlines()[-1]);metadata.update(raw=raw.name,snapshot=str(snapshot),analysis=str(work/'analysis'))
(work/'snapshot.json').write_text(json.dumps(metadata,indent=2)+'\n')
capture.unlink()
print('Snapshot:',json.dumps(metadata,sort_keys=True),flush=True)
code=subprocess.run(['python','/opt/public-work/session_setup/integration_analysis.py',str(snapshot),str(work/'analysis')],
                    env=dict(os.environ,QA_CORRELATIONS='1')).returncode
# The results and snapshot.json remain; the large raw copy is no longer needed.
snapshot.unlink(missing_ok=True)
print('Complete data events in the snapshot: '+str(metadata['data_events']))
print('Corryvreckan configuration: '+str(work/'analysis'/'corry.conf')+' (in the container)')
summary(work/'analysis')
position_check(work/'analysis')
numbers=parsed(work/'analysis')
(work/'origin').write_text('page\n' if args.from_page else 'command\n')
if args.from_page:
    # Results made by the page's refresh: keep the newest few; results of QA run by hand stay.
    made=sorted((d for d in (root/'qa').iterdir() if (d/'origin').is_file() and (d/'origin').read_text().strip()=='page'),
                key=lambda d:d.stat().st_mtime_ns,reverse=True)
    for old in made[KEEP_PAGE_RESULTS:]:shutil.rmtree(old,ignore_errors=True)
write_result('ok' if code==0 else 'failed',id=work.name,origin='page' if args.from_page else 'command',run=raw.name,mode='snapshot',data_events=metadata['data_events'],
             data_events_from='snapshot',tracks=numbers['tracks'],efficiency=numbers['efficiency'],
             tiles_off=tiles_off(work/'analysis-geometry.conf'),histograms=str((work/'analysis'/'output'/'histograms.root').relative_to(root)))
settings_summary()
raise SystemExit(code)
