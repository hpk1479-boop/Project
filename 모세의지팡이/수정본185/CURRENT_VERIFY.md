# 수정본180부터의 검사 범위

| 실행 | 범위 |
|---|---|
| `python -B -m pytest -q -p no:cacheprovider --basetemp %TEMP%\p180 tests Part3/tests` | 상시 시험 (`pytest.ini`). 새 시험 파일은 목록 등록 없이 자동으로 돈다 |
| `PYTHONPATH=통합설치`로 `python -B -m pytest ... 통합설치/releasekit/tests` | 설치·배포 도구 |
| `python verify_current.py` | `verification/` 93파일을 파일마다 따로(외부망·작업본 AI 차단), Python·JS 문법, 목록 검사. verification에 새 시험을 만들면 `verify_current_manifest.json`에 등록해야 한다 |

`PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`로 돌린다. 공식 검사기는 수정본179까지 `tests`·`Part3/tests` 129파일도 다시 돌렸고, 등록 안 된 시험 파일 때문에 항상 실패였다. 정리 내용은 `문서/수정본180_시험정리.md`.

아래는 이전 수정본의 기록이다.

# 수정본151에서 새로 확인한 범위

승인한 백테스트 성능 개선 1·2·3번의 관련 시험 **531개**가 통과했습니다. 최종 기록은 `검증결과/revision151/final_integrated.log`(530개), `final_windows.log`(1개)입니다. 뒤의 1개는 샌드박스가 Windows 폴더의 strict 경로 확인을 차단하여 밖에서 별도로 실행했습니다. 제품 코드나 그 기존 시험은 수정하지 않았습니다.

실제 금 녹화 입력의 교대 측정과 동일 입력 LIVE/재생·발생/미발생 검증 설명은 `문서/수정본151_백테스트반복처리최적화.md`에 있습니다. 이번에는 Part3 prototype/Lab을 열거나 실행하지 않았으며, 아래 과거 전체 검증을 151에서 다시 통과한 것으로 보지 않습니다. 새 회귀 5파일은 공식 검증 목록에도 등록했습니다.

# 이전 검증 안내 — 수정본95

수정본95 작업본 루트에서 실행:

```powershell
python verify_current.py
```

설치 도구와 같은 Python 3.12·3.13 64비트, Node.js, 그리고 프로젝트 실행 의존성 및 pytest를 사용한다. 의존성이 없거나 테스트 수집/실행/문법 검사가 실패하면 종료코드 1이다. 필요한 Python 검사 의존성: `pytest numpy pandas duckdb pyzmq psutil requests jsonschema`. 이미 설치된 프로젝트 환경을 사용하며 실행기가 패키지를 자동 설치하지 않는다.

## 수정본95 추가 검사

사건 시간창, 종료·알림, 확정봉 캐시, WATCH 연속 재생, MT5 복구·캡처 계획, AI 수명, 설치 환경 및 배포 포함 범위를 새 회귀로 검사합니다. 공식 목록은 `verify_current_manifest.json`, 이번 실행 증거는 `검증결과`에 있습니다. 기존 제외 항목은 늘리지 않습니다.

## 수정본94 추가 검사

- `verification/test_ai_settings94.py`: Gemini 기본값, 명시적 실행 방식 보존, 한 줄 모델·키 배치, 직접 LoRA 선택, 사용 안 함 저장·실제 호출 차단·기존 모델 해제·모델/키 보존.
- 기존 AI 설정 화면 검사는 상세 구분 제거와 실행 방식 슬롯 선택에 맞춰 갱신했습니다. 키 교체·삭제, 수동 모델 입력, GGUF 경로 검증과 백테스트 설정의 접힘·저장 검사는 유지합니다.

## 수정본93 추가 검사

- `tests/test_provider_boundary93.py`: 모든 AI Provider의 공통 최소권한, 강제 코드·파일 조회 거부, 전송 payload 정제, 허용된 전체 전략 JSON과 후속 수정 문맥 보존, 직접 Ollama/GGUF 호출의 보안 경계.
- 기존 로컬 개발·진단 권한 유지 기대는 새 공통 보안 정책 검증으로 갱신한다. 전략 의미·schema 검증과 적용/저장 경계는 유지한다.
- 기존 제외 항목과 이유는 변경하지 않는다. 현재 공식 검사 선택의 원본은 `verify_current_manifest.json`이다.

## 수정본92 추가 검사

