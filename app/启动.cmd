@echo off
cd /d "%~dp0"
if exist "%~dp0engine\runtime\python.exe" (
  "%~dp0engine\runtime\python.exe" -B launcher.py
) else (
  python launcher.py
)
if errorlevel 1 pause
