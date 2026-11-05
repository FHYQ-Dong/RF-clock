<#
.SYNOPSIS
Back up results/ to the group server with restic.

.DESCRIPTION
results/ is kept out of Git, and Syncthing's copy on RF-clock-PC-ai is not a
backup: a deletion on either side propagates.  Each run adds one restic
snapshot of results/ to the repository on the group server
(Y:\Haoyu\RF-clock\results, by its UNC path, since a scheduled task may not
see mapped drives).  restic uploads only new data, and results are written
once, so a run costs about the size of the results written since the last one.  Every
snapshot stays restorable, so a file deleted or overwritten here can be got
back.  The repository has an empty password (the lab server is trusted; restic
always encrypts, so restic is still needed to read it).  Output is appended
to %LOCALAPPDATA%\rf-clock-backup\backup.log.

    pwsh tools/backup_results.ps1

The scheduled task "RF-clock results backup" runs it every 4 hours (see .NOTES):
the server is now and then unreachable, and Task Scheduler does not rerun a
task that exits nonzero, so a missed run waits for the next one.
Inspect and restore with restic itself:

    $env:RESTIC_REPOSITORY = '\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results'
    restic --insecure-no-password snapshots
    restic --insecure-no-password restore latest --target Z:\restore --include /D/Program/Research/RF-clock/results/<dir>

.NOTES
The scheduled task was registered with (pwsh runs under conhost --headless because
pwsh's -WindowStyle Hidden still flashes a console window at every run):

    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\conhost.exe" -Argument (
        "--headless `"$((Get-Command pwsh).Source)`" -NoProfile -NonInteractive -File `"D:\Program\Research\RF-clock\tools\backup_results.ps1`"")
    $trigger = New-ScheduledTaskTrigger -Once -At 09:00 -RepetitionInterval (New-TimeSpan -Hours 4)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName 'RF-clock results backup' -Action $action -Trigger $trigger -Settings $settings
#>

$ErrorActionPreference = 'Stop'
$env:RESTIC_REPOSITORY = '\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results'
$results = Join-Path (Split-Path $PSScriptRoot) 'results'
$log = "$env:LOCALAPPDATA\rf-clock-backup\backup.log"

Add-Content $log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') backup $results"
# Syncthing's partial files: ~syncthing~*.tmp on Windows, .syncthing.*.tmp elsewhere.
restic --insecure-no-password backup $results --exclude '~syncthing~*.tmp' --exclude '.syncthing.*.tmp' 2>&1 | Add-Content $log
$code = $LASTEXITCODE
Add-Content $log "=== exit $code"
exit $code
