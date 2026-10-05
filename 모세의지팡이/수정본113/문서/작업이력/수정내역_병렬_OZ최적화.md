# 수정본26 — 병렬·OZ 최적화

구현과 측정 결과를 아래에 기록한다. A1의 실제 발생 사례/단독 대 전체 선택 조건은 별도로 판정하며, 0건 비교나 기존 공유 선정 차이를 무조건 통과로 처리하지 않는다.

## 변경 범위

수정본25 전체를 복사한 독립 수정본26에서 작업한다. 이전 수정본은 실행하지 않고, 비교가 필요하면 `검증결과/parallel_oz/before_runtime`의 전체 Part1/Part2 복사본을 사용한다. 사용자 config, EA, Wire schema, 판정식·문구·임계값은 유지한다. 실제 텔레그램을 포함한 테스트 네트워크는 차단한다.

## A1. 선택 실행 의존성

SPECIAL4와 SPECIAL5에 CHAINS를 추가했다. `_update_local_chain_events`가 등록된 플러그인의 poll을 호출하므로 SPECIAL4의 새 30분 cycle과 SPECIAL5의 부모 감시 복원·자식 생존 관리가 단독 선택에서도 실행된다. 의존성 확인 시험은 실제 플러그인 등록 내용과 대조하고, 시장 이벤트를 넣어 cycle/maintenance 호출까지 확인한다.

| 전략 | 직접 의존 |
| --- | --- |
| SPECIAL1 | OZ, INDICATOR, WONBI |
| SPECIAL2 | OZ, SWEEP |
| SPECIAL3 | OZ, FVG, WATCH, CHAINS |
| SPECIAL4 | OZ, WONBI, CHAINS |
| SPECIAL5 | OZ, CHAINS |
| SPECIAL6 | OZ, FVG, MA |
| SPECIAL7 | OZ, INDICATOR |

OZ의 외부유동성 동적 감시에는 SWEEP_STATE와 SWEEP Consumer가 포함된다. 전체 선택의 기존 공유 알림 우선순위는 유지한다. 실제 입력에서 단독 SPECIAL1이 출력한 같은 OZ를 전체 선택에서는 SPECIAL6이 출력하는 사례가 발견되어, 의존 누락과 공유 선정 차이를 분리해 아래에 기록했다.

## A2. 순서와 ID

원비·OUT의 종목/TF, 가변 MA의 종목/요청 조합, 구독 해제 ID, OZ 외부유동성·manual watch 정리, SPECIAL5 자식 해제의 집합 순회를 정렬했다. TF는 `tf_seconds` 순서다. 전략 등록·우선순위를 표현하는 기존 리스트와 순서가 정해진 딕셔너리는 유지한다. ID 산식 자체는 바꾸지 않는다.

순회 후보 목록: `검증결과/parallel_oz/unordered_iteration_candidates.json`. 해시 시드 0/1/random의 첫 주 전체 신호 원문과 알림을 별도 프로세스로 비교한다. 최종 코드도 세 해시 시드에서 172,167개 SIGNAL 전체와 알림 14건이 ID까지 일치했다. 전후 ID 변경 전문은 `검증결과/parallel_oz/changed_signal_ids.csv`와 `signal_id_payload_differences.json`에 기록했다.

## B. UTC 날짜별 키프레임

새 저장 형식은 MSD2/version 2, 파일은 `capture.delta2`다. Wire 형식과 schema는 바꾸지 않는다.

| 구역 | 내용 |
| --- | --- |
| 파일 magic | 8바이트 `MSD2` + `00 00 00 02` |
| 일 구역 | 독립 gzip member. UTC 날짜의 첫 원본 묶음 앞에서 시작 |
| 키프레임 | 이전 관측까지의 피드별 현재 창을 FULL 묶음으로 보관 |
| 연속성 메타데이터 | STAFF 공개 export 상태의 epoch/seq/준비 상태/진단, seq offset, 가상 수신 시각, 앞부분 묶음 수 |
| 원본 레코드 | 기존 차분 식 그대로. 관측 시각·구조·uint64 비트 차분 |
| 인덱스 | 날짜별 offset/size, 첫·마지막 시각, 묶음 수, 원본 묶음 SHA256 |
| footer | `<QQ8s>` 인덱스 offset/length와 `MSD2IDX2` |

