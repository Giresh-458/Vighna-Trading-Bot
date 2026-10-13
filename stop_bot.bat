@echo off
echo Stopping Vighna Trading Bot and Dashboard...

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.main*' -or $_.CommandLine -like '*dashboard.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

echo All Vighna processes stopped.
