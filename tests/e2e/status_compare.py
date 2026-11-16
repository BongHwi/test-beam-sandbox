# SPDX-License-Identifier: LGPL-3.0-or-later
"""Project immutable raw status events, retaining BORE and EORE."""
class StatusProjection:
    def __init__(self):
        self.status = {}
        self.excluded = {}

    def observe(self, event):
        name = event.GetDescription()
        if not name.endswith('_status'):
            return
        self.excluded.setdefault(name, 0)
        if not event.IsBORE() and not event.IsEORE():
            rows = self.status.get(name, [])
            if len(rows) != 1 or not rows[0]['bore']:
                raise ValueError('intermediate status outside BORE/EORE for ' + name)
            self.excluded[name] += 1
            return
        tags = dict(event.GetTags())
        for key in ('Time', 'stream_id', 'stream_file', 'stream_file_sha256', 'stream_file_bytes'):
            tags.pop(key, None)
        self.status.setdefault(name, []).append({'bore': event.IsBORE(), 'eore': event.IsEORE(), 'tags': tags})

    def finish(self, event_names):
        expected = {('MSENSOR_0_status' if x == 'msensor' else x + '_status') for x in event_names}
        if set(self.status) != expected:
            raise ValueError('missing or unexpected BORE/EORE producer status')
        for name in sorted(expected):
            rows = self.status[name]
            if len(rows) != 2 or not rows[0]['bore'] or rows[0]['eore'] or rows[1]['bore'] or not rows[1]['eore']:
                raise ValueError('missing, duplicated or unordered BORE/EORE for ' + name)
            counter = 'triggers_total' if name == 'MSENSOR_0_status' else 'Event'
            if counter not in rows[1]['tags']:
                raise ValueError('EORE data event count absent for ' + name)
            try:
                if int(rows[1]['tags'][counter]) < 0:raise ValueError('negative count')
            except (TypeError, ValueError):
                raise ValueError('invalid EORE data event count for ' + name)
        return self.status, self.excluded


def comparable(record):
    return {key: record[key] for key in ('events', 'stream', 'status')}