읽을 때 날짜 인덱스로 이동하고, 첫 키프레임의 FULL을 정상 PipeReceiver/StaffIngressAdapter → STAFF 검증 경로로 넣는다. 검증된 배열에 공개 continuation API로 기존 epoch/seq 메타데이터를 복원한다. 키프레임 자체는 엔진의 새 시장 관측으로 발행하지 않는다. 이후 원본 묶음은 원래 순서대로 모두 공급한다.

각 일 구역을 독립적인 키프레임부터 복원하고 그 구역의 원본 해시를 확인한다. 따라서 모든 시작 키프레임 이후 접미 구간도 원본과 같은 일 구역들의 연결이다. 전체 복원 해시도 별도로 원래 MSD1의 검증된 해시와 대조한다. 기존 13개 조각은 검증 완료 후 같은 capture_id의 새 세대로 등록하고 기존 파일은 보존한다. 창고 이동 시험도 새 형식에 적용한다.

`capture_start=beginning`은 같은 파일의 앞부분부터 STAFF에 넣는 기존 시작 방식이다. 기본 경로는 `keyframe`이며 비교 옵션으로 beginning을 유지한다.

## C. 작업 분배

`work_size=MONTH|FORTNIGHT`를 설정·시나리오·CLI에서 지정한다. FORTNIGHT는 실행 시작일 기준 연속 14일, 마지막 작업은 종료일에서 자른다. 출력 구간은 겹치지 않고 앞쪽 워밍업만 겹친다. 기본 겹침은 기존 3거래일이다.

ProcessPoolExecutor의 공용 작업 큐를 사용하여 비는 worker가 다음 작업을 가져간다. 재사용 worker는 새 작업 폴더를 만들기 전에 쓰기 허용 경로를 옮긴다. 각 작업의 pid·출력 기간·워밍업 기간·묶음 수·워밍업 묶음 수·첫 묶음 시간과 worker별 작업 목록을 기록한다. 논리 CPU 번호는 묶음 종료 시 샘플이므로 OS 이동을 포함하며 고정 코어 배정을 뜻하지 않는다.

사용자 지시로 월 작업 6/12 workers의 완료 결과만 사용한다. 14 workers는 진행 중 중단, 2주 작업과 기존 시작 방식 비교는 실행하지 않고 모두 ‘생략’으로 기록한다. 기본값은 MONTH / 최대 12 workers이며, 더 작은 컴퓨터에서는 물리 코어 수를 넘지 않는다. 사용자가 명시한 cores/work_size가 우선한다.

## D. OZ 선택 평가

`event_oz_selection.py`가 선택된 SPECIAL 모듈의 실제 TF 상수와 적용된 trigger 설정으로 정적 상위 집합을 만든다. 숫자 TF/판정값을 별도로 복제하지 않는다.

| 전략 | 모듈의 TF 선언 | 프로필 |
| --- | --- | --- |
| SPECIAL1/2 | OZ_TO_1H | 설정된 FINAL_VALIDATION_MODE/FINAL_TRIGGER_MODE |
| SPECIAL3/6/7 | FINAL_OZ_TFS | 설정된 최종 프로필 |
| SPECIAL4 | OZ_TFS | 설정된 최종 프로필 |
| SPECIAL5 | SOURCE_TFS + FINAL_TFS | 부모 DIVERGENCE/DIVERGENCE_REGIME + 자식 최종 프로필 |
| 공식/연쇄 | 각 정의의 oz_tfs | 정규화한 validation/trigger |

