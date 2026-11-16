# SPDX-License-Identifier: LGPL-3.0-or-later
"""Alignment results: read positions, compose the geometry QA uses, and check the device composition."""
import json
from pathlib import Path
import re

from session_setup.setupfile import geometry_fingerprint

UNITS = {'nm': 1e-6, 'um': 1e-3, 'mm': 1.0, 'cm': 10.0, 'm': 1000.0}


def sections(text):
    """Ordered (name, lines) of an analysis geometry."""
    result, name, lines = [], None, []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            if name is not None:
                result.append((name, lines))
            name, lines = stripped[1:-1], []
        elif name is not None:
            lines.append(line)
    if name is not None:
        result.append((name, lines))
    return result


def value(lines, key):
    for line in lines:
        if '=' in line and not line.strip().startswith('#') and line.split('=', 1)[0].strip() == key:
            return line.split('=', 1)[1].strip()
    return None


def millimetres(text):
    """Parse a corry length list such as '1.2mm,-2003um,60mm' into millimetres."""
    out = []
    for part in text.replace('"', '').split(','):
        match = re.fullmatch(r'\s*([-+0-9.eE]+)\s*([a-z]*)\s*', part)
        if not match:
            raise ValueError('cannot read length: ' + part)
        out.append(float(match.group(1)) * UNITS.get(match.group(2) or 'mm', 1.0))
    return out


def positions(text):
    """Position and orientation lines of every detector."""
    return {name: {'position': value(lines, 'position'), 'orientation': value(lines, 'orientation')}
            for name, lines in sections(text)}


def compose(nominal, aligned):
    """The nominal geometry with only the positions and orientations taken from an alignment."""
    out = []
    for name, lines in sections(nominal):
        out.append(f'[{name}]')
        for line in lines:
            key = line.split('=', 1)[0].strip() if '=' in line and not line.strip().startswith('#') else None
            if key in ('position', 'orientation') and aligned.get(name, {}).get(key):
                line = f'{key} = {aligned[name][key]}'
            out.append(line)
    return '\n'.join(out) + '\n'


def load(root):
    path = Path(root) / 'alignment' / 'alignment.json'
    return json.loads(path.read_text()) if path.is_file() else None


def matches(alignment, nominal):
    """Return a reason when an alignment does not fit this device composition, else None."""
    if not isinstance(alignment, dict) or alignment.get('format') != 'session-alignment/1':
        return 'the file is not an alignment result of scripts/align_session.sh'
    if alignment.get('fingerprint') != geometry_fingerprint(nominal):
        return ('the devices differ from those it was made for (a device was added, removed, moved or '
                'changed); align this session again')
    if set(alignment.get('positions', {})) != {name for name, _ in sections(nominal)}:
        return 'the detector names differ'
    return None
