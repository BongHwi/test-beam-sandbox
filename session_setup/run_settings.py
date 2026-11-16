# SPDX-License-Identifier: LGPL-3.0-or-later
"""Read and change the next-run configuration of a setup-based session."""
import configparser
from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile

from integration.layout import generate
from session_setup import setupfile
from session_setup.command_history import append

ITEMS = {'trigger': ('trg', 'veto', 'count'), 'run': ('events',), 'alpide': ('ithr', 'strobe_length'),
         'msensor': ('threshold_dac', 'readout', 'window_length_ns', 'strobe_delay_ns', 'strobe_length_ns')}
LAYOUT_ERROR = 'settings of a session started with --layout are changed in its files'


@contextmanager
def locked(path):
    with Path(path).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def setup(root):
    path = Path(root) / 'setup.json'
    if not path.exists():
        raise ValueError(LAYOUT_ERROR)
    return json.loads(path.read_text())


def read_conf(initial, path):
    result = copy.deepcopy(initial)
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path)
    result['run']['events'] = cfg.getint('RunControl', 'NEVENTS')
    if result.get('trigger'):
        for key in ITEMS['trigger']:
            result['trigger'][key] = cfg.get('Producer.TRIGGER_0', key)
    for d in result['devices']:
        kind = d['kind']
        name = setupfile.label(d)
        section = 'Producer.' + name
        if kind == 'alpide':
            d['ithr'] = cfg.getint(section, 'ITHR')
            d['strobe_length'] = cfg.getint(section, 'STROBE_LENGTH')
        else:
            d['readout'] = cfg.get(section, 'readout_mode')
            for key in ('threshold_dac', 'window_length_ns', 'strobe_delay_ns', 'strobe_length_ns'):
                if cfg.has_option(section, key):
                    text = cfg.get(section, key)
                    if key == 'threshold_dac' and text.startswith('['):
                        d[key] = json.loads(text)
                    else:
                        d[key] = int(text)
    return result


def visible(data):
    devices = []
    for d in sorted(data['devices'], key=lambda v: (('alpide','msensor').index(v['kind']),v['number'])):
        keys = ITEMS[d['kind']]
        if d['kind'] == 'msensor':
            keys = ('threshold_dac','readout','window_length_ns') if d['readout'] == 'continuous' else ('threshold_dac','readout','strobe_delay_ns','strobe_length_ns')
        devices.append({'section':f"{d['kind']} {d['number']}", 'producer':setupfile.label(d),
                        'settings':{k:d[k] for k in keys}})
    result = {'run':{'events':data['run']['events']},'devices':devices}
    if data.get('trigger'): result['trigger'] = {k:data['trigger'][k] for k in ITEMS['trigger']}
    return result


def changes(base, current):
    a,b=visible(base),visible(current)
    original={f"{d['kind']} {d['number']}":d for d in base['devices']}
    out=[]
    for d in b['devices']:
        source=original[d['section']]
        for key,value in d['settings'].items():
            old=source.get(key)
            if old != value:
                out.append({'section':d['section'],'item':key,'value':setupfile._text(value),'setup':setupfile._text(old)})
    if 'trigger' in b:
        for key,value in b['trigger'].items():
            old=a['trigger'][key]
            if old != value: out.append({'section':'trigger','item':key,'value':setupfile._text(value),'setup':setupfile._text(old)})
    if a['run']['events'] != b['run']['events']:
        out.append({'section':'run','item':'events','value':setupfile._text(b['run']['events']),'setup':setupfile._text(a['run']['events'])})
    return out


def settings(root):
    root=Path(root); original=setup(root)
    with locked(root/'a.conf.lock'):
        nxt=visible(read_conf(original,root/'a.conf'))
        try:
            state=json.loads((root/'status.json').read_text())
            number=state['run']['number']
        except (OSError,ValueError,KeyError,TypeError): number=None
        found=next((p for p in root.glob('run*.conf') if p.stem[3:].isdigit() and int(p.stem[3:])==number),None) if number is not None else None
        current=visible(read_conf(original,found)) if found else None
        if current is not None:current.update(run_number=number,file=found.name)
        return {'format':1,'next':nxt,'current':current}


