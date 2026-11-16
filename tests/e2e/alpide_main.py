# SPDX-License-Identifier: LGPL-3.0-or-later
"""Actual public launcher, producer, ITS3 collector and converter E2E."""
import hashlib,json,os,pathlib,subprocess,time,traceback
import pyeudaq
from alpide_test_source import Source
root=pathlib.Path('/tmp/alpide-e2e');root.mkdir(exist_ok=True);out=pathlib.Path('/tmp/alpide-results');out.mkdir(exist_ok=True);os.chdir('/opt/virtual-producer-cwd')
def emit(n,status,**measurement):(out/f'V-E{n}.json').write_text(json.dumps({'status':status,'measurement':measurement},indent=2)+'\n')
def wait(fn,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.1)
    return None
frames=[{'time_ps':i*1000000,'pixels':p} for i,p in enumerate([[],[(10,20)],[(31,5),(32,5)],[(64+i,100+i%9) for i in range(20)],[(0,0),(1023,0),(0,511),(1023,511)],[(511,255)]])]
source=None
try:
    original={'producer':'e8039fda936bf506448c64ad5f2a79c7f65fa5bdfb967bd55d13c921ed456d80','converter':'e164f466ea361e85eb8bb971363566a41fd33dfced8620dae427bf9943970934'}
    paths={'producer':'/opt/eudaq-src/user/ITS3/python/ALPIDEProducer.py','converter':'/opt/eudaq-src/user/ITS3/module/src/ALPIDERawEvent2StdEventConverter.cc'}
    hashes={k:hashlib.sha256(pathlib.Path(v).read_bytes()).hexdigest() for k,v in paths.items()};emit(1,'pass' if hashes==original else 'fail',sha256=hashes,expected=original)
    compile=subprocess.run(['c++','-std=c++17','-I/opt/eudaq/include','/tmp/alpide_convert.cc','-L/opt/eudaq/lib','-leudaq_core','-ldl','-Wl,-rpath,/opt/eudaq/lib','-o','/tmp/alpide_convert'],capture_output=True,text=True)
    if compile.returncode:raise RuntimeError('converter checker build: '+compile.stderr[-2000:])
    socket_path=str(root/'source.sock');source=Source(socket_path,['VIRTUAL0'],frames)
    init=root/'init.ini';init.write_text(f'''[RunControl]\ndataproducers = ALPIDE_plane_0\nmoreproducers =\nloggers =\ncollectors = dc\nconfigs = {root}/a.conf,{root}/b.conf\n[DataCollector.dc]\ndataproducers = ALPIDE_plane_0\n[Producer.ALPIDE_plane_0]\nserial = VIRTUAL0\nplane = 0\ntriggermode = primary\n''')
    for name,ithr,strobe in [('a',40,20),('b',180,80)]:
        (root/(name+'.conf')).write_text(f'''[RunControl]\nNEVENTS = 6\n[Producer.ALPIDE_plane_0]\nEUDAQ_DC = dc\nEUDAQ_ID = 0\nITHR = {ithr}\nSTROBE_LENGTH = {strobe}\n[DataCollector.dc]\nEUDAQ_ID = 0\nEUDAQ_FW = native\nEUDAQ_FW_PATTERN = /tmp/alpide-e2e/run$3R$X\nDISABLE_PRINT = 1\n''')
    env=dict(os.environ,VIRTUAL_ALPIDE_SOURCE_SOCKET=socket_path)
    cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(init),'--rc-address','44000','--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET','--no-attach','--rc-args',f'--one-pass --status-file {root}/status.json']
    launch=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=20);(root/'launch.log').write_text(launch.stdout+launch.stderr)
    if launch.returncode:raise RuntimeError('launcher exit '+str(launch.returncode))
    def finished():
        try:
            x=json.loads((root/'status.json').read_text());return x if x['rc_state'] in ('TERMINATED','ERROR') else None
        except (OSError,ValueError):return None
    status=wait(finished,int(os.environ.get('ALPIDE_TEST_TIMEOUT','60')));time.sleep(2)
    raws=sorted(root.glob('*.raw'));counts=[];tags=[];conversions=[];same=True
    for raw in raws:
        reader=pyeudaq.FileReader('native',str(raw));records=[];begin=end=None
        while True:
            e=reader.GetNextEvent()
            if e is None:break
            if e.GetDescription()=='ALPIDE_plane_0_status':
                if e.IsBORE():begin=e.GetTags()
                if e.IsEORE():end=e.GetTags()
            for sub in e.GetSubEvents():
                if sub.GetDescription()=='ALPIDE_plane_0':records.append({'trigger':sub.GetTriggerN(),'timestamp':sub.GetTimestampBegin(),'raw':list(sub.GetBlock(0))})
        counts.append(len(records));tags.append({'bore':begin,'eore':end})
        converted=subprocess.run(['/tmp/alpide_convert',str(raw)],capture_output=True,text=True,timeout=20)
        lines=[]
        for line in converted.stdout.splitlines():
            if not line.startswith('ALPIDE_plane_0 '):continue
            words=line.split();lines.append({'trigger':int(words[1]),'timestamp':int(words[2]),'pixels':sorted([list(map(int,w.split(','))) for w in words[4:]])})
        expected=[{'trigger':i,'timestamp':f['time_ps']//12500,'pixels':sorted([[x,y,(f['time_ps']//12500)*12500] for x,y in f['pixels']])} for i,f in enumerate(frames)]
        same=same and converted.returncode==0 and lines==expected and 'WARN' not in converted.stderr and 'WARN' not in converted.stdout
        conversions.append({'exit_code':converted.returncode,'rows':lines,'stderr':converted.stderr[-1500:]})
    joins=[x for x in source.audit if x['op']=='join'];source_events=[sum(x['op']=='event' and x['wave']==wave for x in source.audit) for wave in (0,1)]
    common={'state':status.get('rc_state') if status else None,'raw_counts':counts,'source_counts':source_events,'raw_files':[p.name for p in raws]}
    emit(2,'pass' if status and status['rc_state']=='TERMINATED' and counts==source_events==[6,6] and len(tags)==2 and all(t['bore'] is not None and t['eore'] is not None for t in tags) else 'fail',**common)
    emit(3,'pass' if len(conversions)==2 and same else 'fail',conversions=conversions,expected_frames=frames)
    emit(4,'pass' if len(conversions)==2 and same else 'fail',fixture='empty, one, region boundary, many, four corners',conversions=conversions)
    good_tags=len(tags)==2
    for i,t in enumerate(tags):
        for phase in ('bore','eore'):
            x=t[phase] or {};good_tags=good_tags and x.get('ALPIDE_060e_ITHR')==str((40,180)[i]) and x.get('ALPIDE_0005_FROMU_CONF2')==str((20,80)[i]) and (phase!='bore' or ('virtual' in x.get('FPGA_GIT','') and 'virtual' in x.get('ALPIDEDAQ_GIT',''))) and x.get('IDDA')=='0.00 mA' and x.get('IDDD')=='0.00 mA' and x.get('Temperature')=='Invalid temperature read!'
    emit(5,'pass' if good_tags else 'fail',tags=tags)
    good_response=len(joins)==2 and joins[0]['response']['efficiency']>joins[1]['response']['efficiency'] and joins[0]['response']['cluster_mean']>joins[1]['response']['cluster_mean'] and joins[0]['response']['gate_ps']<joins[1]['response']['gate_ps']
    emit(6,'pass' if good_response else 'fail',joins=joins)
    emit(13,'pass' if len(conversions)==2 and all([x['trigger'] for x in r['rows']]==list(range(6)) for r in conversions) else 'fail',triggers=[[x['trigger'] for x in r['rows']] for r in conversions])
    expected_git='cfb0e422af953a0fec8ad98a8541a98cf6c6c1b3';git_ok=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==expected_git and all((t['bore'] or {}).get('EUDAQ_GIT')==expected_git and (t['bore'] or {}).get('EUDAQ_DIFF')=='' for t in tags)
    # The outside-of-worktree half is handled by the separate negative scenario.
    emit(14,'not_run' if git_ok else 'fail',git_tree=expected_git,inside_tree_matches=git_ok,reason='outside-worktree behavior not yet checked')
except Exception as e:
    panels={n:subprocess.run(['tmux','capture-pane','-p','-t','ITS3:'+n,'-S','-200'],capture_output=True,text=True).stdout[-6000:] for n in ('rc','rp','dc')}
    (out/'main-error.json').write_text(json.dumps({'error':str(e),'traceback':traceback.format_exc(),'panes':panels,'audit':source.audit if source else []},indent=2)+'\n')
finally:
    if source:source.close()
    for n in range(1,15):
        if not (out/f'V-E{n}.json').exists():emit(n,'not_run',reason='condition was not reached; see main-error.json or separate test')
    subprocess.run(['tmux','kill-server'],stderr=subprocess.DEVNULL)
