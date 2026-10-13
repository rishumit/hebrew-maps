@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe -c "import PIL" 2>NUL || tools-bin\uv\uv.exe pip install --python .venv\Scripts\python.exe "pillow>=10"
.venv\Scripts\python.exe tools\media.py %*
pause
