# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run one public-DAQ acquisition per layout and preserve JSON verdicts."""
import copy, hashlib, json, os, platform, shutil, subprocess, sys, tempfile, uuid
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from integration.layout import default_layout, generate
HERE=Path.cwd(); OUT=HERE/'artifacts/layouts';OUT.mkdir(parents=True,exist_ok=True)
IMAGE='local/session-core:native';IMAGE_ID=subprocess.check_output(['docker','image','inspect',IMAGE,'--format','{{.Id}}'],text=True).strip()
COMMIT=(__import__('pathlib').Path('SOURCE_REVISION').read_text().strip())
TEMP=Path(tempfile.mkdtemp(prefix='layouts-e2e-')); results={}

def command(args, *, timeout=300, allow=False):
 p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
 if p.returncode and not allow:raise RuntimeError(args[0]+' exited '+str(p.returncode)+': '+(p.stdout+p.stderr)[-4000:])
 return p

def verdict(name, ok, measurement=None, reason=None, status=None):
 row={'id':name,'status':status or ('pass' if ok else 'fail'),'input_conditions':{'architecture':platform.machine()},
      'measurement':measurement or {},'criterion':'documented end-to-end acceptance criteria',
      'image_id':IMAGE_ID,'repository_commit':COMMIT}
 if reason:row['reason']=reason
 if name=='L-E8':row['corryvreckan_source']='official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'
 results[name]=row;(OUT/(name+'.json')).write_text(json.dumps(row,indent=2,ensure_ascii=False)+'\n')
 print(name,row['status'],flush=True)

def sample(na,nm):
 return {'devices':[*[{'kind':'alpide','number':i,'plane':i,'position_mm':[1,-2,i*20], 'pixels':[1024,512],'pitch_um':[30,30]} for i in range(na)],
   *[{'kind':'msensor','number':i,'plane':na+i,'position_mm':[1,-2,(na+i)*20], 'pixels':[128,128],'pitch_um':[30,30]} for i in range(nm)]]}

def acquire(label,layout,high=False):
 folder=TEMP/label;folder.mkdir();(folder/'layout.json').write_text(json.dumps(layout))
 name='layouts-'+uuid.uuid4().hex[:12]
 command(['docker','create','--name',name,'--network','none',IMAGE,'sleep','600'])
 try:
  for source,dest in [(HERE/'integration','/tmp/integration'),(HERE/'tests/e2e/layout_acquire.py','/tmp/layout_acquire.py'),
   (HERE/'tests/e2e/layout_convert.cc','/tmp/layout_convert.cc'),(folder/'layout.json','/tmp/layout-input.json')]:
   command(['docker','cp',str(source),name+':'+dest])
  command(['docker','start',name])
  command(['docker','exec',name,'c++','-std=c++17','-I/opt/eudaq/include','/tmp/layout_convert.cc','-L/opt/eudaq/lib','-leudaq_core','-ldl','-Wl,-rpath,/opt/eudaq/lib','-o','/tmp/layout-convert'])
  args=['docker','exec']+(['-e','LAYOUT_HIGH_THRESHOLD=1'] if high else [])+[name,'python','/tmp/layout_acquire.py']
  p=command(args,timeout=200,allow=True);(folder/'execution.log').write_text(p.stdout+p.stderr)
  if p.returncode:raise RuntimeError('layout acquisition '+label+' failed: '+(p.stdout+p.stderr)[-3500:])
  command(['docker','cp',name+':/tmp/layout-e2e/measurement.json',str(folder/'measurement.json')])
  return json.loads((folder/'measurement.json').read_text())
 finally:command(['docker','rm','-f',name],allow=True)

def attempt(name,fn):
 try:fn()
 except Exception as e:verdict(name,False,reason=str(e))

def l1():
 x=acquire('l1',sample(1,0));verdict('L-E1',x['common_pass'] and x['counts']['0']['hits']>0,x)
def l2():
 a=acquire('l2-low',sample(0,6));b=acquire('l2-high',sample(0,6),True)
 same=all(a['counts'][str(i)]['hits']==b['counts'][str(i)]['hits'] for i in range(1,6))
 names=[f'MSENSOR_{i}' for i in range(6)]
 ok=a['common_pass'] and b['common_pass'] and a['stream_names']==b['stream_names']==names and same and a['counts']['0']['hits']>b['counts']['0']['hits']
 verdict('L-E2',ok,{'low':a,'high':b,'other_five_hit_counts_equal':same})
