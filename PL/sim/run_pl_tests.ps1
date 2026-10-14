[CmdletBinding()]
param(
    # Tests to run: p0 p1 p3 p4 top, or ALL.
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $Test = @('ALL')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$simDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = (Resolve-Path (Join-Path $simDir '..\src')).Path
$buildDir = Join-Path ([System.IO.Path]::GetTempPath()) 'rfclock_pl_sim'
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

$sources = @(
    'p0_clock_reset.v'
    'p1_timebase_pps.v'
    'p2_control_regs.v'
    'p3_tx_player.v'
    'p4_rx_capture.v'
    'p6_status.v'
    'p_async_fifo.v'
    'p_top.v'
) | ForEach-Object { Join-Path $srcDir $_ }

$testMap = [ordered]@{
    p0  = 'tb_p0_clock_reset'
    p1  = 'tb_p1_timebase_pps'
    p3  = 'tb_p3_tx_player'
    p4  = 'tb_p4_rx_capture'
    top = 'tb_p_top'
}

$selected = [System.Collections.Generic.List[string]]::new()
foreach ($requested in $Test) {
    if ($requested -ieq 'ALL') {
        $testMap.Keys | ForEach-Object { if (-not $selected.Contains($_)) { $selected.Add($_) } }
    } elseif ($testMap.Contains($requested.ToLowerInvariant())) {
        $name = $requested.ToLowerInvariant()
        if (-not $selected.Contains($name)) { $selected.Add($name) }
    } else {
        Write-Error "Unknown test '$requested'. Valid names: $($testMap.Keys -join ' ') ALL"
    }
}

try {
    $iverilogCommand = (Get-Command iverilog -ErrorAction Stop).Source
} catch {
    Write-Error 'iverilog was not found in PATH.'
}

# Scoop exposes iverilog through a shim, but system.vpi needs the DLLs in the
# real application bin directory: put it on PATH and call the real executable.
$iverilogCommandDir = Split-Path -Parent $iverilogCommand
if ((Split-Path -Leaf $iverilogCommandDir) -ieq 'shims') {
    $scoopIverilogBin = Join-Path (Split-Path -Parent $iverilogCommandDir) 'apps\iverilog\current\bin'
    $scoopIverilogExe = Join-Path $scoopIverilogBin 'iverilog.exe'
    if (Test-Path -LiteralPath $scoopIverilogExe) {
        $env:PATH = "$scoopIverilogBin;$env:PATH"
        $iverilogCommand = $scoopIverilogExe
    }
}
$vvpCommand = Join-Path (Split-Path -Parent $iverilogCommand) 'vvp.exe'
if (-not (Test-Path -LiteralPath $vvpCommand)) {
    $vvpCommand = (Get-Command vvp -ErrorAction Stop).Source
}

$failed = [System.Collections.Generic.List[string]]::new()
foreach ($name in $selected) {
    $top = $testMap[$name]
    $tbPath = Join-Path $simDir "$top.v"
    $outputPath = Join-Path $buildDir "$top.vvp"

    Write-Host "[BUILD] $name"
    $compileOutput = @(
        & $iverilogCommand '-g2012' '-Wall' "-I$(Split-Path -Parent $srcDir)" '-s' $top '-o' $outputPath @sources $tbPath 2>&1 |
            ForEach-Object { "$($_)" }
    )
    if ($LASTEXITCODE -ne 0) {
        $compileOutput | ForEach-Object { Write-Host $_ }
        $failed.Add($name)
        Write-Host "[FAIL ] $name (iverilog exit $LASTEXITCODE)" -ForegroundColor Red
        continue
    }

    Write-Host "[RUN  ] $name"
    # The testbenches print "PASS <top>" or "FAIL ..." and always $finish.
    $runOutput = @(& $vvpCommand '-n' $outputPath 2>&1 | ForEach-Object { "$($_)" })
    $runOutput | Where-Object { $_ -match '^(PASS|FAIL)' } | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0 -or -not ($runOutput -match "^PASS $top")) {
        $failed.Add($name)
        Write-Host "[FAIL ] $name" -ForegroundColor Red
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
