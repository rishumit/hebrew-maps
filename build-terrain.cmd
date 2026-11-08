@echo off
call "%~dp0scripts\windows\build-terrain.cmd" %*
exit /b %ERRORLEVEL%