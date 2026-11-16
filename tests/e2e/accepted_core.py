# SPDX-License-Identifier: LGPL-3.0-or-later
"""Observe source joins, stop boundary and release without changing the core."""
import json, os, time
from pathlib import Path
import session_core.server as server
path=Path(os.environ['ACCEPTED_TRACE'])/'core.jsonl'
def record(event,**fields):
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps({'event':event,'monotonic_ns':time.monotonic_ns(),**fields})+'\n')
join,stop,next_event=server.Session.join,server.Session.stop,server.Session.next
sink_message=server.Session.sink_message
hold_seconds=float(os.environ.get('ACCEPTED_SINK_HOLD_SECONDS','0'))
hold_until=None
def observed_sink(self,req):
    global hold_until
    if hold_seconds and req.get('op')=='sink_progress' and req.get('events',0)>0:
        if hold_until is None:
            hold_until=time.monotonic()+hold_seconds
            record('sink_hold_begin',events=req['events'],seconds=hold_seconds)
        if time.monotonic()<hold_until:
            record('sink_hold',events=req['events'])
            return {'kind':'sink_progress'}
        if hold_until:
            record('sink_hold_end',events=req['events'])
            hold_until=0
    return sink_message(self,req)
server.Session.sink_message=observed_sink
def observed_join(self,req):
    record('join_begin',serial=req['serial'],joined=list(self.clients))
    try:r=join(self,req)
    except BaseException:
        record('join_error',serial=req['serial']);raise
    record('join_done',serial=req['serial'],joined=list(self.clients),source_started=self.transport is not None)
    return r
def observed_stop(self,serial):
    record('stop_begin',serial=serial,joined=list(self.clients),boundary=self.boundary,exposed=self.exposed)
    r=stop(self,serial)
    number=self.active_ids[self.boundary] if self.boundary is not None and self.boundary>=0 else None
    record('stop_done',serial=serial,joined=list(self.clients),boundary_index=self.boundary,boundary_trigger=number,
           time_ps=self.clock_ps,source_started=self.transport is not None)
    return r
def observed_next(self,serial,req):
    r=next_event(self,serial,req)
    if r.get('kind')=='finished':
        record('release',serial=serial,boundary_index=self.boundary,time_ps=r.get('time_ps'),
               producer_acks={k:v['ack'] for k,v in self.clients.items()},
               sink_counts=dict(self.sink_counts),
               sink_required=any(c['sink_required'] for c in self.clients.values()))
    return r
server.Session.join=observed_join;server.Session.stop=observed_stop;server.Session.next=observed_next
server.main()
