# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only observations, written solely to the private truth directory."""
import json
import os
import sys

with open('/tmp/runcontrol_observer/sitecustomize.py', encoding='utf-8') as source:
    exec(compile(source.read(), source.name, 'exec'))
if os.path.basename(sys.argv[0]) == 'msensor_producer.py' and os.environ.get('SESSION_E2E_TRUTH'):
    from msensor.device import MSensorDevice
    original = MSensorDevice._crossing
    def observed(self, crossing):
        path = os.path.join(os.environ['SESSION_E2E_TRUTH'], 'received.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps({'run':self.run_number, 'trigger':crossing.crossing_id,
                                     'time_ns':crossing.time_ns, 'col':crossing.col,
                                     'row':crossing.row}) + '\n')
        return original(self, crossing)
    MSensorDevice._crossing = observed

if os.path.basename(sys.argv[0]) == 'ITS3DataCollector.py':
    import pyeudaq
    original_init = pyeudaq.DataCollector.__init__
    def configured_init(self, *args):
        original_init(self, *args)
        self.SetServerAddress('tcp://44001')
    pyeudaq.DataCollector.__init__ = configured_init

if os.path.basename(sys.argv[0]) == 'ALPIDEProducer.py':
    import time
    import alpidedaqboard
    import pyeudaq
    injected_status = os.environ.get('SESSION_E2E_MUTATE_STATUS')
    original_send = pyeudaq.Producer.SendEvent
    def send_with_status_fault(self, event):
        if injected_status and event.GetDescription().endswith('_status'):
            if injected_status == 'bore' and event.IsBORE():event.SetTag('FAULT_BORE', 'injected')
            if injected_status == 'eore' and event.IsEORE():event.SetTag('FAULT_EORE', 'injected')
            if injected_status == 'eore_count' and event.IsEORE():
                event.SetTag('Event', str(int(event.GetTags()['Event']) + 1))
        return original_send(self, event)
    if injected_status:pyeudaq.Producer.SendEvent = send_with_status_fault
    delay_serial = os.environ.get('SESSION_E2E_DELAY_JOIN')
    slow_serial = os.environ.get('SESSION_E2E_SLOW')
    original_control = alpidedaqboard.ALPIDEDAQBoard._trigger_control
    original_read = alpidedaqboard.ALPIDEDAQBoard.event_read
    def delayed_control(self, value):
        if self.serial == delay_serial and not (value & 1):
            time.sleep(1)
            path = os.path.join(os.environ['SESSION_E2E_TRUTH'], 'test-switches.jsonl')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8') as stream: stream.write(json.dumps({'kind':'join_delay','serial':self.serial})+'\n')
        return original_control(self, value)
    def slow_read(self):
        if self.serial == slow_serial:
            time.sleep(.01)
            if not getattr(self, '_test_slow_recorded', False):
                path = os.path.join(os.environ['SESSION_E2E_TRUTH'], 'test-switches.jsonl')
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'a', encoding='utf-8') as stream: stream.write(json.dumps({'kind':'slow_read','serial':self.serial})+'\n')
                self._test_slow_recorded = True
        return original_read(self)
    alpidedaqboard.ALPIDEDAQBoard._trigger_control = delayed_control
    alpidedaqboard.ALPIDEDAQBoard.event_read = slow_read

# Test-only callback ordering evidence. No event or return value is modified.
if os.environ.get('EUDAQ_TIMING_TRACE'):
    import time
    import pyeudaq
    def timing(event, **fields):
        row={'event':event,'pid':os.getpid(),'monotonic_ns':time.monotonic_ns(),**fields}
        data=(json.dumps(row,separators=(',',':'))+'\n').encode()
        try:
            fd=os.open(os.environ['EUDAQ_TIMING_TRACE'],os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            try:os.write(fd,data)
            finally:os.close(fd)
        except OSError:pass
    if os.path.basename(sys.argv[0]) == 'ITS3RunControl.py':
        prior_rc_init=pyeudaq.RunControl.__init__
        def traced_rc_init(self,*args):
            prior_rc_init(self,*args)
            cls=type(self)
            original=cls.DoStatus
            def status_callback(instance,connection,status):
                try:timing('runcontrol_status',name=connection.GetName(),state=int(status.GetState()))
                except Exception:pass
                return original(instance,connection,status)
            cls.DoStatus=status_callback
        pyeudaq.RunControl.__init__=traced_rc_init
    if os.path.basename(sys.argv[0]) == 'ITS3DataCollector.py':
        prior_init=pyeudaq.DataCollector.__init__
        def traced_init(self,*args):
            prior_init(self,*args)
            cls=type(self)
            for name in ('DoStartRun','DoConnect','DoDisconnect','DoReceive'):
                original=getattr(cls,name)
                def callback(instance,*values,_name=name,_original=original):
                    fields={}
                    if values:
                        try:fields['producer']=values[0].GetName()
                        except Exception:pass
                    if _name=='DoReceive' and len(values)>1:
                        try:
                            description=values[1].GetDescription()
                            if 'status' in description:return _original(instance,*values)
                            fields.update(description=description,trigger=values[1].GetTriggerN())
                        except Exception:pass
                    timing('collector_'+_name+'_begin',**fields)
                    try:return _original(instance,*values)
                    finally:
                        if _name=='DoReceive':
                            try:fields['queues']={key.GetName():len(value) for key,value in instance.producer_queues.items()}
                            except Exception:pass
                        timing('collector_'+_name+'_end',**fields)
                setattr(cls,name,callback)
        pyeudaq.DataCollector.__init__=traced_init
    if os.path.basename(sys.argv[0]) in ('ALPIDEProducer.py','msensor_producer.py'):
        prior_send=pyeudaq.Producer.SendEvent
        def traced_send(self,event):
            try:
                description=event.GetDescription()
                if 'status' in description:return prior_send(self,event)
                fields={'description':description,'trigger':event.GetTriggerN(),'device':event.GetDeviceN()}
            except Exception:return prior_send(self,event)
            timing('producer_send_begin',**fields)
            try:return prior_send(self,event)
            finally:timing('producer_send_end',**fields)
        pyeudaq.Producer.SendEvent=traced_send
