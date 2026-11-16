#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise host quick-start entry points against actual and simulated daemons."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/host-compatibility'
SCRIPTS=['scripts/lib/native_arch.sh','scripts/build_session.sh','scripts/start_session.sh','scripts/attach_session.sh',
         'scripts/session_control.sh','scripts/wait_session.sh','scripts/analyze_session.sh']

def run(args, *, cwd=ROOT, env=None, timeout=60):
    return subprocess.run(args,cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)

def snapshot(folder):
    return {p.relative_to(folder).as_posix():p.read_bytes() for p in Path(folder).rglob('*') if p.is_file()}

def shell_check(bash):
    version=run([bash,'--version']).stdout.splitlines()[0]
    if 'version 3.2.' not in version: return False,{'version':version,'reason':'genuine Bash 3.2 required'}
    checks={p:run([bash,'-n',p]).returncode for p in SCRIPTS}
    banned=re.compile(r'\b(?:mapfile|readarray)\b|declare\s+-A|\$\{[^}]*,,|\$\{[^}]*\^\^|\bsed\s+-i\b|\btimeout\b')
    found={p:[n for n,line in enumerate((ROOT/p).read_text().splitlines(),1) if banned.search(line)] for p in SCRIPTS}
    return all(x==0 for x in checks.values()) and not any(found.values()),{'version':version,'parse_codes':checks,'nonportable_lines':found}

def layout_check(python39):
    version=run([python39,'--version']).stdout.strip()
    if not version.startswith('Python 3.9.'): return False,{'version':version,'reason':'genuine Python 3.9 required'}
    with tempfile.TemporaryDirectory(prefix='host-layout-') as d:
        paths=[Path(d)/'py39',Path(d)/'current']; codes=[]
        for exe,path in zip([python39,sys.executable],paths):
            p=run([exe,'-m','integration.layout','--layout','configs/layouts/default.json','--directory',str(path),'--analysis-geometry'])
            codes.append(p.returncode)
            if p.returncode:return False,{'version':version,'exit_codes':codes,'error':p.stderr[-2000:]}
        a,b=map(snapshot,paths)
        diffs=[k for k in sorted(set(a)|set(b)) if a.get(k)!=b.get(k)]
        return not diffs,{'version':version,'current':sys.version.split()[0],'exit_codes':codes,'files':len(a),'differences':diffs}

STUB='''#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
if [ "$1" = info ]; then
  if [ "${FAKE_UNAVAILABLE:-0}" = 1 ]; then echo unavailable >&2; exit 1; fi
  case "$*" in *OSType*) printf '%s\\n' "$FAKE_OS";; *Architecture*) printf '%s\\n' "$FAKE_ARCH";; esac
elif [ "$1" = image ] && [ "$2" = inspect ]; then
  printf 'linux/%s\\n' "$FAKE_ARCH"
elif [ "$1" = create ]; then printf 'fake-container\\n'
fi
'''

def fake_environment(tmp, *, daemon_os='linux', arch='arm64', unavailable=False):
    tmp=Path(tmp); bindir=tmp/'bin';bindir.mkdir(exist_ok=True)
    for name,body in [('docker',STUB),('uname','#!/bin/sh\nprintf "Darwin\\n"\n')]:
        path=bindir/name;path.write_text(body);path.chmod(0o755)
    log=tmp/'calls.log';log.write_text('')
    env=dict(os.environ,PATH=str(bindir)+os.pathsep+os.environ['PATH'],STUB_LOG=str(log),FAKE_OS=daemon_os,FAKE_ARCH=arch,FAKE_UNAVAILABLE='1' if unavailable else '0')
    return env,log

