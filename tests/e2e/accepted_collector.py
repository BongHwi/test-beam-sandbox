# SPDX-License-Identifier: LGPL-3.0-or-later
"""Observe callbacks of the original public collector without editing its source."""
import argparse, json, os, time
from pathlib import Path
from ITS3DataCollector import ITS3DataCollector
path=Path(os.environ['ACCEPTED_TRACE'])/'collector.jsonl'
def record(event,**fields):
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps({'event':event,'monotonic_ns':time.monotonic_ns(),**fields})+'\n')
original_init=ITS3DataCollector.__init__
def init(self,*args):
    original_init(self,*args);self.SetServerAddress('tcp://44001');self._observed_received={}
ITS3DataCollector.__init__=init
for method in ('DoConnect','DoDisconnect','DoReceive'):
    original=getattr(ITS3DataCollector,method)
    def wrapper(self,con,*args,_method=method,_original=original):
        name=con.GetName();fields={'producer':name}
        if _method=='DoReceive':
            ev=args[0];is_data='status' not in ev.GetDescription()
            if is_data:self._observed_received[name]=self._observed_received.get(name,0)+1
            fields.update(description=ev.GetDescription(),is_data=is_data,
                          trigger=ev.GetTriggerN() if is_data else None,
                          producer_data_count=self._observed_received.get(name,0))
        record(_method+'_before',**fields)
        r=_original(self,con,*args)
        record(_method+'_after',**fields,queue_sizes={c.GetName():len(q) for c,q in self.producer_queues.items()},
               producer_data_counts=dict(self._observed_received),global_data_count=self.ndev)
        return r
    setattr(ITS3DataCollector,method,wrapper)
p=argparse.ArgumentParser();p.add_argument('--name','-n',default='dc');p.add_argument('--run-control','--rc',default='tcp://localhost:44000');args=p.parse_args()
dc=ITS3DataCollector(args.name,args.run_control);dc.Connect();time.sleep(2)
while dc.IsConnected():time.sleep(1)
record('collector_exit',producer_data_counts=dict(dc._observed_received),global_data_count=dc.ndev,queue_sizes={c.GetName():len(q) for c,q in dc.producer_queues.items()})