def render_settings(doc):
    out=['Next run (a.conf):',f"  run events {doc['next']['run']['events']}"]
    for d in doc['next']['devices']:
        values=', '.join(f'{k} {setupfile._text(v)}' for k,v in d['settings'].items())
        out.append(f"  {d['section']} ({d['producer']}): {values}")
    if 'trigger' in doc['next']:
        out.append('  trigger: '+', '.join(f'{k} {v}' for k,v in doc['next']['trigger'].items()))
    cur=doc['current']
    if cur is None:out.append('Current run: none yet')
    else:
        old={'run':cur['run'],'devices':cur['devices']};nxt=doc['next'];differences=[]
        for a,b in zip(old['devices'],nxt['devices']):
            for key,val in a['settings'].items():
                v=b['settings'].get(key)
                if val!=v:differences.append(f"{a['section']} {key} {setupfile._text(val)} (next run: {setupfile._text(v)})")
        if 'trigger' in cur and 'trigger' in nxt:
            for key,val in cur['trigger'].items():
                if val != nxt['trigger'][key]: differences.append(f"trigger {key} {val} (next run: {nxt['trigger'][key]})")
        if old['run']['events'] != nxt['run']['events']:
            differences.append(f"run events {old['run']['events']} (next run: {nxt['run']['events']})")
        prefix=f"Current run {cur['run_number']} ({cur['file']})"
        out.append(prefix+(': same as the next run' if not differences else ' differs in: '+'; '.join(differences)))
    return '\n'.join(out)


def set_value(root, section, item, raw, ident):
    root=Path(root);initial=setup(root)
    kind=section.split()[0] if section else ''
    if kind not in ITEMS or item not in ITEMS[kind] or (kind=='run' and section!='run'):
        raise ValueError(f'{item} cannot be changed in a running session: change it in the setup file and start a new session')
    if kind == 'trigger' and not initial.get('scintillators'): raise ValueError('the setup has no scintillators')
    device=next((d for d in initial['devices'] if f"{d['kind']} {d['number']}" == section),None) if kind not in ('run','trigger') else None
    if kind not in ('run','trigger') and device is None:raise ValueError('no such device: '+section)
    with locked(root/'a.conf.lock'):
        current=read_conf(initial,root/'a.conf')
        selected=current['run'] if kind=='run' else current['trigger'] if kind=='trigger' else next(d for d in current['devices'] if f"{d['kind']} {d['number']}"==section)
        previous=setupfile._text(selected[item])
        sections=setupfile.written_sections(current)
        if item=='readout' and raw in ('continuous','triggered'):
            selected['readout']=raw
            sections=setupfile.written_sections(current)
        target=next(v for n,v in sections if n==section)
        import re
        if (re.search(r'[\r\n#;\[\]]',raw) if kind == 'trigger' else setupfile.STRUCTURE.search(raw)):
            spec = setupfile.RUN[item] if kind == 'run' else setupfile.TRIGGER[item] if kind == 'trigger' else setupfile.DEVICE[kind][item]
            raise RuntimeError(f'[{section}] {item} = {raw.strip()}: not accepted; expected {spec.accepted()}')
        target[item]=raw
        try:
            validated=setupfile.load_text(setupfile.sections_text(sections),'setup.ini')
        except setupfile.SetupError as exc:
            raise RuntimeError('\n'.join(exc.errors)) from exc
        chosen=validated['run'] if kind=='run' else validated['trigger'] if kind=='trigger' else next(d for d in validated['devices'] if f"{d['kind']} {d['number']}"==section)
        value=setupfile._text(chosen[item])
        if chosen[item] == selected[item] and item != 'readout':
            return f'Next run: {section} {item} {raw} (unchanged)'
        if item=='readout' and value==previous:
            return f'Next run: {section} {item} {raw} (unchanged)'
        order={(d['kind'],d['number']):i for i,d in enumerate(initial['devices'])}
        validated['devices'].sort(key=lambda d:order[(d['kind'],d['number'])])
        layout,options=setupfile.layout_and_options(validated)
        with tempfile.TemporaryDirectory(prefix='next-settings-') as tmp:
            generate(layout,tmp,mode='operator',options=options)
            data=(Path(tmp)/'a.conf').read_bytes()
        fd,tmp=tempfile.mkstemp(prefix='.a-conf-',dir=root)
        try:
            with os.fdopen(fd,'wb') as out:out.write(data)
            os.replace(tmp,root/'a.conf')
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        append(root,'set',ident,section=section,item=item,value=raw,previous=previous)
        try: state=json.loads((root/'status.json').read_text()).get('rc_state')
        except (OSError,ValueError): state=None
        advice=('RunControl has terminated the sequence; no further run will use it.' if state=='TERMINATED' else
                'Applies when RunControl configures the next run: when this run reaches its events, or after stop or repeat.')
        return f'Next run: {section} {item} {raw} (was {previous})\n{advice}'
