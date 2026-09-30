[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $Test = @('ALL')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
$srcDir = (Resolve-Path (Join-Path $repoDir 'FPGA\src')).Path
$simDir = (Resolve-Path (Join-Path $repoDir 'FPGA\sim')).Path
$buildDir = Join-Path ([System.IO.Path]::GetTempPath()) 'rfclock_iverilog'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$testMap = [ordered]@{
    H01 = @{
        Top = 'tb_H01_clock_reset_guard'
        Dut = 'H01_clock_reset_guard.v'
        Tb  = 'tb_H01_clock_reset_guard.v'
    }
    H02 = @{
        Top = 'tb_H02_AXI_control_regs'
        Dut = 'H02_AXI_control_regs.v'
        Tb  = 'tb_H02_AXI_control_regs.v'
    }
    H03 = @{
        Top = 'tb_H03_raw_tick'
        Dut = 'H03_raw_tick.v'
        Tb  = 'tb_H03_raw_tick.v'
    }
    H04 = @{
        Top = 'tb_H04_pps_capture'
        Dut = 'H04_pps_capture.v'
        Tb  = 'tb_H04_pps_capture.v'
    }
    H05 = @{
        Top = 'tb_H05_scheduled_execute'
        Dut = 'H05_scheduled_execute.v'
        Tb  = 'tb_H05_scheduled_execute.v'
    }
    H08 = @{
        Top = 'tb_H08_PRN_generator'
        Dut = 'H08_PRN_generator.v'
        Tb  = 'tb_H08_PRN_generator.v'
    }
}

function Show-Usage {
    @'
Usage:
  .\run_testbench.ps1             Run all registered testbenches
  .\run_testbench.ps1 H05         Run one testbench
  .\run_testbench.ps1 H01 H03     Run selected testbenches

Valid test names: H01 H02 H03 H04 H05 H08 ALL
'@ | Write-Host
}

if ($Test -contains '-h' -or $Test -contains '--help') {
    Show-Usage
    exit 0
}

$selected = [System.Collections.Generic.List[string]]::new()
foreach ($requested in $Test) {
    $name = $requested.ToUpperInvariant()
    if ($name -eq 'ALL') {
        foreach ($known in $testMap.Keys) {
            if (-not $selected.Contains($known)) {
                $selected.Add($known)
            }
        }
    } elseif ($testMap.Contains($name)) {
        if (-not $selected.Contains($name)) {
            $selected.Add($name)
        }
    } else {
        Write-Error "Unknown test '$requested'. Valid names: H01 H02 H03 H04 H05 H08 ALL"
    }
}

try {
    $iverilogCommand = (Get-Command iverilog -ErrorAction Stop).Source
} catch {
    Write-Error 'iverilog was not found in PATH.'
}

# Scoop normally exposes iverilog through a shim.  The Windows Icarus
# package's system.vpi depends on DLLs in the real application bin directory,
# so add that directory explicitly and invoke the real executable when it is
# available.  This avoids the misleading system.vpi load warning.
$iverilogCommandDir = Split-Path -Parent $iverilogCommand
if ((Split-Path -Leaf $iverilogCommandDir) -ieq 'shims') {
    $scoopRoot = Split-Path -Parent $iverilogCommandDir
    $scoopIverilogBin = Join-Path $scoopRoot 'apps\iverilog\current\bin'
    $scoopIverilogExe = Join-Path $scoopIverilogBin 'iverilog.exe'
    if (Test-Path -LiteralPath $scoopIverilogExe) {
        $env:PATH = "$scoopIverilogBin;$env:PATH"
        $iverilogCommand = $scoopIverilogExe
    }
}

$vvpCommandInfo = Get-Command vvp -ErrorAction SilentlyContinue
if ($null -ne $vvpCommandInfo) {
    $vvpCommand = $vvpCommandInfo.Source
} else {
    $iverilogDir = Split-Path -Parent $iverilogCommand
    $vvpCandidate = Join-Path $iverilogDir 'vvp.exe'
    if (Test-Path -LiteralPath $vvpCandidate) {
        $vvpCommand = $vvpCandidate
    } else {
        Write-Error 'vvp was not found in PATH or next to iverilog.'
    }
}

$failed = [System.Collections.Generic.List[string]]::new()
foreach ($name in $selected) {
    $entry = $testMap[$name]
    $dutPath = Join-Path $srcDir $entry.Dut
    $tbPath = Join-Path $simDir $entry.Tb
    $outputPath = Join-Path $buildDir ("rfclock_{0}.out" -f $name.ToLowerInvariant())

    foreach ($path in @($dutPath, $tbPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Error "Missing source file: $path"
        }
    }

    Write-Host "[BUILD] $name"
    $compileOutput = @(
        & $iverilogCommand '-g2012' ("-I{0}" -f $srcDir) '-s' $entry.Top '-o' $outputPath $dutPath $tbPath 2>&1 |
            ForEach-Object { "$($_)" }
    )
    $compileExitCode = $LASTEXITCODE

    $hasSystemVpiWarning = $compileOutput -match 'Failed to open .*system\.vpi'
    if ($hasSystemVpiWarning) {
        Write-Host '[WARN ] iverilog emitted the known Scoop system.vpi startup warning; continuing.' -ForegroundColor Yellow
    }

    if ($compileExitCode -ne 0) {
        $compileOutput | ForEach-Object { Write-Host $_ }
        $failed.Add($name)
        Write-Host "[FAIL ] $name (iverilog exit $compileExitCode)" -ForegroundColor Red
        continue
    }

    Write-Host "[RUN  ] $name"
    & $vvpCommand $outputPath
    if ($LASTEXITCODE -ne 0) {
        $failed.Add($name)
        Write-Host "[FAIL ] $name (vvp exit $LASTEXITCODE)" -ForegroundColor Red
    } else {
        Write-Host "[PASS ] $name" -ForegroundColor Green
    }
}

if ($failed.Count -ne 0) {
    Write-Host ("Failed tests: {0}" -f ($failed -join ', ')) -ForegroundColor Red
    exit 1
}

Write-Host ("All selected testbenches passed: {0}" -f ($selected -join ', ')) -ForegroundColor Green
exit 0
