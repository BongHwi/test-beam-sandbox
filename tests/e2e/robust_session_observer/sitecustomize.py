# SPDX-License-Identifier: LGPL-3.0-or-later
"""Observe published status and externally pause one joining participant."""
import os
import sys

with open('/tmp/runcontrol_observer/sitecustomize.py',encoding='utf-8') as source:
    exec(compile(source.read(),source.name,'exec'))

if os.path.basename(sys.argv[0])=='ITS3DataCollector.py':
    import pyeudaq
    original_init=pyeudaq.DataCollector.__init__
    def configured_init(self,*args):
        original_init(self,*args)
        self.SetServerAddress('tcp://44001')
    pyeudaq.DataCollector.__init__=configured_init
    if os.environ.get('ROBUST_CASE') in ('c10a','c10b','c10resume','c10c'):
        import json
        import time
        from pathlib import Path
        previous_write=pyeudaq.DataCollector.WriteEvent
        def observe_write(self,event):
            when=time.monotonic_ns()
            outcome=previous_write(self,event)
            try:
                if event.GetDescription()=='ITS3global':
                    subs=[{'name':e.GetDescription(),'trigger':e.GetTriggerN()} for e in event.GetSubEvents()]
                    path=Path('/tmp/session-e2e/truth/collector-output.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
                    with path.open('a') as stream:
                        stream.write(json.dumps({'monotonic_ns':when,'group':subs},separators=(',',':'))+'\n')
            except Exception:
                # A missing observation fails the test; it must not alter collector behavior.
                pass
            return outcome
        pyeudaq.DataCollector.WriteEvent=observe_write

if os.path.basename(sys.argv[0])=='ITS3RunControl.py' and os.environ.get('ROBUST_DELAY_JOIN'):
    import signal
    import time
    from pathlib import Path
    import pyeudaq
    previous_start=pyeudaq.RunControl.StartRun
    def delay_one_participant(self,*args,**kwargs):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            for process in Path('/proc').iterdir():
                if not process.name.isdigit():continue
                try:command=(process/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
                except OSError:continue
                if 'ALPIDEProducer.py' in command and 'ALPIDE_plane_1' in command:
                    os.kill(int(process.name),signal.SIGSTOP)
                    marker=Path(os.environ['SESSION_E2E_TRUTH'])/'paused-pid'
                    marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text(process.name)
                    return previous_start(self,*args,**kwargs)
            time.sleep(.01)
        raise RuntimeError('participant was not found before StartRun')
    pyeudaq.RunControl.StartRun=delay_one_participant
