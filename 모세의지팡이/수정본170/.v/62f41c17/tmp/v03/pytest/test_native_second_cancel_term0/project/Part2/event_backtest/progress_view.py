"""Human progress projection; raw events belong only in the run log."""
import time
from datetime import date

def duration(seconds):
    seconds=max(0,int(seconds));return f'{seconds//60}분 {seconds%60}초'

def size(value):
    return f'{max(0,int(value))/1e9:.2f}GB'

def piece_days(piece):
    if 'start' not in piece or 'end' not in piece:return 1
    return max(1,(date.fromisoformat(piece['end'][:10])-date.fromisoformat(piece['start'][:10])).days)

class ProgressView:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.started=clock();self.phase_started=self.started
        self.phase='준비';self.build=0.;self.replay=0.;self.stage='';self.stage_detail='';self.lines=[];self.warnings=[]
        self.total=0;self.done=0;self.active_piece='';self.eta=None;self._last=None;self.build_only=False;self.finished=None
        self.virtual=0.;self.virtual_started=False;self.virtual_detail=''
        self.record_estimates={};self.active_estimate=None
    def _line(self,text):
        if text!=self._last:self.lines.append(text);self._last=text
    def _phase(self,text):
        if text!=self.phase:self.phase=text;self.phase_started=self.clock();self.eta=None
    def _build_stage(self,stage,fraction):
        if self.total:
            value=100*(self.done+(stage+max(0.,min(1.,fraction)))/3)/self.total
            self.build=max(self.build,min(100.,value))
    def accept(self,event):
        kind=event.get('event');now=self.clock()
        if kind=='BUILD_PLAN':
            records=event.get('record',[])
            self.total=len(records)+len(event.get('convert',[]));self.done=0
            self.build=100. if not self.total else 0.
            estimated=(event.get('estimate') or {}).get('temporary_msp3_bytes') or 0
            total_days=sum(piece_days(p) for p in records)
            self.record_estimates={(p['start'],p['end']):estimated*piece_days(p)/total_days
                for p in records} if estimated and total_days else {}
            if not self.total:self._line('보유 데이터만 사용합니다.' if event.get('available_periods') else '필요한 데이터가 모두 준비되어 있습니다.')
            if event.get('period_adjustment'):
                text=event['period_adjustment']['message'];self._line(text)
                if text not in self.warnings:self.warnings.append(text)
            for period in event.get('excluded_periods',()):
                text=period['start']+' ~ '+period['end']+': '+period['message'];self._line(text)
                if text not in self.warnings:self.warnings.append(text)
        elif kind=='PERIOD_ADJUSTED':
            self._line(event['message'])
            if event['message'] not in self.warnings:self.warnings.append(event['message'])
        elif kind=='CAPTURE_START':
            self.active_piece=event['start'][:7];self.total=event.get('total',self.total)
            self._phase(f"데이터 구축 중 ({self.active_piece}, {event.get('index',self.done+1)}/{self.total}조각)")
            self.stage='MT5 추출';self.active_estimate=self.record_estimates.get((event['start'],event.get('end')))
            self.stage_detail='0.00GB'+(' / '+size(self.active_estimate)+' 예상' if self.active_estimate else ' · 예상치 없음')
            self._build_stage(0,0 if self.active_estimate else 1)
            self._line(self.active_piece+' 추출 시작')
        elif kind=='CAPTURE_RECORDED':
            month=event.get('start','')[:7]
            self._build_stage(0,1);self.stage_detail=size(event.get('raw_bytes',0))+' 추출 완료'
            self._line(f"{month} 추출 완료 {event.get('raw_bytes',0)/1e9:.2f}GB ({duration(event.get('elapsed_seconds',0))})")
        elif kind=='CONVERSION_START':
            self.stage='차분 변환';self.active_piece=event.get('start',self.active_piece)[:7]
            self._phase(f'데이터 구축 중 ({self.active_piece}, {self.done+1}/{max(1,self.total)}조각)')
            self._build_stage(1,0);self.stage_detail='시작'
            self._line(self.active_piece+' 차분 변환 시작')
        elif kind=='CONVERSION_PROGRESS':
            done=event.get('processed_bytes',0);total=event.get('total_bytes')
            days=event.get('processed_days',0);all_days=event.get('total_days')
            fraction=done/total if total else days/all_days if all_days else 0
            self._build_stage(1,min(.99,fraction))
            self.stage_detail=(size(done)+' / '+size(total) if total else f'{days} / {all_days or "?"}일')
        elif kind=='VERIFY_START':
            self._build_stage(2,0);self.stage='복원 검증'
            self.stage_detail='0 / '+str(event.get('total_days','?'))+'일'
            self._line(self.active_piece+' 복원 검증 시작')
        elif kind=='VERIFY_PROGRESS':
            done=event.get('verified_days',0);total=event.get('total_days',0)
            self._build_stage(2,done/total if total else 0)
            self.stage_detail=f'{done} / {total}일'
        elif kind in ('CAPTURE_COMPLETE','CAPTURE_CONVERTED'):
            self.done+=1;self.build=min(100,100*self.done/max(1,self.total))
            self.stage_detail='완료'
            self._line(event.get('start',self.active_piece)[:7]+' 변환·검증 완료')
            warning=(event.get('tick_evidence') or {}).get('warning')
            if warning and warning not in self.warnings:self.warnings.append('생성 틱 포함 또는 실제 틱 미확인: '+warning)
        elif kind=='BUILD_VERIFIED':self.build=100.;self.stage='검증 완료';self._line('데이터 구축·검증 완료')
        elif kind in ('RUN_START','RUN_PROGRESS'):
            self._phase('백테스트 중')
            if kind=='RUN_START':self._line('선택 전략 백테스트 시작')
            self.replay=event.get('percent',self.replay)
            p=self.replay;elapsed=event.get('elapsed_seconds',now-self.phase_started)
            self.eta=elapsed*(100-p)/p if p>0 else None
        elif kind=='REPLAY_REUSED':
            self.replay=100.;self._line('같은 조건으로 끝난 실행의 알림 재생 결과를 씁니다')
        elif kind=='VIRTUAL_ENTRY_START':
            self.virtual_started=True;self.virtual=0.;self.virtual_detail='0 / '+str(event.get('alerts',0))+'건'
            self._phase('가상 진입 계산 중');self._line('알림 결과로 가상 진입 계산 시작')
        elif kind=='VIRTUAL_ENTRY_PROGRESS':
            self.virtual=event['percent'];self.virtual_detail=f"{event['processed_alerts']} / {event['total_alerts']}건"
            p=self.virtual;self.eta=event.get('elapsed_seconds',0)*(100-p)/p if p else None
        elif kind=='VIRTUAL_ENTRY_COMPLETE':
            if not event.get('cancelled'):self.virtual=100.
            self._line('가상 진입 부분 결과 저장' if event.get('cancelled') else '가상 진입 성과 표 저장 완료')
        elif kind=='PROGRESS':
            code=event.get('message_code','')
            if code=='NATIVE_EXPORT_PROGRESS' and self.stage=='MT5 추출':
                done=event.get('export_bytes',0)
                if self.active_estimate:self._build_stage(0,min(.99,done/self.active_estimate))
                self.stage_detail=size(done)+(' / '+size(self.active_estimate)+' 예상' if self.active_estimate else '')
            labels={'NATIVE_TERMINAL_RESTART_PREP':'MT5 정상 종료 대기','NATIVE_TERMINAL_CLOSED':'MT5 종료 확인',
                    'NATIVE_TESTER_START':'MT5 테스터 시작','NATIVE_EXPORT_ACTIVE':'MT5 추출 중',
                    'NATIVE_TERMINAL_REOPENING':'일반 MT5 복원 중','NATIVE_TERMINAL_REOPENED':'일반 MT5 복원 완료'}
            if code in labels:self._line(labels[code])
        elif kind in ('WARNING','ERROR','HISTORY_MISSING','CAPTURE_RETRY'):
            text=event.get('message','1분봉 이력 누락으로 중단' if kind=='HISTORY_MISSING' else 'MT5 종료 확인 후 1회 재시도')
            self._line(text)
            if text not in self.warnings:self.warnings.append(text)
            if kind in ('ERROR','HISTORY_MISSING'):self._phase('중단 / 오류')
        elif kind=='COMPLETE':
            cancelled=event.get('result',{}).get('status')=='CANCELLED'
            self._phase('중단됨(부분 결과)' if cancelled else '완료');self.finished=now
            if not cancelled:self.build=self.replay=100.
            for warning in event.get('result',{}).get('warnings',[]):
                if warning not in self.warnings:self.warnings.append(warning)
            self._line('중단됨(부분 결과) — 결과 보기에서 확인하세요.' if cancelled else '완료 — 결과 파일 저장')
    def remaining(self):
        if self.finished is not None:return '총 경과 '+duration(self.finished-self.started)
        elapsed=self.clock()-self.phase_started
        return '예상 남은 시간 '+duration(self.eta) if self.eta is not None else '경과 '+duration(elapsed)+' (남은 시간 추정 중)'
