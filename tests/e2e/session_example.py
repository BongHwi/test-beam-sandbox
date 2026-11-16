# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the actual launcher fixture with the delivered geometry file unchanged."""
import os
from pathlib import Path

os.environ['SESSION_CASE']='fixed'
path=Path('/tmp/session_scenario.py')
source=path.read_text()
old="(root/'geometry.json').write_text(json.dumps(geometry))"
new="geometry=json.loads(Path('/tmp/session_example_geometry.json').read_text())\n(root/'geometry.json').write_text(json.dumps(geometry))"
if source.count(old)<1:raise RuntimeError('fixture geometry insertion point missing')
exec(compile(source.replace(old,new,1),str(path),'exec'),{'__name__':'__main__'})
