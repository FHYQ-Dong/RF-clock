<#
.SYNOPSIS
Snapshot the working tree as the version it carries: checked in a worktree of its own, its
wheel in dist/, ready to release.

.DESCRIPTION
Run it when a change calls for a new version (docs/ABI.md §14.1), before the first edit for
that change: the working tree then holds the finished version that pyproject.toml still
names.  The script

- writes the working tree (tracked files and untracked ones not ignored) as a git tree
  object, through an index of its own, and keeps it as refs/snapshots/<version>, so that it
  outlives git gc and a later commit by version needs no state rebuilt:
  git commit-tree -S -p <parent> -F <message> refs/snapshots/<version>;
- checks that tree out into a worktree in a temporary directory, runs pytest and pyright
  there and builds the wheel;
- when both pass, copies the wheel to dist/, beside dist/rf_clock-<version>.json (the tree,
  the commit it was taken on, the results, the wheel's sha256) and the log.

The worktree is removed at the end.  It takes a few minutes: run it in the background and go
on with the next version.

    pwsh tools/snapshot_version.ps1
#>
param([string]$WorkDir = (Join-Path ([System.IO.Path]::GetTempPath()) 'rf-clock-snapshots'))

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot
Set-Location $repo
$version = ([regex]'(?m)^version = "([^"]+)"').Match((Get-Content pyproject.toml -Raw)).Groups[1].Value
$dist = Join-Path $repo 'dist'
New-Item -ItemType Directory -Force $dist, $WorkDir | Out-Null
$log = Join-Path $dist "rf_clock-$version.log"
Set-Content $log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') snapshot of rf-clock $version"

# The working tree as a tree object, through an index of its own (the real one is not touched).
$index = Join-Path $WorkDir "index-$version"
Copy-Item (git rev-parse --git-path index) $index -Force
$env:GIT_INDEX_FILE = $index
git add -A
$tree = git write-tree
Remove-Item Env:GIT_INDEX_FILE
Remove-Item $index
$base = git rev-parse HEAD
git update-ref "refs/snapshots/$version" $tree
Add-Content $log "tree $tree on $base (refs/snapshots/$version)"

# A worktree holding exactly that tree.
$wt = Join-Path $WorkDir $version
if (Test-Path $wt) { git worktree remove --force $wt }
git worktree prune
git worktree add --detach $wt HEAD *>> $log
git -C $wt read-tree -u --reset $tree *>> $log

# The checks and the wheel, with one environment for every snapshot; the lock file stays as it is.
$env:UV_PROJECT_ENVIRONMENT = Join-Path $WorkDir 'venv'
Push-Location $wt
$pytest = uv run --frozen -q pytest -q -p no:cacheprovider 2>&1; $pytestCode = $LASTEXITCODE
$pytest | Add-Content $log
$pyright = uv run --frozen -q pyright 2>&1; $pyrightCode = $LASTEXITCODE
$pyright | Add-Content $log
uv build --wheel -o (Join-Path $wt 'dist') *>> $log
$buildCode = $LASTEXITCODE
Pop-Location
$passed = $pytestCode -eq 0 -and $pyrightCode -eq 0 -and $buildCode -eq 0
$record = [ordered]@{
    version = $version; tree = $tree; base = $base; time = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    pytest = ($pytest | Select-Object -Last 1).ToString(); pyright = ($pyright | Select-Object -Last 1).ToString()
    passed = $passed; wheel = $null; sha256 = $null
}
if ($passed) {
    $wheel = Get-ChildItem (Join-Path $wt 'dist') -Filter "rf_clock-$version-*.whl" | Select-Object -First 1
    Copy-Item $wheel.FullName $dist -Force
    $record.wheel = $wheel.Name
    $record.sha256 = (Get-FileHash (Join-Path $dist $wheel.Name) -Algorithm SHA256).Hash.ToLower()
}
$record | ConvertTo-Json | Set-Content (Join-Path $dist "rf_clock-$version.json")
git worktree remove --force $wt
Add-Content $log "=== $(if ($passed) { 'passed' } else { 'FAILED' }): $($record.pytest); $($record.pyright)"
exit $(if ($passed) { 0 } else { 1 })
