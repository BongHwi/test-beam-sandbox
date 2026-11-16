#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only RunControl for an isolated public msensor/EUDAQ acquisition."""
import json, os, pathlib, subprocess, sys, threading, time, traceback
import pyeudaq
from msensor.codec import decode
from msensor.stream import read as read_stream

class RC(pyeudaq.RunControl):
    def __init__(self):
        super().__init__('44001'); self.finished=False; self.time_ns=0; self.error=False
        self.states={}; self.observations=[]; self._last={}
    def observe(self,event,**values):
        self.observations.append({'event':event,'monotonic_ns':time.monotonic_ns(),**values})
    def DoConnect(self,connection):
        name=connection.GetName();self.states[name]=None;self.observe('connect',name=name)
    def DoDisconnect(self,connection):
        name=connection.GetName();self.states.pop(name,None);self.observe('disconnect',name=name)
    def DoStatus(self,connection,status):
        name=connection.GetName();state=status.GetState();self.states[name]=state
        if self._last.get(name)!=state:
            self.observe('status',name=name,state=int(state),message=status.GetMessage());self._last[name]=state
        if status.GetTag('source_finished','0')=='1': self.finished=True
        self.time_ns=max(self.time_ns,int(status.GetTag('time_ns','0')))
        if state==pyeudaq.Status.STATE_ERROR: self.error=True

def listener_ready(port):
    for table in ('/proc/net/tcp','/proc/net/tcp6'):
        try:
            for row in pathlib.Path(table).read_text().splitlines()[1:]:
                fields=row.split()
                if int(fields[1].rsplit(':',1)[1],16)==port and fields[3]=='0A': return True
        except OSError: pass
    return False

