# SPDX-License-Identifier: LGPL-3.0-or-later
"""Launch an isolated nestDAQ session and its msensor runners."""
import configparser
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

import msensor

from nestdaq_mode.redis_resp import Redis

BOARD_FEM_ID = 1000
STRINGS = {'readout_mode', 'stream_dir', 'clock_mode', 'source', 'response_file', 'EUDAQ_DC', 'EUDAQ_ID'}


def runner_configs(root, n, setup):
    """Write the runner configurations; return the msensor runners, all runners, the frame length and the periodic trigger."""
    frame_ns = setup['nestdaq']['frame_ms'] * 1000000
    input_mode = setup['nestdaq']['input']
    period_clk = setup['nestdaq'].get('periodic_clk') if setup['nestdaq'].get('trigger_board') == 'on' else None
    runners = []
    for dev in sorted((d for d in setup['devices'] if d['kind'] == 'msensor'), key=lambda d: 'MSENSOR_' + str(d['number'])):
        name = 'MSENSOR_' + str(dev['number'])
        device = {}
        for filename in (name + '.ini', name + '-a.conf'):
            config = configparser.ConfigParser(interpolation=None)
            config.optionxform = str
            with (root / 'devices' / filename).open() as stream: config.read_file(stream)
            for key, value in config['Producer.' + name].items():
                device[key] = value if key in STRINGS else json.loads(value)
        for key in ('EUDAQ_DC', 'EUDAQ_ID', 'stream_dir'): device.pop(key, None)
        response = Path(device['response_file'])
        if not response.is_absolute(): device['response_file'] = str(root / response)
        if dev.get('noise', 'off') == 'on':
            device['response_file'] = str(Path(msensor.__file__).with_name('response_default.json'))
        path = n / (name + '.runner.json')
        config = {'format': 'msensor-runner-config-1', 'name': name, 'device': device,
                  'frame_ns': frame_ns, 'anchor_windows': 1024 if device['readout_mode'] == 'continuous' else None,
                  'output_dir': str(n), 'output': 'file', 'socket_port': None,
                  'send_buffer_bytes': None, 'socket_sndbuf_bytes': None}
        offset = dev.get('clock_offset_ns', 0)
        drift = round(dev.get('clock_drift_ppm', 0) * 1000)
        if offset or drift:
            config['clock'] = {'offset_ns': offset, 'drift_ppb': drift}
        if dev.get('missed_triggers', []):
            config['missed_triggers'] = dev['missed_triggers']
        if dev.get('noise', 'off') == 'on':
            config['noise'] = 'fast'
        if period_clk:
            config['periodic_clk'] = period_clk
        if input_mode == 'socket':
            config.update(output='socket', socket_port=18600 + dev['number'],
                          send_buffer_bytes=setup['nestdaq']['send_buffer_mb'] * 1048576)
        path.write_text(json.dumps(config, indent=2) + '\n')
        runners.append({'name': name, 'readout_mode': device['readout_mode'], 'config': str(path),
                        'control': str(n / (name + '.ctl')), 'status': str(n / (name + '.status.json')),
                        'data': str(n / (name + '_run{run}.msraw')), 'fem_id': device['plane']})
    all_runners = list(runners)
    if period_clk:
        name = 'TRIGGERBOARD_0'
        path = n / (name + '.runner.json')
        path.write_text(json.dumps({'format': 'board-runner-config-1', 'name': name, 'plane': BOARD_FEM_ID,
                                    'frame_ns': frame_ns, 'period_clk': period_clk, 'output_dir': str(n)}, indent=2) + '\n')
        all_runners.append({'name': name, 'readout_mode': 'board', 'config': str(path), 'control': str(n / (name + '.ctl')),
                            'status': str(n / (name + '.status.json')), 'data': str(n / (name + '_run{run}.msraw')),
                            'fem_id': BOARD_FEM_ID})
    return runners, all_runners, frame_ns, input_mode, period_clk


