@echo off
call "%~dp0scripts\windows\build-basemap.cmd" %*
exit /b %ERRORLEVEL%