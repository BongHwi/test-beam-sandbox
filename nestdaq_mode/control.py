# SPDX-License-Identifier: LGPL-3.0-or-later
"""Control nestDAQ runs and drain participating devices before STOP.

Devices register members, arm against the current request, send their final data
on a new request and acknowledge its id, then remain silent until STOP. Drain
waits for acknowledgements and a settling interval; start, stop and status use
Redis device states and run numbers. The webctl hook invokes drain separately.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
from .redis_resp import Redis, RedisError

PREFIX = 'sandbox:drain:'
class Reject(Exception): pass
class Failure(Exception): pass

def compact(value): return json.dumps(value, separators=(',', ':'), ensure_ascii=False)
def devices(r):
    cursor = '0'; keys = set()
    while True:
        cursor, page = r.command('SCAN', cursor, 'MATCH', 'daq_service:*:*:fair-mq-state', 'COUNT', 1000)
        keys.update(page)
        if cursor == '0': break
    result = []
    for key in sorted(keys):
        parts = key.split(':')
        if len(parts) == 4:
            state = r.command('GET', key)
            if state is not None: result.append({'service':parts[1], 'instance':parts[2], 'state':state})
    return result

def members(r):
    return sorted(r.command('SMEMBERS', PREFIX+'members'))
def member_state(r, member):
    return r.command('GET', 'daq_service:'+member+':fair-mq-state') or 'absent'
def number(r, key):
    value = r.command('GET', 'run_info:'+key)
    try: return int(value) if value is not None else None
    except ValueError: return None

SESSION_PATH = Path('/tmp/session-run/nestdaq/session.json')

def positive(value):
    return value is not None and value.isascii() and value.isdigit() and int(value) > 0

def session_data():
    return json.loads(SESSION_PATH.read_text()) if SESSION_PATH.exists() else None

def data_path(run, session):
    if session is None: return None
    for runner in session['runners']:
        path = Path(runner['data'].replace('{run}', str(run)))
        if path.exists(): return path
    for path in sorted(Path(session['sink']['data_dir']).glob('*run%06d*' % run)):
        if path.is_file(): return path
    return None

def transition(r, cmd, goal, expected=()):
    r.command('PUBLISH', 'daqctl', compact({'command':'change_state','value':cmd,'services':['all'],'instances':['all']}))
    deadline = time.monotonic()+20
    while True:
        ds = devices(r)
        if ds and all(d['state']==goal for d in ds) and set(expected) <= {d['service'] for d in ds if d['state']==goal}: return
        if time.monotonic() >= deadline: raise Failure('transition '+cmd+' timed out; states: '+compact(ds))
        time.sleep(.1)

def drain(r, source):
    start = time.monotonic(); ms = members(r)
    states = [(m, member_state(r,m)) for m in ms]
    waiting = [m for m,s in states if s == 'RUNNING']
    not_running = [{'member':m,'state':s} for m,s in states if s != 'RUNNING']
    request = str(time.time_ns())+'-'+str(os.getpid())
    r.command('SET', PREFIX+'request', request)
    pending = set(waiting); done = set(); deadline = time.monotonic()+20
    while pending:
        for m in sorted(pending):
            if r.command('GET', PREFIX+'done:'+m) == request: done.add(m)
        pending.difference_update(done)
        if not pending or time.monotonic() >= deadline: break
        time.sleep(min(.1, max(0, deadline-time.monotonic())))
    elapsed = round(time.monotonic()-start, 3)
    settle = 300 if done else 0
    if settle: time.sleep(.3)
    result = {'request':request,'source':source,'members':ms,'done':sorted(done),'missing':sorted(pending),'not_running':not_running,'complete':not pending and not not_running,'elapsed_s':elapsed,'settle_ms':settle,'finished_unix_ns':time.time_ns()}
    r.command('SET', PREFIX+'last', compact(result))
    return result

def execute(r, args):
    if args.command == 'status':
        last = r.command('GET', PREFIX+'last')
        cursor = '0'; keys = []
        while True:
            cursor, page = r.command('SCAN', cursor, 'MATCH', 'sandbox:source:*', 'COUNT', 1000)
            keys.extend(page)
            if cursor == '0': break
        sources = sorted((json.loads(value) for key in keys if (value := r.command('GET', key))), key=lambda x: x['member'])
        return {'sources':sources,'run_number':number(r,'run_number'),'latest_run_number':number(r,'latest_run_number'),'devices':devices(r),'drain_members':[{'member':m,'state':member_state(r,m)} for m in members(r)],'drain_last':json.loads(last) if last else None}, 0
    if args.command == 'drain':
        result = drain(r, args.source)
        return result, 0 if result['complete'] else 3
    ds = devices(r)
    if args.command == 'prerun':
        if any(d['state'] == 'RUNNING' for d in ds): return {'skipped':'devices running'}, 0
        raw = r.command('GET', 'run_info:run_number')
        session = session_data()
        if raw is None:
            first, last = 1, 10001
        elif not positive(raw):
            latest = r.command('GET', 'run_info:latest_run_number')
            first = int(latest) if positive(latest) else 1
            last = first + 10000
        else:
            run = int(raw)
            path = data_path(run, session)
            if path is None: return {'run':run}, 0
            first, last = run + 1, run + 10000
        for run in range(first, last + 1):
            if data_path(run, session) is None:
                r.command('SET', 'run_info:run_number', run)
                r.command('SET', 'run_info:latest_run_number', run)
                result = {'run':run}
                if raw is not None:
                    result['moved_from'] = raw
                    result['reason'] = 'invalid run number' if not positive(raw) else f'run {int(raw)} already has data: {path}'
                return result, 0
        return {'error':'no free run number'}, 0
    if args.command == 'start':
        raw = r.command('GET', 'run_info:run_number')
        if raw is not None and not positive(raw): raise Reject('invalid run number')
        if not ds: raise Reject('no devices')
        session = session_data()
        expected = {s['service'] for s in session['sources']} | {session['sink']['service']} if session else set()
        missing = expected - {d['service'] for d in ds}
        if missing: raise Reject('devices missing: '+', '.join(sorted(missing)))
        states = {d['state'] for d in ds}
        if states not in ({'IDLE'}, {'READY'}): raise Reject('devices must all be IDLE or READY: '+compact(ds))
        run = int(raw) if raw is not None else 1
        path = data_path(run, session)
        if path is not None: raise Reject(f'run {run} already has data: {path}')
        if states == {'IDLE'}:
            transition(r,'CONNECT','DEVICE READY',expected); transition(r,'INIT TASK','READY',expected)
        run = int(raw) if raw is not None else 1
        if raw is None: r.command('SET','run_info:run_number',run)
        r.command('SET','run_info:latest_run_number',run)
        transition(r,'RUN','RUNNING',expected)
        return {'ok':True,'run':run}, 0
    if not ds or not any(d['state'] == 'RUNNING' for d in ds) or any(d['state'] not in ('RUNNING', 'READY') for d in ds): raise Reject('devices must be RUNNING or READY with at least one RUNNING: '+compact(ds))
    result = drain(r,'cli')
    transition(r,'STOP','READY')
    next_run = r.command('INCR','run_info:run_number')
    return {'ok':result['complete'],'run':number(r,'latest_run_number'),'next_run':next_run,'drain':result}, 0 if result['complete'] else 3

def qa():
    session = session_data() or {}
    port = session.get('dqm', {}).get('http_port', 8090)
    base = f'http://127.0.0.1:{port}'
    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return json.load(response)
    def labels(axis):
        return {int(x['fUniqueID']): x['fString'] for x in (axis.get('fLabels') or {}).get('arr', [])}
    hierarchy = get('/h.json')
    children = hierarchy.get('_childs', [])
    sensors = sorted(x['_name'] for x in children if any(c.get('_name') == 'hits_per_frame' for c in x.get('_childs', [])))
    result = {'dqm':'ok', 'reference':None, 'msensors':[], 'pairs':[]}
    for name in sensors:
        hits = get('/' + name + '/hits_per_frame/root.json')
        triggers = get('/' + name + '/triggers_per_frame/root.json')
        tiles = get('/' + name + '/tile_hits/root.json')
        axis = triggers['fXaxis']; bins = triggers['fArray'][1:-1]
        width = (axis['fXmax'] - axis['fXmin']) / axis['fNbins']
        result['msensors'].append({'name':name, 'frames':int(hits['fEntries']),
            'triggers':int(round(sum((axis['fXmin'] + (i + .5) * width) * value for i, value in enumerate(bins)))),
            'hits':int(sum(tiles['fArray']))})
    correlation = next ( (x for x in children if x.get('_name') == 'correlation'), {})
    if any(c.get('_name') == 'summary_counts' for c in correlation.get('_childs', [])):
        objects = {n:get('/correlation/summary_' + n + '/root.json') for n in ('counts','dcol','drow','drift','offset')}
        counts = objects['counts']; nx = counts['fXaxis']['fNbins']; names = labels(counts['fXaxis'])
        ref = labels(counts['fYaxis'])[2]
        if not ref.startswith('only in '): raise ValueError('invalid reference label')
        result['reference'] = ref[len('only in '):]
        for i in range(1, nx + 1):
            values = [int(counts['fArray'][i + (nx + 2) * j]) for j in range(1, 5)]
            result['pairs'].append(dict(zip(('msensor','matched','only_reference','only_msensor','skipped','dcol_px','drow_px','drift_ppm','offset_us'),
                [names[i], *values, *[float(objects[n]['fArray'][i]) for n in ('dcol','drow','drift','offset')]])))
    return result

def print_qa(result):
    print('reference: ' + (result['reference'] or 'none'))
    print('msensor frames triggers hits')
    for s in result['msensors']:
        print(f"{s['name']} {s['frames']} {s['triggers']} {s['hits']}")
    if not result['pairs']:
        print('no trigger pairs yet'); return
    print('msensor matched only_reference only_msensor skipped dcol_px drow_px drift_ppm offset_us')
    for p in result['pairs']:
        print(f"{p['msensor']} {p['matched']} {p['only_reference']} {p['only_msensor']} {p['skipped']} {p['dcol_px']:.2f} {p['drow_px']:.2f} {p['drift_ppm']:.3f} {p['offset_us']:.1f}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--redis-uri',default='tcp://127.0.0.1:6379/0')
    subs = parser.add_subparsers(dest='command',required=True)
    subs.add_parser('start'); subs.add_parser('stop'); subs.add_parser('prerun')
    subs.add_parser('drain').add_argument('--source',choices=('cli','webctl'),default='cli')
    subs.add_parser('status').add_argument('--json',action='store_true')
    subs.add_parser('qa').add_argument('--json',action='store_true')
    args = parser.parse_args()
    if args.command == 'qa':
        try:
            result = qa()
            if args.json: print(compact(result))
            else: print_qa(result)
            return 0
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            print('error: the DQM cannot be read (' + str(exc).replace('\n', ' ') + ')', file=sys.stderr)
            return 1
    try:
        result, code = execute(Redis(args.redis_uri), args)
        print(compact(result)); return code
    except Reject as exc:
        print('error: '+str(exc).replace('\n',' '), file=sys.stderr); return 2
    except (OSError, RedisError, ValueError, Failure) as exc:
        print('error: '+str(exc).replace('\n',' '), file=sys.stderr); return 1
if __name__ == '__main__': sys.exit(main())
