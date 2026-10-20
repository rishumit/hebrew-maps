@echo off
rem Online data update (the default way to keep the map current): downloads the weekly data
rem release from GitHub - map + search, basemap, terrain, public transport, images, prayer times -
rem checks every file (SHA-256) and swaps it in while the map keeps running.
rem The same as the "Updates" page in the map menu.
rem
rem   update-online-windows.cmd            install every available update
rem   update-online-windows.cmd /check     only show what is new
rem   update-online-windows.cmd /quiet     no pause at the end, output to data\reports\update-log.txt
rem   update-online-windows.cmd /schedule  run it automatically every Friday at 06:10 (Windows Task Scheduler)
rem
rem Building the data yourself from the original sources (power users): update-windows.cmd,
rem update-transit-windows.cmd, update-zmanim-windows.cmd, build-terrain.cmd.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

if /i "%~1"=="/schedule" (
  schtasks /Create /F /SC WEEKLY /D FRI /ST 06:10 /TN "HebrewMaps online update" /TR "\"%~f0\" /quiet"
  if errorlevel 1 (echo Could not create the scheduled task.) else (echo Scheduled: every Friday at 06:10 ^(the data is published on Thursday night^).)
  echo To remove it:  schtasks /Delete /TN "HebrewMaps online update" /F
  pause
  exit /b 0
)

set "HM="
if exist ".venv\Scripts\python.exe" set "HM=.venv\Scripts\python.exe -m hmaps"
if not defined HM if exist "hebrew-maps.exe" set "HM=hebrew-maps.exe"
if not defined HM (
  echo Please run setup-windows.cmd first.
  if /i not "%~1"=="/quiet" pause
  exit /b 1
)

if /i "%~1"=="/check" (
  %HM% update --check
  pause
  exit /b 0
)

if /i "%~1"=="/quiet" (
  if not exist data\reports mkdir data\reports
  %HM% update >> data\reports\update-log.txt 2>&1
  exit /b %ERRORLEVEL%
)

%HM% update %*
echo.
if errorlevel 1 (
  echo Some updates failed - check the internet connection and run this file again
  echo ^(downloads continue where they stopped^).
) else (
  echo Done. The open map switches to the new data by itself.
)
pause
