#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise the public launcher with deliberately delayed RunControl startup."""
import json, os, platform, subprocess, sys, tempfile, time, uuid
from pathlib import Path

ROOT=Path.cwd(); OUT=ROOT/'artifacts/timing-observations'; OUT.mkdir(parents=True,exist_ok=True)
IMAGE='local/runcontrol:native'; name='launcher-ready-'+uuid.uuid4().hex[:12]
commit=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
def command(*args,check=True):
    result=subprocess.run([str(x) for x in args],text=True,capture_output=True,timeout=180)
    if check and result.returncode:raise RuntimeError(str(args[0])+': '+(result.stdout+result.stderr)[-3000:])
    return result
row={'id':'launcher-readiness','status':'fail','repository_commit':commit,'image_id':None,
     'input_conditions':{'architecture':platform.machine(),'cpu_limit':1,'rc_address':44007,'induced_rc_start_delay_s':3},
     'criterion':'delayed real RunControl starts before clients with port and tcp://port addresses, H-E12–H-E14 pass, and an occupied port is rejected'}
try:
    row['image_id']=command('docker','image','inspect',IMAGE,'--format','{{.Id}}').stdout.strip()
    command('docker','create','--cpus','1','--name',name,'--network','none',IMAGE,'sleep','300')
    quota=int(command('docker','inspect','--format','{{.HostConfig.NanoCpus}}',name).stdout.strip())
    with tempfile.TemporaryDirectory(prefix='launcher-ready-') as tmp:
        tmp=Path(tmp); fixture=(ROOT/'tests/e2e/runcontrol_launcher.py').read_text()
        old="PYTHONPATH='/tmp/runcontrol_observer:'";new="PYTHONPATH='/tmp/launcher_delay_observer:'"
        if fixture.count(old)!=1:raise RuntimeError('unexpected existing fixture observer setup')
        (tmp/'launcher.py').write_text(fixture.replace(old,new))
        delay=tmp/'launcher_delay_observer';delay.mkdir()
        (delay/'sitecustomize.py').write_text('''import json, os, sys, time\nif os.path.basename(sys.argv[0]) == "ITS3RunControl.py":\n    start=time.monotonic_ns()\n    time.sleep(3)\n    with open("/tmp/rc-start-delay.json", "w") as stream: json.dump({"start_ns":start,"end_ns":time.monotonic_ns()},stream)\nwith open("/tmp/runcontrol_observer/sitecustomize.py") as stream:\n    exec(compile(stream.read(),stream.name,"exec"))\n''')
        command('docker','cp',tmp/'launcher.py',name+':/tmp/launcher.py')
        command('docker','cp',delay,name+':/tmp/launcher_delay_observer')
        command('docker','cp',ROOT/'tests/e2e/runcontrol_observer',name+':/tmp/runcontrol_observer')
        command('docker','start',name)
        command('docker','exec',name,'mkdir','-p','/tmp/runcontrol-results')
        run=command('docker','exec',name,'python','/tmp/launcher.py',check=False)
        checks={}
        for number in (12,13,14):
            result=command('docker','exec',name,'cat',f'/tmp/runcontrol-results/H-E{number}.json',check=False)
            try:checks[f'H-E{number}']=json.loads(result.stdout).get('status','not_run')
            except ValueError:checks[f'H-E{number}']='not_run'
        trace=json.loads(command('docker','exec',name,'cat','/tmp/rc-start-delay.json').stdout)
        delayed=trace['end_ns']-trace['start_ns']>=3_000_000_000
        # The public RunControl also accepts the tcp://port address form.
        if fixture.count("'--rc-address','44007',") != 1:
            raise RuntimeError('unexpected existing launcher fixture address')
        alt=fixture.replace(old,new).replace("'--rc-address','44007',",'')
        alt=alt.replace("f'--one-pass --status-file", "f'--address tcp://44000 --one-pass --status-file",1)
        (tmp/'launcher-uri.py').write_text(alt)
        command('docker','cp',tmp/'launcher-uri.py',name+':/tmp/launcher-uri.py')
        command('docker','exec',name,'rm','-rf','/tmp/launcher-e2e','/tmp/runcontrol-results')
        command('docker','exec',name,'mkdir','-p','/tmp/runcontrol-results')
        uri_run=command('docker','exec',name,'python','/tmp/launcher-uri.py',check=False)
        uri_checks={}
        for number in (12,13,14):
            result=command('docker','exec',name,'cat',f'/tmp/runcontrol-results/H-E{number}.json',check=False)
            try:uri_checks[f'H-E{number}']=json.loads(result.stdout).get('status','not_run')
            except ValueError:uri_checks[f'H-E{number}']='not_run'
        # A different process already owns the requested port: do not mistake it
        # for this launcher's RunControl or start clients against it.
        command('docker','exec',name,'rm','-rf','/tmp/launcher-e2e','/tmp/runcontrol-results')
        command('docker','exec',name,'mkdir','-p','/tmp/runcontrol-results')
        binder='import socket,time; s=socket.socket(); s.bind(("127.0.0.1",44007)); s.listen(); open("/tmp/occupied-ready","w").close(); time.sleep(120)'
        command('docker','exec','-d',name,'python','-c',binder)
        deadline=time.monotonic()+15
        while command('docker','exec',name,'test','-f','/tmp/occupied-ready',check=False).returncode:
            if time.monotonic()>=deadline:raise RuntimeError('occupied-port fixture did not start')
            time.sleep(.05)
        occupied=command('docker','exec',name,'python','/tmp/launcher.py',check=False)
        occupied_log=command('docker','exec',name,'cat','/tmp/launcher-e2e/launcher.log',check=False).stdout
        conflict_detected='RunControl TCP port 44007 is already in use' in occupied_log
        row['measurement']={'exit_code':run.returncode,'checks':checks,'quota_nano_cpus':quota,'delay_monotonic_ns':trace,'uri_exit_code':uri_run.returncode,'uri_checks':uri_checks,'occupied_port_rejected':conflict_detected,'occupied_launcher_log':occupied_log[-2000:],'stdout':run.stdout[-4000:],'stderr':run.stderr[-4000:]}
        if run.returncode==0 and uri_run.returncode==0 and delayed and quota==1_000_000_000 and conflict_detected and all(x=='pass' for x in checks.values()) and all(x=='pass' for x in uri_checks.values()):row['status']='pass'
except Exception as exc:row['reason']=str(exc)
finally:command('docker','rm','-f',name,check=False)
(OUT/'launcher-readiness.json').write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(row,ensure_ascii=False));sys.exit(0 if row['status']=='pass' else 1)
