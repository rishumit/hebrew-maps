@echo off
cd /d "%~dp0"
echo Building Hebrew Maps EXE... (log: installer\build-log.txt)
powershell -NoProfile -ExecutionPolicy Bypass -File installer\build.ps1 > installer\build-log.txt 2>&1
echo EXIT CODE %ERRORLEVEL% >> installer\build-log.txt
echo BUILD-FINISHED >> installer\build-log.txt
