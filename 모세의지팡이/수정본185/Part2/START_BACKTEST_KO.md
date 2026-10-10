# 백테스트 실행 — 통합 화면과 독립 CLI

## 통합 화면

프로젝트 루트의 `START_MOSES.pyw`를 실행하고 백테스트 화면을 사용하세요. 브라우저를 사용하려면 `Part3/START_PART3.pyw` 또는 `python Part3/run.py`를 실행합니다. Part1·Part2의 개별 프로그램 UI는 제거했으며 데이터 구축과 백테스트 계산은 기존 Part2 엔진이 담당합니다.

통합 화면의 설치 방법과 종료 정책은 프로젝트 루트의 `README_MOSES_DESKTOP.md`를 확인하세요. Part2에는 웹 UI를 호출하는 코드가 없으며 아래 독립 CLI로도 실행할 수 있습니다.

## 계산 환경 준비와 검사

Windows용 Python 3.12 또는 3.13, 64비트 본체와 pip가 필요합니다. 첫 패키지 설치에는 패키지 저장소 연결이 필요합니다. 소스 폴더에 Python 본체를 포함한 완전 오프라인 실행파일은 아닙니다.

`Part2` 폴더에서 `START_BACKTEST.cmd`를 실행하면 계산 환경을 준비합니다. 이 명령은 창을 열지 않습니다. 실행기는 `BACKTEST_PYTHON`, `py -3.12`, `py -3.13`, `python` 순서로 실행 가능한 Python을 찾습니다. 자동 검색이 실패하면 현재 PC에서 선택한 Python 실행파일을 `BACKTEST_PYTHON` 환경변수로 지정하세요.

```console
START_BACKTEST.cmd
START_BACKTEST.cmd --history
START_BACKTEST.cmd --check
```

`--history`는 MT5 수집 환경도 준비합니다. `--check`는 설치 없이 두 환경을 검사합니다. 가상환경의 실제 실행·import·64비트·prefix를 검사하며 GUI용 Tcl/Tk는 요구하지 않습니다. 손상된 환경은 `.backup-날짜`로 보존하고 현재 위치에서 다시 구성합니다. 설치 실패 시 실패 환경을 `.failed-날짜`로 남긴 뒤 원래 환경 경로를 복원합니다. 시장 데이터·전략·계정·터미널 설정을 수정하거나 삭제하지 않습니다.

## 웹 UI 없이 실행

`START_BACKTEST.cmd --backtest` 뒤에 기존 `event_backtest` 인자를 전달합니다.

```console
START_BACKTEST.cmd --backtest --help
START_BACKTEST.cmd --backtest strategies
START_BACKTEST.cmd --backtest plan --scenario scenarios/xau_selected.json
START_BACKTEST.cmd --backtest run --scenario scenarios/xau_selected.json --yes
```

이미 환경이 준비되어 있으면 같은 엔진을 직접 실행할 수도 있습니다.

```console
.venv-generic\Scripts\python.exe -m event_backtest --help
.venv-generic\Scripts\python.exe -m event_backtest plan --scenario scenarios/xau_selected.json
```

위 명령의 작업 폴더는 `Part2`입니다. 프로젝트나 창고를 옮긴 경우 환경을 다시 검사하고 현재 PC에서 창고 위치와 MT5 프로필을 지정하세요. 데이터 구축 승인, 거래시간·WATCH·OZ 조건, 확정봉/진행봉 기준, 주문·청산 계산은 변경하지 않았습니다.

환경 준비 기록은 `BACKTEST_SETUP.log`, 기술 원본은 `BACKTEST_SETUP_RAW.log`에 저장됩니다. 백테스트 CLI의 진행과 결과는 콘솔 및 기존 창고 실행 기록에 남습니다. 과거 검증 문서의 GUI 실행 설명은 현재 실행 방법으로 사용하지 마세요.
