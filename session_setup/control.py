# SPDX-License-Identifier: LGPL-3.0-or-later
"""Operate beam or RunControl through the existing public sockets."""
import argparse
import configparser
import os
import signal
import time
from collections import deque
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import socket
import sys

root=Path('/tmp/session-run')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=('status','on','off','beam-status','stop','repeat','terminate','set','settings','end-producer','stall'))
parser.add_argument('rest',nargs='*')
parser.add_argument('--json',action='store_true',help='emit the existing machine-readable beam response')
parser.add_argument('--id',help='id of an on, off, stop, repeat or terminate request (letters, digits, ., -, _; at most 64)')
args=parser.parse_args();action=args.action
if (root/'daq-mode').is_file() and (root/'daq-mode').read_text().strip() == 'nestdaq' and action not in ('on','off','beam-status'):
    print('error: this command is for the EUDAQ mode; in the nestDAQ mode use: scripts/session_control.sh NAME nestdaq start|stop|status [--json]',file=sys.stderr);raise SystemExit(2)
# Run requests of the RunControl control socket, with the id each one carries by default.
RUN_INTENTS={'stop':('stop_next','operator-stop'),'repeat':('repeat','operator-repeat'),'terminate':('terminate','operator-terminate')}
if args.id is not None and action not in (*RUN_INTENTS, 'on', 'off', 'set', 'end-producer', 'stall'):
    print('--id applies only to on, off, stop, repeat, terminate, set, end-producer and stall',file=sys.stderr);raise SystemExit(2)
if args.id is not None and not re.fullmatch(r'[A-Za-z0-9._-]{1,64}',args.id):
    print('request not sent: --id must be 1 to 64 letters, digits, ., - or _',file=sys.stderr);raise SystemExit(2)
if action in ('set','settings'):
    from session_setup import run_settings
    try:
        if action == 'settings':
            if args.rest or args.id is not None: parser.error('invalid settings arguments')
            data=run_settings.settings(root)
            print(json.dumps(data,separators=(',',':')) if args.json else run_settings.render_settings(data))
        else:
            if args.json: parser.error('--json does not apply to set')
            parts=args.rest
            if len(parts)==3 and parts[0] in ('run','trigger'): section,item,value=parts
            elif len(parts)==4 and parts[0] in ('alpide','msensor'):
                section=' '.join(parts[:2]);item,value=parts[2:]
            else: parser.error('invalid set arguments')
            print(run_settings.set_value(root,section,item,value,args.id or 'operator-set'))
    except ValueError as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(2)
    except RuntimeError as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
    raise SystemExit(0)