NORMAL의 중위·상위 TF_MAP과 REGIME 상위 TF, 외부유동성 source를 포함한다. 선언 밖의 TF/프로필/외부 source 등록은 명시 오류다. 시작 상태 복원과 checkpoint에도 같은 선택을 유지한다. 임의 TF 명령을 받을 수 있는 OZ/WATCH 선택은 전체 상위 집합을 선언한다. LIVE와 ALL은 전체 평가 그대로다. 비교는 `oz_evaluation=all`로 같은 선택 전략의 OZ 평가 범위만 넓혀 수행한다.

## E. EWM·ATR

`event_engine/recurrence.py`의 엔진 소유 EWMPrefix가 순수 Fact의 기존 `ewm_step`을 호출한다. ATR14_GENERAL과 가변 Watch EMA가 이 저장소를 쓴다. source_epoch, 이력 길이, 마지막 확정봉 시각과 파라미터를 키로 보관한다.

확정 구간의 시각과 입력 비트가 같으면 저장된 확정 상태부터 진행봉 한 단계를 계산한다. 봉이 추가될 때는 직전 진행봉을 그 최종값으로 계산한 뒤 이어간다. 재연결·확정 이력 정정·창 시작 이동에서는 원래 창의 첫 값부터 재계산한다. 650행 창이 한 행 밀릴 때 종전 창의 seed를 계속 쓰면 전체 계산과 값이 달라지므로 이 경우는 보수적으로 재계산한다. NaN과 -0.0 정정도 uint64 비교로 구분한다.

ArrayFactFrame과 Watch 이력도 동일 봉 시각에서 과거 값이 정정되면 닫힌 값 캐시를 무효화한다. 계산식은 그대로다. 진행봉·추가봉·슬라이딩 창·재연결·과거 정정을 포함한 전체 재계산 비트 비교가 통과했다.


## 검증 결과

| 항목 | 결과 |
| --- | --- |
| 관련 시험 | 기존 관련 묶음 최초 206 PASS + 파이프 fixture 종료 경합 1 FAIL. fixture 동기화 후 해당 시험 PASS. 신규 24 PASS + 추가 epoch 경계 시험 PASS(관련 ATR 시험도 재확인). 기본 worker 12·설정 우선순위 시험 2 PASS. 상세 XML 보존. |
| LIVE 수신 = 재생 | synthetic240: 240묶음 / 5,794신호 / 19알림. TIMER239: 230묶음 / 5,668신호 / 13알림. 신호·출력·오류 모두 일치. |
| 해시 시드 | 0 / 1 / random 모두 SIGNAL SHA256 3223c6fbf8b8847c14ab5d8c2d24a34fe462c49cad62e68c216ecf4155b04f15 |
| 키프레임 | 13조각 / 382,501묶음 / 279일. 원본 전체 해시 및 각 날짜 독립 복원 해시 일치. |
| EWM | 진행봉·봉 추가·이력 이동·정정·재연결·NaN/-0.0에서 전체 계산과 비트 일치. |
| 금지 범위 | EA/schema/config 동일. SPECIAL AST는 집합 정렬 외 동일. monitor_OZ 혼합 줄바꿈 유지. 테스트 네트워크 차단. |

### A1: 실제 월별 단독 대 전체 선택

실제 SPECIAL4가 발생한 2025-06을 추가로 전체 선택 및 SPECIAL1~7 단독 실행으로 비교했다.

| 전략 | 전체 선택 속 해당 전략 | 단독 | ID 포함 일치 |
| --- | --- | --- | --- |
| SPECIAL1 | 31 | 40 | False |
| SPECIAL2 | 1 | 15 | False |
| SPECIAL3 | 0 | 0 | True |
| SPECIAL4 | 1 | 1 | True |
| SPECIAL5 | 12 | 12 | True |
| SPECIAL6 | 29 | 36 | False |
| SPECIAL7 | 0 | 0 | True |

