#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Run the public EventLoaderEUDAQ2 against an acquired native raw file."""
import json, os, pathlib, subprocess, sys
raw=pathlib.Path(sys.argv[1]).resolve(); expected=int(sys.argv[2]); work=raw.parent
geometry=work/'corry_geometry.conf'; geometry.write_text('''[MSENSOR_1]
type = "msensor"
number_of_pixels = 256, 128
pixel_pitch = 30um, 30um
position = 0um, 0um, 0um
orientation = 0deg, 0deg, 0deg
orientation_mode = "xyz"
spatial_resolution = 10um, 10um
material_budget = 0.001
time_resolution = 1ns
role = "reference"
''')
config=work/'corry_test.conf'; hist=work/'output'/'corry_test.root'
config.write_text(f'''[Corryvreckan]
log_level = "DEBUG"
detectors_file = "{geometry}"
histogram_file = "corry_test.root"
number_of_events = 2002
[Metronome]
event_length = 1ms
triggers = 1
[EventLoaderEUDAQ2]
name = "MSENSOR_1"
file_name = "{raw}"
''')
env=dict(os.environ,LD_PRELOAD='/opt/eudaq/lib/libeudaq_module_msensor.so')
r=subprocess.run(['corry','-c',str(config)],capture_output=True,text=True,env=env,timeout=120)
result={'status':'fail','expected_hits':expected,'exit_code':r.returncode,'stderr':r.stderr[-5000:],'stdout':r.stdout[-50000:]}
if r.returncode==0 and hist.exists():
    import ROOT
    f=ROOT.TFile.Open(str(hist))
    def walk(directory):
        found=[]
        for key in directory.GetListOfKeys():
            obj=key.ReadObj()
            if obj.InheritsFrom('TDirectory'): found+=walk(obj)
            elif obj.GetName()=='hitmap': found.append(float(obj.GetEntries()))
        return found
    values=walk(f); result['hitmap_entries']=values
    if len(values)==1 and values[0]==expected: result['status']='pass'
print(json.dumps(result)); sys.exit(0 if result['status']=='pass' else 1)
