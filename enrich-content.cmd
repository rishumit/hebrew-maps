@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set HMAPS_LOG_FILE=%~dp0data\reports\enrich-log.txt
if not exist data\reports mkdir data\reports
echo.
echo  Adding photos, texts and facts to trip sites and trails (Wikimedia Commons, Wikipedia, Wikidata, Wikivoyage).
echo  The first run takes about an hour; later runs reuse the cache and are much faster.
echo  The map keeps working meanwhile and switches to the new data automatically at the end.
echo  Log: data\reports\enrich-log.txt
echo.
.venv\Scripts\python.exe -m hmaps.etl enrich %*
echo.
echo  Updating synagogue prayer times (Nedarim Plus) - a few minutes.
.venv\Scripts\python.exe -m hmaps.etl zmanim
echo.
echo  Done (exit code %ERRORLEVEL%).
pause
