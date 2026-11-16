# SPDX-License-Identifier: LGPL-3.0-or-later
"""Check private environment values against process arguments and pane titles."""
import json, os, subprocess
from pathlib import Path
private=None;sensor=None;command_lines=[]
for entry in Path('/proc').iterdir():
 if not entry.name.isdigit():continue
 try:
  cmd=(entry/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
  command_lines.append(cmd)
  env=(entry/'environ').read_bytes().split(b'\0')
  if '-m session_core.server' in cmd:
   for field in env:
    if field.startswith(b'SESSION_SOURCE_CONFIG_JSON='):private=json.loads(field.split(b'=',1)[1])
    if field.startswith(b'MSENSOR_SOURCE_CONFIG_JSON='):sensor=json.loads(field.split(b'=',1)[1])
 except (OSError,ValueError):continue
if not private or not sensor:raise SystemExit('private source process environment unavailable')
markers=[str(private['seed']),str(private['device_seed']),str(sensor['seed']),
         str(private['beam']['position_mm'][0]),str(private['beam']['position_mm'][1]),
         str(private['beam']['direction'][0]),str(private['beam']['phase_space_covariance'][0][0]),
         str(private['beam']['phase_space_covariance'][1][1]),str(private['rate_hz']),
         'SESSION_SOURCE_CONFIG_JSON','MSENSOR_SOURCE_CONFIG_JSON']
p=subprocess.run(['tmux','list-panes','-a','-F','#{pane_title}'],capture_output=True,text=True,check=True)
surfaces={'process_arguments':'\n'.join(command_lines),'pane_titles':p.stdout}
leaks=[name for name,text in surfaces.items() if any(marker in text for marker in markers)]
result={'status':'pass' if not leaks else 'fail','secret_fields_checked':len(markers),
        'process_argument_count':len(command_lines),'pane_title_count':len(p.stdout.splitlines()),
        'surfaces_with_markers':leaks}
print(json.dumps(result));raise SystemExit(0 if not leaks else 1)
