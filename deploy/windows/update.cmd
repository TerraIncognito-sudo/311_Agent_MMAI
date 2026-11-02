@echo off
rem Pulls the latest main, updates dependencies and restarts the site.
rem Run on the server from anywhere: deploy\windows\update.cmd
cd /d "%~dp0\..\.."
git pull --ff-only || exit /b 1
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || exit /b 1
call "%~dp0stop_server.cmd"
schtasks /run /tn Gilead311
