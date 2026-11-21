@echo off
rem Voice Studio repository test entry (Windows cmd / double-click).
rem Usage: run-tests.cmd -Python "F:\path\to\python.exe"
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-tests.ps1" %*
exit /b %ERRORLEVEL%