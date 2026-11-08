@echo off
call "%~dp0scripts\windows\download-satellite.cmd" %*
exit /b %ERRORLEVEL%