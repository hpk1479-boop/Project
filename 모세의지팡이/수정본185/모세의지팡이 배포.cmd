@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0통합설치\모세의지팡이 배포.ps1" %*
if errorlevel 1 (
    echo.
    echo Build launcher failed. See the message above.
    pause
    exit /b 1
)
exit /b %errorlevel%
