from pathlib import Path
import hashlib,json,sys,ast
R=Path(__file__).resolve().parents[1];O=R/'검증결과/log_restore';OLD=R.with_name('수정본29')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
week=json.loads((O/'week_comparison.json').read_text('utf8'));assert week['before']['alerts_digest']==week['after']['alerts_digest']
a=json.loads((O/'behavior/30_synthetic240_live/result.json').read_text('utf8'));b=json.loads((O/'behavior/30_synthetic240_replay/result.json').read_text('utf8'))
assert all(a[k]==b[k] for k in ('signal_sha256','alerts','deliveries','errors'))
manifest=json.loads((O/'source29_sha256.json').read_text('utf8'));changed_old=[n for n,h in manifest.items() if sha(OLD/n)!=h];assert not changed_old
write(O/'previous_preserved.json',{'checked':len(manifest),'changed':changed_old})
files=[]
for part in ('Part1/program','Part2/event_backtest','tests','build'):
 for p in (R/part).rglob('*.py'):
  if '__pycache__' in p.parts:continue
  name=p.relative_to(R).as_posix()
  if not (OLD/name).exists() or sha(p)!=sha(OLD/name):files.append(name)
files.append('Part1/OZ_SYSTEM CONTROL.pyw');files=sorted(files);write(O/'changed_files.json',files)
sys.path.insert(0,str(R/'Part1/audit'));from source_integrity import verify_sources
registered=verify_sources();expected=registered['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
unit=R/'Part1/audit/remediation/48-module-diagnostics'
rows=[{'file':n.removeprefix('Part1/'),'before_sha256':expected.get(n.removeprefix('Part1/'),empty),'after_sha256':sha(R/n)} for n in files if n.startswith('Part1/')]
assert not unit.exists();write(unit/'changes.json',rows);write(unit/'review.json',{'scope':'A module memory traces and warning files; module isolation and viewer','tests':'log_restore evidence','strategy_judgment_changed':False})
result=verify_sources();assert not any('broken hash chain' in e for e in result['integrity_errors'])
write(O/'integrity.json',{'remaining_errors':result['integrity_errors'],'registered_files':len(rows)})
write(R/'build/part1_immutable_sha256.json',{p.relative_to(R).as_posix():sha(p) for p in (R/'Part1').rglob('*') if p.is_file() and not set(p.parts)&{'logs','__pycache__','results'} and p.suffix!='.ex5' and p.name!='special_settings.json'})
# Preserve A checkpoint hashes so B receives an explicit before inventory.
write(O/'a_finished_sources.json',{n:sha(R/n) for n in files})
report=f'''# A — 추적 로그 복원과 모듈 상태 화면

수정본29를 검증결과 제외로 복사한 수정본30에서 작업했다. 원본 레거시 Part1은 읽기만 했다. 이전 수정본 소스·설정 {len(manifest)}개 해시 변경 0건. 실제 텔레그램·MT5 실행 없음. Part3는 열거나 시험하지 않았다.

## 사용법
Part1 시스템 컨트롤의 **모듈 상태 / 추적 로그** 버튼을 연다. 모듈 선택, 일시정지, 자동 스크롤, 오류 파일 열기를 지원한다. 화면은 1초마다 별도 읽기 전용 IPC로 메모리 로그를 읽는다. 엔진은 UI를 호출하지 않는다.

`Part1/program/config.txt` 선택 설정: `TRACE_ENABLED=true`, `TRACE_RING_LINES=2000`, `TRACE_WARNING_BYTES=5242880`, `TRACE_WARNING_BACKUPS=3`. 설정을 생략하면 이 기본값이다. 사용자 기존 config는 A에서 바꾸지 않았다. 추적 INFO/DEBUG는 파일에 쓰지 않는다. 경고·오류만 `Part1/logs/module_errors/<모듈>.log`에 회전 저장한다. 오류에는 예외 스택을 포함한다. 미실행 모듈은 대기, STAFF 무수신 30초는 지연, 60초는 오류·끊김이다. 실제 STAFF 건강 상태도 반영한다. 엔진 대기열 압력은 지연으로 표시한다.

## 모듈 대응
| 모듈 | 복원·연결 정보 |
|---|---|
| STAFF | 수신·검증·전광판 전달, 재연결 예외, stale/unavailable |
| OZ | 후보 등록·B0 갱신·진입/이탈·silent consume 이유·최종 상태 |
| SWEEP | 감시 등록/복원, 레벨 터치·해제, 상태 이벤트 |
| FVG | 생성·터치/터치 종료·채움·만료 |
| INDICATOR | 추세 방향·기울기, 상태/요청 지표 |
| WATCH | 감시 등록/취소·조건 상태 이벤트 |
| SPECIAL1~7 | 기존 모듈 로그 및 조건별 통과/token, 연쇄 처리, 최종 hook 응답/차단 이유 |
| 김매니저 | 신호 수신·수신자, 중복 차단, 전송 성공/HTTP 거부/예외 |

레거시/현재 문구 목록은 `검증결과/log_restore/log_inventory.json`. 기존 INFO/DEBUG 문장은 logger 활성 확인 안에서만 평가한다. 활성화되지 않았을 때 f-string·join 등 추적용 문자열을 만들지 않는다. 기존 판정식과 반환값은 유지했다. 오류 처리기와 Consumer를 격리하며 오류 이벤트 재귀를 막는다. 공유 COMPOSER의 기존 연속 5회 DEGRADED/계속 처리 규칙은 유지한다.

## 검증
- 링버퍼 용량·지연 포맷·rotation·오류 스택·강제 전략 및 처리기 예외·다른 모듈 계속 처리·STAFF 끊김·JSON IPC·실제 Tk 화면 검증 통과.
- 관련 호스트/로그 시험 16 PASS. E1 묶음은 35 PASS, 1 기존 실패: `test_bar_close_fact_ignores_forming_updates_until_new_bar`. 변경 전 격리본에서도 같은 2 != 1을 재현했다. 기존 테스트 기대값이나 Fact는 바꾸지 않았다.
- 실제 모듈 합성 240초 LIVE 수신/재생: {a['bundles']}묶음, {a['signals']}신호, {len(a['alerts'])}알림. 신호 해시·문구·ID·전송 대체 결과 동일, 오류 없음. 모듈별 로그 수는 각 `behavior/*/result.json`에 기록했다. 합성 입력에서 조건에 도달하지 않는 전략은 정상 발생 증거로 과장하지 않는다.

## 로그 OFF 1주 측정
보존 XAU 2025-09-01~08(종료 미포함), SPECIAL1, 사전 3거래일 워밍업. 두 실행을 순차 수행했다. 같은 {week['before']['bundles']}묶음과 6알림이다.

| 항목 | 수정본29 격리본 | 수정본30 |
|---|---:|---:|
| 입력 포함 전체 초 | {week['before']['elapsed_seconds']:.2f} | {week['after']['elapsed_seconds']:.2f} |
| CPU 초 | {week['before']['cpu_seconds']:.2f} | {week['after']['cpu_seconds']:.2f} |
| 엔진 평균 ms/묶음 | {week['before']['mean_ms']:.3f} | {week['after']['mean_ms']:.3f} |
| 엔진 p99 상한 ms | {week['before']['p99_ms_upper_bound']:.1f} | {week['after']['p99_ms_upper_bound']:.1f} |

알림 시각·방향·문구·수신자·ID·순서 해시 동일. 1회 측정으로 속도 저하가 관측되지 않았으며 성능 보증/새 허용치를 만들지 않았다. Part2는 추적을 강제로 끄되 경고·오류는 해당 run 하위 `logs/module_errors`에 남긴다.

## 무결성 및 수정 파일
체인 `48-module-diagnostics` 등록, immutable 목록 재생성. 기존 무결성 진단 {len(result['integrity_errors'])}건은 증거에 보존했다.
'''+''.join('- `'+n+'`\n' for n in files)
(R/'수정내역_로그복원.md').write_text(report,encoding='utf8');write(O/'status.json',{'A':'COMPLETE','B':'NOT_STARTED','alerts_equal':True,'new_logic_failure':0})
print('A report written')
