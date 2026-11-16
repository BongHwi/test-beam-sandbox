# SPDX-License-Identifier: LGPL-3.0-or-later
"""Evaluate scintillator crossings along the session's unscattered beam paths."""
from integration.trigger_logic import clock_cycle, parse


class TriggerDecision:
    def __init__(self, geometry, source_z, busy_ps):
        self.plates = geometry['scintillators']
        self.trg = parse(geometry['trg'])
        self.veto = parse(geometry['veto'], allow_empty=True)
        self.source_z = source_z
        self.busy_ps = busy_ps
        self.last_trigger = self.last_veto = None
        self.number = 0

    def decide(self, time_ps, trace):
        x, y, tx, ty, _ = trace['source_state']
        fired = []
        for plate in self.plates:
            px, py, z = plate['position_mm']
            dx, dy = x + tx * (z - self.source_z) - px, y + ty * (z - self.source_z) - py
            size, hole = plate['size_mm'], plate['hole_mm']
            if plate['shape'] == 'disc':
                r2 = dx * dx + dy * dy
                inside = r2 <= (size[0] / 2) ** 2
                excluded = hole[0] > 0 and r2 < (hole[0] / 2) ** 2
            else:
                inside = abs(dx) <= size[0] / 2 and abs(dy) <= size[1] / 2
                excluded = all(v > 0 for v in hole) and abs(dx) < hole[0] / 2 and abs(dy) < hole[1] / 2
            if inside and not excluded: fired.append(plate['channel'])
        dt_trg = clock_cycle(time_ps) - clock_cycle(self.last_trigger) if self.last_trigger is not None else 2**62
        dt_veto = clock_cycle(time_ps) - clock_cycle(self.last_veto) if self.last_veto is not None else 2**62
        busy = self.last_trigger is not None and time_ps - self.last_trigger < self.busy_ps
        accepted = self.trg.evaluate(fired, dt_trg, dt_veto, busy)
        vetoed = self.veto.evaluate(fired, dt_trg, dt_veto, busy)
        if accepted:
            self.last_trigger = time_ps
            number = self.number
            self.number += 1
        else:
            number = None
        if vetoed: self.last_veto = time_ps
        return {'time_ps': time_ps, 'fired': fired, 'trigger': accepted,
                'reason': None if accepted else 'condition', 'trigger_number': number,
                'source_state': trace['source_state'], 'dt_trg': dt_trg, 'dt_veto': dt_veto,
                'bsy': busy, 'veto': vetoed}
