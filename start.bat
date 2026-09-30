@echo off
rem Start the video-agent service with the project .venv Python.
rem Double-click this file. Keep this window open while the service runs.
rem (ASCII-only and goto-based on purpose: avoids cmd parsing issues.)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto noenv
".venv\Scripts\python.exe" run.py
goto end
:noenv
echo [start.bat] .venv not found. Create it first:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
:end
echo.
echo [start.bat] Service stopped. Read any messages above before closing.
pause
