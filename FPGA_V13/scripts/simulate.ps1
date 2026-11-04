$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "toolchain.ps1")
$BuildDir = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("fpga_v13_questa_{0}" -f $PID)
$QuestaRoot = Get-QuestaBinDirectory
$Vlib = Resolve-ToolExecutable `
    -Name "vlib.exe" `
    -CandidatePaths @((Join-Path $QuestaRoot "vlib.exe"))
$Vlog = Resolve-ToolExecutable `
    -Name "vlog.exe" `
    -CandidatePaths @((Join-Path $QuestaRoot "vlog.exe"))
$Vsim = Resolve-ToolExecutable `
    -Name "vsim.exe" `
    -CandidatePaths @((Join-Path $QuestaRoot "vsim.exe"))

if ([string]::IsNullOrWhiteSpace($env:SALT_LICENSE_SERVER)) {
    $MachineLicense = [Environment]::GetEnvironmentVariable(
        "SALT_LICENSE_SERVER",
        "Machine"
    )
    if (-not [string]::IsNullOrWhiteSpace($MachineLicense)) {
        $env:SALT_LICENSE_SERVER = $MachineLicense
    }
}

$Package = Join-Path $ProjectRoot "rtl\fpga_v2_protocol_pkg.sv"
$RtlFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "rtl") `
        -Filter "*.sv" |
        Where-Object { $_.FullName -ne $Package } |
        Select-Object -ExpandProperty FullName
)
$OnChipFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "rtl\on_chip") `
        -Filter "*.sv" |
        Select-Object -ExpandProperty FullName
)
$SimulationFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "sim") `
        -Filter "*.sv" |
        Select-Object -ExpandProperty FullName
)

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$SimulationSucceeded = $false
Push-Location $BuildDir
try {
    & $Vlib work | Out-Null
    & $Vlog -sv -lint -work work `
        $Package $RtlFiles $OnChipFiles $SimulationFiles
    if ($LASTEXITCODE -ne 0) {
        throw "vlog failed with exit code $LASTEXITCODE"
    }

    $Tests = @(
        @{
            Top = "tb_chip_control_driver"
            Pass = "TEST PASS: 474-bit driver edges, data, enable and START hold timing"
        },
        @{
            Top = "tb_adder"
            Pass = "TEST PASS: adder difference, calibrated integration and saturation"
        },
        @{
            Top = "tb_digital_dsm"
            Pass = "TEST PASS: bounded digital DSM endpoints, density and overflow protection"
        },
        @{
            Top = "tb_poly_engines_q20"
            Pass = "TEST PASS: 37-bit signed Q20.16 polynomial constants and products"
        },
        @{
            Top = "tb_sar_calibrator"
            Pass = "TEST PASS: centered SAR range, endpoints and hold behavior"
        },
        @{
            Top = "tb_triangular_decimator_runtime"
            Pass = "TEST PASS: continuous triangular decimation and runtime N update"
        },
        @{
            Top = "tb_filter_down_two_clock"
            Pass = "TEST PASS: FS sampling and 2 MHz data processing are separated"
        },
        @{
            Top = "tb_statistics_capture_buffer"
            Pass = "TEST PASS: 100-point statistics capture, alignment and dual-clock RAM"
        },
        @{
            Top = "tb_fpga_v2_core"
            Pass = "TEST PASS: UI command core, control driver, continuous mu and SAR"
        },
        @{
            Top = "tb_avalon_host_bridge"
            Pass = "TEST PASS: Avalon address decode, exactly-once actions and frozen statistics batch"
        }
    )

    foreach ($Test in $Tests) {
        $Output = @(
            & $Vsim -c "work.$($Test.Top)" `
                -do "run -all; quit -f" 2>&1
        )
        $ExitCode = $LASTEXITCODE
        $Output | Write-Host
        $Text = $Output -join "`n"

        if ($ExitCode -ne 0 -or
                $Text -match "\*\* Fatal:" -or
                $Text -notmatch [regex]::Escape($Test.Pass)) {
            throw "$($Test.Top) failed"
        }
    }

    Write-Host "FPGA V13 Q20.16 RTL SIMULATION PASS"
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
        "fpga_v13_questa_",
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
