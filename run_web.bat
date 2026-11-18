@echo off
title BerthMonitor Web
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
if exist "venv\Scripts\python.exe" set PY=venv\Scripts\python.exe
%PY% -c "import requests, bs4, certifi" 2>nul || %PY% -m pip install requests beautifulsoup4 certifi
%PY% server.py
pause
