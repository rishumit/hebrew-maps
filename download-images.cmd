@echo off
call "%~dp0scripts\windows\download-images.cmd" %*
exit /b %ERRORLEVEL%