@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Pan-Browser-Trace.ps1" %*
if errorlevel 1 pause
