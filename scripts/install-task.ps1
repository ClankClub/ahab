# Registers Perro's Windows scheduled tasks, hidden. No admin needed.
#
#   "Perro heartbeat"  wakes Perro on the schedule it chooses (starts at login)
#   "Perro telegram"   the Telegram bridge (starts at login; only if
#                      PERRO_TELEGRAM_TOKEN is set)
#   "Perro watchdog"   every 10 minutes, checks the other two and n8n and tells
#                      you on Telegram if one's down (also only with the token)
#
#   .\scripts\install-task.ps1            install (or update) the tasks
#   .\scripts\install-task.ps1 -Remove    uninstall them
#
# Start/stop by hand:  Start-ScheduledTask "Perro heartbeat"
#                      Stop-ScheduledTask  "Perro heartbeat"   (same for "Perro telegram")
# Output goes to home\heartbeat.log, home\telegram.log and home\watchdog.log.
param(
    [switch]$Remove,
    [string]$Pythonw = ""
)

$Tasks = @(
    @{ Name = "Perro heartbeat"; Module = "perro.heartbeat --log --scheduled"
       Description = "Perro's heartbeat: wakes the agent on the schedule it chooses." },
    @{ Name = "Perro telegram"; Module = "perro.telegram --log"
       Description = "Perro's Telegram bridge: passes chat between your phone and Perro." }
)

$Watchdog = "Perro watchdog"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $Watchdog -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Watchdog -Confirm:$false
        Write-Host "Removed '$Watchdog'."
    }
    foreach ($t in $Tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "Removed '$($t.Name)'."
        }
    }
    return
}

# Find pythonw.exe (Python without a console window). `python` may not be on
# the PATH, so ask the py launcher where Python lives and look next to it.
if (-not $Pythonw) {
    $python = if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -c "import sys; print(sys.executable)"
    } else {
        (Get-Command python -ErrorAction Stop).Source
    }
    $Pythonw = Join-Path (Split-Path $python) "pythonw.exe"
}
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found at $Pythonw" }

$repo = Split-Path -Parent $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

# ExecutionTimeLimit 0 = never kill it (the default is 72 hours). IgnoreNew means
# a second copy is never started. Restart: if it dies, retry 3 times, a minute apart.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

$hasToken = [bool][Environment]::GetEnvironmentVariable("PERRO_TELEGRAM_TOKEN", "User")

foreach ($t in $Tasks) {
    if ($t.Name -eq "Perro telegram" -and -not $hasToken) {
        Write-Host "Skipped '$($t.Name)': PERRO_TELEGRAM_TOKEN isn't set (see README)."
        continue
    }
    # The working directory is the repo so `-m perro...` finds the package. API
    # keys come from your user environment variables, which a task running as
    # you inherits at login.
    $action = New-ScheduledTaskAction -Execute $Pythonw `
        -Argument "-X utf8 -m $($t.Module)" -WorkingDirectory $repo
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger `
        -Settings $settings -Force -Description $t.Description | Out-Null
    Write-Host "Installed '$($t.Name)'. It starts at your next login."
}
# The watchdog: a short check every 10 minutes, starting a minute from now, for
# ~10 years. StartWhenAvailable runs a missed check as soon as the PC is back.
if ($hasToken) {
    $action = New-ScheduledTaskAction -Execute $Pythonw `
        -Argument "-X utf8 -m perro.watchdog --log" -WorkingDirectory $repo
    $every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 3650)
    $quick = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $Watchdog -Action $action -Trigger $every `
        -Settings $quick -Force `
        -Description "Checks every 10 minutes that Perro's heartbeat, bridge and n8n are up; tells you on Telegram if not." | Out-Null
    Write-Host "Installed '$Watchdog': every 10 minutes, first check in about a minute."
} else {
    Write-Host "Skipped '$Watchdog': it tells you through the Telegram bot, so it needs PERRO_TELEGRAM_TOKEN."
}
Write-Host "Start them now with:  Start-ScheduledTask 'Perro heartbeat'; Start-ScheduledTask 'Perro telegram'"
