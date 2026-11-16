# SPDX-License-Identifier: LGPL-3.0-or-later
"""Independent test producer: log actual EUDAQ lifecycle, events, and every tag update."""
import json, os, threading, time
import pyeudaq

class Producer(pyeudaq.Producer):
    def __init__(self):
        super().__init__('TEST_0','tcp://127.0.0.1:44003')
        self.running=False; self.count=0; self.run_index=0; self.count_lock=threading.Lock()
        self.SetStatusTag('TestReporter','TEST_0')
    def record(self,kind,**values):
        with open('/tmp/runcontrol-e2e/calls.jsonl','a') as f: f.write(json.dumps({'call':kind,**values})+'\n')
    def publish_count(self,source):
        # Caller holds count_lock; every path that writes the tag leaves a causal record.
        self.record('Status',count=self.count,run_index=self.run_index,monotonic_ns=time.monotonic_ns(),source=source)
        self.SetStatusTag('DataEventN',str(self.count))
    def DoInitialise(self): self.record('Initialise')
    def DoConfigure(self):
        self.record('Configure',value=self.GetConfiguration().Get('VALUE'))
        if os.environ.get('RUNCONTROL_TEST_ERROR') == '1': raise RuntimeError('injected test producer configuration error')
    def DoStartRun(self):
        with self.count_lock:
            self.run_index+=1; self.count=0; self.publish_count('start')
        self.running=True; self.record('StartRun')
    def RunLoop(self):
        while self.running:
            ev=pyeudaq.Event('RawEvent','test'); ev.SetTriggerN(self.count); ev.AddBlock(0,b'x'); self.SendEvent(ev)
            with self.count_lock:
                self.count+=1; self.record('Event',count=self.count,run_index=self.run_index); self.publish_count('event')
            time.sleep(.05)
    def DoStatus(self):
        with self.count_lock: self.publish_count('status')
    def DoStopRun(self): self.running=False; self.record('StopRun',count=self.count)
    def DoReset(self): self.record('Reset')
    def DoTerminate(self): self.running=False; self.record('Terminate')

p=Producer(); p.Connect()
while p.IsConnected(): time.sleep(.1)
os._exit(0)
