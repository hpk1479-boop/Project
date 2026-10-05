# 입력 복원·SPECIAL2 COMPOSER·공유 알림 진단

## 범위와 결론

수정본31을 수정하지 않고 수정본32를 복사했다. 이전 `검증결과`는 복사하지 않았다. 이번에는 기능·전략·EA·Part3를 고치지 않고, 보존 녹화와 기존 실행 결과를 읽어 원인을 진단했다. 테스트 중 네트워크와 텔레그램은 사용하지 않았다. 이전 수정본 수치는 진단 자료이며 정답 기준으로 취급하지 않는다.

1. **MSD2 복원은 실제 비용이다. 그러나 디스크 읽기나 gzip 자체가 가장 큰 원인이라는 가설은 측정과 맞지 않는다.** 매 묶음의 차분 배열 복원·무결성 검사와 그 뒤의 STAFF 검증이 비용을 만든다. 일별 키프레임 시작 비용은 첫 이벤트에 집중되어 있다.
2. **SPECIAL2 COMPOSER는 Fact마다 전체 조건을 반복 평가하는 구조적 병목이다.** 수정본25→26의 과거 1주 표에 나온 2배 증가는 동일 하루 입력의 재측정에서는 재현되지 않았다. 호출 수와 주요 코드가 같아 그 수치를 특정 코드 변경의 효과로 귀속할 근거가 없다.
3. **ALL과 단독의 알림 차이는 선택 전략 간 공유 OZ 감시와 단일 대표 출력 규칙에서 발생한다.** 같은 시장 사건이 다른 SPECIAL 이름으로 출력되거나 SPECIAL5 연쇄의 내부 단계로 소비된 사례를 확인했다. 일부 월간 개별 건은 내부 신호 추적이 없어 원인 확정 대상에서 제외했다.

## 1. MSD2 읽기와 병렬 확장

보존된 XAUUSD+ 2025-09 BAR MSD2의 첫 800묶음(복원 원본 약 762MB)을 같은 프레임의 임시 MSD1로 변환했다. 복원 SHA256은 일치했다. 같은 세션에 MSD2→MSD1→MSD1→MSD2 순으로 읽었다. 단일 프로세스의 이 표본은 1년 병렬 환경 전체를 대표하지 않는다.

| 측정 | MSD1 | MSD2 | 차이 |
| --- | ---: | ---: | ---: |
| 복원 wall 중앙값 | 5.415 ms/묶음 | 5.741 ms/묶음 | +0.326 ms, +6.0% |
| 복원 CPU 중앙값 | 5.225 ms/묶음 | 5.625 ms/묶음 | +0.400 ms, +7.7% |

MSD2 복원 cProfile 800묶음에서 `DeltaCodec.decode` 누적 4.177초, 그 안의 피드별 배열 갱신 `update` 0.902초였다. CRC32 0.703초와 해시 갱신 0.659초도 반복된다. gzip `read` 누적 0.115초, zlib 압축 해제 자체 0.064초였다. 누적 시간은 호출 관계가 겹치므로 합산하지 않는다. MSD2가 날짜마다 키프레임과 gzip 구역을 더 담아 2025-09 조각은 약 232.35MB이며, 수정본25의 MSD1 같은 달 약 158.90MB보다 46% 크다. 용량 증가는 맞지만 단일 읽기 5ms 증가를 설명하지는 못한다.

동일 800묶음을 정규 `CaptureInputs → StaffIngressAdapter → STAFF`까지 통과시키면 wall 15.274ms/묶음이었다. 해당 cProfile에서 `read_indexed` 4.193초, `StaffIngressAdapter.publish` 6.860초, 그 안의 STAFF `receive_publication` 6.063초였다. STAFF 경로에는 Wire decode, CRC, 배열 복사·불변화, Snapshot 게시가 포함된다. 입력/호스트 수치를 곧바로 디스크 시간이라 부르면 안 된다. 다음 날짜 키프레임에서 첫 저장 이벤트까지 0.048초, STAFF bootstrap까지 첫 시장 이벤트 0.094초였다. 일별 키프레임은 묶음마다 재실행되는 병목이 아니다.

