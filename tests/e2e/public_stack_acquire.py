#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only RunControl: exercise the public example producer and collector."""
import json, os, pathlib, subprocess, sys, threading, time, traceback
import pyeudaq

work = pathlib.Path('/tmp/public-stack-e2e')
work.mkdir(exist_ok=True)
os.chdir(work)
if len(sys.argv) > 1 and sys.argv[1] == 'producer':
    sys.path.insert(0, '/opt/eudaq-src/user/example/python')
    from ExamplePyProducer import ExamplePyProducer
    class CountedExample(ExamplePyProducer):
        def SendEvent(self, event):
            super().SendEvent(event)
            with open('sent.txt', 'a', encoding='utf-8') as f:
                f.write(str(event.GetTriggerN()) + '\n')
                f.flush()
            if event.GetTriggerN() >= 5: self.is_running = 0
    p = CountedExample('myproducer', 'tcp://127.0.0.1:44000')
    p.Connect()
    while p.IsConnected(): time.sleep(.1)
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == 'collector':
    sys.path.insert(0, '/opt/eudaq-src/user/example/python')
    from ExamplePyDataCollector import ExamplePyDataCollector
    d = ExamplePyDataCollector('mydc', 'tcp://127.0.0.1:44000')
    d.Connect()
    while d.IsConnected(): time.sleep(.1)
    sys.exit(0)

class TestRunControl(pyeudaq.RunControl):
    def __init__(self): super().__init__('44000'); self.collected=0
    def DoStatus(self, connection, status):
        if connection.GetType()=='DataCollector':
            self.collected=max(self.collected,int(status.GetTag('EventN','0')))

result = {'status':'fail', 'sent':None, 'read':None}
processes=[]
try:
    pathlib.Path('init.ini').write_text('[Producer.myproducer]\n[DataCollector.mydc]\n')
    pathlib.Path('run.conf').write_text('[RunControl]\nEUDAQ_CTRL_PRODUCER_FIRST_STOP = myproducer\n[Producer.myproducer]\nEUDAQ_DC = mydc\n[DataCollector.mydc]\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = run$3R$X\nDISABLE_PRINT = 1\n')
    rc = TestRunControl()
    threading.Thread(target=rc.Exec, daemon=True).start()
    time.sleep(1)
    for role in ('collector', 'producer'):
        log=open(role+'.log', 'w')
        command = ['euCliCollector', '-n', 'DirectSaveDataCollector', '-t', 'mydc', '-r', 'tcp://127.0.0.1:44000'] if role == 'collector' else [sys.executable, '-u', __file__, role]
        processes.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT))
    time.sleep(3)
    if any(p.poll() is not None for p in processes): raise RuntimeError('public component exited before run')
    rc.ReadInitialiseFile('init.ini'); rc.Initialise(); time.sleep(2)
    rc.ReadConfigureFile('run.conf'); rc.Configure(); time.sleep(2)
    rc.StartRun(); deadline=time.monotonic()+18
    while time.monotonic()<deadline:
        count=len(pathlib.Path('sent.txt').read_text().splitlines()) if pathlib.Path('sent.txt').exists() else 0
        if count==6 and rc.collected>=5: break
        time.sleep(.1)
    if count!=6: raise RuntimeError(f'producer did not send six data events: sent={count}')
    rc.StopRun(); time.sleep(3)
    rc.Terminate()
    for p in processes:
        try: p.wait(timeout=8)
        except subprocess.TimeoutExpired: p.terminate(); p.wait(timeout=5)
    files=list(work.glob('*.raw'))
    if len(files) != 1: raise RuntimeError(f'expected one closed raw file, got {files}')
    sent=[int(x) for x in pathlib.Path('sent.txt').read_text().splitlines()]
    reader=pyeudaq.FileReader('native', str(files[0]))
    read=[]
    while True:
        event=reader.GetNextEvent()
        if event is None: break
        subs=event.GetSubEvents()
        if subs:
            for sub in subs: read.append(sub.GetTriggerN())
        elif event.GetDescription()=='sub_name': read.append(event.GetTriggerN())
    result.update(sent=sent, read=read, raw_bytes=files[0].stat().st_size,
                  criterion='nonempty sent trigger sequence equals sequence read from closed native raw',
                  status='pass' if sent and sent == read else 'fail')
except Exception as e:
    result.update(error=str(e), traceback=traceback.format_exc(), logs={name: pathlib.Path(name+'.log').read_text(errors='replace')[-12000:] if pathlib.Path(name+'.log').exists() else '' for name in ('producer', 'collector')}, directory=[str(x) for x in work.iterdir()])
finally:
    for p in processes:
        if p.poll() is None: p.terminate()
    for p in processes:
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill()
    pathlib.Path('/tmp/acquire-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
os._exit(0 if result['status']=='pass' else 1)
