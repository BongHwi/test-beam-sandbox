# SPDX-License-Identifier: LGPL-3.0-or-later
"""Produce a public Corryvreckan output and inspect all operator surfaces."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pyeudaq

root=Path('/tmp/beam-acquire');raw=sorted(root.glob('*.raw'))[0]
geometry=root/'corry_geometry.conf'
geometry.write_text('''[MSENSOR_2]\ntype = "msensor"\nnumber_of_pixels = 128, 128\npixel_pitch = 30um, 30um\nposition = 1mm, -2mm, 80mm\norientation = 0deg, 0deg, 0deg\norientation_mode = "xyz"\nspatial_resolution = 10um, 10um\nmaterial_budget = 0.001\ntime_resolution = 1ns\nrole = "reference"\n''')
config=root/'corry_test.conf'
config.write_text(f'''[Corryvreckan]\nlog_level = "INFO"\ndetectors_file = "{geometry}"\nhistogram_file = "corry_test.root"\nnumber_of_events = 3000\n[Metronome]\nevent_length = 1ms\ntriggers = 1\n[EventLoaderEUDAQ2]\nname = "MSENSOR_2"\nfile_name = "{raw}"\n''')
env=dict(os.environ,LD_PRELOAD='/opt/eudaq/lib/libeudaq_module_msensor.so')
corry=subprocess.run(['corry','-c',str(config)],cwd=str(root),env=env,text=True,capture_output=True,timeout=120)
(root/'corry.log').write_text(corry.stdout+corry.stderr)
roots=list(root.rglob('*.root'))
markers=['1857092461','1857092460','86753091234','1.234567891','-2.345678912',
         '0.00123456789','0.00345678912','237.654321987',
         'SESSION_SOURCE_CONFIG_JSON','MSENSOR_SOURCE_CONFIG_JSON']
received=[json.loads(line) for line in (root/'truth/received.jsonl').read_text().splitlines()]
for row in received[:3]:markers.extend((str(row['col']),str(row['row'])))
surfaces={}
for path in root.rglob('*'):
    if path.is_file() and root/'truth' not in path.parents and path.suffix in ('.ini','.conf','.json','.jsonl','.log','.msws'):
        surfaces[str(path.relative_to(root))]=path.read_bytes().decode('utf-8',errors='ignore')
rc_log=Path('/opt/virtual-producer-cwd/rc.log')
if rc_log.exists():surfaces['runcontrol-log']=rc_log.read_text(errors='replace')
for path in root.glob('*.raw'):
    reader=pyeudaq.FileReader('native',str(path));tags=[]
    while True:
        ev=reader.GetNextEvent()
        if ev is None:break
        tags.append(dict(ev.GetTags()))
        tags.extend(dict(x.GetTags()) for x in ev.GetSubEvents())
    surfaces['raw-tags-'+path.name]=json.dumps(tags)
if roots:
    import ROOT
    def walk(directory):
        result=[]
        for key in directory.GetListOfKeys():
            obj=key.ReadObj()
            if obj.InheritsFrom('TDirectory'):result.extend(walk(obj))
            else:
                result.append(str(obj.GetName())+' '+str(obj.GetTitle()))
                result.append(str(ROOT.TBufferJSON.ConvertToJSON(obj)))
        return result
    for path in roots:
        file=ROOT.TFile.Open(str(path))
        if not file or file.IsZombie():raise RuntimeError('invalid Corryvreckan ROOT output')
        surfaces['corry-root-'+path.name]='\n'.join(walk(file))
        file.Close()
        surfaces['corry-binary-'+path.name]=path.read_bytes().decode('utf-8',errors='ignore')
leaks=[name for name,text in surfaces.items() if any(marker in text for marker in markers)]
required={'status':(root/'status.json').is_file(),'panes':(root/'panes.log').is_file(),
          'raw_tags':any(x.startswith('raw-tags-') for x in surfaces),'streams':bool(list(root.glob('*.msws'))),
          'config':(root/'init.ini').is_file(),'geometry':(root/'geometry.json').is_file(),
          'corry':corry.returncode==0 and bool(roots) and 'Found 0 hits' not in corry.stdout,
          'truth_values':len(received)>=3,'beam_status':(root/'source.sock.beam.json').is_file(),'beam_log':(root/'source.sock.beam.log').is_file()}
result={'status':'pass' if not leaks and all(required.values()) else 'fail','measurement':{'surfaces_with_markers':leaks,'required_surfaces':required,'surface_count':len(surfaces),'corry_exit':corry.returncode,'corry_input':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'}}
out=Path('/tmp/beam-results');out.mkdir(exist_ok=True)
(out/'M-E15.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(result,ensure_ascii=False));sys.exit(0 if result['status']=='pass' else 1)
