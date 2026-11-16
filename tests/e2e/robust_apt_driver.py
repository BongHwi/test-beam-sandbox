#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Verify a partial index failure through the actual RunControl Dockerfile."""
import json,platform,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'artifacts/robustness';OUT.mkdir(parents=True,exist_ok=True)
rev=(ROOT/'SOURCE_REVISION').read_text().strip()
image=subprocess.run(['docker','image','inspect','local/public-stack:native','--format','{{.Id}}'],text=True,capture_output=True).stdout.strip()
source=(ROOT/'docker/runcontrol/Dockerfile').read_text();first,rest=source.split('\n',1)
injection="RUN printf '%s\\n' 'deb [trusted=yes] http://127.0.0.1:9/failure-index focal main' > /etc/apt/sources.list.d/unavailable-index.list\n"
with tempfile.TemporaryDirectory(prefix='robust-apt-') as d:
 path=Path(d)/'Dockerfile';path.write_text(first+'\n'+injection+rest)
 p=subprocess.run(['docker','build','--no-cache','--platform','linux/'+{'aarch64':'arm64','x86_64':'amd64'}[platform.machine()],'--memory','8g','-f',str(path),str(ROOT)],text=True,capture_output=True,timeout=600)
 log=p.stdout+p.stderr
 failed_index='http://127.0.0.1:9/failure-index' in log and 'W: Failed to fetch' in log
 successful_index='Get:' in log or 'Hit:' in log
 stopped_update='Package index update was incomplete' in log and 'Unable to locate package' not in log and 'Version ' not in log
 ok=p.returncode!=0 and failed_index and successful_index and stopped_update
 result={'id':'G5','status':'pass' if ok else 'fail','input_conditions':{'architecture':platform.machine(),'injection':'unavailable index endpoint alongside normal public repositories'},'measurement':{'build_exit':p.returncode,'failed_index_visible':failed_index,'other_index_succeeded':successful_index,'stopped_at_update':stopped_update,'log_tail':log[-4500:]},'criterion':'documented end-to-end acceptance criteria','repository_commit':rev,'image_id':image}
 (OUT/'G5.json').write_text(json.dumps(result,indent=2)+'\n');print('G5',result['status']);sys.exit(0 if ok else 1)
