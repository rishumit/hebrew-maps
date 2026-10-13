@echo off
cd /d "%~dp0"
docker compose up -d api routing
if errorlevel 1 (echo Docker Desktop must be running. & pause & exit /b 1)
start "" http://localhost:8080
