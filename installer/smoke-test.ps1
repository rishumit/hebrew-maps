# installer/smoke-test.ps1
#
# Starts the built server (installer\dist\hebrew-maps\hebrew-maps.exe) with an empty data
# folder and checks that /api/health answers. Catches broken PyInstaller bundles (for example
# "Importing the numpy C-extensions failed") before they are packaged into the installer.
# 200 = ready, 503 = running without data ("degraded") – both mean the bundle works.
#
#   powershell -ExecutionPolicy Bypass -File installer\smoke-test.ps1 [-Exe path] [-TimeoutSec 90]
param(
    [string]$Exe = "installer\dist\hebrew-maps\hebrew-maps.exe",
    [int]$Port = 48197,
    [int]$TimeoutSec = 90
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Exe)) { Write-Error "Server exe not found: $Exe"; exit 1 }

$data = Join-Path $env:TEMP ("hmaps-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $data | Out-Null
$env:HMAPS_DATA = $data
$env:HMAPS_NO_BROWSER = "1"

Write-Host "Smoke test: $Exe on port $Port"
$proc = Start-Process -FilePath $Exe -ArgumentList "--no-browser", "--host", "127.0.0.1", "--port", "$Port" -PassThru
$ok = $false
$deadline = (Get-Date).AddSeconds($TimeoutSec)
try {
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { Write-Host "Server exited early with code $($proc.ExitCode)"; break }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 3
            $code = [int]$r.StatusCode
        } catch {
            $code = 0
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        }
        if ($code -eq 200 -or $code -eq 503) { Write-Host "  /api/health -> $code"; $ok = $true; break }
        Start-Sleep -Seconds 1
    }
} finally {
    # A broken windowed bundle shows a modal error dialog and never exits on its own.
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue
}

if (-not $ok) {
    Write-Error "Smoke test failed: the server did not answer /api/health within $TimeoutSec s (broken bundle?)."
    exit 1
}
Write-Host "Smoke test passed." -ForegroundColor Green
exit 0
