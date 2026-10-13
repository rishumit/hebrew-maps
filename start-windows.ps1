# Start the Hebrew Maps server (no Docker) and open the browser.
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
if (-not $env:HMAPS_NO_BROWSER) { Start-Process $url }
