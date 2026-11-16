#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Test-only producer subclass that injects an I/O failure after stream HEADER."""
import time
from msensor.msensor_producer import MSensorProducer

class BrokenFile:
    def __init__(self, original): self.original=original
    def write(self, data): raise OSError('injected stream write error')
    def flush(self): return self.original.flush()
    def close(self): return self.original.close()
    def fileno(self): return self.original.fileno()

class FaultProducer(MSensorProducer):
    def DoStartRun(self):
        super().DoStartRun()
        self.device.stream.file=BrokenFile(self.device.stream.file)

if __name__=='__main__':
    p=FaultProducer('MSENSOR_0','tcp://127.0.0.1:44001'); p.Connect()
    while p.IsConnected(): time.sleep(.1)
