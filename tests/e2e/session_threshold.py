# SPDX-License-Identifier: LGPL-3.0-or-later
"""Check threshold causality and noise-zero isolation from actual public raws."""
import json
from pathlib import Path
import subprocess
import sys
root=Path('/tmp/session-e2e')
raws=sorted(root.glob('*.raw'))
if len(raws)!=2:raise SystemExit('expected two raw files')
subprocess.run(['python','/tmp/session_compare.py',str(root)],capture_output=True,text=True)
comparisons=[json.loads(p.read_text()) for p in sorted(root.glob('comparison-*.json'))]
counts=[]
for path in raws:
    conversion=subprocess.run(['/tmp/alpide_convert',str(path)],capture_output=True,text=True)
    if conversion.returncode or 'WARN' in conversion.stderr or 'WARN' in conversion.stdout:raise SystemExit('public conversion failed')
    hits=0
    for line in conversion.stdout.splitlines():
        words=line.split()
        if words and words[0]=='ALPIDE_plane_0':hits+=int(words[3])
    counts.append(hits)
checks={'threshold_reduces_hits':counts[1]<counts[0],
        'unchanged_plane_bytes':comparisons[0]['events']['ALPIDE_plane_1']==comparisons[1]['events']['ALPIDE_plane_1'],
        'unchanged_device_payload':comparisons[0]['events']['msensor']==comparisons[1]['events']['msensor'],
        'unchanged_device_stream':comparisons[0]['stream']==comparisons[1]['stream']}
out=Path('/tmp/session-results');out.mkdir(exist_ok=True)
(out/'C-E8.json').write_text(json.dumps({'status':'pass' if all(checks.values()) else 'fail','measurement':{'hits':counts,'checks':checks}},indent=2)+'\n')
print(json.dumps({'hits':counts,'checks':checks}))
sys.exit(0 if all(checks.values()) else 1)
