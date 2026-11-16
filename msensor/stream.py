# SPDX-License-Identifier: LGPL-3.0-or-later
"""Framed, checksummed stream with a bounded reader and explicit recovery status."""
import argparse
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

MAGIC = b'MSWINDOW\x01'
MAX_LENGTH = 16 * 1024 * 1024
TYPES = {'HEADER':1, 'WINDOW':2, 'EMPTY_RUN':3, 'TRIGGER':4, 'FOOTER':5}
NAMES = {v:k for k,v in TYPES.items()}


def _frame(kind, data):
    body = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    prefix = bytes([TYPES[kind]]) + struct.pack('>I', len(body))
    return prefix + body + struct.pack('>I', zlib.crc32(prefix + body))


class Writer:
    def __init__(self, path, header):
        self.path = Path(path)
        self.file = self.path.open('xb')
        self.digest = hashlib.sha256()
        self.empty_first = None
        self.empty_count = 0
        self._write_bytes(MAGIC)
        self.write('HEADER', header)
        self.file.flush()

    def _write_bytes(self, data):
        self.file.write(data)
        self.digest.update(data)

    def write(self, kind, data):
        self._write_bytes(_frame(kind, data))

    def flush_empty(self):
        if self.empty_count:
            self.write('EMPTY_RUN', {'first_index':self.empty_first,'count':self.empty_count})
            self.empty_first, self.empty_count = None, 0

    def window(self, frame):
        if not frame['hits'] and not frame['truncated']:
            if not self.empty_count: self.empty_first = frame['window_index']
            self.empty_count += 1
            if self.empty_count == 2**32-1: self.flush_empty()
        else:
            self.flush_empty()
            self.write('WINDOW', frame)

    def trigger(self, trigger):
        self.flush_empty()
        self.write('TRIGGER', trigger)

    def flush(self):
        self.file.flush()

    def close(self, summary):
        self.flush_empty()
        footer = dict(summary, body_sha256=self.digest.hexdigest())
        self.write('FOOTER', footer)
        self.file.flush(); os.fsync(self.file.fileno()); self.file.close()
        return {'stream_closed':1,'stream_file_bytes':self.path.stat().st_size,'stream_file_sha256':self.digest.hexdigest()}


