# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only RunControl and existing session observers."""
import json, os, sys, time
with open('/tmp/session_observer/sitecustomize.py',encoding='utf-8') as source:
    exec(compile(source.read(),source.name,'exec'))
if os.path.basename(sys.argv[0])=='ITS3RunControl.py' and os.environ.get('ACCEPTED_TRACE'):
    import pyeudaq
    original=pyeudaq.RunControl.StartRun
    def start(self,*args):
        path=os.environ['ACCEPTED_TRACE']+'/runcontrol.jsonl'
        with open(path,'a',encoding='utf-8') as f:
            f.write(json.dumps({'event':'start_call','monotonic_ns':time.monotonic_ns()})+'\n')
        return original(self,*args)
    pyeudaq.RunControl.StartRun=start

    original_init=pyeudaq.RunControl.__init__
    def traced_init(self,*args):
        original_init(self,*args)
        cls=type(self);original_disconnect=cls.DoDisconnect
        def disconnect(instance,con,*values):
            path=os.environ['ACCEPTED_TRACE']+'/runcontrol.jsonl'
            with open(path,'a',encoding='utf-8') as f:
                f.write(json.dumps({'event':'runcontrol_disconnect','monotonic_ns':time.monotonic_ns(),'producer':con.GetName()})+'\n')
            return original_disconnect(instance,con,*values)
        cls.DoDisconnect=disconnect
    pyeudaq.RunControl.__init__=traced_init
