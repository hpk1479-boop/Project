@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title OZ SYSTEM - Python Auto Setup

echo ============================================================
echo   THE STAFF OF MOSES - Python / Package Auto Setup
echo ============================================================
echo.

set "PYEXE="

rem ------------------------------------------------------------
rem 1) Find an existing compatible Python (prefer 3.12)
rem ------------------------------------------------------------
where py >nul 2>&1
if %errorlevel%==0 (
    py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1
    if !errorlevel!==0 set "PYEXE=py -3.12"
)

if not defined PYEXE (
    where python >nul 2>&1
    if !errorlevel!==0 (
        for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "FOUND_PY=%%P"
        if defined FOUND_PY (
            "%FOUND_PY%" -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
            if !errorlevel!==0 set "PYEXE="%FOUND_PY%""
        )
    )
)

rem ------------------------------------------------------------
rem 2) Install Python 3.12 automatically when Python is missing
rem ------------------------------------------------------------
if not defined PYEXE (
    echo [1/4] Compatible Python was not found.
    echo       Installing Python 3.12 with Windows Package Manager...
    echo.

    where winget >nul 2>&1
    if not !errorlevel!==0 (
        echo [ERROR] Windows Package Manager ^(winget^) is not available.
        echo.
        echo Please install "App Installer" from Microsoft Store,
        echo then run this file again.
        echo.
        pause
        exit /b 1
    )

    winget install --id Python.Python.3.12 -e --scope user ^
        --accept-package-agreements --accept-source-agreements

    if not !errorlevel!==0 (
        echo.
        echo [ERROR] Python installation failed.
        echo Check the message above and run this file again.
        echo.
        pause
        exit /b 1
    )

    set "CANDIDATE=%LocalAppData%\Programs\Python\Python312\python.exe"
    if exist "!CANDIDATE!" (
        set "PYEXE="!CANDIDATE!""
    ) else (
        rem py.exe may become available immediately on some systems.
        if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" (
            set "PYEXE="%LocalAppData%\Programs\Python\Launcher\py.exe" -3.12"
        )
    )
)

if not defined PYEXE (
    echo.
    echo [ERROR] Python was installed but this setup could not locate python.exe.
    echo Close this window, reopen Windows, and run this file once more.
    echo.
    pause
    exit /b 1
)

echo [1/4] Python ready:
%PYEXE% --version
if not !errorlevel!==0 (
    echo [ERROR] Python could not be started.
    pause
    exit /b 1
)

rem ------------------------------------------------------------
rem 3) Upgrade pip tooling
rem ------------------------------------------------------------
echo.
echo [2/4] Updating pip / setuptools / wheel...
%PYEXE% -m pip install --upgrade pip setuptools wheel
if not !errorlevel!==0 (
    echo.
    echo [ERROR] pip update failed.
    pause
    exit /b 1
)

rem ------------------------------------------------------------
rem 4) Install all packages used by the OZ system
rem ------------------------------------------------------------
echo.
echo [3/4] Installing OZ packages...
%PYEXE% -m pip install --upgrade numpy pandas pyzmq requests tzdata
if not !errorlevel!==0 (
    echo.
    echo [ERROR] Package installation failed.
    pause
    exit /b 1
)

rem ------------------------------------------------------------
rem 5) Import test
rem ------------------------------------------------------------
echo.
echo [4/4] Verifying installed packages...
%PYEXE% -c "import sys, numpy, pandas, zmq, requests, zoneinfo, tkinter; print('Python:', sys.version.split()[0]); print('numpy:', numpy.__version__); print('pandas:', pandas.__version__); print('pyzmq:', zmq.__version__); print('requests:', requests.__version__); print('tkinter: OK'); print('zoneinfo: OK')"

if not !errorlevel!==0 (
    echo.
    echo [ERROR] Package verification failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   SETUP COMPLETE
echo ============================================================
echo.
echo Python and all required OZ packages are installed.
echo You can now run START_OZ.bat or OZ_SYSTEM CONTROL.pyw.
echo.
pause
exit /b 0
