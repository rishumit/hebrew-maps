# Hebrew Maps - installation WITHOUT Docker (Windows 10/11, no admin rights needed)
#   Run: setup-windows.cmd     Options: -Update  -SkipBasemap  -NoBrowser
param([switch]$Update, [switch]$SkipBasemap, [switch]$NoBrowser)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
$env:PYTHONUTF8 = "1"
$env:HMAPS_ROUTER = "local"
$started = Get-Date

function Step($n, $total, $text) { Write-Host ""; Write-Host "[$n/$total] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Fail($text) {
  Write-Host ""; Write-Host "ERROR: $text" -ForegroundColor Red
  Write-Host "You can run setup-windows.cmd again - finished steps are skipped."
  Write-Host "Press Enter to close"; Read-Host | Out-Null; exit 1
}

Write-Host "==============================================" -ForegroundColor Blue
Write-Host "  Hebrew Maps - setup without Docker" -ForegroundColor Blue
Write-Host "==============================================" -ForegroundColor Blue

# ------------------------------------------------------------------ 1. uv (Python manager, user-level)
Step 1 6 "Preparing Python (downloaded automatically, nothing is installed system-wide)"
$uv = Join-Path $PSScriptRoot "tools-bin\uv\uv.exe"
if (-not (Test-Path $uv)) {
  $onPath = Get-Command uv -ErrorAction SilentlyContinue
  if ($onPath) { $uv = $onPath.Source }
  else {
    try {
      New-Item -ItemType Directory -Force -Path "tools-bin\uv" | Out-Null
      $zip = Join-Path $env:TEMP "uv-win.zip"
      Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
      Expand-Archive -Force -Path $zip -DestinationPath "tools-bin\uv"
      Remove-Item $zip -ErrorAction SilentlyContinue
      $found = Get-ChildItem -Path "tools-bin\uv" -Recurse -Filter uv.exe | Select-Object -First 1
      if ($found -and $found.FullName -ne $uv) { Copy-Item $found.FullName $uv -Force }
    } catch { Fail "Could not download uv (check the internet connection): $($_.Exception.Message)" }
  }
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  & $uv venv .venv --python 3.12
  if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment." }
}
& $uv pip install --python ".venv\Scripts\python.exe" -r requirements-native.txt
if ($LASTEXITCODE -ne 0) { Fail "Could not install the Python packages." }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Ok "Python ready"

# ------------------------------------------------------------------ 2. web assets + demo data
Step 2 6 "Downloading the map library and fonts"
& $py tools\fetch_assets.py
if ($LASTEXITCODE -ne 0) { Fail "Could not download the web assets." }
Ok "assets ready"

# ------------------------------------------------------------------ 3. start the server (demo data)
Step 3 6 "Starting the map (demo data until the real data is ready)"
# the app was called "מפות בעברית" until v1.1 – drop the old shortcut
$oldShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "מפות בעברית.lnk"
if (Test-Path $oldShortcut) { Remove-Item $oldShortcut -Force -ErrorAction SilentlyContinue }
$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "מפות ישראל.lnk"
try {
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut($shortcut)
  $lnk.TargetPath = Join-Path $PSScriptRoot "start-windows.cmd"
  $lnk.WorkingDirectory = $PSScriptRoot
  $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
  $lnk.Save()
  Ok "desktop shortcut created"
} catch { Warn "could not create a desktop shortcut" }
if ($NoBrowser) { $env:HMAPS_NO_BROWSER = "1" }
& (Join-Path $PSScriptRoot "start-windows.ps1")
Ok "map is running at http://localhost:8080"

# ------------------------------------------------------------------ 4. OSM download
Step 4 6 "Downloading the map of Israel from OpenStreetMap (~150 MB)"
$dl = @("-m", "hmaps.etl", "download")
if ($Update) { $dl += "--force" }
& $py @dl
if ($LASTEXITCODE -ne 0) { Fail "Download failed." }
Ok "downloaded"

# ------------------------------------------------------------------ 5. database + routing graph
Step 5 6 "Building places, streets, search and navigation (15-30 minutes)"
& $py -m hmaps.etl build --engine pyosmium
if ($LASTEXITCODE -ne 0) { Fail "Building the data failed. Make sure at least 4 GB of RAM are free." }
Ok "full data of Israel is live (the open map switched automatically)"

# ------------------------------------------------------------------ 5b. public transport timetables (optional)
if (-not $env:HMAPS_SKIP_TRANSIT) {
  Write-Host ""; Write-Host "[+] Public transport timetables (Ministry of Transport GTFS, ~150 MB, 2-5 minutes)" -ForegroundColor Cyan
  $gt = @("-m", "hmaps.etl", "gtfs")
  if ($Update) { $gt += "--force" }
  & $py @gt
  if ($LASTEXITCODE -eq 0) { Ok "public transport ready (timetables are valid for about a month - update weekly)" }
  else { Warn "public transport timetables were not built - everything else works. Retry: update-transit-windows.cmd" }
}

# ------------------------------------------------------------------ 6. basemap (optional)
if (-not $SkipBasemap) {
  Step 6 6 "Building the full basemap (portable Java; 15-40 minutes)"
  & $py tools\basemap.py
  if ($LASTEXITCODE -eq 0) { Ok "basemap ready" }
  else { Warn "basemap was not built - the map keeps working with the simple basemap. You can retry later: update-windows.cmd" }
}

# ------------------------------------------------------------------ extra: topographic layers (optional, built once)
if (-not $env:HMAPS_SKIP_TERRAIN) {
  Write-Host ""; Write-Host "[+] Topographic layers: contour lines, hillshade, elevation profiles (optional; ~530 MB download once, 10-20 minutes)" -ForegroundColor Cyan
  & $uv pip install --python $py -r requirements-terrain.txt
  if ($LASTEXITCODE -eq 0) { & $py -m tools.terrain build --if-missing --clean }
  if ($LASTEXITCODE -eq 0) { Ok "topographic layers ready" }
  else { Warn "topographic layers were not built - everything else works. You can retry later: build-terrain.cmd" }
}

$mins = [math]::Round(((Get-Date) - $started).TotalMinutes)
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done in $mins minutes.  Open: http://localhost:8080" -ForegroundColor Green
Write-Host "  Next time: double-click the desktop shortcut (or start-windows.cmd)" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "Press Enter to close"; Read-Host | Out-Null
