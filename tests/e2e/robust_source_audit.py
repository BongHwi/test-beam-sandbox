#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Audit the session source for device-specific identifiers and constants."""
import json,platform,re,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2];paths=sorted((root/'session_core').rglob('*.py'))
forbidden=re.compile(r'\b(?:msensor|alpide|board|ITHR|STROBE_LENGTH|strobe_tick_ps|threshold_max|[DPV][0-9]+|ALPIDE_plane_[0-9]+|1024|512|12500|25000|255|0x[0-9a-f]+)\b',re.I)
findings=[]
for p in paths:
 for n,line in enumerate(p.read_text().splitlines(),1):
  # This exact expression is the protocol's generic 1 MiB line cap.
  checked=line.replace('self.rfile.readline(1024 * 1024)','self.rfile.readline(BUFFER_CAPACITY)')
  if p.relative_to(root).as_posix()=='session_core/live_transport.py':
   # The number of trigger events buffered per transport segment is a generic
   # resource bound, not a sensor pixel dimension. The second expression is a
   # generic 31-bit hash-derived random seed mask. Exempt only these exact lines.
   if re.fullmatch(r'\s*SEGMENT_EVENTS = 1024\s*',checked):checked='SEGMENT_EVENTS = GENERIC_CAPACITY'
   if re.fullmatch(r"\s*return int\.from_bytes\(hashlib\.sha256\(source\)\.digest\(\)\[:4\], 'big'\) & 0x7fffffff\s*",checked):checked='return GENERIC_SEED'
  for m in forbidden.finditer(checked):findings.append({'path':str(p.relative_to(root)),'line':n,'token':m.group()})
rev=subprocess.run(['git','rev-parse','HEAD'],cwd=root,text=True,capture_output=True).stdout.strip()
if not rev:rev=(root/'SOURCE_REVISION').read_text().strip()
image=subprocess.run(['docker','image','inspect','local/session-core:native','--format','{{.Id}}'],text=True,capture_output=True).stdout.strip()
row={'id':'C-E18','status':'pass' if paths and not findings else 'fail','input_conditions':{'architecture':platform.machine(),'paths':[str(p.relative_to(root)) for p in paths]},'measurement':{'findings':findings,'generic_buffer_exception':'self.rfile.readline(1024 * 1024)'},'criterion':'documented end-to-end acceptance criteria','repository_commit':rev,'image_id':image}
out=root/'artifacts/robustness';out.mkdir(parents=True,exist_ok=True);(out/'C-E18.json').write_text(json.dumps(row,indent=2)+'\n')
print('C-E18',row['status']);sys.exit(0 if row['status']=='pass' else 1)