def daemon_rejection(bash):
    results=[]
    for osname,arch,unavailable in [('windows','arm64',False),('linux','riscv64',False),('linux','arm64',True)]:
        with tempfile.TemporaryDirectory(prefix='bad-daemon-') as tmp:
            env,log=fake_environment(tmp,daemon_os=osname,arch=arch,unavailable=unavailable)
            p=run([bash,'scripts/build_session.sh'],env=env)
            calls=log.read_text().splitlines()
            results.append({'daemon_os':osname,'architecture':arch,'unavailable':unavailable,'exit_code':p.returncode,'error':p.stderr.strip(),'build_called':any(x.startswith('build ') for x in calls)})
    return all(x['exit_code']!=0 and x['error'] and not x['build_called'] for x in results),results

def daemon_darwin(bash, python39):
    observations=[]
    for arch in ('arm64','amd64'):
        observations.append(daemon_darwin_arch(bash,python39,arch))
    return all(item[0] for item in observations),[item[1] for item in observations]

def daemon_darwin_arch(bash, python39, arch):
    with tempfile.TemporaryDirectory(prefix='darwin-daemon-') as tmp:
        env,log=fake_environment(tmp,arch=arch);layout=Path(tmp)/'layout';layout.mkdir()
        commands=[['scripts/build_session.sh'],['scripts/start_session.sh','--layout',str(layout),'--name','test'],
                  ['scripts/session_control.sh','test','on'],['scripts/wait_session.sh','test','ready'],
                  ['scripts/session_control.sh','test','status'],['scripts/session_control.sh','test','stop'],
                  ['scripts/wait_session.sh','test','closed'],['scripts/analyze_session.sh','test']]
        results=[]
        for c in commands:
            p=run([bash]+c,env=env);results.append({'script':c[0],'exit_code':p.returncode,'error':p.stderr[-500:]})
        p=run([python39,'-m','integration.layout','--layout','configs/layouts/default.json',
               '--directory',str(Path(tmp)/'generated'),'--analysis-geometry'],env=env)
        results.append({'script':'integration.layout','exit_code':p.returncode,'error':p.stderr[-500:]})
        for c in [['docker','cp','test:/tmp/session-run',str(Path(tmp)/'output')],['docker','rm','-f','test']]:
            p=run(c,env=env);results.append({'script':' '.join(c[:2]),'exit_code':p.returncode,'error':p.stderr[-500:]})
        calls=log.read_text().splitlines()
        expected_build = 'build --platform linux/' + env['FAKE_ARCH'] + ' '
        good=all(x['exit_code']==0 for x in results) and any(x.startswith(expected_build) for x in calls) and any(x.startswith('create ') for x in calls) and sum(x.startswith('exec ') for x in calls)>=6
        return good,{'simulated_uname':'Darwin','daemon':'linux/'+arch,'commands':results,'docker_calls':calls}

def write_result(name,ok,detail):
    OUT.mkdir(parents=True,exist_ok=True)
    rev=run(['git','rev-parse','HEAD']).stdout.strip() or (ROOT/'SOURCE_REVISION').read_text().strip()
    image=run(['docker','image','inspect','local/public-stack:native','--format','{{.Id}}']).stdout.strip()
    row={'id':name,'status':'pass' if ok else 'fail','input_conditions':{'architecture':platform.machine()},'measurement':detail,'criterion':'documented end-to-end acceptance criteria','repository_commit':rev,'image_id':image}
    (OUT/(name+'.json')).write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n')
    print(name,row['status'],flush=True)
    return ok

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bash32',required=True);ap.add_argument('--python39',required=True);args=ap.parse_args()
    outcomes=[]
    for name,fn in [('P-E1',lambda:shell_check(args.bash32)),('P-E2',lambda:layout_check(args.python39)),('P-E3',lambda:daemon_rejection(args.bash32)),('P-E4',lambda:daemon_darwin(args.bash32,args.python39))]:
        try:ok,details=fn()
        except Exception as e:ok,details=False,{'error':str(e)}
        outcomes.append(write_result(name,ok,details))
    return 0 if all(outcomes) else 1

if __name__=='__main__':sys.exit(main())
