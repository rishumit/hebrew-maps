# Stop the Hebrew Maps server started by start-windows.cmd
$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*hmaps.api.app*" }
if ($procs) { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host "Stopped." } else { Write-Host "The server is not running." }
