@echo off
rem Download aerial photos (govmap orthophoto; Sentinel-2 as fallback) for offline use -> data\tiles\satellite*.mbtiles
rem   download-satellite.cmd                    - ALL of Israel: asks for the level and shows tiles / GB / time first
rem                                               1 basic (zoom up to 13, ~3,000 tiles, ~80MB, ~10 min)
rem                                               2 detailed (zoom up to 15, ~41,000 tiles, ~1GB, ~2-3 hours)
rem                                               3 full (zoom up to 16, ~160,000 tiles, ~4GB, ~9 hours)
rem   download-satellite.cmd --level detailed -y            - no questions
rem   download-satellite.cmd --bbox 34.75,32.03,34.83,32.12 --minzoom 12 --maxzoom 17
rem                                             - one area (minlon,minlat,maxlon,maxlat) in more detail
rem   download-satellite.cmd --dry-run          - only show the estimate
rem   download-satellite.cmd --test             - check which imagery servers work from this PC (filter / NetFree)
rem   download-satellite.cmd --source eox       - use a specific source (govmap, govmap-2023, govmap-2022, eox, eox-2016)
rem Already downloaded tiles are skipped: stop any time (Ctrl+C) and run it again to resume.
rem From the map: layers button - "download aerial photos for the whole country".
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
  echo To see which imagery server is blocked:  download-satellite.cmd --test
) else (
  echo.
  echo Done. The downloaded aerial photos now show on the map also without internet.
)
pause
