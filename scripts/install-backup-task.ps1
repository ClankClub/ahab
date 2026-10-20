# Registers a scheduled task that backs up Ahab's home (commit + push to its
# private repo) every few hours, hidden. No admin needed.
#
#   .\scripts\install-backup-task.ps1                 every 6 hours
#   .\scripts\install-backup-task.ps1 -EveryHours 2   a different interval
#   .\scripts\install-backup-task.ps1 -Remove         uninstall
#
# Run it now by hand:  Start-ScheduledTask "Ahab backup"
# Its output goes to home\backup.log.
param(
    [switch]$Remove,
    [int]$EveryHours = 6,
    [string]$Pythonw = ""
)

$TaskName = "Ahab backup"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'."
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

$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found at $Pythonw" }

$action = New-ScheduledTaskAction -Execute $Pythonw `
    -Argument "-X utf8 -m ahab.backup --log" -WorkingDirectory $repo

# Repeat every N hours, starting a minute from now, for ~10 years.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Hours $EveryHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# StartWhenAvailable: if the PC was off or asleep at a scheduled time, run as
# soon as it's back. A backup is quick, so cap it at 10 minutes.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description "Commits and pushes Ahab's home/ to its private backup repo." | Out-Null

Write-Host "Installed '$TaskName': every $EveryHours hours, first run in about a minute."