def read(path, expect_sha256=None):
    data=Path(path).read_bytes()
    result={'status':'truncated','records':[], 'valid_bytes':0, 'tail_bytes':len(data), 'counts':{}}
    def corrupt(reason, offset):
        result.update(status='corrupt', reason=reason, offset=offset)
        return result
    if len(data)<len(MAGIC):
        if not MAGIC.startswith(data): return corrupt('bad magic',0)
        return result
    if data[:len(MAGIC)]!=MAGIC: return corrupt('bad magic',0)
    pos=len(MAGIC); covered=0; hits=0; nonempty=0; triggers=0; header=None; last_end=0; last_truncated=False; last_order=(-1,-1); last_trigger=-1
    while pos < len(data):
        start=pos
        if len(data)-pos<5: break
        code=data[pos]; length=struct.unpack_from('>I',data,pos+1)[0]
        if code not in NAMES: return corrupt('unknown type',pos)
        if length>MAX_LENGTH or length<2: return corrupt('invalid length',pos+1)
        end=pos+5+length+4
        if end>len(data): break
        check=struct.unpack_from('>I',data,end-4)[0]
        if zlib.crc32(data[pos:end-4])!=check: return corrupt('checksum',pos)
        try: rec=json.loads(data[pos+5:end-4]); kind=NAMES[code]
        except (ValueError,UnicodeError): return corrupt('invalid json',pos)
        try:
            if header is None:
                if kind!='HEADER': return corrupt('missing header',pos)
                header=rec
                if rec.get('format')!='mswindow' or rec.get('version')!=1: return corrupt('invalid header',pos)
                if not isinstance(rec.get('window_length_ns'),int) or rec['window_length_ns']<=0 or not isinstance(rec.get('columns'),int) or not isinstance(rec.get('rows'),int) or rec['columns']<=0 or rec['rows']<=0: return corrupt('invalid header fields',pos)
            elif kind=='HEADER': return corrupt('duplicate header',pos)
            elif kind=='WINDOW':
                if last_truncated or rec['window_index'] != covered: return corrupt('window coverage',pos)
                begin=covered*header['window_length_ns']; full_end=begin+header['window_length_ns']
                if rec['t_begin_ns']!=begin or not isinstance(rec['truncated'],bool) or not (begin<rec['t_end_ns']<=full_end) or (rec['truncated'] != (rec['t_end_ns']<full_end)): return corrupt('window bounds',pos)
                pixels=rec['hits']
                if not isinstance(pixels,list) or any(not isinstance(h,list) or len(h)!=2 or any(isinstance(v,bool) or not isinstance(v,int) for v in h) or not (0<=h[0]<header['columns'] and 0<=h[1]<header['rows']) for h in pixels): return corrupt('invalid hits',pos)
                if pixels!=sorted(pixels,key=lambda h:(h[1],h[0])) or len({tuple(h) for h in pixels})!=len(pixels): return corrupt('hit order',pos)
                order=(rec['t_end_ns'],0)
                if order<last_order: return corrupt('record order',pos)
                last_order=order
                covered+=1; hits+=len(pixels); nonempty+=bool(pixels); last_end=rec['t_end_ns']; last_truncated=rec['truncated']
            elif kind=='EMPTY_RUN':
                if last_truncated or rec['first_index']!=covered or not isinstance(rec['count'],int) or not 0<rec['count']<=2**32-1: return corrupt('window coverage',pos)
                covered+=rec['count']; last_end=covered*header['window_length_ns']; last_truncated=False
                order=(last_end,0)
                if order<last_order: return corrupt('record order',pos)
                last_order=order
            elif kind=='TRIGGER':
                if last_truncated or rec['window_index']<covered or rec['window_index']!=rec['trigger_time_ns']//header['window_length_ns']: return corrupt('trigger window',pos)
                if rec['trigger_number']<=last_trigger: return corrupt('trigger order',pos)
                order=(rec['trigger_time_ns'],1)
                if order<last_order: return corrupt('record order',pos)
                last_order=order; last_trigger=rec['trigger_number']; triggers+=1
            elif kind=='FOOTER':
                counts={'windows_total':covered,'windows_nonempty':nonempty,'hits_total':hits,'triggers_total':triggers,'last_window_index':covered-1 if covered else None,'last_window_truncated':last_truncated}
                if any(rec.get(k)!=v for k,v in counts.items()): return corrupt('footer counts',pos)
                if rec.get('body_sha256')!=hashlib.sha256(data[:start]).hexdigest(): return corrupt('body digest',pos)
                if end!=len(data): return corrupt('trailing bytes',end)
                if any(r['kind']=='TRIGGER' and r['data']['window_index']>=covered for r in result['records']): return corrupt('trigger past end',pos)
                result['counts']=counts
                result['status']='closed_clean'
            result['records'].append({'kind':kind,'data':rec,'offset':pos})
            pos=end; result['valid_bytes']=pos; result['tail_bytes']=len(data)-pos
            if kind=='FOOTER': break
        except (KeyError,TypeError,ValueError,ZeroDivisionError): return corrupt('invalid record fields',pos)
    result['file_sha256']=hashlib.sha256(data).hexdigest()
    if expect_sha256 and result['file_sha256']!=expect_sha256: return corrupt('file digest',0)
    if not result['counts']: result['counts']={'windows_total':covered,'windows_nonempty':nonempty,'hits_total':hits,'triggers_total':triggers,'last_window_index':covered-1 if covered else None,'last_window_truncated':last_truncated}
    return result


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['verify']); parser.add_argument('file'); parser.add_argument('--expect-sha256'); a=parser.parse_args()
    try: result=read(a.file,a.expect_sha256)
    except OSError as e: result={'status':'corrupt','reason':str(e)}
    print(json.dumps(result,sort_keys=True)); return {'closed_clean':0,'truncated':2,'corrupt':3}[result['status']]

if __name__=='__main__': raise SystemExit(main())
