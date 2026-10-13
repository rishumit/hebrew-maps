@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-windows.ps1"
timeout /t 2 >nul
