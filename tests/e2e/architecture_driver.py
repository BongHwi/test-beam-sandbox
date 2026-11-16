# SPDX-License-Identifier: LGPL-3.0-or-later
"""Verify native build, existing E2E, and active architecture references."""
import json, os, platform, re, shutil, subprocess, sys, urllib.request
from pathlib import Path
ROOT=Path.cwd();OUT=ROOT/'artifacts/architecture';OUT.mkdir(parents=True,exist_ok=True)
COMMIT=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
ARCH={'aarch64':'arm64','x86_64':'amd64'}.get(platform.machine())
EXPECTED='linux/'+str(ARCH)

def command(args,timeout=3600):
 try:return subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 except subprocess.TimeoutExpired as e:
  class Timeout: returncode=None;stdout='';stderr=str(e)
  return Timeout()

def image_info(name):
 p=command(['docker','image','inspect',name,'--format','{{.Id}} {{.Os}}/{{.Architecture}}'])
 if p.returncode:return {'error':p.stderr.strip()}
 parts=p.stdout.strip().split();return {'id':parts[0],'platform':parts[1]}

def result(name,status,measurement,criterion,reason=None):
 data={'id':name,'status':status,'input_conditions':{'host':platform.machine(),'expected_platform':EXPECTED},
       'measurement':measurement,'criterion':criterion,'repository_commit':COMMIT,
       'image_id':image_info('local/public-session:native').get('id'),
       'amd64_verification':{'status':'not_run','reason':'amd64 is not built or run on this ARM64 host'} if ARCH=='arm64' else {'status':'not_run','reason':'this execution verifies only the current native architecture'}}
 if reason:data['reason']=reason
 (OUT/(name+'.json')).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
 print(name,status,flush=True)
 return status

def registry():
 entries=[]
 for tag in ('6.30.02-conda','6.26.10-conda'):
  url='https://hub.docker.com/v2/repositories/rootproject/root/tags/'+tag
  with urllib.request.urlopen(url,timeout=30) as stream:entry=json.load(stream)
  entries.append({'url':url,'tag':tag,'images':[{'os':x.get('os'),'architecture':x.get('architecture'),'digest':x.get('digest')} for x in entry.get('images',[])]})
 return entries

def audit():
 paths=[ROOT/'README.md']+list((ROOT/'scripts').rglob('*.sh'))+list((ROOT/'tests/scripts').glob('*.sh'))+list((ROOT/'tests/e2e').rglob('*.py'))+list((ROOT/'tests/e2e').rglob('*.sh'))+list((ROOT/'docker').rglob('Dockerfile'))+list((ROOT/'docs').glob('*.md'))
 arch_group='(?:'+'|'.join(('arm64','amd64'))+')'
 pattern=re.compile(':'+arch_group+r'\b|--platform(?:=|\s+)linux/'+arch_group+r'\b')
 violations=[]
 for path in paths:
  for i,line in enumerate(path.read_text(errors='replace').splitlines(),1):
   if pattern.search(line):violations.append({'path':str(path.relative_to(ROOT)),'line':i,'text':line.strip()})
 locks=(ROOT/'docker/architectures.lock').read_text();version_lock=(ROOT/'docker/versions.lock').read_text()
 registry_data=registry()
 want={}
 for arch in ('arm64','amd64'):
  match=re.search(r'(?m)^ROOT_IMAGE_'+arch+r"='[^']*@(sha256:[0-9a-f]{64})'",locks)
  if not match:raise ValueError('missing native image lock: '+arch)
  want[arch]=match.group(1)
 public={arch:any(image['architecture']==arch and image['os']=='linux' and image['digest']==digest for r in registry_data for image in r['images']) for arch,digest in want.items()}
 lock_matches=all(value in locks and value in version_lock for value in want.values())
 explicit=[line for line in (ROOT/'docker/base/conda-amd64.explicit').read_text().splitlines() if line and not line.startswith('#') and line!='@EXPLICIT']
 hashes=[line.split(None,1) for line in (ROOT/'docker/base/conda-amd64.sha256').read_text().splitlines() if line]
 explicit_names={line.split('#',1)[0].rsplit('/',1)[-1] for line in explicit}
 hash_names={line[1].strip() for line in hashes if len(line)==2 and re.fullmatch(r'[0-9a-f]{64}',line[0])}
 package_locks_ok=(len(explicit)==len(hashes)==279 and len(explicit_names)==279 and explicit_names==hash_names and
                   all(line.startswith('https://conda.anaconda.org/conda-forge/') and re.search(r'#[0-9a-f]{32}$',line) for line in explicit))
 data={'active_files_scanned':len(paths),'violations':violations,'public_registry_manifests':registry_data,
       'amd64_package_locks_valid':package_locks_ok,'amd64_package_count':len(explicit),
       'platform_digests_confirmed':public,'digests_in_both_locks':lock_matches,
       'exclusions':'generated results and the unchanged tracksim source are excluded from the active-file audit',
       'amd64_build':'not_run'}
 return not violations and all(public.values()) and lock_matches and package_locks_ok,data

def main():
 if ARCH not in ('arm64','amd64'):raise SystemExit('unsupported native architecture')
 checks={}
 build=command(['scripts/build_session.sh']);(OUT/'native-build.log').write_text(build.stdout+build.stderr)
 base=image_info('local/public-session-base:native');session=image_info('local/public-session:native')
 ok=build.returncode==0 and base.get('platform')==EXPECTED and session.get('platform')==EXPECTED
 checks['A-E1']=result('A-E1','pass' if ok else 'fail',{'exit_code':build.returncode,'base':base,'session':session},'build exit 0; both images match native architecture')
 try:
  ok,detail=audit();checks['A-E3']=result('A-E3','pass' if ok else 'fail',detail,'no active architecture-pinned tag/platform; public registry manifests match locks')
 except Exception as e:checks['A-E3']=result('A-E3','fail',{},'registry and active source audit',str(e))
 runs=[]
 for script in ('tests/scripts/smoke_public_stack.sh','tests/scripts/run_setup_e2e.sh',
                'tests/e2e/public_stack_user_module.sh','tests/scripts/run_session_example_e2e.sh'):
  print('A-E2 START',script,flush=True)
  p=command([script],timeout=5400)
  (OUT/('regression-'+Path(script).name+'.log')).write_text(p.stdout+p.stderr)
  runs.append({'script':script,'exit_code':p.returncode})
  print('A-E2 END',script,p.returncode,flush=True)
 nested={}
 for directory,names in [('setup',[f'U-E{i}' for i in range(1,6)]),('layouts',[f'L-E{i}' for i in range(1,9)])]:
  for name in names:
   p=ROOT/'artifacts'/directory/(name+'.json')
   nested[name]=json.loads(p.read_text()).get('status') if p.exists() else 'missing'
 ok=all(r['exit_code']==0 for r in runs) and all(x=='pass' for x in nested.values())
 checks['A-E2']=result('A-E2','pass' if ok else 'fail',{'scripts':runs,'nested_checks':nested,'included_checks':'U-E5 / L-E8 include the existing end-to-end checks'},'existing in-scope E2Es exit 0 and mandatory checks pass')
 (OUT/'summary.json').write_text(json.dumps({'status':'pass' if all(v=='pass' for v in checks.values()) else 'fail','checks':checks,'native_architecture':ARCH,'amd64':'not_run' if ARCH=='arm64' else 'native run'},indent=2)+'\n')
 return 0 if all(v=='pass' for v in checks.values()) else 1
if __name__=='__main__':sys.exit(main())
