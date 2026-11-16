# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise the documented build and session entry points in clean containers."""
import json, os, platform, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
HERE=Path.cwd();OUT=HERE/'artifacts/setup';OUT.mkdir(parents=True,exist_ok=True)
TEMP=Path(tempfile.mkdtemp(prefix='setup-e2e-'))
COMMIT=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
FULL='local/release-setup-u1';ACQ='local/release-setup-u2';results={}

def command(args,timeout=2400,allow=False):
 p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 if p.returncode and not allow:raise RuntimeError(args[0]+' exit '+str(p.returncode)+': '+(p.stdout+p.stderr)[-3500:])
 return p

def image_id(image):
 p=command(['docker','image','inspect',image,'--format','{{.Id}}'],allow=True)
 return p.stdout.strip() if p.returncode==0 else None

def result(name,ok,measure=None,reason=None,image=None,status=None):
 row={'id':name,'status':status or ('pass' if ok else 'fail'),'input_conditions':{'architecture':platform.machine()},
      'measurement':measure or {},'criterion':'documented end-to-end acceptance criteria',
      'image_id':image_id(image or FULL+':native'),'repository_commit':COMMIT}
 if reason:row['reason']=reason
 if name in ('U-E1','U-E3','U-E4','U-E5'):row['corryvreckan_source']='official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'
 results[name]=row;(OUT/(name+'.json')).write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n')
 print(name,row['status'],flush=True)

def attempt(name,fn):
 try:fn()
 except Exception as exc:result(name,False,reason=str(exc),image=ACQ+':native' if name=='U-E2' else None)

def layout(folder,analysis=False):
 args=['python3','-m','integration.layout','--layout','configs/layouts/default.json','--directory',str(folder),'--example']
 if analysis:args.append('--analysis-geometry')
 command(args)

def start(name,image,folder):
 return command(['scripts/start_session.sh','--layout',str(folder),'--name',name,'--image',image,'--example','--no-attach'],timeout=120)

def session(name,image,folder):
 start(name,image,folder)
 command(['scripts/session_control.sh',name,'on'],timeout=20)
 command(['scripts/wait_session.sh',name,'ready'],timeout=200)
 command(['docker','cp',str(HERE/'tests/e2e/setup_secrecy.py'),name+':/tmp/setup_secrecy.py'])
 secret=command(['docker','exec',name,'python','/tmp/setup_secrecy.py'],timeout=30)
 privacy=json.loads(secret.stdout.splitlines()[-1])
 command(['scripts/session_control.sh',name,'stop'],timeout=20)
 command(['scripts/wait_session.sh',name,'closed'],timeout=200)
 for src,dst in [('layout_convert.cc','/tmp/layout_convert.cc'),('setup_inspect.py','/tmp/setup_inspect.py')]:
  command(['docker','cp',str(HERE/'tests/e2e'/src),name+':'+dst])
 command(['docker','exec',name,'c++','-std=c++17','-I/opt/eudaq/include','/tmp/layout_convert.cc','-L/opt/eudaq/lib','-leudaq_core','-ldl','-Wl,-rpath,/opt/eudaq/lib','-o','/tmp/layout-convert'],timeout=120)
 inspected=command(['docker','exec',name,'python','/tmp/setup_inspect.py'],timeout=120)
 data=json.loads(inspected.stdout.splitlines()[-1])
 return data,privacy

def u1():
 p=command(['scripts/build_session.sh','--image',FULL],timeout=2400,allow=True)
 (OUT/'build-full.log').write_text(p.stdout+p.stderr)
 base=image_id(FULL+'-base:native');image=image_id(FULL+':native')
 result('U-E1',p.returncode==0 and bool(base) and bool(image),{'exit_code':p.returncode,'base_image_id':base,'session_image_id':image,'tag':FULL+':native'})

def u2():
 p=command(['scripts/build_session.sh','--acquisition-only','--image',ACQ],timeout=2400,allow=True)
 (OUT/'build-acquisition.log').write_text(p.stdout+p.stderr)
 if p.returncode:raise RuntimeError('acquisition image build failed')
 name='release-setup-u2';folder=TEMP/'u2';layout(folder)
 try:
  data,privacy=session(name,ACQ+':native',folder)
  exists=command(['docker','exec',name,'sh','-c','command -v corry >/dev/null 2>&1 || test -e /opt/corry'],allow=True)
  analysis=command(['scripts/analyze_session.sh',name],timeout=60,allow=True)
  absent=exists.returncode!=0
  notice=analysis.returncode!=0 and 'not installed' in (analysis.stdout+analysis.stderr).lower()
  result('U-E2',absent and notice and data['common_pass'] and privacy['status']=='pass',
         {'build_exit':p.returncode,'analysis_tool_absent':absent,'analysis_exit':analysis.returncode,'analysis_missing_notice':notice,'acquisition':data,'secrecy':privacy},image=ACQ+':native')
 finally:command(['docker','rm','-f',name],allow=True)

