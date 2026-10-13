@echo off
rem Builds the optional topographic layers (contour lines + hillshade) - about 10-20 minutes,
rem downloads ~530 MB of elevation data (Copernicus DEM) once. The map works without them.
cd /d "%~dp0"
set PYTHONUTF8=1
if exist tools-bin\uv\uv.exe (
  tools-bin\uv\uv.exe pip install --python .venv\Scripts\python.exe -r requirements-terrain.txt
) else (
  uv pip install --python .venv\Scripts\python.exe -r requirements-terrain.txt
)
.venv\Scripts\python.exe -m tools.terrain build %*
pause
