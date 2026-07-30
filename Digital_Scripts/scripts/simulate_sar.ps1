$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceDir = Join-Path $ProjectRoot "Digosc_code"
$SimulationDir = Join-Path $ProjectRoot "sim"
$BuildDir = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("digosc_sar_questa_{0}" -f $PID)
$QuestaRoot = $null
$QuestaRoots = @(
    $env:QUESTA_FSE_ROOT,
    $env:QUESTA_ROOT
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

foreach ($Root in $QuestaRoots) {
    foreach ($Candidate in @($Root, (Join-Path $Root "win64"))) {
        if (Test-Path -LiteralPath (Join-Path $Candidate "vsim.exe")) {
            $QuestaRoot = (Resolve-Path -LiteralPath $Candidate).Path
            break
        }
    }
    if ($null -ne $QuestaRoot) {
        break
    }
}

if ($null -eq $QuestaRoot) {
    $VsimFromPath = Get-Command "vsim.exe" -ErrorAction SilentlyContinue
    if ($null -ne $VsimFromPath) {
        $QuestaRoot = Split-Path -Parent $VsimFromPath.Source
    }
}

if ($null -eq $QuestaRoot) {
    throw (
        "Questa was not found. Add its win64 directory to PATH, or set " +
        "QUESTA_FSE_ROOT/QUESTA_ROOT."
    )
}

$Vlib = Join-Path $QuestaRoot "vlib.exe"
$Vlog = Join-Path $QuestaRoot "vlog.exe"
$Vsim = Join-Path $QuestaRoot "vsim.exe"

if ([string]::IsNullOrWhiteSpace($env:SALT_LICENSE_SERVER)) {
    $MachineLicense = [Environment]::GetEnvironmentVariable(
        "SALT_LICENSE_SERVER",
        "Machine"
    )
    if (-not [string]::IsNullOrWhiteSpace($MachineLicense)) {
        $env:SALT_LICENSE_SERVER = $MachineLicense
    }
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$SimulationSucceeded = $false
Push-Location $BuildDir
try {
    if (-not (Test-Path -LiteralPath "work")) {
        & $Vlib work
        if ($LASTEXITCODE -ne 0) {
            throw "vlib failed with exit code $LASTEXITCODE"
        }
    }

    & $Vlog -sv -lint -work work `
        (Join-Path $SourceDir "filter_down.v") `
        (Join-Path $SourceDir "divider.v") `
        (Join-Path $SourceDir "poly_engine_system.v") `
        (Join-Path $SourceDir "poly_engine_freq.v") `
        (Join-Path $SourceDir "sar_calibrator.v") `
        (Join-Path $SourceDir "adder.v") `
        (Join-Path $SourceDir "filter_up.v") `
        (Join-Path $SourceDir "digital_DSM.v") `
        (Join-Path $SourceDir "DigOsc.v") `
        (Join-Path $SimulationDir "tb_sar_calibrator.sv") `
        (Join-Path $SimulationDir "tb_adder_calibration.sv")
    if ($LASTEXITCODE -ne 0) {
        throw "vlog failed with exit code $LASTEXITCODE"
    }

    function Invoke-SimulationTest {
        param(
            [string]$TopLevel,
            [string]$ExpectedPassText
        )

        $Output = @(
            & $Vsim -c "work.$TopLevel" `
                -do "run -all; quit -f" 2>&1
        )
        $ExitCode = $LASTEXITCODE
        $Output | Write-Host
        $Text = $Output -join "`n"

        if ($ExitCode -ne 0 -or
                $Text -match '\*\* Fatal:' -or
                $Text -notmatch [regex]::Escape($ExpectedPassText)) {
            throw "$TopLevel failed"
        }
    }

    Invoke-SimulationTest `
        -TopLevel "tb_sar_calibrator" `
        -ExpectedPassText `
            "TEST PASS: single-bit SAR, A/B format, range and restart"

    Invoke-SimulationTest `
        -TopLevel "tb_adder_calibration" `
        -ExpectedPassText `
            "TEST PASS: calibrated error, polarity and signed saturation"

    Write-Host "SAR SIMULATION PASS"
    $SimulationSucceeded = $true
}
finally {
    Pop-Location

    $FullTempRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::GetTempPath()
    )
    $FullBuildDir = [System.IO.Path]::GetFullPath($BuildDir)
    $BuildLeaf = Split-Path -Leaf $FullBuildDir
    $IsSafeAutomaticPath = $FullBuildDir.StartsWith(
        $FullTempRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -and $BuildLeaf.StartsWith(
        "digosc_sar_questa_",
        [System.StringComparison]::OrdinalIgnoreCase
    )

    if (-not $IsSafeAutomaticPath) {
        Write-Warning "Refusing to clean an unexpected path: $FullBuildDir"
    }
    elseif ($SimulationSucceeded) {
        Remove-Item -LiteralPath $FullBuildDir -Recurse -Force
        Write-Host "Removed temporary simulation directory: $FullBuildDir"
    }
    else {
        Write-Warning "Simulation failed; retained: $FullBuildDir"
    }
}