2025-09의 보조 비교:
| 전략 | 전체 선택 | 단독 | ID 포함 일치 |
| --- | --- | --- | --- |
| SPECIAL1 | 22 | 30 | False |
| SPECIAL2 | 3 | 25 | False |
| SPECIAL3 | 0 | 0 | True |
| SPECIAL4 | 0 | 0 | True |
| SPECIAL5 | 16 | 16 | True |
| SPECIAL6 | 18 | 24 | False |
| SPECIAL7 | 0 | 0 | True |

SPECIAL4 단독 실제 발생 검색: 2024-10=0건, 2024-11=0건, 2024-12=0건, 2025-01=0건, 2025-02=0건, 2025-03=0건, 2025-04=0건, 2025-05=1건, 2025-06=1건, 2025-07=1건, 2025-08=0건. 2025-09는 월별 비교에 포함했다.

SPECIAL4 첫 주 관측 진단에서는 setup 26건이 실제 생성되고 모두 종료되었다. 6분 만료와 ATR 선행이동 취소 등이 관측됐으며 poll 예외는 없었다. 진단은 `runs/parallel26_special4_flow_week_recorded/special4_flow_diagnostic.json`(창고 기준)에 있다. 첫 주 0건만으로 완료 처리하지 않고 위 6월 실제 발생 월까지 확인했다.

단독 대 전체 선택의 남은 차이는 기존 공유 감시 병합/출력 선정과 분리해 보아야 한다. `event_composer_domain._arm_oz_locked`는 동일 profile을 공유 child로 병합하고, 최종 출력과 child 소모는 공유 상태를 사용한다. 그 결과 단독 SPECIAL1의 같은 OZ를 전체 선택에서는 SPECIAL6이 대표 출력하는 사례가 있다. 이 규칙은 수정본25에도 존재하며 이번에 바꾸지 않았다. 모든 전략의 독립 알림을 전체 선택에서 각각 출력하도록 강제하면 기존 결정·출력 정책 변경이 된다. 차이 전문은 `comparisons.json`의 `standalone_vs_all`·`positive_june_vs_all`에 보존했다. 직접 같은 시각의 대표 출력으로 연결되지 않은 차이도 있어, 모든 차이의 개별 원인이 증명됐다고 보지 않는다. SPECIAL1·2·6의 단독 대 전체 동일성은 통과로 표시하지 않는다.

### A2: ID 변경 목록

종류별 SIGNAL 수는 전후 동일하다. 고유 ID 5개가 바뀌었고, 같은 ID의 payload에서 파생 watch_id가 바뀐 항목은 4개다. 최종 알림은 14건 모두 시각·방향·문구·수신자가 같고 ID 1건만 바뀌었다. 기존 내부 OZ 신호의 동일 ID 반복 120회는 전후 동일하며, 진단에서는 발생 순번까지 사용해 누락 없이 비교했다.

| 변경 전 ID | 변경 후 ID |
| --- | --- |
| 2db1f49d9d0083cd4032f7e78e42fbfcdcbf06a17c440dfd5e4f8b8bf1ceb972 | af6555579e72f6c41dcc2405b554ae23a106fd8964953946b2c458d8ed376c05 |
| 72415e44e6489b944eb448b531563a62b08ac15c00e744e89874fe0b5b6f8ec6 | 2b5b685f68dc558cf9140e2c926d9fbbde76fc471469c910fc7f3ca57c3cf4c5 |
| 85972e641c372952681bf6c770ab9fb4106ae4b3df30513e273111e16c17e40e | eb54aee1dd65232c30818da9f1a868024d3426a84f0fe4f70c345f1ee00fc646 |
| 9803d9bdbe296cb4708cdf40513af610c5227a2ce2e031bb3e348c496473c078 | 832ae9ac5416e69325d48213465b7cae844827b750dab0735472be9e7b5869da |
| af90e42937c53402f88ac9b6e6cc65878b1fffa1220b7ade5efd6711cb922049 | ba80a954a49955a0cb8adeb4bd38b3de6aa1b49af3713645a7b859d6d8a63c68 |