def wait_condition(predicate, description, children=(), timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return
        if any(p.poll() is not None for p in children):raise RuntimeError(description+': component exited')
        time.sleep(.01)
    raise RuntimeError(description+': timed out')

def producer_writes_stream(process,path):
    try:
        target=str(path.resolve())
        for fd in pathlib.Path('/proc',str(process.pid),'fd').iterdir():
            try:
                if os.readlink(fd)==target:
                    flags=int(next(x.split()[1] for x in (pathlib.Path('/proc',str(process.pid),'fdinfo',fd.name)).read_text().splitlines() if x.startswith('flags:')),8)
                    if flags & os.O_ACCMODE in (os.O_WRONLY,os.O_RDWR):return True
            except (FileNotFoundError,ProcessLookupError,PermissionError,StopIteration,ValueError):continue
    except (FileNotFoundError,ProcessLookupError,PermissionError):pass
    return False

work=pathlib.Path('/tmp/msensor-eudaq'); work.mkdir(exist_ok=True); os.chdir(work)
mode=os.environ.get('MSENSOR_TEST_MODE','triggered')
its3=os.environ.get('MSENSOR_TEST_COLLECTOR')=='its3'
kill_producer=os.environ.get('MSENSOR_TEST_KILL')=='1'
fail_write=os.environ.get('MSENSOR_TEST_WRITE_FAIL')=='1'
if kill_producer or fail_write: mode='continuous'
fixture=os.environ.get('MSENSOR_TEST_FIXTURE','')
threshold=int(os.environ.get('MSENSOR_TEST_THRESHOLD','100'))
delay=int(os.environ.get('MSENSOR_TEST_DELAY','0'))
if fixture in ('C_thr','C_rate0','C_offset'): mode='continuous'
if fixture in ('T_thr','T_strobe'): mode='triggered'
r={'status':'fail','input_conditions':{'fixture':('C_stream' if its3 else fixture or mode+'-small'),'sim_end_ns':(500000000 if its3 else 200000000 if fixture=='C_rate0' else 2000000000 if fixture else 1000000),'threshold_dac':threshold,'strobe_delay_ns':delay},'measurement':{},'criterion':'wait for source_finished; closed raw contains one data event per trigger and converter hit count matches payload'}
procs=[]
def native_timing():
    try:
        return [json.loads(line) for line in pathlib.Path('/tmp/msensor-eudaq-timing.jsonl').read_text().splitlines() if line]
    except (OSError,ValueError):return []
try:
    pathlib.Path('init.ini').write_text('[Producer.MSENSOR_0]\nplane = 1\ncolumns = 32\nrows = 32\nregions = 1\ndeterministic_ids = 1\nclock_mode = virtual\nsim_step_ns = 100000\n')
    pathlib.Path('run.conf').write_text('[Producer.MSENSOR_0]\nEUDAQ_DC = direct\nEUDAQ_ID = 1\nreadout_mode = triggered\nthreshold_dac = 100\nstrobe_delay_ns = 0\nstrobe_length_ns = 2000\n[DataCollector.direct]\nEUDAQ_ID = 1\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = msrun$3R$X\nDISABLE_PRINT = 1\n')
    if mode=='continuous':
        ini=pathlib.Path('init.ini'); ini.write_text(ini.read_text()+'stream_dir = /tmp/msensor-eudaq\n')
        conf=pathlib.Path('run.conf'); conf.write_text(conf.read_text().replace('readout_mode = triggered\nthreshold_dac = 100\nstrobe_delay_ns = 0\nstrobe_length_ns = 2000', 'readout_mode = continuous\nthreshold_dac = 100\nwindow_length_ns = 100000\nwindow_guard_before = 1\nwindow_guard_after = 1'))
    if its3:
        ini=pathlib.Path('init.ini'); ini.write_text(ini.read_text()+'[DataCollector.direct]\ndataproducers = MSENSOR_0\n')
    if its3 or fixture:
        ini=pathlib.Path('init.ini'); ini.write_text(ini.read_text().replace('columns = 32','columns = 256').replace('rows = 32','rows = 128').replace('regions = 1','regions = 4').replace('sim_step_ns = 100000','sim_step_ns = 1000000'))
    if fixture and fixture!='C_rate0':
        response=json.loads(pathlib.Path('/work/msensor/response_default.json').read_text()); response['r0']=0
        pathlib.Path('/tmp/msensor-eudaq/response.json').write_text(json.dumps(response))
        ini=pathlib.Path('init.ini'); ini.write_text(ini.read_text()+'response_file = /tmp/msensor-eudaq/response.json\n')
        conf=pathlib.Path('run.conf'); conf.write_text(conf.read_text().replace('threshold_dac = 100',f'threshold_dac = {threshold}').replace('strobe_delay_ns = 0',f'strobe_delay_ns = {delay}'))
    if kill_producer:
        ini=pathlib.Path('init.ini'); ini.write_text(ini.read_text().replace('clock_mode = virtual','clock_mode = paced'))
    if fixture=='C_rate0':
        conf=pathlib.Path('run.conf'); conf.write_text(conf.read_text().replace('threshold_dac = 100','threshold_dac = 80'))
    rc=RC(); threading.Thread(target=rc.Exec,daemon=True).start()
    wait_condition(lambda:listener_ready(44001),'RunControl listener')
    commands=[['euCliCollector','-n','DirectSaveDataCollector','-t','direct','-r','tcp://127.0.0.1:44001'],[sys.executable,'-m','msensor.msensor_producer','-n','MSENSOR_0','-r','tcp://127.0.0.1:44001']]
    source={'seed':20261110,'trigger_times_ns':[50000,250000,750000],'sim_end_ns':1000000,'beam_center_col':16,'beam_center_row':16,'beam_sigma_px':0}
    if its3:
        commands[0]=[sys.executable,'/opt/eudaq-src/user/ITS3/python/ITS3DataCollector.py','-n','direct','--rc','tcp://127.0.0.1:44001']
        source={'seed':20261110,'trigger_rate_hz':1000,'sim_end_ns':500000000,'beam_center_col':128.0,'beam_center_row':64.0,'beam_sigma_px':20.0}
    if fixture:
        source={'seed':20261110,'trigger_period_ns':1000037,'trigger_first_ns':50000,'sim_end_ns':2000000000,'beam_center_col':128.0,'beam_center_row':64.0,'beam_sigma_px':20.0}
    if fixture=='C_rate0':
        source={'seed':20261110,'trigger_rate_hz':0,'sim_end_ns':200000000,'beam_center_col':128.0,'beam_center_row':64.0,'beam_sigma_px':20.0}
    if fail_write: commands[1]=[sys.executable,'/work/tests/e2e/msensor_fault_producer.py']
    if kill_producer:
        source={'seed':20261110,'trigger_rate_hz':1000,'beam_center_col':16,'beam_center_row':16,'beam_sigma_px':0}
        r['input_conditions']={'fixture':'producer-kill','sim_end_ns':None,'clock_mode':'paced'}
        r['criterion']='producer holds an open stream before kill; recovery is truncated with no EORE'
    env=dict(os.environ, MSENSOR_SOURCE_CONFIG_JSON=json.dumps(source))
    for i,cmd in enumerate(commands): procs.append(subprocess.Popen(cmd,stdout=open(f'process{i}.log','w'),stderr=subprocess.STDOUT,env=env))
    names={'direct','MSENSOR_0'}
    wait_condition(lambda:names <= set(rc.states),'component connection',procs)
    wait_condition(lambda:all(rc.states.get(n)==pyeudaq.Status.STATE_UNINIT for n in names),'uninitialized status',procs)
    rc.ReadInitialiseFile('init.ini');rc.observe('initialise_call');rc.Initialise()
    wait_condition(lambda:all(rc.states.get(n)==pyeudaq.Status.STATE_UNCONF for n in names),'initialized status',procs)
    rc.ReadConfigureFile('run.conf');rc.observe('configure_call');rc.Configure()
    wait_condition(lambda:all(rc.states.get(n)==pyeudaq.Status.STATE_CONF for n in names),'configured status',procs)
    rc.observe('start_call');rc.StartRun();rc.observe('start_return');deadline=time.monotonic()+25
    if fail_write:
        while not rc.error and time.monotonic()<deadline and procs[1].poll() is None: time.sleep(.1)
        rc.StopRun(); time.sleep(1); rc.Terminate()
        for child in procs:
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired: child.terminate(); child.wait(timeout=5)
        raw_files=list(pathlib.Path('.').glob('*.raw')); closed_tags=[]
        if len(raw_files)!=1: raise RuntimeError('raw unavailable after injected write failure')
        reader=pyeudaq.FileReader('native',str(raw_files[0]))
        while True:
            ev=reader.GetNextEvent()
            if ev is None: break
            if ev.IsEORE(): closed_tags.append(ev.GetTag('stream_closed',''))
        r['input_conditions']={'fixture':'injected-stream-write-error'}; r['criterion']='producer enters error and EORE indicates stream_closed=0'; r['measurement']={'observed_error':rc.error,'stream_closed_tags':closed_tags}; r['status']='pass' if rc.error and '0' in closed_tags else 'fail'
        r['measurement']['native_timing']=native_timing(); pathlib.Path('/tmp/msensor-eudaq-result.json').write_text(json.dumps(r,indent=2)+'\n'); print(json.dumps(r),flush=True); os._exit(0 if r['status']=='pass' else 1)
    if kill_producer:
        streams=[]; before=None
        while time.monotonic()<deadline and all(p.poll() is None for p in procs) and not rc.error:
            streams=list(pathlib.Path('.').glob('*.msws'))
            if rc.time_ns>=10000000 and not rc.finished and len(streams)==1 and producer_writes_stream(procs[1],streams[0]):
                before=read_stream(streams[0])
                if before['status']=='truncated' and any(x['kind']=='HEADER' for x in before['records']): break
            time.sleep(.01)
        alive=procs[1].poll() is None
        active=alive and not rc.finished and len(streams)==1 and producer_writes_stream(procs[1],streams[0]) and before is not None and before['status']=='truncated'
        if not active: raise RuntimeError('active open stream precondition not reached; inspect component states and producer exit')
        rc.observe('kill',producer_pid=procs[1].pid,stream_open=active,time_ns=rc.time_ns,source_finished=rc.finished)
        procs[1].kill(); procs[1].wait(timeout=5)
        recovered=read_stream(streams[0])
        procs[0].terminate(); procs[0].wait(timeout=5)
        raw_files=list(pathlib.Path('.').glob('*.raw')); eore_count=0
        if len(raw_files)!=1: raise RuntimeError('raw file unavailable after producer kill')
        reader=pyeudaq.FileReader('native',str(raw_files[0]))
        while True:
            event=reader.GetNextEvent()
            if event is None: break
            eore_count+=bool(event.IsEORE())
        r['input_conditions']={'fixture':'producer-kill','observed_time_ns':rc.time_ns}; r['criterion']='killed producer leaves a valid truncated stream and no EORE event'; r['measurement']={'pre_kill_stream_open':active,'pre_kill_source_finished':rc.finished,'pre_kill_stream_status':before['status'],'producer_alive_before_kill':alive,'status_timeline':rc.observations,'stream_status':recovered['status'],'valid_records':len(recovered['records']),'valid_bytes':recovered['valid_bytes'],'tail_bytes':recovered['tail_bytes'],'eore_count':eore_count}; r['status']='pass' if recovered['status']=='truncated' and eore_count==0 else 'fail'
        for child in procs:
            if child.poll() is None: child.terminate()
        r['measurement']['native_timing']=native_timing(); pathlib.Path('/tmp/msensor-eudaq-result.json').write_text(json.dumps(r,indent=2)+'\n'); print(json.dumps(r),flush=True); os._exit(0 if r['status']=='pass' else 1)
    while not rc.finished and time.monotonic()<deadline and all(p.poll() is None for p in procs): time.sleep(.1)
    if not rc.finished: raise RuntimeError('source_finished status not observed')
    rc.StopRun(); time.sleep(2); rc.Terminate()
    for p in procs:
        try: p.wait(timeout=8)
        except subprocess.TimeoutExpired: p.terminate(); p.wait(timeout=5)
    files=list(pathlib.Path('.').glob('*.raw'))
    if len(files)!=1: raise RuntimeError(f'raw files: {files}')
    reader=pyeudaq.FileReader('native',str(files[0])); records=[]; device_numbers=[]; begin=end=None
    while True:
        e=reader.GetNextEvent()
        if e is None: break
        if e.GetDescription()=='msensor': records.append(decode(e.GetBlock(0))); device_numbers.append(e.GetDeviceN())
        if e.GetDescription()=='ITS3global':
            for sub in e.GetSubEvents():
                if sub.GetDescription()=='msensor': records.append(decode(sub.GetBlock(0))); device_numbers.append(sub.GetDeviceN())
        if e.IsBORE(): begin=e.GetTags()
        if e.IsEORE(): end=e.GetTags()
    values=[e['trigger_number'] for e in records]
    converted=subprocess.run(['/work/tests/e2e/msensor_convert',str(files[0])],capture_output=True,text=True,timeout=20)
    expected_hits=sum(len(frame['hits']) for record in records for frame in record['frames'] if mode=='triggered' or frame['window_index']==record['window_index'])
    if converted.returncode!=0: raise RuntimeError(f'converter failed: {converted.returncode} {converted.stderr}')
    converted_events,converted_hits=map(int,converted.stdout.strip().split())
    r['measurement']={'trigger_numbers':values,'device_numbers':sorted(set(device_numbers)),'trigger_times_ns':[e['trigger_time_ns'] for e in records],'bore':begin,'eore':end,'raw_bytes':files[0].stat().st_size,'converted_events':converted_events,'converted_hits':converted_hits,'device_hits':expected_hits}
    if fixture=='C_offset':
        offsets={}
        for off in (-1,0,1):
            converted_offset=subprocess.run(['/work/tests/e2e/msensor_convert',str(files[0]),str(off)],capture_output=True,text=True,timeout=30)
            if converted_offset.returncode: raise RuntimeError('offset converter failed: '+converted_offset.stderr[-1000:])
            ev_count,hit_count,rejected=map(int,converted_offset.stdout.split())
            offsets[str(off)]={'events':ev_count,'hits':hit_count,'rejected':rejected}
        r['measurement']['offsets']=offsets
    if os.environ.get('MSENSOR_RUN_CORRY')=='1':
        ctest=subprocess.run([sys.executable,'/work/tests/e2e/msensor_corry.py',str(files[0]),str(converted_hits)],capture_output=True,text=True,timeout=150)
        try: r['measurement']['corry']=json.loads(ctest.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError): r['measurement']['corry']={'status':'fail','exit_code':ctest.returncode,'stderr':ctest.stderr[-5000:],'stdout':ctest.stdout[-5000:]}
    stream_ok=True
    if mode=='triggered':
        r['measurement']['stream_files']=len(list(pathlib.Path('.').glob('*.msws')))
        r['measurement']['triggered_stream_ids']=sorted({record['stream_id'] for record in records})
        stream_ok=r['measurement']['stream_files']==0 and all(record['stream_id']==0 for record in records)
    if mode=='continuous':
        streams=list(pathlib.Path('.').glob('*.msws'))
        if len(streams)!=1: raise RuntimeError(f'stream files: {streams}')
        decoded=read_stream(streams[0]); stream_ok=decoded['status']=='closed_clean' and decoded['counts']['triggers_total']==len(records) and decoded['counts']['windows_total']==(5000 if its3 else 2000 if fixture=='C_rate0' else 20000 if fixture else 10)
        if its3 or fixture=='C_rate0':
            windows={}
            for sr in decoded['records']:
                if sr['kind']=='WINDOW': windows[sr['data']['window_index']]=sr['data']['hits']
                elif sr['kind']=='EMPTY_RUN':
                    for k in range(sr['data']['first_index'],sr['data']['first_index']+sr['data']['count']): windows[k]=[]
            triggers={sr['data']['trigger_number']:sr['data'] for sr in decoded['records'] if sr['kind']=='TRIGGER'}
            stream_ok=stream_ok and all(sorted(map(tuple,f['hits']))==sorted(map(tuple,windows[f['window_index']])) for event in records for f in event['frames'])
            stream_ok=stream_ok and all(event['trigger_number'] in triggers and event['trigger_time_ns']==triggers[event['trigger_number']]['trigger_time_ns'] and event['window_index']==triggers[event['trigger_number']]['window_index'] for event in records)
            header=decoded['records'][0]['data']
            stream_ok=stream_ok and begin.get('stream_id')==header['stream_id'] and begin.get('config_hash')==header['config_hash'] and end.get('stream_file_sha256')==decoded['file_sha256']
            stream_ok=stream_ok and all(json.loads(end[k])==v for k,v in decoded['counts'].items()) and len(triggers)==len(records)
            r['measurement']['stream_projection_and_summary_match']=stream_ok
        r['measurement']['stream_status']=decoded['status']; r['measurement']['stream_counts']=decoded['counts']
    visible=json.dumps([begin,end,records])
    r['measurement']['seed_or_source_leak']=any(x in visible for x in ('20261110','trigger_times_ns','trigger_rate_hz','beam_center_col','sim_end_ns'))
    r['status']='pass' if ((len(values)>0 and values==list(range(len(values)))) if its3 else values==[] if fixture=='C_rate0' else values==list(range(2000)) if fixture else values==[0,1,2]) and begin is not None and end is not None and not r['measurement']['seed_or_source_leak'] and converted_events==len(records) and converted_hits==expected_hits and (not records or set(device_numbers)=={1}) and stream_ok and (os.environ.get('MSENSOR_RUN_CORRY')!='1' or r['measurement']['corry']['status']=='pass') else 'fail'
except Exception as e:
    r['measurement']={'error':str(e),'traceback':traceback.format_exc(),'status_timeline':rc.observations if 'rc' in locals() else [],'component_exit_codes':[p.poll() for p in procs],'logs':{str(i):pathlib.Path(f'process{i}.log').read_text(errors='replace')[-8000:] for i in range(len(procs))}}
finally:
    for p in procs:
        if p.poll() is None: p.terminate()
    r['measurement']['native_timing']=native_timing()
    pathlib.Path('/tmp/msensor-eudaq-result.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({'status':r['status'],'fixture':r['input_conditions']['fixture'],'measurement':{k:v for k,v in r['measurement'].items() if k in ('error','converted_events','converted_hits','device_hits','stream_status')}}),flush=True)
os._exit(0 if r['status']=='pass' else 1)
