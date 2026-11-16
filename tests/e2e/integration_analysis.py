# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the pinned official Corryvreckan and public EUDAQ converters on a closed raw."""
import os
from pathlib import Path
import subprocess
import sys

raw=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
# An optional third argument names an aligned geometry; masks are found next to it.
geometry=Path(sys.argv[3]).resolve() if len(sys.argv)>3 else raw.parent/'analysis-geometry.conf'
if not geometry.is_file(): raise SystemExit('generated analysis geometry is missing')
config=out/'corry.conf'
text=f'''[Corryvreckan]\nlog_level = "INFO"\ndetectors_file = "{geometry}"\nhistogram_file = "histograms.root"\nnumber_of_events = -1\nlibrary_directories = "/tmp"\n[EventDefinitionRawTrigger]\nfile_name = "{raw}"\n'''
# Detector names and roles come from the session's analysis geometry, which is
# generated from its setup or layout: DUTs first, then the reference planes.
sections=[];roles={}
for line in geometry.read_text().splitlines():
    line=line.strip()
    if line.startswith('[') and line.endswith(']'):sections.append(line[1:-1]);roles[sections[-1]]=''
    elif line.replace(' ','').startswith('role=') and sections:roles[sections[-1]]=line.split('=',1)[1].strip().strip('"').lower()
duts=[n for n in sections if roles[n]=='dut'];references=[n for n in sections if roles[n]!='dut']
if len(references)<3:
    raise SystemExit(f'QA not possible: {len(references)} reference plane(s) in the analysis geometry; tracks need at least 3. No result was produced.')
# Hits required on a track: 5 as before, fewer only when fewer reference planes exist.
min_hits=max(3,min(5,len(references)-1))
for name in duts+references:
    text+=f'''[EventLoaderEUDAQ2]\nname = "{name}"\nidentifier = "{name}"\nfile_name = "{raw}"\n'''
# The QA command asks for Correlations (correlation with the reference plane, for the result
# page); it only adds histograms and a runtime warning, so the plain analysis stays without it.
correlations='[Correlations]\n' if os.environ.get('QA_CORRELATIONS')=='1' else ''
text+=f'''[ClusteringSpatial]\nuse_trigger_timestamp = true\n{correlations}[Tracking4D]\ntime_cut_abs = 1us\nspatial_cut_abs = 200um, 200um\nmin_hits_on_track = {min_hits}\nexclude_dut = true\n'''
if duts:
    text+='''[DUTAssociation]\nspatial_cut_abs = 100um, 100um\ntime_cut_abs = 1us\n[AnalysisDUT]\ntime_cut_frameedge = 0ns\nchi2ndof_cut = 100\nn_time_bins = 2000\ntime_binning = 1ns\n[AnalysisEfficiency]\ntime_cut_frameedge = 0ns\nchi2ndof_cut = 100\n'''
text+='''[FileWriter]\nfile_name = "events.root"\ninclude = "Track", "Cluster"\n'''
config.write_text(text)
env=dict(os.environ,LD_PRELOAD='/tmp/libmsensor_status.so:/opt/eudaq/lib/libeudaq_module_ITS3.so:/opt/eudaq/lib/libeudaq_module_msensor.so')
p=subprocess.run(['corry','-c',str(config)],cwd=str(out),env=env,text=True,capture_output=True)
(out/'corry.log').write_text(p.stdout+p.stderr)
print('corry_exit',p.returncode,'output',out,flush=True)
root_errors=[line for line in (p.stdout+p.stderr).splitlines() if 'Error in <' in line]
if p.returncode or root_errors:print((p.stdout+p.stderr)[-5000:],flush=True)
sys.exit(p.returncode or bool(root_errors))
