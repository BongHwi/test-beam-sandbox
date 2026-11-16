#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Container-side public-stack smoke checks; acquisition is delegated to the test-only controller."""
import ast, importlib, json, os, pathlib, shutil, subprocess, sys, time
P = pathlib.Path
out = {'corryvreckan_source': 'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe', 'architecture': os.uname().machine,
       'source_commit': subprocess.check_output(['git','-C','/opt/eudaq-src','rev-parse','HEAD'], text=True).strip(),
       'repository_commit': os.environ.get('TEST_COMMIT'), 'image_id': os.environ.get('IMAGE_ID'), 'checks': {}}
c = out['checks']
def item(name, ok, measurement, criterion):
    c[name] = {'status': 'pass' if ok else 'fail', 'measurement': measurement, 'criterion': criterion}
try:
    import pyeudaq
    loader=P('/opt/corry/lib/libCorryvreckanModuleEventLoaderEUDAQ2.so')
    ldd=subprocess.run(['ldd',str(loader)],capture_output=True,text=True)
    m={'pyeudaq':pyeudaq.__file__, 'euCliProducer':shutil.which('euCliProducer'), 'corry':shutil.which('corry'),
       'loader':str(loader) if loader.exists() else None, 'ldd':ldd.stdout,
       'its3_module':str(P('/opt/eudaq/lib/libeudaq_module_ITS3.so'))}
    ok=(all((m['euCliProducer'],m['corry'],m['loader'],P(m['its3_module']).exists())) and
        '/opt/eudaq/lib/libeudaq_core' in ldd.stdout and out['source_commit']=='bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e' and out['architecture']==os.environ.get('EXPECTED_MACHINE',os.uname().machine))
    item('a',ok,m,'imports and executable/module files exist, source commit and EUDAQ link match')
except Exception as e: item('a',False,str(e),'imports and executables available')
try:
    run=subprocess.run([sys.executable,'/tmp/public_stack_acquire.py'],capture_output=True,text=True,timeout=100)
    acq=json.loads(P('/tmp/acquire-result.json').read_text())
    acq['process_exit_code']=run.returncode
    item('b',run.returncode==0 and acq.get('status')=='pass',acq,'closed native raw has exactly the producer trigger sequence')
except Exception as e: item('b',False,str(e),'public acquisition and native FileReader succeed')
paths=[P('/opt/eudaq-src/user/ITS3/python') / x for x in ('ITS3RunControl.py','ITS3DataCollector.py','ALPIDEProducer.py')]
paths.append(P('/opt/eudaq-src/user/ITS3/misc/ITS3start.py'))
try:
    imports=set()
    for path in paths:
        tree=ast.parse(path.read_text())
        for n in ast.walk(tree):
            if isinstance(n,ast.Import): imports.update(x.name.split('.')[0] for x in n.names)
            elif isinstance(n,ast.ImportFrom) and n.module and n.level==0: imports.add(n.module.split('.')[0])
    results={}
    for name in sorted(imports):
        r=subprocess.run([sys.executable,'-c','import importlib,sys; importlib.import_module(sys.argv[1])',name],capture_output=True,text=True,timeout=10)
        results[name]={'status':'pass' if r.returncode==0 else 'fail','reason':r.stderr[-500:] if r.returncode else ''}
    item('c',all(x.exists() for x in paths),{'files':[str(x) for x in paths], 'imports':results},'all four public files exist; individual module import failures are listed')
except Exception as e: item('c',False,str(e),'four files and import diagnostics')
try:
    p=paths[2]; root=ast.parse(p.read_text()); expected={}
    def attribute_path(n):
        if isinstance(n,ast.Name): return n.id
        if isinstance(n,ast.Attribute):
            parent=attribute_path(n.value); return parent+'.'+n.attr if parent else None
        if isinstance(n,ast.Subscript):
            parent=attribute_path(n.value); return parent+'['+ast.unparse(n.slice)+']' if parent else None
        return None
    for n in ast.walk(root):
        if isinstance(n,ast.Attribute):
            value=attribute_path(n)
            if value and value.startswith('self.daq.'):
                expected.setdefault(value[len('self.daq.'):],set()).add(n.lineno)
    doc=P('/tmp/alpide-board-surface.md').read_text()
    missing=[k for k,v in expected.items() if f'`{k}` | '+', '.join(map(str,sorted(v))) not in doc]
    item('d',bool(expected) and not missing,{'attributes':len(expected),'missing_from_doc':missing},'all static board attribute paths and line numbers documented')
except Exception as e: item('d',False,str(e),'board API paths documented')
try:
    g=P('/opt/eudaq-src/user/ITS3/module/src/ITS3global2StdEventConverter.cc').read_text()
    a=P('/opt/eudaq-src/user/ITS3/module/src/ALPIDERawEvent2StdEventConverter.cc').read_text()
    p=paths[2].read_text(); doc=P('/tmp/public-stack.md').read_text()
    conditions={'subevents': all(x in g for x in ('GetSubEvents()', 'GetDeviceN()', 'StdEventConverter::Convert', 'AddPlane')),
                'plane': 'StandardPlane plane(rawev->GetDeviceN(),"ITS3DAQ","ALPIDE")' in a,
                'status_bore_eore': all(x in p for x in ("self.name+'_status'", 'ev.SetBORE()', 'ev.SetEORE()')),
                'documented_commit':out['source_commit'] in doc}
    item('e',all(conditions.values()),conditions,'source-backed subevent dispatch, plane and run boundary conventions documented')
except Exception as e: item('e',False,str(e),'source-backed conventions documented')
if os.environ.get('INJECT_FAILURE')=='1': c['injected']={'status':'fail','measurement':'deliberate failure','criterion':'script exits nonzero'}
out['status']='pass' if all(v['status']=='pass' for v in c.values()) else 'fail'
P('/tmp/public_stack_smoke.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'status':out['status'],'checks':{k:v['status'] for k,v in c.items()}},ensure_ascii=False))
sys.exit(0 if out['status']=='pass' else 1)
