@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0engine\runtime\python.exe" -X utf8 -B offline_check.py %*
pause
