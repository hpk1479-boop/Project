@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0통합설치\launch.ps1" %*
set "MOSES_LAUNCH_EXIT=%errorlevel%"
exit /b %MOSES_LAUNCH_EXIT%
