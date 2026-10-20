@echo off
rem Download govmap aerial photos (orthophoto) for offline use -> data\tiles\satellite.mbtiles
rem   download-satellite.cmd                       - all of Israel, zoom 7-13 (about 6,000 tiles, ~130MB)
rem   download-satellite.cmd --bbox 34.75,32.03,34.83,32.12 --minzoom 12 --maxzoom 17
rem                                                - one area (minlon,minlat,maxlon,maxlat) in more detail
rem   download-satellite.cmd --dry-run             - only show how many tiles / MB it would be
rem Already downloaded tiles are skipped, so running it again resumes an interrupted download.
rem From the map: layers button - "save the current area for offline use".
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup-windows.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m hmaps.etl satellite %*
if errorlevel 1 (
  echo.
  echo The download stopped - check the internet connection and run this file again to resume.
) else (
  echo.
  echo Done. The aerial photos of this area now show on the map also without internet.
)
pause