### OZ 전체 평가 대 선택 평가 (2025-09, 각 30,146묶음)

| 전략 | 전체 평가 알림 | 선택 평가 알림 | ID·순서 포함 일치 |
| --- | --- | --- | --- |
| SPECIAL1 | 30 | 30 | True |
| SPECIAL2 | 25 | 25 | True |
| SPECIAL3 | 0 | 0 | True |
| SPECIAL4 | 0 | 0 | True |
| SPECIAL5 | 16 | 16 | True |
| SPECIAL6 | 24 | 24 | True |
| SPECIAL7 | 0 | 0 | True |

발생이 0건인 SPECIAL3/4/7 구간은 양성 알림의 증거로 해석하지 않는다. 선언/동적 등록/선언 밖 오류는 별도 로직 시험으로 확인했다. 실제 적용 TF·프로필 전체는 `dependency_declarations.json`에 있다.

### 첫 주 전후 알림 차이 분류

| 전략 | 25 알림 | 26 알림 | ID 포함 일치 | ID 외 동일 |
| --- | --- | --- | --- | --- |
| SPECIAL1 | 6 | 6 | True | True |
| SPECIAL2 | 6 | 6 | True | True |
| SPECIAL3 | 0 | 0 | True | True |
| SPECIAL4 | 0 | 0 | True | True |
| SPECIAL5 | 5 | 4 | False | False |
| SPECIAL6 | 7 | 7 | True | True |
| SPECIAL7 | 0 | 0 | True | True |

첫 주 비교는 같은 월 시작에서 시작하므로 B/C 경계 변경이 없다. D는 위 월간 전체/선택 비교로 분리 확인했고, E는 판정 입력 비트 일치 시험으로 확인했다. 순수 ID 차이는 A2, SPECIAL4/5 cycle·maintenance 복구에 따른 차이는 A1로 분류한다. 상세 추가·삭제 행은 `comparisons.json`에 있다. C 작업 크기 비교는 사용자 지시로 생략했다.

## 키프레임 변환 실측

| 조각 | 묶음 | 키프레임 | 기존 MB | MSD2 MB | 복원 |
| --- | --- | --- | --- | --- | --- |
| 2024-09 | 28656 | 21 | 149.83 | 219.38 | 일치 |
| 2024-10 | 31689 | 23 | 165.24 | 241.86 | 일치 |
| 2024-11 | 28647 | 21 | 150.27 | 220.10 | 일치 |
| 2024-12 | 28739 | 21 | 148.86 | 218.64 | 일치 |
| 2025-01 | 30153 | 22 | 152.14 | 221.61 | 일치 |
| 2025-02 | 27391 | 20 | 139.56 | 202.78 | 일치 |
| 2025-03 | 28930 | 21 | 146.76 | 213.34 | 일치 |
| 2025-04 | 28933 | 21 | 147.56 | 214.58 | 일치 |
| 2025-05 | 30148 | 22 | 158.91 | 232.53 | 일치 |
| 2025-06 | 28699 | 21 | 150.21 | 220.31 | 일치 |
| 2025-07 | 31438 | 23 | 165.03 | 241.87 | 일치 |
| 2025-08 | 28932 | 21 | 151.38 | 221.10 | 일치 |
| 2025-09 | 30146 | 22 | 158.90 | 232.35 | 일치 |

합계: 1.985GB → 2.900GB. 이전 녹화·차분 파일은 보존했다. 현재 창고 목록은 MSD2를 가리킨다. MSD2를 모르는 수정본25 비교는 보존된 `old_captures.json`의 MSD1 조각 목록을 사용했다. 변환 상세와 각 원본 SHA256은 `keyframe_conversion.json`.

