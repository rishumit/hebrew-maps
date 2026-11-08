@echo off
call "%~dp0scripts\windows\build-world.cmd" %*
exit /b %ERRORLEVEL%