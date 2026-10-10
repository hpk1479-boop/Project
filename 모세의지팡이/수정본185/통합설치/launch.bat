@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" %*
set "MOSES_LAUNCH_EXIT=%errorlevel%"
exit /b %MOSES_LAUNCH_EXIT%