def l3():
 x=acquire('l3',sample(3,2));verdict('L-E3',x['common_pass'],x)
def l4():
 layout=sample(6,1);a=acquire('l4-base',layout);layout2=copy.deepcopy(layout);layout2['devices'][-1]['position_mm'][0]+=.3;b=acquire('l4-shift',layout2)
 def mean(x):
  v=x['counts']['6'];return v['sum_columns']/v['hits'] if v['hits'] else None
 m,n=mean(a),mean(b);delta=(n-m if m is not None and n is not None else None)
 partial=copy.deepcopy(layout);partial['devices'][-1]['position_mm'][0]+=2.15
 c=acquire('l4-partial',partial);edge=c['counts']['6']
 partial_ok=c['common_pass'] and edge['hits']>0 and 0<=edge['min_column']<=edge['max_column']<16
 verdict('L-E4',a['common_pass'] and b['common_pass'] and delta is not None and delta!=0 and abs(delta+10)<2 and partial_ok,{'base':a,'shifted':b,'partial':c,'partial_edge_only':partial_ok,'mean_column_before':m,'mean_column_after':n,'delta':delta,'expected_delta':-10,'absolute_tolerance_exclusive':2})
def l5():
 layout=sample(6,1);layout['devices'][0]['position_mm'][0]+=100;x=acquire('l5',layout)
 verdict('L-E5',x['common_pass'] and x['counts']['0']['hits']==0 and all(x['counts'][str(i)]['hits']>0 for i in range(1,7)),x)
def l6():
 layout=sample(6,1);layout['devices'][-1]['position_mm'][0]+=100;x=acquire('l6',layout)
 verdict('L-E6',x['common_pass'] and x['counts']['6']['hits']==0,x)
def l7():
 folder=TEMP/'determinism';folder.mkdir();sentinel=folder/'analysis-geometry.conf';sentinel.write_text('preserve analysis geometry\n')
 generate(default_layout(),folder)
 preserved=sentinel.read_text()=='preserve analysis geometry\n'
 first={str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
 generate(default_layout(),folder)
 second={str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
 clean=TEMP/'no-analysis';generate(default_layout(),clean)
 absent=not (clean/'analysis-geometry.conf').exists()
 clean2=TEMP/'no-analysis-other';generate(default_layout(),clean2)
 def hashes(path):return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
 portable=hashes(clean)==hashes(clean2)
 generate(default_layout(),folder,analysis=True)
 text=(folder/'analysis-geometry.conf').read_text();analysis_ok='[MSENSOR_6]' in text and 'number_of_pixels = 128, 128' in text and 'position = 1mm, -2mm, 60mm' in text and 'pixel_pitch = 30um, 30um' in text
 verdict('L-E7',preserved and first==second and portable and absent and analysis_ok,{'existing_analysis_unchanged':preserved,'repeat_byte_identical':first==second,'different_directories_byte_identical':portable,'analysis_absent_by_default':absent,'analysis_geometry_matches':analysis_ok})
def l8():
 scripts=['run_msensor_e2e.sh','run_runcontrol_e2e.sh','run_tracksim_e2e.sh','run_alpide_board_e2e.sh','run_session_core_e2e.sh','run_beam_e2e.sh','run_session_e2e.sh'];rows=[]
 for script in scripts:
  print('L-E8 START',script,flush=True)
  p=command(['bash','tests/scripts/'+script],timeout=2400,allow=True)
  (OUT/('regression-'+script+'.log')).write_text(p.stdout+p.stderr)
  rows.append({'script':script,'exit_code':p.returncode})
  print('L-E8 END',script,p.returncode,flush=True)
 verdict('L-E8',all(x['exit_code']==0 for x in rows),{'scripts':rows,'uses_generated_analysis_geometry':True})

selected=os.environ.get('LAYOUT_ONLY')
try:
 for i,fn in enumerate((l1,l2,l3,l4,l5,l6,l7,l8),1):
  name='L-E'+str(i)
  if not selected or selected==name:attempt(name,fn)
 if selected and selected not in results:raise ValueError('unknown selected check')
finally:shutil.rmtree(TEMP,ignore_errors=True)
sys.exit(0 if all(r['status']=='pass' for r in results.values()) else 1)
