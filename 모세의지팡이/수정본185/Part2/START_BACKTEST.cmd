@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem Independent environment/CLI utility. The only UI is START_MOSES.pyw.
if defined BACKTEST_PYTHON (
  "%BACKTEST_PYTHON%" -c "import sys,struct;assert sys.version_info[:2] in ((3,12),(3,13));assert struct.calcsize('P')==8" >nul 2>nul
  if not errorlevel 1 (
    "%BACKTEST_PYTHON%" "%~dp0bootstrap.py" %*
    goto finished
  )
)
py -3.12 -c "import struct;assert struct.calcsize('P')==8" >nul 2>nul
if not errorlevel 1 (
  py -3.12 "%~dp0bootstrap.py" %*
  goto finished
)
py -3.13 -c "import struct;assert struct.calcsize('P')==8" >nul 2>nul
if not errorlevel 1 (
  py -3.13 "%~dp0bootstrap.py" %*
  goto finished
)
python -c "import sys,struct;assert sys.version_info[:2] in ((3,12),(3,13));assert struct.calcsize('P')==8" >nul 2>nul
if not errorlevel 1 (
  python "%~dp0bootstrap.py" %*
  goto finished
)
echo [오류] Python 3.12/3.13 64비트를 찾지 못했습니다.
echo [조치] pip를 포함한 Python을 설치하거나 BACKTEST_PYTHON을 python.exe로 지정해 주세요.
echo 프로젝트 데이터는 변경되지 않았습니다.
pause
exit /b 1
:finished
if errorlevel 1 (
  echo [오류] 백테스트 실행 환경 준비에 실패했습니다.
  echo [확인] 이 폴더의 BACKTEST_SETUP.log를 열어 원인을 확인해 주세요.
  pause
  exit /b 1
)
endlocal
