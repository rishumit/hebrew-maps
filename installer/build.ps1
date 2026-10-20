# installer\build.ps1
# Build the Hebrew Maps Windows installer.
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass installer\build.ps1
#
# Optional parameters:
#   -Version      "1.0.0"  (default)
#   -IsccPath     path to ISCC.exe (default: standard Inno Setup 6 location)
#   -SkipDesktop  do not build the Tauri desktop app (the installer then opens the browser)
#
# Steps: icons (from web\icons\icon.svg) -> PyInstaller server -> Tauri desktop exe
#        (cargo, copied next to the server exe) -> Inno Setup.

param(
    [string]$Version  = "1.0.0",
    [string]$IsccPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [switch]$SkipDesktop
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
# 1. Install PyInstaller + the icon renderer into the venv
# ---------------------------------------------------------------------------
Write-Host "[1/5] Installing PyInstaller and resvg into venv..." -ForegroundColor Cyan

# The .venv is created by uv and has no pip — install with uv when available.
$uv = ".\tools-bin\uv\uv.exe"
if (-not (Test-Path $uv)) { $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source }
if ($uv) {
    & $uv pip install --quiet --python $py "pyinstaller>=6.10" -r requirements-icons.txt
} else {
    & $py -m pip install --quiet "pyinstaller>=6.10" -r requirements-icons.txt
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install pyinstaller failed (exit $LASTEXITCODE)"
    exit 1
}
Write-Host "      PyInstaller ready." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Render all icons locally from web\icons\icon.svg (never downloaded – NetFree)
#    -> installer\icon.ico, web\icons\*.png, src-tauri\icons\*
# ---------------------------------------------------------------------------
Write-Host "[2/5] Generating icons..." -ForegroundColor Cyan

& $py tools\make_icons.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "Icon generation failed (exit $LASTEXITCODE)"
    exit 1
}
Write-Host "      Icons ready." -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. Run PyInstaller
# ---------------------------------------------------------------------------
Write-Host "[3/5] Running PyInstaller (this may take a few minutes)..." -ForegroundColor Cyan

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
# 4. Build the Tauri desktop app (src-tauri) and put it next to hebrew-maps.exe.
#    Only the exe is built (bundle.active=false) – Inno Setup packages it. A plain
#    "cargo build" is enough: tauri's custom-protocol feature is enabled in Cargo.toml.
# ---------------------------------------------------------------------------
Write-Host "[4/5] Building the desktop app (Tauri)..." -ForegroundColor Cyan

$desktopExe = "installer\dist\hebrew-maps\hebrew-maps-desktop.exe"
if (Test-Path $desktopExe) { Remove-Item $desktopExe -Force }
$cargo = (Get-Command cargo -ErrorAction SilentlyContinue).Source
if (-not $cargo -and (Test-Path "$env:USERPROFILE\.cargo\bin\cargo.exe")) { $cargo = "$env:USERPROFILE\.cargo\bin\cargo.exe" }
if ($SkipDesktop) {
    Write-Host "      Skipped (-SkipDesktop): the installer will open the map in the browser." -ForegroundColor Yellow
} elseif (-not $cargo) {
    Write-Host "      Rust (cargo) not found - skipping the desktop app; the installer will open the browser." -ForegroundColor Yellow
    Write-Host "      Install Rust from https://rustup.rs (MSVC toolchain) to build it." -ForegroundColor Yellow
} else {
    & $cargo build --release --locked --manifest-path src-tauri\Cargo.toml
    if ($LASTEXITCODE -ne 0) {
        Write-Error "cargo build (src-tauri) failed (exit $LASTEXITCODE)"
        exit 1
    }
    Copy-Item "src-tauri\target\release\hebrew-maps-desktop.exe" $desktopExe -Force
    Write-Host "      Desktop app ready: $desktopExe" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 5. Run Inno Setup
# ---------------------------------------------------------------------------
Write-Host "[5/5] Building installer with Inno Setup..." -ForegroundColor Cyan

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
