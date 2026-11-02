@echo off
rem Stops the site: ends the Gilead311 task, then the Python process listening
rem on the site's port (ending the task alone leaves Python running) and its
rem venv launcher parent. Port defaults to 8311; pass another as %1.
set PORT=%1
if "%PORT%"=="" set PORT=8311
schtasks /end /tn Gilead311 >nul 2>&1
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess); if ($p.Name -eq 'python.exe') { $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId); Stop-Process -Id $p.ProcessId -Force; if ($parent.Name -eq 'python.exe') { Stop-Process -Id $parent.ProcessId -Force } } }"
ping -n 3 127.0.0.1 >nul
