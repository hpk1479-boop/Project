"""Independent MT5 process inventory. No other application files are read."""
import json
import os
from pathlib import Path
import time
import subprocess
from ..contracts import ROOT,GenericError


def running_processes():
    """Read process identities only; no terminal IPC, launch or account APIs."""
    command = r"""[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" | ForEach-Object { @{pid=[int]$_.ProcessId; executable=$_.ExecutablePath; portable=[bool]($_.CommandLine -match '(?i)(^|\s)/portable(\s|$)'); created_at=([DateTimeOffset]$_.CreationDate).ToUnixTimeSeconds()} }) | ConvertTo-Json -Compress"""
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
            capture_output=True,text=True,encoding='utf-8',timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise GenericError('E_MT5_DISCOVERY','실행 중인 프로세스 목록을 읽을 수 없습니다.')
        rows=json.loads(result.stdout or '[]')
        return rows if isinstance(rows,list) else [rows]
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        raise GenericError('E_MT5_DISCOVERY','프로세스 조회 실패') from exc



