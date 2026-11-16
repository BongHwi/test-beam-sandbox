# SPDX-License-Identifier: LGPL-3.0-or-later
"""Observe the real session protocol in an isolated E2E container."""
import json,os,time
from pathlib import Path
from session_core import server

Base=server.Session
class Observed(Base):
    def request(self,req):
        with self.lock:
            try:
                value=super().request(req)
            except Exception as exc:
                if req.get('op') in ('join','next','stop'):
                    row={'op':'join','serial':req.get('serial'),'kind':'rejected',
                         'joined':len(self.clients),'expected':len(self.expected),
                         'monotonic_ns':time.monotonic_ns(),'committed_ps':self.committed_ps,
                         'slowest_ps':None,'error_type':type(exc).__name__}
                    row['op']=req.get('op')
                    path=Path(os.environ['ROBUST_PROTOCOL_AUDIT']);path.parent.mkdir(parents=True,exist_ok=True)
                    with path.open('a') as out:out.write(json.dumps(row,separators=(',',':'))+'\n')
                raise
            if req.get('op') in ('join','next','stop'):
                active=[c for c in self.clients.values() if not c.get('lost',False)]
                row={'op':req.get('op'),'serial':req.get('serial'),'kind':value.get('kind'),
                     'joined':len(self.clients),'expected':len(self.expected),
                     'monotonic_ns':time.monotonic_ns(),'committed_ps':self.committed_ps,
                     'slowest_ps':min((c['processed_ps'] for c in active),default=None)}
                if 'trigger' in value:row['trigger']=value['trigger']
                path=Path(os.environ['ROBUST_PROTOCOL_AUDIT']);path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('a') as out:out.write(json.dumps(row,separators=(',',':'))+'\n')
            return value
server.Session=Observed
server.main()