- `verification/test_research_presets92.py`: 동적 registry 조회·원본 의미 보존·불러오기 실패/이전 승인 차단·후속 수정·새 번호 저장·프로젝트 이동.
- `verification/test_preset_ui92.py`: 불러오기·예문 표시·취소/실패 시 기존 표 유지·표 수정과 최종 확인 연결.
- `verification/test_preset_server92.py`: 실제 서버의 표 편집 JS/CSS 제공 및 인증된 불러오기 API 연결.
- 기존 제외 항목과 이유는 변경하지 않는다.

## 수정본91 추가 검사

- `verification/test_research_editor91.py`: 해석표 전체 intent 검증, 칸별 오류, 오래된 승인 차단, 최신 수정본의 AI 대화 반영, 작업 전환, 최종 확인 전 생성·실행 차단.
- `verification/test_ai_editor_ui91.py`: 실제 schema 기반 슬롯, 숫자 입력, 잘못된 칸만 표시, 참조·분기·수명·복수 시간봉 보존, 작업·확인 질문 선택.
- `verification/test_editor_flow91.py`: 빠른 연속 수정, 저장 연결 실패 시 이전 값 사용 차단, 최종 확인 후 기존 생성·백테스트 연결.
- 기존 계산·schema·Recipe 검사를 유지하며, 기존 제외 항목과 이유는 변경하지 않는다.

## 수정본90 추가 검사

- `tests/test_command_dictionary90.py`: 공통 원본, 사용자 override, 정상·손상 사전 갱신, 상대 경로 이동.
- `tests/test_secretary_routing90.py`: 실행 없는 김비서 해석, WATCH/조회/등록/취소/오류, 조건·TF·방향·순서·시간창 유지.
- `tests/test_language_dictionary90.py`: 확정 예문 100개 보존, 불변·1회 읽기 캐시, 검색 갱신, 정상값 유지.
- `tests/test_ai_language_dictionary90.py`: 로컬/외부 AI의 동일한 안전 용어 참조, 기존 계약·권한·예제 선택 유지.
- Python 문법 검사에 새 공통 패키지 `moses_language`를 포함한다. 기존 제외 항목과 이유는 유지한다.

## 공식 범위

| 구분 | 현재 공식 검사 |
|---|---|
| Part1 | 이벤트 입력/판정/복원, STAFF/Wire, OZ·BREAKER·FVG·SWEEP·INDICATOR·WATCH, 가변 MA 계산, 확정봉/진행봉, 동일 입력 LIVE/재생, 손상 설정 시작 차단, 정상 종료·저장 확인, 중복 엔진 확인·실제 준비 상태, 공통 AI WATCH 연결·기존 의미 검증·진행 중 해석 취소 |
| Part2 | event_backtest 설정/선택/캡처/저장/분할/워커/진행/결과, 가상 진입과 CSV, 데이터 누락·생성 틱·이동 경로 |
| Part3 | AI 설정/모델 선택·대화 수정·확인 후 적용, 웹 HTTP/서버·창 종료 계약, 현행 schema와 Recipe v2/validator/execution_plan/compiler, 공식 연결 API, 결과 대시보드, 백테스트 시작 직전 취소, 설정 복구 확인, 엔진 재시작 확인·종료 경고, 공통 백테스트 작업·재연결·독립 프로세스·AI 명령 확인, 설정 기본·상세 분류와 접힘·저장, 전략 설정 팝업의 초안·저장·취소 및 MAIN 세션 보존, 종료 후 화면 유지와 창 닫기 진행 안내, 공통 AI 서비스·모델 공유·설정 변경·키 마스킹·독립 연결·종료 |
| Python | Part1/Part2/Part3/common_ai 현존 Python 및 실행 진입점, 검증 도구를 compileall로 검사. 참조 복사본·폐기 시험·가상환경 제외 |
| JS | Part3/web 아래 모든 .js에 node --check |
| Verifier | 실제 assertion/누락 모듈/문법 오류/누락 Node.js/xfail이 실패 및 종료코드 1이 되는지, 목록 누락과 경로 이동 검사. 통합 설치의 Python 선택·네이티브 인수·CheckOnly·실패 판정·환경 복원·이동 후 실행 검사 |

