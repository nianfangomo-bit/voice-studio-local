@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" -Mode migrate
if errorlevel 1 pause