## 1주 단독 측정 (2025-09-01~07)

후보 측정은 다른 재생 작업 종료 후 한 번씩 순차 실행했다. 전 수치는 수정본25의 같은 입력/방법 실측을 사용했다. 시간은 ms/묶음, 메모리는 MiB. 입력 포함은 worker의 스트리밍 구간이며, 초기 생성/결과 병합을 포함하는 연간 총 경과와 구분한다.

| 판 | 전략 | 입력 포함 | 엔진 | 입력/호스트 | 최대 RSS |
| --- | --- | --- | --- | --- | --- |
| 25 | SPECIAL1 | 21.553 | 13.745 | 7.807 | 383.4 |
| 26 | SPECIAL1 | 21.843 | 8.517 | 13.326 | 296.9 |
| 25 | SPECIAL2 | 28.749 | 20.894 | 7.855 | 413.4 |
| 26 | SPECIAL2 | 41.148 | 27.218 | 13.930 | 304.8 |
| 25 | SPECIAL3 | 17.793 | 9.878 | 7.914 | 403.1 |
| 26 | SPECIAL3 | 16.813 | 5.008 | 11.805 | 242.6 |
| 25 | SPECIAL4 | 14.741 | 6.929 | 7.812 | 440.2 |
| 26 | SPECIAL4 | 24.632 | 9.367 | 15.265 | 246.0 |
| 25 | SPECIAL5 | 15.599 | 7.773 | 7.826 | 474.4 |
| 26 | SPECIAL5 | 16.961 | 3.880 | 13.081 | 447.2 |
| 25 | SPECIAL6 | 16.558 | 8.672 | 7.886 | 442.5 |
| 26 | SPECIAL6 | 19.948 | 4.851 | 15.097 | 330.4 |
| 25 | SPECIAL7 | 16.650 | 8.881 | 7.769 | 466.0 |
| 26 | SPECIAL7 | 18.262 | 3.003 | 15.259 | 257.3 |

| 판 | 전략 | COMPOSER | FVG | FVG_STATE | INDICATOR | OZ | OZ_STATE | SWEEP | SWEEP_STATE | WATCH_CONDITIONS | 엔진 기타 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | SPECIAL1 | 1.356 | 0.000 | 0.000 | 4.622 | 0.002 | 6.580 | 0.003 | 0.009 | 0.000 | 1.173 |
| 26 | SPECIAL1 | 2.510 | 0.000 | 0.000 | 2.320 | 0.003 | 1.910 | 0.005 | 0.015 | 0.000 | 1.753 |
| 25 | SPECIAL2 | 8.782 | 0.000 | 0.000 | 0.000 | 0.003 | 8.327 | 0.864 | 1.062 | 0.000 | 1.856 |
| 26 | SPECIAL2 | 17.813 | 0.000 | 0.000 | 0.000 | 0.004 | 3.746 | 1.393 | 1.867 | 0.000 | 2.394 |
| 25 | SPECIAL3 | 0.672 | 0.348 | 0.944 | 0.000 | 0.001 | 6.574 | 0.002 | 0.009 | 0.162 | 1.166 |
| 26 | SPECIAL3 | 0.972 | 0.468 | 1.632 | 0.000 | 0.002 | 0.557 | 0.003 | 0.011 | 0.216 | 1.145 |
| 25 | SPECIAL4 | 0.026 | 0.000 | 0.000 | 0.000 | 0.001 | 6.384 | 0.003 | 0.010 | 0.000 | 0.506 |
| 26 | SPECIAL4 | 8.366 | 0.000 | 0.000 | 0.000 | 0.004 | 0.478 | 0.005 | 0.060 | 0.000 | 0.455 |
| 25 | SPECIAL5 | 0.038 | 0.000 | 0.000 | 0.000 | 0.002 | 7.209 | 0.003 | 0.010 | 0.000 | 0.511 |
| 26 | SPECIAL5 | 0.674 | 0.000 | 0.000 | 0.000 | 0.004 | 2.877 | 0.004 | 0.016 | 0.000 | 0.306 |
| 25 | SPECIAL6 | 0.833 | 0.157 | 0.269 | 0.000 | 0.001 | 6.659 | 0.002 | 0.009 | 0.000 | 0.742 |
| 26 | SPECIAL6 | 1.846 | 0.289 | 0.573 | 0.000 | 0.003 | 1.448 | 0.004 | 0.014 | 0.000 | 0.673 |
| 25 | SPECIAL7 | 0.237 | 0.000 | 0.000 | 1.441 | 0.001 | 6.541 | 0.003 | 0.009 | 0.000 | 0.649 |
| 26 | SPECIAL7 | 0.400 | 0.000 | 0.000 | 1.512 | 0.003 | 0.440 | 0.004 | 0.013 | 0.000 | 0.630 |

