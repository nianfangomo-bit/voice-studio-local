@echo off
cd /d "%~dp0"
call "%~dp0Start.cmd"
if errorlevel 1 pause
