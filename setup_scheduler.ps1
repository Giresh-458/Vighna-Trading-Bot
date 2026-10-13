# Windows Task Scheduler Setup for Vighna Trading Bot
# Runs Monday to Friday:
# Trigger 1: 08:55 AM
# Trigger 2: At Logon (if laptop was opened after 8:55 AM)
# Stops at 11:00 PM (after MCX EOD square-off)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

$actionStart = New-ScheduledTaskAction -Execute "C:\Users\krono\AppData\Local\Programs\Python\Python311\python.exe" -Argument "C:\Users\krono\Desktop\Vighna\startup_check.py"
$triggerTime  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:55AM
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn

Register-ScheduledTask -TaskName "Vighna_Start" -Action $actionStart -Trigger @($triggerTime, $triggerLogon) -Settings $settings -Description "Auto-start Vighna Trading Bot and Dashboard" -Force

$actionStop = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c C:\Users\krono\Desktop\Vighna\stop_bot.bat"
$triggerStop = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 11:00PM
Register-ScheduledTask -TaskName "Vighna_Stop" -Action $actionStop -Trigger $triggerStop -Settings $settings -Description "Auto-stop Vighna Trading Bot and Dashboard" -Force

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Windows Scheduled Tasks Registered Successfully!" -ForegroundColor Green
Write-Host " - Vighna_Start: Triggers at 08:55 AM AND on User Login" -ForegroundColor Cyan
Write-Host " - Vighna_Stop:  Auto stops Mon-Fri at 11:00 PM" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Green