def launch(root, geometry_path, env):
    root = Path(root).resolve()
    geometry_path = Path(geometry_path)
    geometry = json.loads(geometry_path.read_text())
    participants = [p for p in geometry['participants'] if p['serial'].startswith('MSENSOR_')]
    geometry['participants'] = participants
    if json.loads((root / 'setup.json').read_text())['nestdaq'].get('trigger_board') == 'on':
        geometry['trigger_board'] = True
    geometry_path.write_text(json.dumps(geometry, indent=2) + '\n')
    active = root / 'active-geometry.json'
    if geometry_path != active:
        public = json.loads(active.read_text())
        public['participants'] = participants
        active.write_text(json.dumps(public, indent=2) + '\n')
    setup = json.loads((root / 'setup.json').read_text())
    n = root / 'nestdaq'
    n.mkdir(exist_ok=True)
    (n / 'logs').mkdir(exist_ok=True)
    (n / 'data').mkdir(exist_ok=True)
    shutil.copytree(Path(__file__).parent / 'slowdash', n / 'slowdash')
    runners, all_runners, frame_ns, input_mode, period_clk = runner_configs(root, n, setup)
    config_dir = n / 'slowdash' / 'config'
    config_dir.mkdir(exist_ok=True)
    panels = []
    for runner in runners:
        for metric in ('hits_per_frame', 'triggers_per_frame'):
            panels.append({'type': 'timeaxis', 'plots': [{'type': 'timeseries',
                'channel': 'ts:msensor:' + runner['name'] + ':' + metric,
                'color': '#009090', 'label': '', 'format': '%.5g', 'opacity': 1,
                'marker_type': 'circle', 'marker_size': 2, 'line_width': 1,
                'line_type': 'connect', 'fill_opacity': 0, 'fill_baseline': 1e-100}],
                'axes': {'xfixed': False, 'yfixed': False, 'xlog': False, 'ylog': False,
                         'title': runner['name'] + ' ' + metric, 'zlog': False},
                'legend': {'style': 'side'}})
    layout = {'meta': {'name': 'msensor', 'title': 'msensor frames', 'description': ''},
              'control': {'range': {'length': 300}, 'reload': 2,
                          'grid': {'rows': len(runners), 'columns': 2}, 'mode': 'normal'},
              'style': {}, 'panels': panels}
    (config_dir / 'slowplot-msensor.json').write_text(json.dumps(layout, indent=2) + '\n')
    subprocess.Popen([sys.executable, '-m', 'session_core.server', '--geometry', str(geometry_path),
                      '--socket', str(root / 'source.sock')], env=env, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while not (root / 'source.sock').exists():
        if time.monotonic() >= deadline: raise SystemExit('session core did not start')
        time.sleep(.05)
    tmux_env = dict(env)
    tmux_env.pop('SESSION_SOURCE_CONFIG_JSON', None)
    def tmux(*args):
        return subprocess.run(['tmux', *args], env=tmux_env, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()
    tmux('new-session', '-d', '-s', 'NESTDAQ', '-n', 'redis', '-x', '200', '-y', '50', '-c', str(n))
    for window in ('webctl', 'slowdash', 'runners', 'sources', 'sink', 'dqm'):
        tmux('new-window', '-t', 'NESTDAQ', '-n', window, '-c', str(n))
    commands = {
        'redis': ['/opt/nestdaq/bin/redis-server', '--bind', '127.0.0.1', '--port', '6379', '--save', '', '--appendonly', 'no', '--loadmodule', '/opt/nestdaq/lib/redis/modules/redistimeseries.so'],
        'webctl': ['/opt/nestdaq/bin/daq-webctl', '--http-uri', 'http://127.0.0.1:8080', '--redis-uri', 'tcp://127.0.0.1:6379', '--color', 'false', '--pre-run', '/opt/public-work/nestdaq_mode/pre-run.sh', '--pre-stop', '/opt/public-work/nestdaq_mode/pre-stop.sh'],
        'slowdash': ['/opt/slowdash/bin/slowdash', '--project-dir', str(n / 'slowdash'), '--port', '18881'],
    }
    log_lines = []
    def send(target, title, args, log):
        command = ' '.join(shlex.quote(x) for x in args)
        log_lines.append(title + ': ' + command)
        tmux('send-keys', '-t', target, command + ' 2>&1 | tee -a ' + shlex.quote(str(n / 'logs' / log)), 'Enter')
    def wait(label, seconds, predicate, logs):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if predicate(): return
            except Exception: pass
            time.sleep(.1)
        tail = []
        for log in logs:
            path = n / 'logs' / log
            if path.exists(): tail.extend(path.read_text(errors='replace').splitlines()[-20:])
        raise SystemExit(label + ' did not become ready\n' + '\n'.join(tail))
    def ping():
        client = Redis('tcp://127.0.0.1:6379/0')
        try: return client.command('PING') == 'PONG'
        finally: client.file.close(); client.sock.close()
    def http(port):
        with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/', timeout=1) as response: return response.status == 200
    send('NESTDAQ:redis', 'redis', commands['redis'], 'redis.log')
    wait('redis', 10, ping, ['redis.log'])
    send('NESTDAQ:webctl', 'webctl', commands['webctl'], 'webctl.log')
    wait('webctl', 20, lambda: http(8080), ['webctl.log'])
    send('NESTDAQ:slowdash', 'slowdash', commands['slowdash'], 'slowdash.log')
    wait('slowdash', 30, lambda: http(18881), ['slowdash.log'])
    for i, runner in enumerate(all_runners):
        if i:
            pane = tmux('split-window', '-P', '-F', '#{pane_id}', '-t', 'NESTDAQ:runners', '-c', str(n))
            tmux('select-layout', '-t', 'NESTDAQ:runners', 'tiled')
        else: pane = tmux('display-message', '-p', '-t', 'NESTDAQ:runners', '#{pane_id}')
        tmux('select-pane', '-t', pane, '-T', runner['name'])
        args = [sys.executable, '-m', 'msensor.runner', '--config', runner['config']]
        rate = setup['nestdaq'].get('generator_mb_s', 0)
        if runner['readout_mode'] == 'board':
            args = [sys.executable, '-m', 'nestdaq_mode.board_runner', '--config', runner['config']]
        elif rate > 0:
            args = [sys.executable, '-m', 'nestdaq_mode.generator', '--mb-s', str(rate), '--config', runner['config']]
        command = ' '.join(shlex.quote(x) for x in args)
        log = shlex.quote(str(n / 'logs' / (runner['name'] + '.log')))
        loop = ('while true; do rm -f ' + shlex.quote(runner['control']) + '; ' + command +
                ' 2>&1 | tee -a ' + log + "; echo 'runner exited; restarting in 2 s' | tee -a " + log + '; sleep 2; done')
        log_lines.append('runners: ' + loop)
        tmux('send-keys', '-t', pane, loop, 'Enter')
    (root / 'launcher.log').write_text('\n'.join(log_lines) + '\n')
    wait('runners', 15, lambda: all(json.loads(Path(r['status']).read_text())['state'] == 'idle' for r in all_runners), [r['name'] + '.log' for r in all_runners])
    client = Redis('tcp://127.0.0.1:6379/0')
    sources = [{'name': r['name'], 'service': ('board-source-' if r['readout_mode'] == 'board' else 'msensor-source-') + r['name'],
                'fem_id': r['fem_id'], 'input': 'file'} for r in all_runners]
    if input_mode == 'socket':
        for source, runner in zip(sources, runners):
            source.update(input='socket', port=18600 + int(runner['name'].split('_')[-1]))
    for source in sources:
        service = source['service']
        client.command('HSET', 'daq_service:topology:endpoint:' + service + ':out',
                       'type', 'push', 'method', 'bind', 'autoSubChannel', 'false')
        client.command('SET', 'daq_service:topology:link:' + service + ':out,FileSink:in', 'none')
        if service.startswith('board-source-'):
            continue
        client.command('HSET', 'daq_service:topology:endpoint:' + service + ':dqm',
                       'type', 'push', 'method', 'bind', 'autoSubChannel', 'false', 'sndBufSize', '4', 'rcvBufSize', '4')
        client.command('SET', 'daq_service:topology:link:' + service + ':dqm,MSensorDqm:in', 'none')
    client.command('HSET', 'daq_service:topology:endpoint:FileSink:in',
                   'type', 'pull', 'method', 'connect', 'autoSubChannel', 'true')
    client.command('HSET', 'daq_service:topology:endpoint:MSensorDqm:in',
                   'type', 'pull', 'method', 'connect', 'autoSubChannel', 'true', 'sndBufSize', '4', 'rcvBufSize', '4')
    common = ['-S', '</opt/nestdaq/lib', '-P', 'daq_service', '-P', 'parameter_config', '-P', 'metrics',
              '--parameter-config-uri', 'tcp://127.0.0.1:6379/2', '--metrics-uri', 'tcp://127.0.0.1:6379/1',
              '--proc-stat-update-interval', '1000', '--host-ip', '127.0.0.1', '--severity', 'info']
    for i, (runner, source) in enumerate(zip(all_runners, sources)):
        if i:
            pane = tmux('split-window', '-P', '-F', '#{pane_id}', '-t', 'NESTDAQ:sources', '-c', str(n))
            tmux('select-layout', '-t', 'NESTDAQ:sources', 'tiled')
        else: pane = tmux('display-message', '-p', '-t', 'NESTDAQ:sources', '#{pane_id}')
        tmux('select-pane', '-t', pane, '-T', runner['name'])
        send(pane, 'sources', ['/opt/nestdaq/bin/' + ('MSensorStreamServer' if source['input'] == 'socket' else 'MSensorFileSource'), *common,
             '--service-name', source['service'], '--runner-control', runner['control'],
             *(['--listen-port', str(source['port'])] if source['input'] == 'socket' else ['--runner-data', runner['data']]), '--fem-id', str(source['fem_id']),
             '--drain-redis-uri', 'tcp://127.0.0.1:6379/0'], 'source-' + runner['name'] + '.log')
    send('NESTDAQ:sink', 'sink', ['/opt/nestdaq/bin/FileSink', *common, '--multipart', 'false',
         '--openmode', 'create', '--prefix', str(n / 'data') + '/'], 'sink.log')
    send('NESTDAQ:dqm', 'dqm', ['/opt/nestdaq/bin/MSensorDqm', *common, '--service-name', 'MSensorDqm', *(['--reference-fem', str(min((d for d in setup['devices'] if d['kind'] == 'msensor'), key=lambda d: d['position_mm'][2])['plane'])] if len(runners) > 1 else [])], 'dqm.log')
    def ready():
        cursor = '0'; keys = []
        while True:
            cursor, page = client.command('SCAN', cursor, 'MATCH', 'daq_service:*:*:fair-mq-state', 'COUNT', 1000)
            keys.extend(page)
            if cursor == '0': break
        return len(keys) == len(sources) + 2 and all(client.command('GET', k) == 'IDLE' for k in keys)
    wait('devices', 30, ready, ['sink.log'] + ['source-' + r['name'] + '.log' for r in all_runners])
    wait('dqm HTTP', 30, lambda: http(8090), ['dqm.log'])
    client.file.close(); client.sock.close()
    (root / 'launcher.log').write_text('\n'.join(log_lines) + '\n')
    session = {'format': 'sandbox-nestdaq-session-1', 'input': input_mode, 'frame_ns': frame_ns,
               'output_dir': str(n), 'redis': 'tcp://127.0.0.1:6379/0', 'webctl_port': 8080,
               'slowdash_port': 18881, 'runners': all_runners, 'sources': sources,
               'sink': {'service': 'FileSink', 'data_dir': str(n / 'data')},
               'dqm': {'service': 'MSensorDqm', 'http_port': 8090}}
    if period_clk:
        session['trigger_board'] = {'name': 'TRIGGERBOARD_0', 'fem_id': BOARD_FEM_ID, 'period_clk': period_clk}
    (n / 'session.json').write_text(json.dumps(session, indent=2) + '\n')
    print('Session started in the nestDAQ mode. Beam is initially off; use the beam command to turn it on.')