schema/Recipe/compiler는 `Part3/tests/test_ai_intent_v2.py`, `test_common_recipe60.py::Contracts`, `test_confirmed_50.py`(자체 포함된 50개 canonical fixture), `test_ai_only_generation.py`에서 검사한다. 생성 Python의 import/register는 `test_ai_execution.py::ActualEngine::test_generated_register_to_python_board_to_notification` 및 `test_common_recipe60.py::GeneratedExecution`에서 실제 생성 모듈/현재 엔진/Part3 백테스트 로더를 통해 검사한다.

시간 제한 없는 SEQUENTIAL, MA_PRICE_CROSS, MA_PRICE_TOUCH와 #054/#059/#060의 정식 경로 및 같은 Wire 입력의 LIVE/Part2 캡처 재생은 `test_common_recipe60.py`에 포함된다. 실제 Ollama/MT5/텔레그램으로 접속하지 않는다. 로컬 임시 HTTP 서버와 테스트 전용 로컬 IPC는 허용한다. 실제 시장 이력 전체 백테스트 완료를 뜻하지 않는다.

## 선택한 파일

### Part1 (37 files)

- `tests/test_array_validity43.py`
- `tests/test_composer_input32.py`
- `tests/test_control_reconnect.py`
- `tests/test_engine_optimization.py`
- `tests/test_event_e1.py`
- `tests/test_event_e2_boundaries.py`
- `tests/test_event_e2_composition.py`
- `tests/test_event_e2_domains.py`
- `tests/test_event_e2_startup.py`
- `tests/test_event_e3.py`
- `tests/test_event_perf1.py`
- `tests/test_fact_optimization42.py`
- `tests/test_integration43.py`
- `tests/test_live_daily33.py`
- `tests/test_ma_strategy_chain49.py`
- `tests/test_module_diagnostics.py`
- `tests/test_module_log_modes30.py`
- `tests/test_numpy_processors.py`
- `tests/test_oz_profiles48.py`
- `tests/test_oz_regime_super_removed48.py`
- `tests/test_oz_rewrite.py`
- `tests/test_schema_cleanup.py`
- `tests/test_shared_oz32.py`
- `tests/test_special3_time.py`
- `tests/test_symbol_registry.py`
- `Part1/watch_ma_validation/test_ma_contract.py`
- `verification/test_live_settings66.py`
- `verification/test_engine_lifecycle66.py`
- `verification/test_engine_processes66.py`
- `verification/test_watch_shared74.py`: 기존 로컬 해석 우선·의미 검증 유지·공통 모델 선택·Part3 없이 독립 연결·진행 중 해석을 취소한 정상 종료.
- `verification/test_symbol_settings84.py`
- `tests/test_command_dictionary90.py`: 공통 원본, 사용자 override, 정상·손상 사전 갱신, 상대 경로 이동.
- `tests/test_secretary_routing90.py`: 실행 없는 김비서 해석, WATCH/조회/등록/취소/오류, 조건·TF·방향·순서·시간창 유지.
- `verification/test_release_window95.py`
- `verification/test_release_output95.py`
- `verification/test_release_cache95.py`
- `verification/test_release_persistent95.py`

### Part2 (25 files)

- `tests/test_alert_stats.py`
- `tests/test_capture_folder_layout.py`
- `tests/test_data_selection.py`
- `tests/test_ea_build_compatibility.py`
- `tests/test_event_watch_selection50.py`
- `tests/test_parallel_oz.py`
- `tests/test_part2_event_runner.py`
- `tests/test_part2_optimization37.py`
- `tests/test_part2_optimization38.py`
- `tests/test_progress_build30.py`
- `tests/test_result_analytics53.py`
- `tests/test_stop_virtual33.py`
- `tests/test_tester_start55.py`
- `tests/test_ui_slots.py`
- `tests/test_virtual_entry.py`
- `tests/test_virtual_optimization44.py`
- `tests/test_virtual_read33.py`
- `tests/test_virtual_recorded44.py`
- `tests/test_web_plan_optimization56.py`
- `tests/test_worker39.py`
- `tests/test_worker_measurement43.py`
- `verification/test_current_backtest.py`
- `verification/test_backtest_auto78.py`: 물리 코어 수 자동 선택·12개 제한 제거·AUTO 기본값·구간 전체의 중복 없는 분할·설정 저장 및 시나리오 연결·수동 지정 유지·구축 조각 및 승인 보존·Windows 고코어 작업 풀의 총 작업 수 보존과 실패 전달.
- `verification/test_release_capture95.py`
- `verification/test_release_watch95.py`

### Part3 (88 files)

