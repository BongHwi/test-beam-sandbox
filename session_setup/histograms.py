# SPDX-License-Identifier: LGPL-3.0-or-later
"""Read tracks per event and residual means from a Corryvreckan histogram file (JSON on stdout).

Usage: python -m session_setup.histograms FILE REFERENCE_NAMES DUT_NAMES (names comma-separated)
"""
import json
import sys

import ROOT

f = ROOT.TFile.Open(sys.argv[1])
references = [x for x in sys.argv[2].split(',') if x]
duts = [x for x in sys.argv[3].split(',') if x]


def stats(path, scale):
    h = f.Get(path)
    if not h:
        return None
    return {'entries': int(h.GetEntries()), 'mean_um': h.GetMean() * scale, 'rms_um': h.GetRMS() * scale}


per_event = f.Get('Tracking4D/tracksPerEvent')
out = {'tracks': int(f.Get('Tracking4D/clustersPerTrack').GetEntries()) if f.Get('Tracking4D/clustersPerTrack') else 0,
       'tracks_per_event': per_event.GetMean() if per_event and per_event.GetEntries() else 0.0,
       'events': int(per_event.GetEntries()) if per_event else 0, 'residuals': {}}
for name in references:
    # Tracking4D residuals are in mm.
    out['residuals'][name] = {axis: stats(f'Tracking4D/{name}/local_residuals/LocalResiduals{axis.upper()}', 1000.0)
                              for axis in ('x', 'y')}
for name in duts:
    # AnalysisDUT residuals are in micrometres.
    out['residuals'][name] = {axis: stats(f'AnalysisDUT/{name}/global_residuals/residuals{axis.upper()}', 1.0)
                              for axis in ('x', 'y')}
print(json.dumps(out))
