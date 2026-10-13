# Hebrew Maps - first-time setup / full data refresh (Windows, Docker Desktop)
#   Run:  setup.cmd   (or: powershell -ExecutionPolicy Bypass -File setup.ps1)
#   Options: -Update   refresh all data (download a new Israel extract)
#            -SkipTiles / -SkipRouting   skip the heavy steps
param([switch]$Update, [switch]$SkipTiles, [switch]$SkipRouting, [switch]$NoBrowser)

$ErrorActionPreference = "Continue"  # native tools report errors via exit codes (checked in Run)
Set-Location -Path $PSScriptRoot
$port = 8080
if (Test-Path ".env") {
  $line = Select-String -Path ".env" -Pattern '^\s*HMAPS_PORT\s*=\s*(\d+)' | Select-Object -First 1
  if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}
$url = "http://localhost:$port"
# data volume folders: the app runs as an unprivileged user (uid 10001)
$prepareVolume = "mkdir -p /data/sources /data/work /data/reports /data/tiles/build && chown 10001:10001 /data /data/sources /data/work /data/reports && chmod 777 /data/tiles/build"
$started = Get-Date

function Step($n, $total, $text) { Write-Host ""; Write-Host "[$n/$total] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host ""; Write-Host "ERROR: $text" -ForegroundColor Red; Write-Host "Press Enter to close"; Read-Host | Out-Null; exit 1 }
function Run([string[]]$cmd) {
  & $cmd[0] $cmd[1..($cmd.Length - 1)]
  if ($LASTEXITCODE -ne 0) { throw "command failed: $($cmd -join ' ')" }
}

Write-Host "==============================================" -ForegroundColor Blue
Write-Host "  Hebrew Maps (mapot be-ivrit) - setup" -ForegroundColor Blue
Write-Host "==============================================" -ForegroundColor Blue

# ------------------------------------------------------------------ checks
Step 1 7 "Checking Docker Desktop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Fail "Docker Desktop is not installed. Install it from https://www.docker.com/products/docker-desktop/ , start it, and run setup again."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
  Warn "Docker is not running - trying to start Docker Desktop..."
  $dd = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
  if (Test-Path $dd) { Start-Process $dd }
  for ($i = 0; $i -lt 60; $i++) { Start-Sleep 5; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop did not start. Open it manually, wait until it says 'Running', and run setup again." }
}
$mem = [int64](docker info --format "{{.MemTotal}}")
$memGb = [math]::Round($mem / 1GB, 1)
if ($memGb -lt 5.5) { Warn "Docker has only $memGb GB of memory. 6 GB or more is recommended (Docker Desktop > Settings > Resources, or .wslconfig)." }
else { Ok "Docker is running ($memGb GB memory)" }
$free = (Get-PSDrive -Name (Get-Location).Drive.Name).Free / 1GB
if ($free -lt 8) { Warn ("Only {0:N1} GB free on this drive; about 6 GB are needed." -f $free) }

try {
  # ---------------------------------------------------------------- images
  Step 2 7 "Building the application image (first time: a few minutes)"
  Run @("docker", "compose", "build", "api")
  Ok "image ready"

  Step 3 7 "Starting the web app (demo data until the real data is ready)"
  Run @("docker", "compose", "up", "-d", "api")
  Ok "running at $url"
  if (-not $NoBrowser) { Start-Process $url }

  # ---------------------------------------------------------------- data
  Step 4 7 "Downloading the map of Israel from OpenStreetMap (~150 MB)"
  Run @("docker", "compose", "run", "--rm", "--user", "root", "etl", "sh", "-c", $prepareVolume)
  $dl = @("docker", "compose", "run", "--rm", "etl", "python", "-m", "hmaps.etl", "download")
  if ($Update) { $dl += "--force" }
  Run $dl
  Ok "downloaded"

  Step 5 7 "Building the places / streets / search database (10-20 minutes)"
  Run @("docker", "compose", "run", "--rm", "etl", "python", "-m", "hmaps.etl", "build")
  Ok "database ready - search now uses the full data of Israel"
  # public transport timetables (optional; Ministry of Transport GTFS)
  $gt = @("compose", "run", "--rm", "etl", "python", "-m", "hmaps.etl", "gtfs")
  if ($Update) { $gt += "--force" }
  & docker @gt
  if ($LASTEXITCODE -eq 0) { Ok "public transport timetables ready" }
  else { Warn "public transport timetables were not built (optional) - retry: docker compose run --rm etl python -m hmaps.etl gtfs" }

  if (-not $SkipRouting) {
    Step 6 7 "Preparing navigation (car / walking / bicycle) - runs in the background"
    if ($Update) { Run @("docker", "compose", "restart", "routing") } else { Run @("docker", "compose", "up", "-d", "routing") }
    Ok "routing is being prepared (about 10-20 minutes, see: docker compose logs -f routing)"
  }

  if (-not $SkipTiles) {
    Step 7 7 "Building the full basemap (downloads ~1 GB of coastline data; 15-40 minutes)"
    Run @("docker", "compose", "run", "--rm", "tiles")
    Run @("docker", "compose", "run", "--rm", "--user", "root", "etl", "sh", "-c", "mv -f /data/tiles/build/israel.mbtiles /data/tiles/israel.mbtiles && chmod 644 /data/tiles/israel.mbtiles")
    Ok "basemap ready"
  }

  Run @("docker", "compose", "up", "-d", "api", "routing")
} catch {
  Fail "$($_.Exception.Message)`nSee the messages above. You can run setup again - finished steps are fast the second time."
}

# ------------------------------------------------------------------ wait for routing
if (-not $SkipRouting) {
  Write-Host ""
  Write-Host "Waiting for navigation to become available (you can already use the map)..." -ForegroundColor Cyan
  for ($i = 0; $i -lt 120; $i++) {
    try {
      $meta = Invoke-RestMethod "$url/api/meta" -TimeoutSec 5
      if ($meta.routing.car -and $meta.routing.foot -and $meta.routing.bike) { Ok "navigation ready"; break }
    } catch { }
    Start-Sleep 15
    if ($i % 4 -eq 3) { Write-Host "  ...still preparing routes" }
  }
}

$mins = [math]::Round(((Get-Date) - $started).TotalMinutes)
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  Done in $mins minutes.  Open: $url" -ForegroundColor Green
Write-Host "  Data quality report: $url/reports" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
if (-not $NoBrowser) { Start-Process $url }
Write-Host "Press Enter to close"; Read-Host | Out-Null