SPECIAL4/5의 기존 낮은 비용에는 빠진 CHAINS 작업이 있었으므로 속도만 비교해 정확성 복구 비용을 회귀로 판단하지 않는다. 전체 값은 `weekly_measurements.csv`.

## 1년 SPECIAL1 병렬 실측

XAUUSD+ 2024-10-01~2025-10-01, 겹침 3거래일. i5-14500: 물리 14 / 논리 20. 각 조합은 순차 측정했다. 총 경과에는 runner의 검증·호스트 초기화·pool·결과 병합을 포함한다. CLI의 별도 plan/녹화 확인 단계는 제외한다. 생성 틱은 경고로 기록하고 실행했다.

수정본25 기록: 14 workers 설정/12 월 작업, runner 2,528.21초, CLI 전체 2,532.39초, 첫 진행률 590.85초. 근거는 `검증결과/engine_optimization/year_special1.json`·`year_special1.jsonl`·`year_special1_wall.json`. 당시 첫 진행률은 pool 시작 기준이며 새 parent 시간은 검증/초기화까지 포함한다. 이번 동일 코드의 keyframe/beginning 직접 비교는 사용자 지시로 생략했으므로, 과거 수치는 참고값이다.

| 작업 | 설정 workers | 실제 workers | 시작 | 총 초 | 첫 진행률 초 | 묶음 | 워밍업 묶음 | 워밍업 비율 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MONTH | 12 | 12 | keyframe | 1407.40 | 5.17 | 403159 | 49314 | 12.23% |
| MONTH | 6 | 6 | keyframe | 1657.57 | 5.15 | 403159 | 49314 | 12.23% |

| 비교 | 알림 수 | ID 포함 일치 |
| --- | --- | --- |
| year_month_6_keyframe__year_month_12_keyframe | 475 / 475 | True |

**생략(사용자 지시): 14 workers, 2주 작업, 기존 시작 방식 비교.** 14 workers의 부분 진행 기록은 보존하지만 완료 측정값으로 사용하지 않는다. 따라서 MONTH/FORTNIGHT 알림 차이와 연간 keyframe/beginning 알림 동일성은 미검증이다. 키프레임 복원 해시와 관련 연속성 로직 시험 결과는 위에 별도로 기록했다.

사용자가 확정한 기본값: **MONTH / 12 workers**. 완료된 6/12 비교에서 12가 더 빨랐다. 전체 후보 중 최적이라고 주장하지 않는다. 결정 근거는 `measured_default.json`. 성능 허용치/게이트는 없다.

각 worker가 맡은 기간과 묶음 수는 `year_*.json`의 `worker_distribution`, 논리 코어별·worker별·작업별 묶음 수는 `year_*_cpu_distribution.csv`에 있다. 묶음 종료 시 CPU 샘플이므로 OS의 코어 이동을 포함한다. 코어 고정 측정은 아니다.

## 사용법

