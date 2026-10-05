"""LIVE output audit: append/flush CSV, optional KST daily output summary.

Host I/O only. Neither this module nor manager_KIM imports Part2.
"""
from pathlib import Path
from types import SimpleNamespace
import csv
import datetime as dt
import json
import logging
import re
import threading
import uuid
from event_engine.model import Kind,Signal,signal_id
from machine_roots import load_live_root

KST=dt.timezone(dt.timedelta(hours=9))
FIELDS=('run_id','time_ms','strategy','profile','grade','symbol','tf','direction','trigger','message','recipient','signal_id','b0_price','b0_time','delivery_result')

def is_notice(event):
    if event is None or event.kind!=Kind.SIGNAL:return False
    c=event.payload.get('content',{})
    return c.get('type')=='NOTIFICATION' or c.get('status')=='DEGRADED'

class LiveAlertRecorder:
    def __init__(self,config,*,root_provider=load_live_root,logger=None):
        self.config=dict(config);self.provider=root_provider
        self.logger=logger or logging.getLogger('LIVE_RECORD')
        self.run_id='live_'+uuid.uuid4().hex;self.lock=threading.RLock()
        self.context={};self.root=None;self.choice=None;self.disabled=False
        self.warned=set();self.summary_attempt=None;self.summary_sent=set()
        self.refresh()

    def warn_once(self,key,message,*,error=None):
        if key in self.warned:return
        self.warned.add(key)
        # Exception text may contain a machine-specific path. Keep it out of logs.
        self.logger.log(logging.ERROR if error else logging.WARNING,message+(' ['+type(error).__name__+']' if error else ''),
            extra={'trace_module':'KIM','record_error':type(error).__name__ if error else ''})

    def refresh(self):
        try:
            choice=self.provider()
            candidate=Path(choice['path']) if choice else None
            if candidate is not None and not candidate.is_absolute():raise ValueError('computer root must be absolute')
            available=candidate is None or candidate.is_dir()
        except Exception as exc:
            self.warn_once('selection_error','LIVE 기록 폴더 설정을 읽지 못했습니다. 기록을 중지합니다.',error=exc)
            self.disabled=True;return
        if choice!=self.choice:
            self.choice=choice;self.disabled=False;self.warned.clear();self.summary_sent.clear()
            self.root=candidate
        if self.root is None:
            self.warn_once('unset','LIVE 기록 폴더 미지정 · 알림 기록을 하지 않습니다.')
        elif not self.disabled and not available:
            self.disabled=True;self.warn_once('unavailable','LIVE 기록 폴더가 없거나 접근할 수 없습니다. 기록을 중지합니다.')

    def note(self,consumer,event,output):
        if not isinstance(output,Signal) or output.content.get('type')!='NOTIFICATION':return
        raw=event.payload.get('content',{}).get('event',{})
        key=signal_id(output.strategy or consumer,output.symbol,event.source_time,output.condition_key)
        with self.lock:
            self.context[key]={
                'b0_price':raw.get('b0_price'),'b0_time':raw.get('b0_time'),
                'strategy':output.strategy or raw.get('strategy',consumer),
                'profile':raw.get('profile',raw.get('profile_id','')) or '/'.join(str(raw.get(k,'')) for k in ('validation_mode','trigger_mode')).strip('/'),
                'grade':raw.get('grade',''),'trigger':raw.get('trigger',raw.get('final_trigger',raw.get('kind',''))),
                'tf':raw.get('source_tf',raw.get('tf','')),'direction':raw.get('direction','')}

    def bind(self,engine):
        for consumer in engine.strategies:
            original=consumer.on_event
            def observed(event,board,state,emit,_fn=original,_name=consumer.name):
                def output(value):
                    try:self.note(_name,event,value)
                    except Exception as exc:self.warn_once('context','LIVE 알림 부가 정보 기록 실패',error=exc)
                    emit(value)
                return _fn(event,board,state,output)
            consumer.on_event=observed

    def release(self,event):
        if event is not None:
            with self.lock:self.context.pop(event.payload.get('signal_id'),None)

    def record(self,event,recipient,result):
        if not is_notice(event):return
        with self.lock:
            self.refresh()
            if self.root is None or self.disabled:return
            try:
                p=event.payload;c=p['content'];raw=c.get('event',c);context=self.context.get(p['signal_id'],{})
                text=str(c.get('message',''));special=re.match(r'^\[(\d)[.]',text);grade=re.search(r'\b([SABC])급\b',text)
                row={'run_id':self.run_id,'time_ms':event.source_time,'signal_id':p['signal_id'],
                    'strategy':c.get('signal_strategy') or ('SPECIAL'+special[1] if special else context.get('strategy',p.get('strategy',''))),
                    'symbol':p.get('symbol',''),'tf':context.get('tf') or raw.get('source_tf',raw.get('tf','')),
                    'direction':context.get('direction') or raw.get('direction',''),
                    'grade':context.get('grade') or raw.get('grade',raw.get('level','')) or (grade[1] if grade else ''),
                    'profile':context.get('profile') or raw.get('profile',raw.get('profile_id','')),
                    'trigger':context.get('trigger') or raw.get('trigger',raw.get('kind','')),
                    'message':text,'recipient':recipient,'b0_price':context.get('b0_price') if context.get('b0_price') is not None else raw.get('b0_price'),
                    'b0_time':context.get('b0_time') if context.get('b0_time') is not None else raw.get('b0_time'),'delivery_result':result}
                day=dt.datetime.fromtimestamp(event.source_time/1000,KST).date().isoformat()
                folder=self.root/'alerts';folder.mkdir(exist_ok=True)
                path=folder/(day+'.csv')
                if path.exists() and path.stat().st_size:
                    with path.open(encoding='utf-8-sig',newline='') as f:
                        if tuple(next(csv.reader(f)))!=FIELDS:raise ValueError('LIVE CSV columns mismatch')
                empty=not path.exists() or path.stat().st_size==0
                with path.open('a',encoding='utf-8',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=FIELDS)
                    if empty:writer.writeheader()
                    writer.writerow(row);f.flush()
            except Exception as exc:
                self.disabled=True
                self.warn_once('write','LIVE 알림 기록 실패 · 기록을 중지하고 알림 전송은 계속합니다.',error=exc)

    def summary_due(self,now):
        if str(self.config.get('LIVE_DAILY_SUMMARY_ENABLED','false')).strip().lower() not in ('1','true','yes','on'):return None
        with self.lock:
            self.refresh()
            if self.root is None or self.disabled:return None
            local=now.astimezone(KST)
            if local.hour<7:return None
            day=(local.date()-dt.timedelta(days=1)).isoformat()
            attempt=(day,local.strftime('%Y-%m-%dT%H:%M'))
            if attempt==self.summary_attempt or day in self.summary_sent:return None
            self.summary_attempt=attempt
            try:
                state=self.root/'daily_summary.json'
                sent=json.loads(state.read_text('utf-8')) if state.exists() else {}
                if day in sent:self.summary_sent.add(day);return None
                path=self.root/'alerts'/(day+'.csv');latest={}
                if path.exists():
                    with path.open(encoding='utf-8-sig',newline='') as f:
                        for row in csv.DictReader(f):
                            if row['strategy']!='LIVE_DAILY_SUMMARY':latest[row['signal_id'],row['recipient']]=row
                signals=len({key[0] for key in latest});counts={}
                for row in latest.values():counts[row['delivery_result']]=counts.get(row['delivery_result'],0)+1
                text=f'[LIVE 일일 기록] {day} (KST)\n알림 신호 {signals}건'
                if counts:text+='\n수신자별 결과: '+', '.join(f'{k} {v}건' for k,v in sorted(counts.items()))
                return SimpleNamespace(kind=Kind.SIGNAL,source_time=int(now.timestamp()*1000),payload={
                    'signal_id':'live-summary:'+day,'strategy':'LIVE_DAILY_SUMMARY','symbol':'',
                    'content':{'type':'NOTIFICATION','message':text,'summary_day':day,
                        'recipients':[str(self.config.get('TELEGRAM_CHAT_ID',''))]}})
            except Exception as exc:
                self.disabled=True
                self.warn_once('summary_read','LIVE 전날 알림 요약을 읽지 못했습니다.',error=exc)
                return None

    def mark_summary_sent(self,event):
        day=event.payload.get('content',{}).get('summary_day')
        if not day or self.root is None:return
        with self.lock:
            self.summary_sent.add(day)
            try:
                path=self.root/'daily_summary.json';data=json.loads(path.read_text('utf-8')) if path.exists() else {}
                data[day]=True
                temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,sort_keys=True),encoding='utf-8');temp.replace(path)
            except Exception as exc:self.warn_once('summary_save','LIVE 일일 요약 완료 기록 실패',error=exc)
