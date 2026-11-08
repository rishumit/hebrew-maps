@echo off
call "%~dp0scripts\windows\build-exe.cmd" %*
exit /b %ERRORLEVEL%