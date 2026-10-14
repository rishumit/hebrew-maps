@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe tools\michlol_diag.py
