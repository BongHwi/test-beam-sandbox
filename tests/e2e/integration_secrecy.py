# SPDX-License-Identifier: LGPL-3.0-or-later
"""Search all contract-specified operator surfaces, including real ROOT output."""
import json
from pathlib import Path
import sys
import pyeudaq
import ROOT
root=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/integration-e2e')
out=Path('/tmp/integration-results');out.mkdir(exist_ok=True)
markers=['1857092461','1857092460','86753091234','1.234567891','-2.345678912',
         '0.00123456789','0.00345678912','0.00456789123','237.654321987',
         'SESSION_SOURCE_CONFIG_JSON','MSENSOR_SOURCE_CONFIG_JSON']
received=[json.loads(x) for x in (root/'truth/received.jsonl').read_text().splitlines()]
for row in received[:3]:markers.extend((str(row['col']),str(row['row'])))
leaks=[];examined=[]
def scan(name,text):
    examined.append(name)
    if any(x in text for x in markers):leaks.append(name)
for p in root.rglob('*'):
    if not p.is_file() or root/'truth' in p.parents:continue
    if p.suffix in ('.ini','.conf','.json','.jsonl','.log','.msws'):
        scan(str(p.relative_to(root)),p.read_bytes().decode('utf-8',errors='ignore'))
rc=root/'rc.log'
if rc.exists():scan('runcontrol-log',rc.read_text(errors='replace'))
for path in root.glob('*.raw'):
    reader=pyeudaq.FileReader('native',str(path));tags=[]
    while True:
        ev=reader.GetNextEvent()
        if ev is None:break
        tags.append(dict(ev.GetTags()));tags.extend(dict(s.GetTags()) for s in ev.GetSubEvents())
    scan('raw-tags-'+path.name,json.dumps(tags))
ROOT.gSystem.Load('/opt/corry/lib/libCorryvreckanObjects.so')
def walk(directory,name):
    for key in directory.GetListOfKeys():
        child=name+'/'+str(key.GetName())
        if str(key.GetClassName())=='string':
            scan(child,str(directory.Get(key.GetName())));continue
        obj=key.ReadObj()
        if obj.InheritsFrom('TDirectory'):walk(obj,child)
        elif obj.InheritsFrom('TTree'):
            # TBufferJSON on a whole TTree crashes in ROOT 6.30; inspect every
            # entry and branch object individually instead of skipping the tree.
            scan(child,str(obj.GetName())+' '+str(obj.GetTitle()))
            branches=[str(x.GetName()) for x in obj.GetListOfBranches()]
            for index in range(obj.GetEntries()):
                obj.GetEntry(index)
                for branch in branches:
                    value=getattr(obj,branch)
                    items=value if hasattr(value,'size') else [value]
                    for item in items:scan(child+'/'+branch,str(ROOT.TBufferJSON.ConvertToJSON(item)))
        else:
            scan(child,str(obj.GetName())+' '+str(obj.GetTitle()))
            scan(child+':json',str(ROOT.TBufferJSON.ConvertToJSON(obj)))
roots=list((root/'analysis').rglob('*.root'))
for path in roots:
    scan('corry-binary-'+path.name,path.read_bytes().decode('utf-8',errors='ignore'))
    file=ROOT.TFile.Open(str(path))
    if not file or file.IsZombie():raise RuntimeError('invalid ROOT output')
    walk(file,'corry-root-'+path.name);file.Close()
required={'status':(root/'status.json').exists(),'beam_status':(root/'source.sock.beam.json').exists(),
          'beam_log':(root/'source.sock.beam.log').exists(),'panes':(root/'panes.log').exists(),
          'raw_tags':any(x.startswith('raw-tags-') for x in examined),'stream':bool(list(root.glob('*.msws'))),
          'config':(root/'init.ini').exists(),'geometry':(root/'geometry.json').exists(),
          'corry':len(roots)>=2,'truth_values':len(received)>=3,'process_log':rc.exists()}
result={'status':'pass' if not leaks and all(required.values()) else 'fail',
        'measurement':{'surfaces_with_markers':leaks,'required_surfaces':required,
                       'surface_count':len(examined),'corryvreckan_source':'official master commit 22fca3e43ceec7ba6263a5cf179cd57d5fff42fe'}}
(out/'I-E10.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(result,ensure_ascii=False),flush=True)
sys.exit(0 if result['status']=='pass' else 1)