- `Part3/tests/test_ai_agent.py`
- `Part3/tests/test_ai_execution.py`
- `Part3/tests/test_ai_guard_50.py`
- `Part3/tests/test_ai_intent_v2.py`
- `Part3/tests/test_ai_model_settings57.py`
- `Part3/tests/test_ai_no_default59.py`
- `Part3/tests/test_ai_revision59.py`
- `Part3/tests/test_close_engines57.py`
- `Part3/tests/test_common_recipe60.py`
- `Part3/tests/test_desktop_window52.py`
- `Part3/tests/test_ollama_models58.py`
- `Part3/tests/test_part3_http_flow.py`
- `Part3/tests/test_unified_web.py`
- `Part3/tests/test_web_headless51.py`
- `verification/test_dashboard63.py`
- `verification/test_part3_connection62.py`
- `verification/test_web_only65.py`
- `verification/test_launchers65.py`
- `verification/test_backtest_cancel66.py`
- `verification/test_settings_recovery66.py`
- `verification/test_live_restart66.py`
- `verification/test_backtest_routes67.py`
- `verification/test_backtest_jobs67.py`
- `verification/test_generated_backtest_adapter67.py`
- `verification/test_backtest_commands67.py`
- `verification/test_backtest_process67.py`
- `verification/test_settings_layout68.py`
- `verification/test_strategy_popup69.py`
- `verification/test_window_feedback69.py`
- `verification/test_strategy_metadata69.py`
- `verification/test_backtest_recovery70.py`: 오류 복귀·원래 조건 재시도·즉시 메뉴 이동·늦은 응답 및 중복 실행 방지.
- `verification/test_close_runtime72.py`: 종료 확인·취소·부분 결과 저장·전체 작업 정지·시간 초과·재시도·창고 변경·소유 모델 종료.
- `verification/test_close_recovery76.py`: Windows 프로세스 조회 실패 시 목록 재확인·조회 불가와 종료 구분·부분 결과와 프로세스 종료 확인·자동 재확인 후 창 종료·대기 중 새 작업 차단.
- `verification/test_ai_reset72.py`: 상단 버튼·현재 대화 초기화·신규 전략 시작·설정과 생성 파일 및 다른 대화 보존.
- `verification/test_gguf_engine73.py`
- `verification/test_local_gguf73.py`
- `verification/test_gguf_ui73.py`
- `verification/test_gguf_catalog73.py`: 파일명 조회·부분 파일·읽기 전용·프로젝트 상대 경로.
- `verification/test_shared_ai74.py`: 실제 로컬 IPC·독립 프로세스·동시 시작·공통 모델·요청 직렬화·세대 변경·정상/강제 종료·WATCH 연결 취소.
- `verification/test_shared_ai_ui74.py`: GGUF 라벨/기본값·파일명 선택·공통 WATCH 선택·Gemini 키 입력 및 보존.
- `verification/test_part3_shared_ai74.py`: 기존 Agent/백테스트 명령 schema 유지·공통 설정 저장 및 비밀키 마스킹·종료 연결.
- `verification/test_common_settings74.py`: 공통/기존 설정 읽기·손상 오류·Gemini 설정값/JSON/도구/오류·공통 모델 전환.
- `verification/test_common_runtime74.py`: 사용한 Ollama 모델만 종료·다른 모델 보존·확인 실패 잠금 유지 및 재시도.
- `verification/test_ai_settings75.py`: GGUF 실행기 자동 선택·경로 변경과 초기화·프로젝트 이동·기본 선택 3가지·상세 설정의 LoRA 선택·저장·복귀·필수 값 검증.
- `verification/test_desktop_instance76.py`: 두 번째 창 실행 차단·기존 창 경고·서버와 엔진 보존·동시 시작·정상/강제 종료 후 잠금 해제·다른 위치의 동일 잠금.
- `verification/test_symbol_input76.py`: 기존 입력칸의 창고 종목 선택·직접 수정·빈 목록·메뉴 복귀·목록 갱신·안전한 종목명 표시.
- `verification/test_warehouse76.py`: 내부/외부 창고 지정·내부 상대 경로 저장·다른 cwd 및 프로젝트 이동·Part2와 생성 전략 실행 연결·실제 captures 종목 폴더 조회 및 DuckDB 원본 보존.
- `verification/test_backtest_list77.py`: 실제 폴더만 심볼 목록에 표시·오래된 DB 종목 제외·폴더 이동·종료된 기록 선택삭제·실행 중 및 조회 불가 작업 차단·원본 캡처/DB/생성 전략 보존·경로 검증과 Windows 연결 폴더 차단·API 인증.
- `verification/test_backtest_selection77.py`: 행 체크박스·전체선택·선택삭제·확인 취소·빈 목록·일부 삭제 실패·중복 클릭·동시 새로고침·삭제한 결과의 늦은 응답 차단·재생 모드 화살표.
- `verification/test_research79.py`
- `verification/test_research_ui79.py`: 통합 대화의 실행 확인·진행/결과 연결·결과 0건·새 대화 초기화·설정 변경의 늦은 응답 차단·생성 파일명 표시 보존.
- `verification/test_research_modes80.py`
- `verification/test_research_modes_ui80.py`: 실제 웹 JS의 모드 전환, 대화 표시, 확인 대상 유지, 요청 중 전환 차단, 선택 모드를 유지하는 대화 초기화.
- `verification/test_agent_context83.py`
- `verification/test_gguf_transport83.py`
- `verification/test_research_flow83.py`
- `Part3/tests/test_common_recipe_contract84.py`
- `Part3/tests/test_recipe_runtime84.py`
- `Part3/tests/test_transition_scope85.py`
- `Part3/tests/test_ai_storage_boundaries84.py`
- `verification/test_dynamic_presets84.py`
- `verification/test_recipe_pipeline84.py`
- `tests/test_provider_boundary86.py`
- `tests/test_external_transport86.py`
- `Part3/tests/test_provider_security86.py`
- `Part3/tests/test_gemini_key_settings86.py`
- `tests/test_gemini_request86.py`
- `tests/test_model_choices87.py`
- `tests/test_external_errors88.py`
- `tests/test_reference_pack88.py`
- `tests/test_external_prompt88.py`
- `tests/test_schema_contract89.py`
- `tests/test_external_reply89.py`
- `tests/test_external_reply_errors89.py`
- `verification/test_external_correction89.py`
- `tests/test_provider_removal89.py`
- `tests/test_language_dictionary90.py`: 확정 예문 100개 보존, 불변·1회 읽기 캐시, 검색 갱신, 정상값 유지.
- `tests/test_ai_language_dictionary90.py`: 로컬/외부 AI의 동일한 안전 용어 참조, 기존 계약·권한·예제 선택 유지.
- `verification/test_research_editor91.py`: 해석표 전체 intent 검증, 칸별 오류, 오래된 승인 차단, 최신 수정본의 AI 대화 반영, 작업 전환, 최종 확인 전 생성·실행 차단.
- `verification/test_ai_editor_ui91.py`: 실제 schema 기반 슬롯, 숫자 입력, 잘못된 칸만 표시, 참조·분기·수명·복수 시간봉 보존, 작업·확인 질문 선택.
- `verification/test_editor_flow91.py`: 빠른 연속 수정, 저장 연결 실패 시 이전 값 사용 차단, 최종 확인 후 기존 생성·백테스트 연결.
- `verification/test_research_presets92.py`: 동적 registry 조회·원본 의미 보존·불러오기 실패/이전 승인 차단·후속 수정·새 번호 저장·프로젝트 이동.
- `verification/test_preset_ui92.py`: 불러오기·예문 표시·취소/실패 시 기존 표 유지·표 수정과 최종 확인 연결.
- `verification/test_preset_server92.py`: 실제 서버의 표 편집 JS/CSS 제공 및 인증된 불러오기 API 연결.
- `tests/test_provider_boundary93.py`: 모든 AI Provider의 공통 최소권한, 강제 코드·파일 조회 거부, 전송 payload 정제, 허용된 전체 전략 JSON과 후속 수정 문맥 보존, 직접 Ollama/GGUF 호출의 보안 경계.
- `verification/test_ai_settings94.py`: Gemini 기본값, 명시적 실행 방식 보존, 한 줄 모델·키 배치, 직접 LoRA 선택, 사용 안 함 저장·실제 호출 차단·기존 모델 해제·모델/키 보존.
- `verification/test_release_capture_ui95.py`
- `verification/test_release_ai95.py`

