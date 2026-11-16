#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the documented acquisition and the existing native regression."""
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/host-compatibility';OUT.mkdir(parents=True,exist_ok=True)
if (ROOT/'session-output').exists(): raise SystemExit('session-output already exists; use a clean checkout')
text=(ROOT/'README.md').read_text()
blocks=re.findall(r'```sh\n(.*?)```',text,re.S)
if len(blocks)!=1:raise SystemExit('README command block missing or ambiguous')
commands=OUT/'readme-commands.sh';commands.write_text('set -euo pipefail\n'+blocks[0])
readme=subprocess.run(['bash',str(commands)],cwd=ROOT,text=True,capture_output=True,timeout=3600)
(OUT/'readme.log').write_text(readme.stdout+readme.stderr)
files={'raw':bool(list((ROOT/'session-output').glob('*.raw'))),
       'analysis':(ROOT/'session-output/analysis/output/events.root').is_file()}
if (ROOT/'session-output').exists(): shutil.rmtree(ROOT/'session-output')
regression = None
if readme.returncode == 0 and all(files.values()):
    with (OUT/'regression.log').open('w') as log:
        regression=subprocess.run(['tests/scripts/run_arch_e2e.sh'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=10800)
rev=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True).stdout.strip()
if not rev: rev=(ROOT/'SOURCE_REVISION').read_text().strip()
image=subprocess.run(['docker','image','inspect','local/public-session:native','--format','{{.Id}}'],capture_output=True,text=True).stdout.strip()
checks={}
for name in ['A-E1','A-E2','A-E3']:
    path=ROOT/'artifacts/architecture'/f'{name}.json'
    checks[name]=json.loads(path.read_text()).get('status') if path.exists() else 'not_run'
ok=readme.returncode==0 and all(files.values()) and regression is not None and regression.returncode==0 and all(v=='pass' for v in checks.values())
result={'id':'P-E5','status':'pass' if ok else 'fail','input_conditions':{'architecture':platform.machine()},
        'measurement':{'readme_exit':readme.returncode,'readme_outputs':files,'regression_exit':regression.returncode if regression else None,'architecture_checks':checks},
        'criterion':'documented end-to-end acceptance criteria','repository_commit':rev,'image_id':image}
(OUT/'P-E5.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print('P-E5',result['status'],flush=True)
sys.exit(0 if ok else 1)
