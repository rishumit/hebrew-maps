@echo off
call "%~dp0scripts\windows\update-windows.cmd" %*
exit /b %ERRORLEVEL%