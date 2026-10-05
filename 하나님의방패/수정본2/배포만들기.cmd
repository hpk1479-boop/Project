@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0배포만들기.exe"
if errorlevel 1 pause
endlocal
