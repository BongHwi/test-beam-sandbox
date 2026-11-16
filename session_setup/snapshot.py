# SPDX-License-Identifier: LGPL-3.0-or-later
"""Copy the complete public EUDAQ events from an immutable raw snapshot."""
import gc
import json
import sys
import pyeudaq

reader=pyeudaq.FileReader('native',sys.argv[1])
writer=pyeudaq.FileWriter('native',sys.argv[2])
count=0;total=0;tail=None
while True:
    try:event=reader.GetNextEvent()
    except RuntimeError as exc:
        message=str(exc)
        if 'too many failed attempts' not in message and 'End of file' not in message:raise
        tail='incomplete final event omitted';break
    if event is None:break
    writer.WriteEvent(event);total+=1
    if event.GetDescription()=='ITS3global':count+=1
# The public writer closes its output when its last reference is released.
del writer;del reader;gc.collect()
if not count:raise SystemExit('snapshot contains no complete data events')
print(json.dumps({'data_events':count,'all_events':total,'tail':tail},sort_keys=True))