기존 1년 SPECIAL1 병렬 기록은 6 worker 1657.57초, 12 worker 1407.40초로 **15.1% 단축**됐다. 그러나 worker CPU 합계는 9551.3→11710.4초(+22.6%), worker 경과 합계는 9627.8→16267.1초였다. `CPU/worker 경과` 비율은 약 99.2%→72.0%로 떨어진다. 12개 월 작업의 경과는 1313~1403초로 비슷해 단일 느린 월보다 동시 실행 자원 경합이 더 유력하다. CPU 스케줄링·메모리 대역·디스크 대기 중 어느 것이 지배적인지는 당시 I/O 카운터가 없어 확정할 수 없다. **6→12 확장 둔화를 디스크·gzip만으로 설명하는 것은 근거 부족**이다.

후속 최적화 후보는 디스크 압축 방식 교체보다 `DeltaCodec`의 피드별 Python 루프·배열 재구성, 중복 CRC/해시와 STAFF 재검증의 안전한 비용 축소이다. LIVE와 재생의 동일 STAFF 경로 및 무손실 검증은 유지해야 한다. 이 문서는 구현을 변경하지 않았다.

## 2. SPECIAL2 COMPOSER

기존 2025-09 첫 주(6726묶음) 표에는 COMPOSER 8.782→17.813ms/묶음(+102.8%)가 기록돼 있다. 같은 실행의 COMPOSER 호출 수는 양쪽 모두 **88,356회**여서 호출 증가가 아니다. 설정 파일 SHA256도 수정본25와 26이 같다. `event_composer_domain.py`의 두 판 차이는 결과 순서를 고정하는 일부 `sorted` 순회이며, SPECIAL2의 주요 조건 재평가 함수 본문은 바뀌지 않았다.

동일한 원본 2025-09-01 하루 1215묶음을 MSD1/2 양쪽에서 비트 동일하게 복원하여 별도 프로세스로 재생했다. 출력 알림은 두 판 모두 3건이고 실행 ID를 제외하면 동일했다.

| 같은 하루, cProfile 제외 | 수정본25 | 수정본26 |
| --- | ---: | ---: |
| 엔진 | 36.052 ms/묶음 | 28.551 ms/묶음 |
| COMPOSER | 17.110 ms/묶음 | 19.240 ms/묶음 |
| COMPOSER 호출 | 15,925 | 15,925 |

이 구간에서 COMPOSER 증가는 약 12.5%여서 과거 1주의 2배 증가가 재현되지 않는다. cProfile을 켜면 호출 계측 오버헤드가 커져 절대 시간은 비교에 쓰지 않았지만, 양쪽 모두 `_reconcile_facts` 14,568회, `_evaluate_symbol_locked` 15,891회, `_evaluate_spec_direction_locked` 381,384회였다. 묶음당 약 314번의 전략 방향 조건 평가다. FACT_SNAPSHOT을 받을 때 `_reconcile_facts`가 갱신 사실과 관련 없는 공식 spec까지 다시 검사하는 것이 **확인된 구조적 비용**이다. 원래 1주 수치가 왜 정확히 2배가 됐는지는 그 시점의 부하/자원 계측이 없어 확정할 수 없다. 하루와 한 주의 분포 차이도 있다. 특정 `sorted` 변경이 2배의 원인이라고 단정하면 안 된다.

## 3. ALL 대 단독 알림

기존 월간 실행의 최종 알림을 동일한 시각·종목·TF·방향·문구와 `signal_id`로 분류했다. 다음 표의 `다른 전략`은 단독 알림과 같은 시장 키가 ALL에서 다른 SPECIAL로 출력된 건수다. `시장 키 없음`은 최종 알림 CSV에 없다는 뜻이지 내부 OZ 신호까지 없었다는 뜻은 아니다.