if action=='end-producer':
    if args.json or len(args.rest)!=1: parser.error('invalid end-producer arguments')
    from session_setup.command_history import append
    producer=args.rest[0]
    cfg=configparser.ConfigParser(interpolation=None);cfg.read(root/'init.ini')
    names=[x.strip() for key in ('dataproducers','moreproducers') for x in cfg.get('RunControl',key,fallback='').split(',') if x.strip()]
    if producer not in names:
        print(f"unknown producer: {producer}; producers of this session: "+', '.join(names),file=sys.stderr);raise SystemExit(2)
    targets=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) in (os.getpid(), os.getppid()):continue
        try:
            cmd=(proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if any(cmd[i]=='--name' and cmd[i+1]==producer for i in range(len(cmd)-1)):
                targets.append(int(proc.name))
        except (OSError,ProcessLookupError):pass
    if not targets:
        print(producer+' is not running');raise SystemExit(1)
    def alive(pid):
        try:
            stat=Path(f'/proc/{pid}/stat').read_text();return stat.rsplit(')',1)[1].strip().split()[0]!='Z'
        except (OSError,IndexError):return False
    result=0
    for pid in sorted(targets):
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:continue
        deadline=time.monotonic()+5
        while alive(pid) and time.monotonic()<deadline:time.sleep(.1)
        used='SIGTERM'
        if alive(pid):
            used='SIGKILL'
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
            deadline=time.monotonic()+2
            while alive(pid) and time.monotonic()<deadline:time.sleep(.1)
        ended=not alive(pid)
        if not ended:
            print(f'{producer} (process {pid}) is still running after SIGKILL.');result=1
        elif used=='SIGKILL':print(f'{producer} (process {pid}) did not end within 5 s after SIGTERM; ended it with SIGKILL.')
        else:print(f'Ended {producer} (process {pid}) with SIGTERM.')
        append(root,'end_producer',args.id or 'operator-end-producer',producer=producer,pid=pid,signal=used,ended=ended)
    print('RunControl shows it as disconnected. Next steps: "A producer stops responding" in docs/operator-screen.md.')
    raise SystemExit(result)
if action=='stall':
    from msensor import stall as stall_mod
    from session_setup.command_history import append
    if args.json or len(args.rest)!=2: parser.error('usage: stall PRODUCER SECONDS|cancel')
    producer,length=args.rest
    try:seconds=0.0 if length=='cancel' else float(length)
    except ValueError:seconds=-1.0
    if not 0<=seconds<=stall_mod.MAX_SECONDS or (seconds==0 and length!='cancel'):
        print(f'request not sent: SECONDS must be above 0 and at most {stall_mod.MAX_SECONDS}, or "cancel"',file=sys.stderr);raise SystemExit(2)
    cfg=configparser.ConfigParser(interpolation=None);cfg.read(root/'init.ini')
    names=[x.strip() for key in ('dataproducers','moreproducers') for x in cfg.get('RunControl',key,fallback='').split(',') if x.strip()]
    msensors=[n for n in names if n.startswith('MSENSOR_')]
    if producer not in msensors:
        print(f"unknown or unsupported producer: {producer}; msensor producers of this session: "+(', '.join(msensors) or 'none'),file=sys.stderr);raise SystemExit(2)
    try:status=json.loads((root/'status.json').read_text())
    except (OSError,ValueError):print('RunControl status is not available',file=sys.stderr);raise SystemExit(1)
    entry=next((c for c in status.get('connections',[]) if c['name']==producer),None)
    if status.get('rc_state')!='RUNNING' or entry is None or not entry['connected'] or entry['state']!='RUNNING':
        print(f'request not sent: {producer} must be connected and RUNNING in a running run',file=sys.stderr);raise SystemExit(1)
    run=status['run']['number']
    try:previous=json.loads((root/'stall'/(producer+'.state.json')).read_text())
    except (OSError,ValueError):previous={}
    if seconds>0 and previous.get('state')=='active' and previous.get('run')==run:
        print(f'request not sent: {producer} is already stalled (id {previous.get("id")}); cancel it first',file=sys.stderr);raise SystemExit(1)
    ident=args.id or ('operator-stall' if seconds>0 else 'operator-stall-cancel')
    answer=stall_mod.submit(root,producer,seconds,run,ident)
    if answer is None:
        print(f'{producer} did not answer within 5 s; no stall started. The producer may be an older build or not stalled in this run.',file=sys.stderr);raise SystemExit(1)
    if seconds==0:
        print(f'Stall of {producer} cancelled' if answer.get('state')=='ended' else f'Nothing to cancel: {answer.get("reason","no stall in progress")}');raise SystemExit(0 if answer.get('state')=='ended' else 1)
    if answer.get('state')!='active':
        print(f'Stall of {producer} not started: {answer.get("reason","rejected")}',file=sys.stderr);raise SystemExit(1)
    append(root,'stall',ident,producer=producer,run=run,seconds=seconds,started_monotonic_ns=answer.get('started_monotonic_ns'))
    print(f'{producer} stalled for {length} s in run {run}; it keeps answering and continues in the same run.')
    raise SystemExit(0)
if action=='status':
    print((root/'status.json').read_text());raise SystemExit(0)
if action in ('on','off','beam-status'):
    from session_core.beamctl import main
    sys.argv=['beamctl','--socket',str(root/'source.sock'),'status' if action=='beam-status' else action]
    if action in ('on','off'):sys.argv.extend(['--id',args.id if args.id is not None else 'operator-'+action])
    if args.json:raise SystemExit(main())
    captured=io.StringIO()
    with redirect_stdout(captured):code=main()
    response=json.loads(captured.getvalue())
    if not response.get('accepted'):
        print('Beam request rejected: '+str(response.get('reason','unknown reason')))
        raise SystemExit(code)
    status=response['status'];print('Beam: '+('on' if status['beam_on'] else 'off'))
    log=root/'source.sock.beam.log';recent=deque(maxlen=5);previous=0
    # A change made by this command is reported from its own accepted reply,
    # so it is always the last line even if the history read misses it.
    changed=action in ('on','off') and status.get('origin')=='command' and status.get('last_changed_utc')==status.get('actual_utc')
    if log.exists():
        with log.open(encoding='utf-8') as stream:
            for line in stream:
                try:record=json.loads(line)
                except ValueError:continue
                seq=record.get('seq',0)
                if seq>previous and (not changed or seq<status['seq']):
                    recent.append(record)
                previous=max(previous,seq)
    if changed:recent.append(status)
    print('Recent beam changes (UTC):')
    if not recent:print('  none')
    for row in recent:print('  '+row['actual_utc']+'  '+('on' if row['beam_on'] else 'off'))
    print('Full beam history: '+str(log)+' (inside the session container)')
    raise SystemExit(code)
if action in RUN_INTENTS:
    intent,ident=RUN_INTENTS[action]
    request={'id':args.id if args.id is not None else ident,'intent':intent}
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(5);s.connect(str(root/'control.sock'))
        s.sendall((json.dumps(request,separators=(',',':'))+'\n').encode())
        response=json.loads(s.recv(4096))
    print(json.dumps(response))
    raise SystemExit(0 if response.get('accepted') else 1)
