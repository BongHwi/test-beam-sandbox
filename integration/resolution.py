# SPDX-License-Identifier: LGPL-3.0-or-later
"""Estimate straight-line track and residual resolutions in the session geometry."""
import math

# Match the constants in tracksim/src/tracksim/beam.py.
ELECTRON_MASS_GEV = 0.00051099895
SILICON_X0_MM = 93.70
AIR_X0_MM = 303900.0
# The generated session geometry uses 40 um active and 60 um passive silicon.
PLANE_SILICON_UM = 100
PLANE_X0 = PLANE_SILICON_UM / 1000 / SILICON_X0_MM
# The scattering coefficient accepts at most 0.02 * cos(0.05) X0.
BUDGET_LIMIT = 0.02 * math.cos(0.05)


def intrinsic_um(kind, pitch_um, ithr=0):
    if kind == 'msensor':
        return [p / math.sqrt(12) for p in pitch_um]
    if kind != 'alpide':
        raise ValueError('unknown device kind: ' + str(kind))
    cm = 2 - min(max(ithr, 0), 255) / 255
    return [math.sqrt(((p - 2 * (p * (math.sqrt(cm) - 1) / 2)) ** 3 +
                       (2 * (p * (math.sqrt(cm) - 1) / 2)) ** 3) / (12 * p)) for p in pitch_um]


def material_budget(devices, source_z_mm):
    if not devices:
        return 0.0
    return len(devices) * PLANE_X0 + (max(d['position_mm'][2] for d in devices) - source_z_mm) / AIR_X0_MM


def estimate(devices, source_z_mm, energy_gev, scattering=True):
    refs = [d for d in devices if d['kind'] == 'alpide' or d.get('role') == 'reference']
    duts = [d for d in devices if d['kind'] == 'msensor' and d.get('role', 'dut') == 'dut']
    if len(refs) < 2 or not duts:
        return []
    kicks = []
    if scattering:
        mass = ELECTRON_MASS_GEV
        momentum = math.sqrt(energy_gev ** 2 + 2 * energy_gev * mass)
        beta = momentum / (energy_gev + mass)
        total = material_budget(devices, source_z_mm)
        coefficient = (13.6 / (beta * momentum * 1000) * (1 + 0.038 * math.log(total / beta ** 2))) ** 2
        kicks = [(d['position_mm'][2], coefficient * PLANE_X0) for d in devices]
        last = max(d['position_mm'][2] for d in devices)
        count = max(1, math.ceil(last - source_z_mm))
        length = (last - source_z_mm) / count
        kicks.extend((source_z_mm + (i + 0.5) * length, coefficient * length / AIR_X0_MM) for i in range(count))
    output = []
    for dut in duts:
        z = dut['position_mm'][2]
        track = []
        residual = []
        for axis in (0, 1):
            sigma = [intrinsic_um(d['kind'], d['pitch_um'], d.get('ithr', 0))[axis] for d in refs]
            # Centre z on the DUT to avoid cancellation in the 2x2 fit.
            dz = [d['position_mm'][2] - z for d in refs]
            weights = [1 / s ** 2 for s in sigma]
            s0 = sum(weights); s1 = sum(w * x for w, x in zip(weights, dz)); s2 = sum(w * x * x for w, x in zip(weights, dz))
            determinant = s0 * s2 - s1 * s1
            if determinant <= 0:
                continue
            g = [w * (s2 - s1 * x) / determinant for w, x in zip(weights, dz)]
            variance = sum((v * s) ** 2 for v, s in zip(g, sigma))
            # Algebraically identical to g C_ref g^T + C_DD - 2 g C_ref,D.
            variance += sum(theta2 * (sum(weight * max(d['position_mm'][2] - zk, 0) for weight, d in zip(g, refs)) - max(z - zk, 0)) ** 2 * 1e6 for zk, theta2 in kicks)
            track.append(math.sqrt(max(variance, 0)))
            intrinsic = intrinsic_um(dut['kind'], dut['pitch_um'], dut.get('ithr', 0))[axis]
            residual.append(math.sqrt(max(variance, 0) + intrinsic ** 2))
        if len(track) == 2:
            output.append({'label': f'{dut["kind"]} {dut["number"]}', 'kind': dut['kind'], 'number': dut['number'],
                           'z_mm': z, 'track_um': track, 'residual_um': residual})
    return output
