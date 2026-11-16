# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only process inspection when requested by SIGUSR1."""
import json,os,pathlib,signal,sys
if os.path.basename(sys.argv[0])=='ALPIDEProducer.py':
    def inspect(signum,frame):
        result={'modules':sorted(sys.modules),'fds':{}}
        for f in pathlib.Path('/proc/self/fd').iterdir():
            try: result['fds'][f.name]=os.readlink(f)
            except OSError: pass
        pathlib.Path('/tmp/alpide-process-inspection.json').write_text(json.dumps(result))
    signal.signal(signal.SIGUSR1,inspect)
