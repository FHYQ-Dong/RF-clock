<#
.SYNOPSIS
Back up results/ to the group server as plain files, with rclone.

.DESCRIPTION
results/ is kept out of Git, and Syncthing's copy on RF-clock-PC-ai is not a
backup: a deletion on either side propagates.  Each run makes
Y:\Haoyu\RF-clock\results on the group server match results/ here (rclone
sync), as plain files that can be opened or copied over SMB.  A file deleted
or changed here is not lost there: its previous server copy is first moved to
Y:\Haoyu\RF-clock\results-old\<time of the run>\, at the same relative path
(a run that changes nothing creates no folder).  The server is addressed by
its UNC path, since a scheduled task may not see mapped drives.  Output is
appended to %LOCALAPPDATA%\rf-clock-backup\backup.log.

    pwsh tools/backup_results.ps1

The scheduled task "RF-clock results backup" runs it every 4 hours (see .NOTES):
the server is now and then unreachable, and Task Scheduler does not rerun a
task that exits nonzero, so a missed run waits for the next one.

To get a file back, copy it from results\ (the state at the last run) or, for
an older version, from the results-old\<time>\ folders.  Until 2026-11-05
the backup was a restic repository, kept at Y:\Haoyu\RF-clock\results.restic
(35 snapshots from 2026-10-19 on; empty password):

    restic -r '\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results.restic' --insecure-no-password snapshots

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
$server = '\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock'
$results = Join-Path (Split-Path $PSScriptRoot) 'results'
$log = "$env:LOCALAPPDATA\rf-clock-backup\backup.log"
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'

Add-Content $log "=== $stamp backup $results"
# Syncthing's partial files: ~syncthing~*.tmp on Windows, .syncthing.*.tmp elsewhere.
rclone sync $results "$server\results" --backup-dir "$server\results-old\$stamp" `
    --exclude '~syncthing~*.tmp' --exclude '.syncthing.*.tmp' -v --stats 0 2>&1 | Add-Content $log
$code = $LASTEXITCODE
Add-Content $log "=== exit $code"
exit $code
