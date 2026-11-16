# SPDX-License-Identifier: LGPL-3.0-or-later
"""Start the public launcher in a generated layout directory."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('layout')
parser.add_argument('--example',action='store_true')
args=parser.parse_args()
root=Path(args.layout).resolve()
for filename in ('geometry.json','init.ini','a.conf','mapping.json'):
    if not (root/filename).is_file():raise SystemExit('missing generated layout file: '+filename)
geometry=json.loads((root/'geometry.json').read_text())
if args.example:
    geometry.pop('continuous',None)
    geometry.setdefault('end_ns',1500000000)
else:
    geometry['continuous']=True
    geometry.pop('end_ns',None)
(root/'session-mode').write_text(('example' if args.example else 'operator')+'\n')
geometry_path=root/'active-geometry.json'
geometry_path.write_text(json.dumps(geometry,indent=2)+'\n')
private_dir = None
# The actual (offset) device positions go only to the session core, through a
# private file outside the session directory; every session file keeps the
# surveyed positions of the setup.
if (root/'setup.json').is_file():
    from session_setup.setupfile import actual_offsets
    setup=json.loads((root/'setup.json').read_text())
    if setup.get('positions',{}).get('offset_max_mm'):
        offsets=actual_offsets(setup)
        detector={m['producer']:m['detector'] for m in json.loads((root/'mapping.json').read_text())}
        moved=json.loads(json.dumps(geometry))
        for row in moved['settings']['detectors']:
            for name,ident in detector.items():
                if ident==row['id'] and name in offsets:
                    row['position_mm']=[row['position_mm'][0]+offsets[name][0],row['position_mm'][1]+offsets[name][1],row['position_mm'][2]]
        private_dir=Path('/tmp/session-private')/secrets.token_hex(8)
        private_dir.mkdir(parents=True,mode=0o700)
        geometry_path=private_dir/'geometry.json'
        geometry_path.write_text(json.dumps(moved))
sock=str(root/'source.sock')
# Session-private synthetic conditions stay in the process environment; they
# are never placed on a command line, in a generated layout, or in a log.
# A session started from a setup file keeps its normalized copy; its beam
# values are the nominal centre of the private draw (defaults otherwise).
from session_setup.setupfile import beam_covariance

nominal={'position_mm':[1.1,-2.1,-20.0],'spread_mm2':[0.003,0.004],'rate_hz':220}
if (root/'setup.json').is_file():
    beam=json.loads((root/'setup.json').read_text())['beam']
    nominal={'position_mm':[beam['position_mm'][0],beam['position_mm'][1],float(beam['position_mm'][2])],
             'spread_mm2':beam['spread_mm2'],'rate_hz':beam['rate_hz'],'profile_values':beam.get('profile_values')}
x,y,z=nominal['position_mm'];vx,vy=nominal['spread_mm2']
private={'seed':10_000_000+secrets.randbelow(2**31-10_000_000),'device_seed':10_000_000+secrets.randbelow(2**31-10_000_000),
 'beam':{'position_mm':[x+secrets.randbelow(1000000)/10000000, y-secrets.randbelow(1000000)/10000000,z],
         'direction':[0.0001+secrets.randbelow(100000)/1000000000,0,1],
         'phase_space_covariance':beam_covariance(vx+secrets.randbelow(10000)/1000000000,
                                                  vy+secrets.randbelow(10000)/1000000000,
                                                  nominal.get('profile_values'))},
 'rate_hz':nominal['rate_hz']+secrets.randbelow(2000000)/100000}
env=dict(os.environ,SESSION_SOURCE_CONFIG_JSON=json.dumps(private),
         MSENSOR_SOURCE_CONFIG_JSON=json.dumps({'seed':10_000_000+secrets.randbelow(2**31-10_000_000)}),
         SESSION_SOURCE_SOCKET=sock,VIRTUAL_ALPIDE_SOURCE_SOCKET=sock,
         GIT_DIR='/opt/virtual-producer-cwd/.git',GIT_WORK_TREE='/opt/virtual-producer-cwd')
cmd=['python','/opt/eudaq-src/user/ITS3/misc/ITS3start.py',str(root/'init.ini'),
     '--extra-dut','MSENSOR=/opt/public-work/msensor/msensor_producer.py',
     '--rc-address','44000','--producer-env','SESSION_SOURCE_SOCKET',
     '--producer-env','VIRTUAL_ALPIDE_SOURCE_SOCKET',
     '--producer-env','GIT_DIR','--producer-env','GIT_WORK_TREE','--no-attach',
     '--session-geometry',str(geometry_path),'--session-socket',sock,
     '--rc-args',f"{'--one-pass ' if args.example else ''}--status-file {root}/status.json --control-socket {root}/control.sock"]
if geometry.get('trigger'):
    if private_dir is None:
        private_dir=Path('/tmp/session-private')/secrets.token_hex(8)
        private_dir.mkdir(parents=True,mode=0o700)
    cmd.extend(['--session-truth-dir',str(private_dir/'truth')])
p=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True,timeout=30)
(root/'launcher.log').write_text(p.stdout+p.stderr)
if p.returncode:raise SystemExit('session launcher failed; see launcher.log')
print('Session started. Beam is initially off; use the beam command to turn it on.')
