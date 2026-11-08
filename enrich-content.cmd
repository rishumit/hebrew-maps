@echo off
call "%~dp0scripts\windows\enrich-content.cmd" %*
exit /b %ERRORLEVEL%