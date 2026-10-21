# Start the Hebrew Maps server (no Docker) and show the map: in the desktop app
# (Tauri, src-tauri/) when it is built or installed, otherwise in the browser.
#   HMAPS_NO_BROWSER=1  start the server only
#   HMAPS_BROWSER=1     always use the browser, even if the desktop app exists
#   HMAPS_DESKTOP_EXE   explicit path of hebrew-maps-desktop.exe
Set-Location -Path $PSScriptRoot
$port = if ($env:HMAPS_PORT) { $env:HMAPS_PORT } else { "8080" }
$hostIp = if ($env:HMAPS_HOST) { $env:HMAPS_HOST } else { "127.0.0.1" }
$url = "http://localhost:$port"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Please run setup-windows.cmd first."; Read-Host; exit 1 }

function Healthy { try { Invoke-WebRequest -UseBasicParsing "$url/api/health" -TimeoutSec 2 | Out-Null; return $true } catch { return $false } }

if (-not (Healthy)) {
  $env:PYTHONUTF8 = "1"
  $env:HMAPS_ROUTER = "local"
  Start-Process -FilePath $py -WorkingDirectory $PSScriptRoot -WindowStyle Minimized `
    -ArgumentList @("-m", "uvicorn", "hmaps.api.app:app", "--host", $hostIp, "--port", $port)
  for ($i = 0; $i -lt 40; $i++) { if (Healthy) { break }; Start-Sleep -Milliseconds 500 }
}
if ($env:HMAPS_NO_BROWSER) { exit 0 }

# Desktop app: built here (installer\build.ps1 / cargo build) or installed by the setup EXE.
# It is started in "dev mode" (HMAPS_URL): it only shows this server, it does not start its own.
$desktop = $null
if (-not $env:HMAPS_BROWSER) {
  $candidates = @(
    $env:HMAPS_DESKTOP_EXE,
    (Join-Path $PSScriptRoot "src-tauri\target\release\hebrew-maps-desktop.exe"),
    (Join-Path $PSScriptRoot "installer\dist\hebrew-maps\hebrew-maps-desktop.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\HebrewMaps\hebrew-maps-desktop.exe")
  )
  $desktop = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if ($desktop) {
  $env:HMAPS_URL = "$url/desktop.html"
  Start-Process -FilePath $desktop
} else {
  Start-Process $url
}
