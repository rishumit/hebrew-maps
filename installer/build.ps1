# installer\build.ps1
# Build the Hebrew Maps Windows installer.
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass installer\build.ps1
#
# Optional parameters:
#   -Version   "1.0.0"  (default)
#   -IsccPath  path to ISCC.exe (default: standard Inno Setup 6 location)

param(
    [string]$Version  = "1.0.0",
    [string]$IsccPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = 'Stop'

# Always run from the project root (one level up from this script's directory)
Set-Location -Path (Join-Path $PSScriptRoot "..")

# ---------------------------------------------------------------------------
# 0. Validate venv exists
# ---------------------------------------------------------------------------
$py  = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Error "Virtual environment not found at .venv\Scripts\python.exe.  Run setup-windows.cmd first."
    exit 1
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Hebrew Maps — Windows Installer Build" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Install PyInstaller into the venv
# ---------------------------------------------------------------------------
Write-Host "[1/4] Installing PyInstaller into venv..." -ForegroundColor Cyan

# The .venv is created by uv and has no pip — install with uv when available.
$uv = ".\tools-bin\uv\uv.exe"
if (-not (Test-Path $uv)) { $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source }
if ($uv) {
    & $uv pip install --quiet --python $py "pyinstaller>=6.10" "pillow>=10"
} else {
    & $py -m pip install --quiet "pyinstaller>=6.10" "pillow>=10"
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install pyinstaller failed (exit $LASTEXITCODE)"
    exit 1
}
Write-Host "      PyInstaller ready." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Generate installer\icon.ico
# ---------------------------------------------------------------------------
Write-Host "[2/4] Generating icon..." -ForegroundColor Cyan

& $py installer\make_icon.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Icon generation failed (exit $LASTEXITCODE)"
    exit 1
}
Write-Host "      Icon ready." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. Run PyInstaller
# ---------------------------------------------------------------------------
Write-Host "[3/4] Running PyInstaller (this may take a few minutes)..." -ForegroundColor Cyan

& $py -m PyInstaller installer\hebrew-maps.spec `
    --distpath installer\dist `
    --workpath installer\build `
    --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed (exit $LASTEXITCODE)"
    exit 1
}
Write-Host "      PyInstaller done: installer\dist\hebrew-maps\" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. Run Inno Setup
# ---------------------------------------------------------------------------
Write-Host "[4/4] Building installer with Inno Setup..." -ForegroundColor Cyan

if (-not (Test-Path $IsccPath)) {
    Write-Host ""
    Write-Host "  Inno Setup 6 not found at: $IsccPath" -ForegroundColor Yellow
    Write-Host "  Download from: https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    Write-Host "  Then rerun with:  installer\build.ps1 -IsccPath `"<path>\ISCC.exe`"" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  PyInstaller output is ready at installer\dist\hebrew-maps\" -ForegroundColor Yellow
    Write-Host "  Open installer\hebrew-maps.iss in Inno Setup manually to finish." -ForegroundColor Yellow
    exit 0
}

& $IsccPath /DMyAppVersion=$Version installer\hebrew-maps.iss
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed (exit $LASTEXITCODE)"
    exit 1
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Green
Write-Host "  Done!  Installer: installer\HebrewMaps-Setup-$Version.exe" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host ""
