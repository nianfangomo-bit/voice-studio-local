@echo off
cd /d "%~dp0"
if exist "%~dp0engine\runtime\python.exe" (
  "%~dp0engine\runtime\python.exe" -I -X utf8 -B launcher.py
) else (
  python -I -X utf8 launcher.py
)
if errorlevel 1 pause