### Verifier (4 files)

- `verification/test_verify_current.py`
- `verification/test_unified_installer4.py`
- `verification/test_release_installer95.py`
- `verification/test_release_verifier95.py`

## 제외와 결과 해석

- 번호가 붙은 파일도 현재 코드를 검사하면 포함한다. 파일명 숫자만으로 제외하지 않는다.
- 이전 수정본 원문/바이트 비교, 동결 oracle/배열 해시, 사라진 모듈/API, Recipe v1 및 수동 생성 전용 계약, 필수 Tk/Edge UI, 외부 교육 문서와 opt-in 실제 Windows 프로세스 종료 시험을 기본 대상에서 제외한다.
- 개별 제외는 나머지 시험을 유지한다. 제거된 API를 수집 단계에서 import해 파일 전체가 실행 불가능한 두 파일은 파일 단위로 제외한다.
- `tests/test_data_selection.py`의 생성 틱 시험은 가짜 runner가 문자열을 반환하는 낡은 계약이라 제외한다. 같은 허용/경고/재생 동작을 현행 결과 객체로 검사하는 `verification/test_current_backtest.py`를 추가했다.
- `Part3/tests/verify_interface.py`와 build의 과거 수동 검증/골든 생성 스크립트는 공식 목록에 없는 이전 보고서 생성 도구다. 기본 실행은 이를 호출하지 않는다.
- 구체적인 제외 파일/시험과 이유는 `verify_current_manifest.json` 및 실행 마지막 출력/summary.json에서 확인한다. 수정본65의 UI 통일에서 현재 유효한 검사 7파일의 import·연결 mock·GUI 화면 단정을 headless API/웹 계약으로 이관했다. 삭제된 Tk 창 전용 검사는 이유를 명시해 제외했으며 pytest.ini는 그대로다. 수정본66에서는 이 목록과 제외를 유지하고 1·2번 회귀 3파일, 후속 3·4번 회귀 3파일을 추가했다.
- PASS는 실제 통과한 pytest node와 Python/JS 문법 검사 파일의 합이다. unittest의 한 메서드 안 여러 subTest는 한 node로 센다. FAIL은 실패 node/수집 오류/검증 도구 오류다.
- SKIP는 제외한 Python 시험 선언 수(파라미터 확장 전) + 선택적 브라우저 시험 파일 수 + 실제 실행 시 skip node다. 따라서 PASS와 SKIP의 단위가 같다고 해석하지 않는다.
- 새 테스트 파일이 선택/제외 목록 어느 쪽에도 없거나 목록의 파일/시험이 사라지면 공식 목록 검토가 필요하므로 FAIL이다. 실패를 보고 난 후 자동 제외하거나 테스트를 PASS로 바꾸지 않는다.
- 각 파일을 새 Python 프로세스에서 실행하고 과거 pytest.ini, PYTEST_ADDOPTS, 자동 플러그인의 영향을 차단한다. 알려진 개별 제외 이외 deselect와 xfail/xpass는 FAIL이다.
- 제품 폴더에 pyc를 쓰지 않는다. Python 소스 원문을 임시 폴더에 복사해 compileall로 검사하며 .pyw도 임시 .py로 검사한다. 임시 경로는 Windows 길이 제한을 고려해 짧게 만들고 완료 뒤 지운다.
- 설정·목록·보고서에는 프로젝트 상대 경로를 기록한다. 검증 도구는 __file__에서 루트를 찾는다. 한글/공백이 있는 새 위치로 옮기고 다른 cwd에서 실행하는 시험을 통과했다.
## 이번 수정본에서 실행한 결과

수정본94를 별도 수정본95로 복사했고 이전 검증결과 폴더는 복사하지 않았습니다. 최종 전체 검증은 **3,731 PASS / 0 FAIL / 기존 제외 797항목**입니다. pytest 3,328건, Python 문법 396파일, JS 문법 7파일의 합계이며 결과는 `검증결과/current_verify/20261003_164618_647803/summary.json`에 있습니다. 초기 감사와 중간 실패 증거도 이 수정본에서 생성했으며 그대로 보존합니다.

오프라인 검증기는 외부 네트워크와 실제 작업본 AI 서비스의 endpoint 조회·시작·요청을 차단합니다. 임시 프로젝트와 가짜 로컬 전송은 허용합니다. 오래된 AI 설정·종료 fixture를 이 격리 계약에 맞췄으며, 실패 항목을 제외하지 않습니다.

이 문서의 PASS는 실제 MT5 수집·복구, Telegram 도착, 선택 실모델의 해석 정확도 또는 새 PC 설치의 완료를 뜻하지 않습니다. 상세 변경·실환경 확인 범위는 `수정내역95.txt`와 `배포안내95.txt`를 확인합니다.
