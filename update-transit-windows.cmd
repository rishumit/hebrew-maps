@echo off
rem Weekly refresh of the public transport timetables only (2-5 minutes; the map keeps running)
rem   update-transit-windows.cmd /quiet   - no pause at the end (for scheduled tasks)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup-windows.cmd first.
  if /i not "%~1"=="/quiet" pause
  exit /b 1
)
".venv\Scripts\python.exe" -m hmaps.etl gtfs --force
if errorlevel 1 (
  echo.
  echo Updating the public transport timetables failed - check the internet connection and try again.
) else (
  echo.
  echo Public transport timetables updated. The open map switches to them automatically.
)
if /i not "%~1"=="/quiet" pause