| 기간 | 전략 | 단독 / ALL | 완전 동일 | 출력 동일·ID 변경 | 다른 전략 출력 | 최종 알림 시장 키 없음 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2025-06 | SPECIAL1 | 40 / 31 | 26 | 5 | 9 | 0 |
| 2025-06 | SPECIAL2 | 15 / 1 | 1 | 0 | 8 | 6 |
| 2025-06 | SPECIAL6 | 36 / 29 | 19 | 10 | 5 | 2 |
| 2025-09 | SPECIAL1 | 30 / 22 | 16 | 6 | 8 | 0 |
| 2025-09 | SPECIAL2 | 25 / 3 | 3 | 0 | 4 | 18 |
| 2025-09 | SPECIAL6 | 24 / 18 | 10 | 8 | 6 | 0 |

코드상 `_arm_oz_locked`는 `spec_id`를 공유 판정 키에 넣지 않는다. 목적지·종목·방향·TF 집합·검증/트리거 모드 등이 같으면 한 OZ child에 여러 `source_spec_ids`를 붙인다. `_dispatch_special_oz_event`는 이 ID들의 handler를 순서대로 호출하고 **첫 non-None 응답에서 종료**한다. 따라서 동일 OZ의 SPECIAL별 알림을 모두 출력하는 구조가 아니다. `_handle_oz_event_core`는 연쇄 내부 OZ 단계의 알림을 숨길 수 있고, 최초 최종 알림 이후 공유 child를 해제한다. 출력 대표와 child ID가 달라지면 문구가 같아도 `signal_id`는 달라질 수 있다.

추가로 보존된 2025-09 첫 주 ALL 내부 SIGNAL 추적을 확인했다. 그 주에 `시장 키 없음`으로 분류된 SPECIAL2 단독 알림 **4건 모두** 같은 시각·TF·방향의 OZ `FINAL_ALERT`가 ALL 내부에는 있었다. 네 이벤트 모두 `source_spec_id=PIPELINE_5`로 귀속됐고 SPECIAL5의 연쇄 단계로 처리됐다. 예를 들어 2025-09-01의 5m·6m LONG 두 건은 ALL 내부 OZ 이벤트와 일치하지만 최종 SPECIAL2 알림은 없었다. 이는 시장 판정 누락이 아니라 공유 감시의 귀속·내부 소비 차이라는 직접 근거다.

첫 주 밖의 `시장 키 없음` 22건(2025-06 SPECIAL2 6·SPECIAL6 2, 2025-09 SPECIAL2 나머지 14)은 보존된 최종 CSV만으로 개별 child·연쇄 귀속을 확정할 수 없다. **이 22건까지 모두 정상 공유 조정이라고 판정하지 않는다.** 이 구간에 내부 SIGNAL 추적을 다시 남기면 event_id·watch_id·source_spec_ids로 개별 원인을 연결할 수 있다. 전략마다 독립 알림이 요구되는지, 공유 child당 한 번의 대표 알림이 요구되는지는 기능 정책 결정이 필요하며 이번 진단에서 바꾸지 않았다.

## 증거와 변경 파일

- 읽기/호스트 시간: `검증결과/msd2_read_cost.json`, `msd2_read_profile.txt`, `input_bridge_cost.json`, `input_bridge_profile.txt`
- SPECIAL2 동일 하루: `검증결과/composer_msd1_sample/sample.json`, `composer_rev25_plain/summary.json`, `composer_rev26_plain/summary.json`, 양쪽 `profile.txt`
- 공유 알림: `검증결과/shared_alert_classification.json`, `shared_alert_cases.json`, `shared_signal_links.json`
- 이전 1주·1년 측정 근거: 수정본26의 `검증결과/parallel_oz/weekly_measurements.json`, `year_analysis.json`; 보존 실행의 `alerts.csv`와 `signals.jsonl.gz`
- 수정본32에 새로 둔 진단 스크립트: `build/diagnose_msd2_cost.py`, `build/diagnose_input_bridge.py`, `build/prepare_composer_probe.py`, `build/diagnose_composer_cost.py`, `build/diagnose_shared_alerts.py`, `build/diagnose_shared_signals.py`

Part1·Part2의 소스·설정 대상 468개 파일을 수정본31과 SHA256으로 대조한 결과 불일치 0건이었다. 기능 소스, 전략 조건, EA, 설정은 수정하지 않았다.
