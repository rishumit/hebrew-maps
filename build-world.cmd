@echo off
rem Builds the low-zoom world map (web\world\world.json) from Natural Earth - about a minute,
rem downloads ~15 MB once. Commit web\world\world.json afterwards: it ships with the app.
cd /d "%~dp0"
set PYTHONUTF8=1
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m tools.world %*
) else (
  where py >nul 2>nul && (py -3 -m tools.world %*) || (python -m tools.world %*)
)
pause
