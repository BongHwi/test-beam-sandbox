# SPDX-License-Identifier: LGPL-3.0-or-later
"""Virtual-time synthetic pixel device derived from requirements R1–R4, R7."""
import hashlib
import math
import secrets
from pathlib import Path
from .config import validate
from .source import Crossing, Trigger, uniform, normal
from .stream import Writer


class MSensorDevice:
    def __init__(self, name='MSENSOR_0'):
        self.name=name
        self.config=None
        self.running=False

    def configure(self, settings):
        if self.running: raise RuntimeError('cannot configure during run')
        c,p,d,h=validate(settings)
        self.config,self.response,self.detector,self.config_hash=c,p,d,h
        return {'settings':d,'config_hash':h}

    def arm(self, run_number, source, stream_path=None):
        if self.running or self.config is None: raise RuntimeError('device not ready')
        c=self.config
        self.time=0; self.source=source; source.start(run_number); self.run_number=run_number
        self.bounded_history=getattr(source,'bounded_history',False)
        self.signals={}; self.all_hits=[]; self.frames={}; self.pending=[]; self.trigger_records=[]
        self.records_frames_total=0; self.records_hits_total=0
        self.windows_total=0; self.windows_nonempty=0; self.hits_total=0; self.triggers_total=0
        self.stream=None; self.stream_id=0; self.last_truncated=False
        if c['readout_mode']=='continuous':
            identity=f'{run_number}:{self.name}:{self.config_hash}'.encode()
            self.stream_id=(hashlib.sha256(identity).digest()[:16] if c['deterministic_ids'] else secrets.token_bytes(16)).hex()
            path=Path(stream_path) if stream_path is not None else Path(c['stream_dir'])/f'{self.name}_run{run_number}.msws'
            header={'format':'mswindow','version':1,'stream_id':self.stream_id,'run_number':run_number,'name':self.name,
                    'plane':c['plane'],'columns':c['columns'],'rows':c['rows'],'window_length_ns':c['window_length_ns'],'config_hash':self.config_hash}
            self.stream=Writer(path,header)
        elif stream_path is not None: raise ValueError('stream only in continuous mode')
        self.running=True

    def _zone(self, col, row):
        """Tile number (row-major from column 0, row 0) or, without tiles, the column region."""
        c=self.config
        if c['tiles_x']*c['tiles_y']>1:
            return (row//(c['rows']//c['tiles_y']))*c['tiles_x']+col//(c['columns']//c['tiles_x'])
        return col//(c['columns']//c['regions'])

    def _zones(self):
        """Each threshold zone with its column and row ranges."""
        c=self.config
        if c['tiles_x']*c['tiles_y']>1:
            w,h=c['columns']//c['tiles_x'],c['rows']//c['tiles_y']
            return [(i,(i%c['tiles_x'])*w,w,(i//c['tiles_x'])*h,h) for i in range(c['tiles_x']*c['tiles_y'])]
        w=c['columns']//c['regions']
        return [(i,i*w,w,0,c['rows']) for i in range(c['regions'])]

    def _crossing(self, crossing):
        c=self.config; p=self.response
        col,row=math.floor(crossing.col),math.floor(crossing.row)
        if not (0<=col<c['columns'] and 0<=row<c['rows']): return
        region=self._zone(col,row)
        if c['tiles_off'] and region in c['tiles_off']: return
        th=c['threshold_dac'][region]
        z=(th-p['t_half'])/p['w_e']
        efficiency=p['e_max']/(1+math.exp(max(-700,min(700,z))))
        if uniform(c['seed'],'response',self.name,crossing.crossing_id)>=efficiency: return
        jitter=p['jitter_ns']*normal(c['seed'],'jitter',f'{self.name}:{crossing.crossing_id}',0) if p['jitter_ns'] else 0
        t=max(crossing.time_ns, int(crossing.time_ns+p['response_delay_ns']+jitter))
        if not self.bounded_history or c['readout_mode']=='triggered': self.all_hits.append((t,col,row))
        if c['readout_mode']=='continuous': self.signals.setdefault(t//c['window_length_ns'], []).append((t,col,row))

    def _fake(self, key, duration):
        c=self.config; p=self.response; result=[]
        for region,first_col,width,first_row,height in self._zones():
            if c['tiles_off'] and region in c['tiles_off']: continue
            th=c['threshold_dac'][region]
            rate=min(p['fake_rate_max'],p['r0']*math.exp(max(-700,min(700,-(th-p['t_n'])/p['w_n']))))
            lam=width*height*rate*duration/p['reference_duration_ns']
            # Exponential inter-arrivals implement a Poisson process without underflow.
            count=0; elapsed=0.0
            while True:
                elapsed += -math.log(uniform(c['seed'],'fake-count',key,region,count))
                if elapsed>lam: break
                col=first_col+int(uniform(c['seed'],'fake-col',key,region,count)*width)
                row=first_row+int(uniform(c['seed'],'fake-row',key,region,count)*height)
                result.append((col,row)); count+=1
        return result

    def _window(self, index, end, truncated):
        c=self.config; begin=index*c['window_length_ns']
        hit={(col,row) for t,col,row in self.signals.pop(index,[]) if t<end}
        hit.update(self._fake('window-'+str(index),end-begin))
        frame={'window_index':index,'t_begin_ns':begin,'t_end_ns':end,'truncated':truncated,'hits':sorted(hit,key=lambda x:(x[1],x[0]))}
        self.frames[index]=frame; self.windows_total+=1; self.windows_nonempty+=bool(hit); self.hits_total+=len(hit); self.last_truncated=truncated
        self.stream.window(frame)

    def _close_windows(self, target):
        w=self.config['window_length_ns']
        while (self.windows_total+1)*w<=target:
            self._window(self.windows_total,(self.windows_total+1)*w,False)

    def _completed(self, target, final=False):
        c=self.config; result=[]; remaining=[]
        for trigger in self.pending:
            t=trigger.time_ns
            if c['readout_mode']=='triggered':
                begin=t+c['strobe_delay_ns']; end=begin+c['strobe_length_ns']
                if end>target and not final: remaining.append(trigger); continue
                end=min(end,target) if final else end
                hits={(col,row) for ht,col,row in self.all_hits if begin<=ht<end}
                hits.update(self._fake('gate-'+str(trigger.trigger_number),max(0,end-begin)))
                frames=[{'window_index':None,'t_begin_ns':begin,'t_end_ns':end,'truncated':end<begin+c['strobe_length_ns'],'hits':sorted(hits,key=lambda x:(x[1],x[0]))}]
                before=after=False; base=None
            else:
                w=c['window_length_ns']; base=t//w; lo=base-c['window_guard_before']; hi=base+c['window_guard_after']
                if (hi+1)*w>target and not final: remaining.append(trigger); continue
                before=lo<0; after=hi>=self.windows_total
                frames=[self.frames[i] for i in range(max(0,lo),min(hi+1,self.windows_total))]
            result.append({'trigger_number':trigger.trigger_number,'trigger_time_ns':t,'stream_id':self.stream_id,
                           'window_index':base,'guard_incomplete_before':before,'guard_incomplete_after':after,'frames':frames})
        self.pending=remaining
        self.records_frames_total+=sum(len(x['frames']) for x in result)
        self.records_hits_total+=sum(len(f['hits']) for x in result for f in x['frames'])
        if not self.bounded_history:self.trigger_records.extend(result)
        return result

    def _prune_history(self):
        if not self.bounded_history:return
        c=self.config
        earliest=min([self.time]+[trigger.time_ns for trigger in self.pending])
        if c['readout_mode']=='continuous':
            lowest=max(0,earliest//c['window_length_ns']-c['window_guard_before'])
            self.frames={index:frame for index,frame in self.frames.items() if index>=lowest}
        else:
            cutoff=earliest+c['strobe_delay_ns']
            self.all_hits=[hit for hit in self.all_hits if hit[0]>=cutoff]

    def advance_to(self, t_ns):
        if not self.running or not isinstance(t_ns,int) or t_ns<self.time: raise ValueError('invalid advance')
        limit=self.source.end_ns(); watermark=self.source.watermark_ns()
        if (limit is not None and t_ns>limit) or (watermark is not None and t_ns>watermark): raise ValueError('source limit')
        c=self.config
        for stimulus in self.source.take_until(t_ns):
            if c['readout_mode']=='continuous': self._close_windows(stimulus.time_ns)
            if isinstance(stimulus,Crossing): self._crossing(stimulus)
            elif isinstance(stimulus,Trigger):
                self.pending.append(stimulus); self.triggers_total+=1
                if self.stream: self.stream.trigger({'trigger_number':stimulus.trigger_number,'trigger_time_ns':stimulus.time_ns,'window_index':stimulus.time_ns//c['window_length_ns']})
        if self.stream: self._close_windows(t_ns)
        self.time=t_ns
        result=self._completed(t_ns)
        self._prune_history()
        return result

    def disarm(self):
        if not self.running: raise RuntimeError('not armed')
        if self.stream and self.time%self.config['window_length_ns']:
            self._window(self.windows_total,self.time,True)
        remaining=self._completed(self.time,True)
        summary={'triggers_total':self.triggers_total,'frames_total':self.records_frames_total,
                 'hits_total':self.hits_total if self.stream else self.records_hits_total, 'remaining_records':remaining}
        if self.stream:
            counts={'windows_total':self.windows_total,'windows_nonempty':self.windows_nonempty,'hits_total':self.hits_total,'triggers_total':self.triggers_total,
                    'last_window_index':self.windows_total-1 if self.windows_total else None,'last_window_truncated':self.last_truncated}
            summary.update(counts); summary.update(self.stream.close(counts))
        self.running=False
        return summary

    def status(self):
        return {'running':self.running,'time_ns':getattr(self,'time',0),'source_finished':bool(self.running and self.source.end_ns() is not None and self.time==self.source.end_ns()),'config_hash':getattr(self,'config_hash',None)}