def u3():
 name='release-setup-u3';folder=TEMP/'u3';layout(folder)
 try:
  data,privacy=session(name,FULL+':native',folder)
  result('U-E3',data['common_pass'] and privacy['status']=='pass',{'acquisition':data,'secrecy':privacy,'single_start_command':True})
 finally:command(['docker','rm','-f',name],allow=True)

def u4():
 # The automated example moved from the README to its own document (the exported docs/, or release/docs/docs here).
 source=next(p for p in (HERE/'docs/automation.md',HERE/'release/docs/docs/automation.md') if p.is_file())
 readme=source.read_text().split('<!-- automated-example -->',1)[1];blocks=re.findall(r'```sh\n(.*?)```',readme,re.S)
 if len(blocks)!=1:raise RuntimeError('automated example command block missing or ambiguous')
 existing=command(['docker','inspect','public-session'],allow=True)
 if existing.returncode==0:
  result('U-E4',False,reason='public-session already exists; preserving the existing container',status='not_run');return
 # Execute the unmodified commands in a disposable working tree and restore
 # the pre-existing absolute layout path even if a command fails.
 checkout=TEMP/'readme-checkout'
 shutil.copytree(HERE,checkout,ignore=shutil.ignore_patterns('.git','artifacts','session-output','__pycache__','*.pyc','*.bundle'))
 doc=TEMP/'readme-commands.sh';doc.write_text('set -euo pipefail\n'+blocks[0])
 layout_path=Path('/tmp/public-session-layout');backup=Path(tempfile.mkdtemp(prefix='setup-layout-backup-'));prior=backup/'prior';owned=None
 docker=shutil.which('docker');created=TEMP/'readme-container-id';bindir=TEMP/'readme-bin';bindir.mkdir()
 wrapper=bindir/'docker';wrapper.write_text("""#!/usr/bin/env python3
import os,subprocess,sys
args=sys.argv[1:];real=os.environ['SETUP_REAL_DOCKER'];record=os.environ['SETUP_CREATED_ID']
if args and args[0]=='create' and '--name' in args and args[args.index('--name')+1]=='public-session':
 p=subprocess.run([real,*args],capture_output=True);sys.stdout.buffer.write(p.stdout);sys.stderr.buffer.write(p.stderr)
 if p.returncode==0:open(record,'w').write(p.stdout.decode().strip())
 sys.exit(p.returncode)
if args and args[0]=='rm' and 'public-session' in args:
 try:owned=open(record).read().strip()
 except OSError:owned=''
 current=subprocess.run([real,'inspect','--format','{{.Id}}','public-session'],capture_output=True,text=True)
 if not owned or current.returncode or current.stdout.strip()!=owned:
  print('Refusing to remove a container not created by this check',file=sys.stderr);sys.exit(1)
os.execv(real,[real,*args])
""");wrapper.chmod(0o755)
 env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],SETUP_REAL_DOCKER=docker,SETUP_CREATED_ID=str(created))
 restored=False;run_result=None;files={}
 try:
  if os.path.lexists(layout_path):os.rename(layout_path,prior)
  layout_path.mkdir();st=layout_path.stat();owned=(st.st_dev,st.st_ino)
  run_result=subprocess.run(['bash',str(doc)],cwd=checkout,env=env,text=True,capture_output=True,timeout=2400)
  (OUT/'readme.log').write_text(run_result.stdout+run_result.stderr)
  output=checkout/'session-output';files={'raw':bool(list(output.glob('*.raw'))),'analysis':(output/'analysis/output/events.root').is_file(),'status':(output/'status.json').is_file()}
 finally:
  if created.exists():
   cid=created.read_text().strip()
   if cid:subprocess.run([docker,'rm','-f',cid],capture_output=True)
  if os.path.lexists(layout_path):
   st=layout_path.lstat()
   if (st.st_dev,st.st_ino)==owned:shutil.rmtree(layout_path)
   else:raise RuntimeError('layout path was replaced; preserving it and prior data at '+str(prior))
  if os.path.lexists(prior):os.rename(prior,layout_path)
  backup.rmdir();restored=True
 result('U-E4',bool(run_result and run_result.returncode==0 and all(files.values()) and restored),{'exit_code':run_result.returncode if run_result else None,'output_files':files,'prior_layout_preserved':restored,'isolated_checkout':True})

def u5():
 with (OUT/'regression-layouts.log').open('w') as stream:
  p=subprocess.run(['tests/scripts/run_layouts_e2e.sh'],stdout=stream,stderr=subprocess.STDOUT,timeout=3600)
 statuses={}
 for i in range(1,9):
  f=HERE/'artifacts/layouts'/f'L-E{i}.json';statuses[f'L-E{i}']=json.loads(f.read_text()).get('status') if f.exists() else 'missing'
 result('U-E5',p.returncode==0 and all(x=='pass' for x in statuses.values()),{'exit_code':p.returncode,'layout_checks':statuses,'existing_e2e_included_in':'L-E8'})

try:
 for i,fn in enumerate((u1,u2,u3,u4,u5),1):attempt('U-E'+str(i),fn)
finally:shutil.rmtree(TEMP,ignore_errors=True)
sys.exit(0 if len(results)==5 and all(x['status']=='pass' for x in results.values()) else 1)
