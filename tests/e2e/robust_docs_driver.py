#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the unmodified session guide in a disposable checkout, preserving host data."""
import json,os,platform,re,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'artifacts/robustness';OUT.mkdir(parents=True,exist_ok=True)
rev=(ROOT/'SOURCE_REVISION').read_text().strip()
layout=Path('/tmp/public-session-layout')

def emit(status,measurement,reason=None):
 row={'id':'I-E11','status':status,'input_conditions':{'architecture':platform.machine(),'command_source':'docs/session-run.md','code_block_unmodified':True},'measurement':measurement,'criterion':'documented end-to-end acceptance criteria','repository_commit':rev,'image_id':subprocess.run(['docker','image','inspect','local/public-session:native','--format','{{.Id}}'],text=True,capture_output=True).stdout.strip()}
 if reason:row['reason']=reason
 (OUT/'I-E11.json').write_text(json.dumps(row,indent=2)+'\n');print('I-E11',status,flush=True)
 return 0 if status=='pass' else 1

source=(ROOT/'docs/session-run.md').read_text();blocks=re.findall(r'```sh\n(.*?)```',source,re.S)
if len(blocks)!=1:sys.exit(emit('fail',{},'session guide needs exactly one executable block'))
containers=subprocess.run(['docker','container','ls','--all','--format','{{.Names}}'],text=True,capture_output=True)
if containers.returncode:sys.exit(emit('not_run',{},'cannot inspect existing containers: '+containers.stderr[-500:]))
if 'public-session' in containers.stdout.splitlines():sys.exit(emit('not_run',{},'existing public-session container must be preserved'))

with tempfile.TemporaryDirectory(prefix='session-docs-') as temp:
 temp=Path(temp);checkout=temp/'checkout';backup_root=Path(tempfile.mkdtemp(prefix='session-docs-layout-',dir='/tmp'));prior=backup_root/'prior-layout';script=temp/'commands.sh';bin_dir=temp/'bin';bin_dir.mkdir()
 if (ROOT/'.git').exists():
  subprocess.run(['git','clone','--quiet','--no-hardlinks',str(ROOT),str(checkout)],check=True)
  # Preserve tested modifications, including injected documentation faults.
  changed=subprocess.check_output(['git','diff','--name-only','--diff-filter=ACM','HEAD'],cwd=ROOT,text=True).splitlines()
  for name in changed:
   src=ROOT/name;dst=checkout/name
   if src.is_file():dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
 else:
  # Public export has no Git metadata; copy only source, not user outputs.
  shutil.copytree(ROOT,checkout,ignore=shutil.ignore_patterns('artifacts','session-output','__pycache__','*.pyc'))
 script.write_text('set -euo pipefail\n'+blocks[0])
 docker=shutil.which('docker');created=temp/'created-container-id'
 # Record the ID returned by this invocation's successful docker create. On failure,
 # cleanup addresses only that ID, never a container merely sharing the name.
 wrapper=bin_dir/'docker';wrapper.write_text('''#!/usr/bin/env python3\nimport os,subprocess,sys\nargs=sys.argv[1:]\nif args and args[0]=="create" and "--name" in args and args[args.index("--name")+1]=="public-session":\n p=subprocess.run([os.environ["DOCS_REAL_DOCKER"],*args],capture_output=True)\n sys.stdout.buffer.write(p.stdout);sys.stderr.buffer.write(p.stderr)\n if p.returncode==0:open(os.environ["DOCS_CREATED_ID"],"w").write(p.stdout.decode().strip())\n sys.exit(p.returncode)\nif args and args[0]=="rm" and "public-session" in args:\n try:owned=open(os.environ["DOCS_CREATED_ID"]).read().strip()\n except OSError:owned=""\n current=subprocess.run([os.environ["DOCS_REAL_DOCKER"],"inspect","--format","{{.Id}}","public-session"],capture_output=True,text=True)\n if not owned or current.returncode or current.stdout.strip()!=owned:\n  print("Refusing to remove a container not created by this check",file=sys.stderr);sys.exit(1)\nos.execv(os.environ["DOCS_REAL_DOCKER"],[os.environ["DOCS_REAL_DOCKER"],*args])\n''');wrapper.chmod(0o755)
 env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],DOCS_REAL_DOCKER=docker,DOCS_CREATED_ID=str(created))
 had_layout=os.path.lexists(layout);owned=None;restored=False;p=None;files={};error=None
 try:
  if had_layout:os.rename(layout,prior)
  layout.mkdir();st=layout.stat();owned=(st.st_dev,st.st_ino)
  p=subprocess.run(['bash',str(script)],cwd=checkout,text=True,capture_output=True,timeout=3600,env=env)
  (OUT/'I-E11.log').write_text(p.stdout+p.stderr)
  output=checkout/'session-output';files={'raw':bool(list(output.glob('*.raw'))),'analysis':(output/'analysis/output/events.root').is_file()}
 except Exception as exc:
  error=str(exc)
 finally:
  if created.exists():
   cid=created.read_text().strip()
   if cid:subprocess.run([docker,'rm','-f',cid],capture_output=True)
  if os.path.lexists(layout):
   st=layout.lstat()
   if owned==(st.st_dev,st.st_ino):shutil.rmtree(layout)
   else:sys.exit(emit('not_run',{},'layout path was replaced; preserving it and any prior layout at '+str(prior)))
  if had_layout:os.rename(prior,layout)
  backup_root.rmdir()
  restored=True
 ok=p is not None and p.returncode==0 and bool(files) and all(files.values()) and restored
 sys.exit(emit('pass' if ok else 'fail',{'exit_code':p.returncode if p else None,'outputs':files,'prior_layout_preserved':restored,'isolated_checkout':True},None if ok else error or 'guide command or output failed'))
