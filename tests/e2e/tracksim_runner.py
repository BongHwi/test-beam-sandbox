#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Execute tracksim checks without modifying or installing tracksim."""
import concurrent.futures,datetime,hashlib,json,os,pathlib,platform,shutil,subprocess,sys,tempfile,traceback
ROOT=pathlib.Path(__file__).resolve().parents[2]; OUT=ROOT/'artifacts/tracksim'; OUT.mkdir(parents=True,exist_ok=True)
COMMIT=(ROOT/'SOURCE_REVISION').read_text().strip()
BASE='local/public-stack:native'
def call(args,*,cwd=ROOT,env=None,timeout=7200):
    try:
        p=subprocess.run([str(x) for x in args],cwd=cwd,env=env,text=True,capture_output=True,timeout=timeout)
        return {'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    except (OSError,subprocess.TimeoutExpired) as e: return {'exit_code':None,'error':str(e),'stdout':'','stderr':''}
def image_info():
    p=call(['docker','image','inspect',BASE]);return json.loads(p['stdout'])[0] if p['exit_code']==0 else {}
image=image_info();image_id=image.get('Id')
def emit(key,status,measurement,criterion,input_conditions):
    obj={'id':key,'status':status,'input_conditions':input_conditions,'measurement':measurement,'criterion':criterion,'repository_commit':COMMIT,'base_image_id':image_id,'architecture':platform.machine(),'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (OUT/(key+'.json')).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def check_manifest():
    manifest=ROOT/'configs/tracksim-files.sha256'; expected={}
    for line in manifest.read_text().splitlines():
        digest,path=line.split('  ',1)
        if path.startswith('./tracksim/'):expected[path[2:]]=digest
    actual={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tracksim').rglob('*') if p.is_file()}
    bad={k:{'expected':expected.get(k),'actual':actual.get(k)} for k in sorted(set(expected)|set(actual)) if expected.get(k)!=actual.get(k)}
    emit('K-E5','pass' if not bad and expected else 'fail',{'expected_files':len(expected),'actual_files':len(actual),'mismatches':bad},'identical file list and SHA-256',{'manifest':'configs/tracksim-files.sha256'})
def suite_result(kind,p,evidence):
    report_path=evidence/'e2e-report.json'; report=json.loads(report_path.read_text()) if report_path.exists() else None
    checks={c:x for script in (report or {}).get('scripts',[]) for c,x in script.get('checks',{}).items()}
    skips={k:v.get('reason') for k,v in checks.items() if v.get('status')=='skip'}
    failures=[k for k,v in checks.items() if v.get('status')=='fail']; unexpected={k:v for k,v in skips.items() if k not in ('D3','D4','O4')}
    ok=p['exit_code']==0 and bool(report) and report.get('passed') and not failures and not unexpected
    measurement={'exit_code':p['exit_code'],'check_count':len(checks),'failed':failures,'skipped':skips,'unexpected_skips':unexpected,'suite_failures':(report or {}).get('failures'),'identity':(report or {}).get('python'),'stdout_tail':p.get('stdout','')[-2500:],'stderr_tail':p.get('stderr','')[-2500:],'error':p.get('error')}
    emit('K-E1' if kind=='host' else 'K-E2','pass' if ok else 'fail' if p['exit_code'] is not None else 'not_run',measurement,'exit 0, all checks pass except D3/D4/O4 allowed to skip',{'interpreter':kind,'evidence':str(evidence.relative_to(ROOT)),'container_network':'none' if kind=='base' else None})
def host_suite():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'); evidence=OUT/'host';evidence.mkdir(exist_ok=True)
    p=call([sys.executable,'tracksim/tests/e2e/run_all.py','--evidence',evidence],env=env);suite_result('host',p,evidence)
def base_suite():
    evidence=OUT/'base';evidence.mkdir(exist_ok=True);name='tracksim-check-'+str(os.getpid())
    create=call(['docker','create','--name',name,'--network','none',BASE,'sleep','7200'])
    if create['exit_code']!=0: suite_result('base',create,evidence);return
    try:
        cp=call(['docker','cp',str(ROOT/'tracksim'),name+':/tmp/tracksim'])
        if cp['exit_code']!=0: suite_result('base',cp,evidence);return
        start=call(['docker','start',name])
        if start['exit_code']!=0: suite_result('base',start,evidence);return
        p=call(['docker','exec','-w','/tmp','-e','PYTHONDONTWRITEBYTECODE=1',name,'python','tracksim/tests/e2e/run_all.py','--evidence','/tmp/tracksim-evidence'])
        copied=call(['docker','cp',name+':/tmp/tracksim-evidence/.',str(evidence)])
        if copied['exit_code']!=0: p['stderr']+='\nevidence copy failed: '+copied['stderr'];p['exit_code']=p['exit_code'] or 1
        suite_result('base',p,evidence)
    finally: call(['docker','rm','-f',name])
def examples():
    env=dict(os.environ,PYTHONPATH=str(ROOT/'tracksim/src'),PYTHONDONTWRITEBYTECODE='1')
    golden=json.loads((ROOT/'tracksim/tests/e2e/golden/examples.json').read_text())
    summaries={};hashes={};comparisons={};determinism={}
    with tempfile.TemporaryDirectory(prefix='tracksim-check-') as td:
        for name in ('three-plane','three-plane-dark'):
            config=ROOT/'tracksim/examples'/(name+'.json');prefix=[sys.executable,'-m','tracksim']
            pre=call(prefix+['preflight','--config',config],env=env)
            first=pathlib.Path(td)/(name+'-1');second=pathlib.Path(td)/(name+'-2')
            gen=call(prefix+['generate','--config',config,'--out',first],env=env)
            verify=call(prefix+['verify',first],env=env) if gen['exit_code']==0 else {'exit_code':None,'stdout':'','stderr':'generation failed'}
            gen2=call(prefix+['generate','--config',config,'--out',second],env=env)
            try: verify_status=json.loads(verify['stdout']).get('status')
            except (ValueError,AttributeError): verify_status=None
            summaries[name]={'preflight_exit':pre['exit_code'],'generate_exit':gen['exit_code'],'verify_exit':verify['exit_code'],'verify_status':verify_status,'preflight_stdout':pre['stdout'],'errors':[pre['stderr'][-1000:],gen['stderr'][-1000:],verify['stderr'][-1000:]],'second_generate_exit':gen2['exit_code']}
            files={str(f.relative_to(first)):hashlib.sha256(f.read_bytes()).hexdigest() for f in first.rglob('*') if f.is_file()} if first.exists() else {}
            hashes[name]=files; gold=golden['examples'][name]
            comparisons[name]={path:{'actual_sha256':files.get(path),'golden_sha256':gold.get(path),'comparison':'same' if files.get(path)==gold.get(path) else 'different' if path in files and path in gold else 'no_golden' if path in files else 'missing_output'} for path in sorted(set(files)|set(gold))}
            try:
                one=json.loads((first/'manifest.json').read_text());two=json.loads((second/'manifest.json').read_text());time_values=[one.pop('created_utc',None),two.pop('created_utc',None)]; determinism[name]={'equal_except_created_utc':one==two,'created_utc':time_values}
            except (OSError,ValueError) as e: determinism[name]={'equal_except_created_utc':False,'error':str(e)}
    ok=all(x['preflight_exit']==x['generate_exit']==x['verify_exit']==0 and x['verify_status']=='verified' for x in summaries.values())
    emit('K-E3','pass' if ok else 'fail',summaries,'preflight, generate and verify exit 0 with status verified',{'examples':list(summaries),'interpreter':sys.version})
    emit('K-E4','pass' if all(x['equal_except_created_utc'] for x in determinism.values()) and all(x['second_generate_exit']==0 for x in summaries.values()) else 'fail',determinism,'manifests match after removing created_utc',{'examples':list(summaries)})
    (OUT/'example-hashes.json').write_text(json.dumps({'identity':platform.platform(),'golden_identity':golden['python'],'examples':hashes,'golden_comparison':comparisons},indent=2,ensure_ascii=False)+'\n')
def main():
    # A failed run must never reuse an earlier successful evidence file.
    for n in range(1,6): (OUT/f'K-E{n}.json').unlink(missing_ok=True)
    for name in ('host','base'):
        if (OUT/name).exists(): shutil.rmtree(OUT/name)
    host={'version':platform.python_version(),'executable':sys.executable,'machine':platform.machine(),'system':platform.system(),'free_bytes':shutil.disk_usage(ROOT).free}
    bp=call(['docker','run','--rm','--network','none',BASE,'python','-c','import json,platform,sys; print(json.dumps({"version":platform.python_version(),"executable":sys.executable,"machine":platform.machine()}))'])
    base=json.loads(bp['stdout']) if bp['exit_code']==0 else {'error':bp['stderr']}
    (OUT/'preflight.json').write_text(json.dumps({'status':'pass' if sys.version_info>=(3,11) and tuple(map(int,base.get('version','0.0').split('.')[:2])) >= (3,11) and platform.machine() in ('aarch64','x86_64') and base.get('machine')==platform.machine() else 'fail','host':host,'base':base,'base_image_id':image_id,'other_architecture':{'status':'not_run','reason':'this execution tests the native architecture only'}},indent=2)+'\n')
    check_manifest()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        tasks=[pool.submit(host_suite),pool.submit(base_suite)]
        examples()
        for task in tasks:
            try: task.result()
            except Exception as e: print('suite exception:',repr(e),traceback.format_exc(),file=sys.stderr)
    check_manifest()  # Repeat after the suites to detect any package changes.
    statuses={}
    for k in range(1,6):
        p=OUT/f'K-E{k}.json';statuses[f'K-E{k}']=json.loads(p.read_text())['status'] if p.exists() else 'not_run'
    print(json.dumps(statuses,indent=2));return 0 if all(x=='pass' for x in statuses.values()) else 1
if __name__=='__main__':sys.exit(main())
