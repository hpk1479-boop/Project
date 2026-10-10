@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-generic\Scripts\python.exe" (
 echo Run START_BACKTEST.cmd once to prepare the original local environment.
 pause
 exit /b 1
)
.venv-generic\Scripts\python.exe -m pip install -r requirements-duckdb.txt
if errorlevel 1 (
 echo Optional DuckDB install failed. The original runtime remains available.
 pause
 exit /b 1
)
echo Optional DuckDB installation complete.
pause
