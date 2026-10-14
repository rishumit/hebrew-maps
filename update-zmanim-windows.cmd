@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set HMAPS_LOG_FILE=%~dp0data\reports\zmanim-log.txt
if not exist data\reports mkdir data\reports
echo.
echo  Updating synagogue prayer times from Nedarim Plus (zmanim project), about 10-15 minutes.
echo  Recommended once a week. Best on Friday around noon: synagogues that list only Shabbat
echo  prayers get their names then. The map keeps working and switches to the new data at the end.
echo  Log: data\reports\zmanim-log.txt
echo.
.venv\Scripts\python.exe -m hmaps.etl zmanim %*
echo.
echo  Done (exit code %ERRORLEVEL%).
echo  To run it every Friday automatically:
echo    schtasks /Create /SC WEEKLY /D FRI /ST 11:50 /TN "HebrewMaps zmanim" /TR "cmd /c set HMAPS_NO_PAUSE=1&& \"%~f0\""
if "%HMAPS_NO_PAUSE%"=="" pause
