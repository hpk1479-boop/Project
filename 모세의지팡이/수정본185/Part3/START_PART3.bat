@echo off
rem Starts PART3 without a console window (same as double-clicking START_PART3.pyw).
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
where pyw >nul 2>&1
if not errorlevel 1 (
    start "" pyw -3 START_PART3.pyw
) else (
    start "" pythonw START_PART3.pyw
)
