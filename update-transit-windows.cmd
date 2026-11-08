@echo off
call "%~dp0scripts\windows\update-transit-windows.cmd" %*
exit /b %ERRORLEVEL%