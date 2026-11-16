# SPDX-License-Identifier: LGPL-3.0-or-later
"""Read actual EUDAQ raw events and summarize their producer/trigger structure."""
import json,re,sys
from pathlib import Path
import pyeudaq
root=Path(sys.argv[1]);reports=[]
for path in sorted(root.glob('*.raw')):
 run=int(re.search(r'run(\d+)\.raw$',path.name).group(1));reader=pyeudaq.FileReader('native',str(path))
 groups=[];bore=[];eore=[]
 while True:
  event=reader.GetNextEvent()
  if event is None:break
  if event.IsBORE():bore.append(event.GetDescription())
  if event.IsEORE():eore.append(event.GetDescription())
  if event.GetDescription()=='ITS3global':
   groups.append([{'name':s.GetDescription(),'trigger':s.GetTriggerN()} for s in event.GetSubEvents()])
 reports.append({'run':run,'groups':groups,'bore':bore,'eore':eore})
print(json.dumps(reports,separators=(',',':')))