기존 GUI/CLI 선택 실행은 새 기본값을 사용한다. CLI `run`의 `--work-size MONTH|FORTNIGHT`, `--cores N`, `--capture-start keyframe|beginning`, `--oz-evaluation selected|all`로 비교/재설정한다. 설정 파일 → 시나리오 → 명시 CLI 값 순으로 덮어쓴다. 사용자 config 파일은 바꾸지 않았다.

## 시험 수정 및 무결성

test_data_selection의 저장 확인은 MSD2 공통 reader/verifier로 옮기고 실패 rollback/이동 검사를 유지했다. test_numpy_processors의 진행봉 fixture가 전체 이력을 덮어쓰던 부분을 마지막 행 변경으로 고치고 과거 정정 시험을 별도로 추가했다. test_oz_rewrite의 checkpoint mock은 공개 export_memory API를 따른다. test_event_e1의 Windows 파이프 writer는 reader가 끝날 때까지 연결을 유지하도록 fixture만 동기화했다. 최종 신규 시험의 Windows 기본 임시 폴더 접근 오류는 수정본 내부 basetemp로 해결했다. 원 실패 기록도 남긴다.

무결성 체인: `Part1/audit/remediation/45-parallel-oz`. 새 오류 0건; 이전 오류 10건은 유지. `build/part1_immutable_sha256.json` 재생성. 수정본25 보존 확인: 2092파일 / 변경 0건.

## 수정 파일 목록

- `AGENTS.md.txt`
- `Part1/program/SPECIAL/SPECIAL5.py`
- `Part1/program/event_application.py`
- `Part1/program/event_composer_domain.py`
- `Part1/program/event_engine/facts.py`
- `Part1/program/event_engine/oz_processor.py`
- `Part1/program/event_engine/recurrence.py`
- `Part1/program/event_oz_selection.py`
- `Part1/program/event_selection.py`
- `Part1/program/indicator_facts.py`
- `Part1/program/indicator_facts_numpy.py`
- `Part1/program/monitor_OZ.py`
- `Part1/program/oz_engine/controllers.py`
- `Part1/program/oz_engine/profile.py`
- `Part1/program/oz_engine/runtime.py`
- `Part1/program/watch_array_facts.py`
- `Part2/event_backtest/__main__.py`
- `Part2/event_backtest/bridge.py`
- `Part2/event_backtest/build_plan.py`
- `Part2/event_backtest/keyframes.py`
- `Part2/event_backtest/runner.py`
- `Part2/event_backtest/settings.py`
- `Part2/event_backtest/storage.py`
- `Part2/event_backtest/system.py`
- `build/analyze_parallel_arbitration.py`
- `build/analyze_parallel_year.py`
- `build/check_parallel_evidence.py`
- `build/compare_parallel_signal_ids.py`
- `build/convert_parallel_keyframes.py`
- `build/diagnose_parallel_special4.py`
- `build/document_parallel_declarations.py`
- `build/edit_parallel_a.py`
- `build/edit_parallel_a2.py`
- `build/finalize_parallel_report.py`
- `build/find_parallel_special4.py`
- `build/measure_parallel_oz.py`
- `build/measure_parallel_year.py`
- `build/record_parallel_measurement_stop.py`
- `build/run_parallel_after_checks.py`
- `build/run_parallel_cases.py`
- `build/run_parallel_measurements.py`
- `build/run_parallel_positive_month.py`
- `build/seal_parallel_oz.py`
- `build/setup_parallel_oz.py`
- `build/summarize_parallel_measurements.py`
- `build/verify_parallel_behavior.py`
- `tests/test_data_selection.py`
- `tests/test_event_e1.py`
- `tests/test_numpy_processors.py`
- `tests/test_oz_rewrite.py`
- `tests/test_parallel_oz.py`
- `수정내역_병렬_OZ최적화.md`
- `검증결과/parallel_oz/` (시험·변환·비교·측정 증거)
