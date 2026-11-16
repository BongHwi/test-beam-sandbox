# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise container entrypoint and tmux when the conda shell hook is absent."""
import json,os,platform,subprocess,sys,time,uuid
from pathlib import Path

ROOT=Path.cwd();OUT=ROOT/'artifacts/timing-observations';OUT.mkdir(parents=True,exist_ok=True)
image='local/runcontrol:native';name='shell-guard-'+uuid.uuid4().hex[:12]
commit=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
def run(args,timeout=30,allow=False):
 p=subprocess.run([str(x) for x in args],text=True,capture_output=True,timeout=timeout)
 if p.returncode and not allow:raise RuntimeError(str(args[0])+' exit '+str(p.returncode)+': '+(p.stdout+p.stderr)[-2000:])
 return p
result={'status':'fail','repository_commit':commit,'image_id':None,'input_conditions':{'architecture':platform.machine(),'condition':'temporarily remove conda.sh inside a disposable native container'},'criterion':'entrypoint and interactive tmux shell use native Python without missing conda.sh errors'}
try:
 result['image_id']=run(['docker','image','inspect',image,'--format','{{.Id}}']).stdout.strip()
 run(['docker','create','--name',name,'--network','none',image,'sleep','300'])
 run(['docker','cp',ROOT/'docker/base/entrypoint.sh',name+':/tmp/entrypoint-guard.sh']);run(['docker','start',name])
 # Apply exactly the same bashrc substitution used by the Dockerfile.
 command=r'''cp /tmp/entrypoint-guard.sh /entry_point.sh; chmod 755 /entry_point.sh; sed -i 's|^\. /opt/conda/etc/profile.d/conda.sh && conda activate base$|if [ -r /opt/conda/etc/profile.d/conda.sh ]; then . /opt/conda/etc/profile.d/conda.sh \&\& conda activate base; fi|' /root/.bashrc; mv /opt/conda/etc/profile.d/conda.sh /tmp/conda.sh.saved'''
 run(['docker','exec',name,'bash','-c',command])
 entry=run(['docker','exec',name,'/entry_point.sh','python','-c','import sys; print(sys.executable)'],allow=True)
 run(['docker','exec',name,'tmux','new-session','-d','-s','guard',"bash -i -c 'command -v python; echo SHELL_READY; cat'"])
 deadline=time.monotonic()+20;screen=''
 while time.monotonic()<deadline:
  screen=run(['docker','exec',name,'tmux','capture-pane','-p','-t','guard','-S','-100'],allow=True).stdout
  if 'SHELL_READY' in screen:break
  time.sleep(.05)
 warning='conda.sh: No such file or directory'
 ok=entry.returncode==0 and '/opt/conda/bin/python' in entry.stdout and 'SHELL_READY' in screen and '/opt/conda/bin/python' in screen and warning not in entry.stderr and warning not in screen
 result.update(status='pass' if ok else 'fail',measurement={'entry_exit':entry.returncode,'entry_stdout':entry.stdout,'entry_stderr':entry.stderr,'tmux_screen':screen,'conda_hook_present':False})
except Exception as exc:result['reason']=str(exc)
finally:run(['docker','rm','-f',name],allow=True)
(OUT/'shell-init.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));sys.exit(0 if result['status']=='pass' else 1)
