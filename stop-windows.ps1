# Stop the Hebrew Maps server - whichever copy is running:
# the dev server started by start-windows.cmd (python -m uvicorn hmaps.api.app),
# the installed app (hebrew-maps.exe), or anything else listening on the port.
$port = if ($env:HMAPS_PORT) { [int]$env:HMAPS_PORT } else { 8080 }
$ids = @()
$ids += Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*hmaps.api.app*" } | ForEach-Object { $_.ProcessId }
$ids += Get-Process -Name "hebrew-maps" -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }
$ids += Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }
$ids = $ids | Where-Object { $_ -and $_ -ne 0 -and $_ -ne $PID } | Sort-Object -Unique
if ($ids) {
  foreach ($id in $ids) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) { Write-Host ("Stopping {0} (PID {1})" -f $p.ProcessName, $id); Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  }
  Start-Sleep -Milliseconds 800
  if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $port is still in use - try running stop-windows.cmd as administrator."
  } else { Write-Host "Stopped." }
} else { Write-Host "The server is not running." }
