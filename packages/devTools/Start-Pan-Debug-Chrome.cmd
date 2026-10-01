@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Pan-Debug-Chrome.ps1" %*
if errorlevel 1 pause
