@echo off
cd /d "%~dp0"
"%~dp0engine\runtime\python.exe" -I -X utf8 -B launcher.py
if errorlevel 1 pause
