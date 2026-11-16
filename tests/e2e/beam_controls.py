# SPDX-License-Identifier: LGPL-3.0-or-later
"""Exercise independent beam control through its actual command and socket."""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import traceback

root=Path('/tmp/beam-control-e2e');root.mkdir(exist_ok=True)
out=Path('/tmp/beam-results');out.mkdir(exist_ok=True)
sock=str(root/'source.sock');state=Path(sock+'.beam.json');log=Path(sock+'.beam.log')
private={'seed':1857092461,'device_seed':1857092460,
         'beam':{'position_mm':[1.234567891,-2.345678912,-20], 'direction':[0.00123456789,0,1],
                 'phase_space_covariance':[[0.00345678912,0,0,0,0]]+[[0]*5 for _ in range(4)]},
         'rate_hz':237.654321987}
env=dict(os.environ,SESSION_SOURCE_CONFIG_JSON=json.dumps(private))
process=subprocess.Popen(['python','-m','session_core.server','--geometry','/tmp/session_example_geometry.json','--socket',sock],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def emit(name,status,**details):
    (out/(name+'.json')).write_text(json.dumps({'status':status,'measurement':details},indent=2)+'\n')


def cli(action):
    p=subprocess.run(['python','-m','session_core.beamctl','--socket',sock,action],capture_output=True,text=True,timeout=10)
    if p.returncode:raise RuntimeError('beam command failed')
    result=json.loads(p.stdout)
    if json.loads(state.read_text()) != result['status']:raise RuntimeError('state file differs from command response')
    return result


def api(action):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(3);client.connect(sock)
        client.sendall((json.dumps({'op':'beam','action':action,'origin':'socket'})+'\n').encode())
        with client.makefile('rb') as stream:result=json.loads(stream.readline())
    if json.loads(state.read_text()) != result['status']:raise RuntimeError('state file differs from socket response')
    return result

try:
    deadline=time.monotonic()+10
    while not state.exists():
        if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('source did not start')
        time.sleep(.01)
    startup=json.loads(state.read_text())
    emit('M-E1','pass' if startup['beam_on'] is False and startup['origin']=='startup' else 'fail',startup=startup)
    stop=threading.Event();observed=[];errors=[]
    def watch():
        while not stop.is_set():
            try:
                x=json.loads(state.read_text());observed.append((x['seq'],x['beam_on']))
            except Exception as exc:errors.append(type(exc).__name__)
            time.sleep(.001)
    thread=threading.Thread(target=watch);thread.start()
    initial=cli('status');first=cli('on');duplicate=cli('on');second=api('off');third=cli('on');fourth=cli('off');last=cli('status')
    for _ in range(6):cli('on');cli('off')
    stop.set();thread.join()
    final=json.loads(state.read_text());records=[json.loads(x) for x in log.read_text().splitlines()]
    seqs=[x[0] for x in observed]
    ok=(initial['status']['beam_on'] is False and first['status']['beam_on'] is True and
        first['status']['origin']=='command' and first['status']['last_control_origin']=='command' and duplicate['status']['seq']==first['status']['seq'] and
        duplicate['status']['last_changed_utc']==first['status']['last_changed_utc'] and second['status']['beam_on'] is False and
        second['status']['origin']=='socket' and second['status']['last_control_origin']=='socket' and third['status']['beam_on'] is True and
        fourth['status']['beam_on'] is False and last['status']['beam_on'] is False and
        final['beam_on'] is False and not errors and bool(observed) and seqs==sorted(seqs) and
        all(a['seq']<=b['seq'] for a,b in zip(records,records[1:])))
    emit('M-E5','pass' if ok else 'fail',atomic_reads=len(observed),read_errors=errors,
         seq_monotonic=seqs==sorted(seqs),final_on=final['beam_on'],record_count=len(records),
         origins=[first['status']['origin'],second['status']['origin']])
except Exception as exc:
    # A reader actually saw a partially written/invalid state file, or an
    # operation failed after startup: both violate M-E5, not an unrun test.
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)) or (out/'M-E1.json').exists():
        emit('M-E5','fail',reason='control or state read failed',error_type=type(exc).__name__)
    (out/'control-error.json').write_text(json.dumps({'traceback':traceback.format_exc()},indent=2)+'\n')
finally:
    process.terminate()
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.kill();process.wait()
    for name in ('M-E1','M-E5'):
        if not (out/(name+'.json')).exists():emit(name,'not_run',reason='control scenario did not reach criterion; see control-error.json')
