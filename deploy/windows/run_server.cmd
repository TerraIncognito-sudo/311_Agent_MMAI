@echo off
rem Starts the 311 site from the repo root. Used by the "Gilead311" scheduled
rem task (see docs/deployment.md). Output goes to logs\server.log.
cd /d "%~dp0\..\.."
if not exist logs mkdir logs
".venv\Scripts\python.exe" run.py serve >> logs\server.log 2>&1
