[CmdletBinding()]
param(
    [string]$BuildRoot
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "toolchain.ps1")
$AutomaticBuildRoot = [string]::IsNullOrWhiteSpace($BuildRoot)
if ($AutomaticBuildRoot) {
    $BuildRoot = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        ("fpga_v4_build_{0}" -f $PID)
}
$QuartusBin = Get-QuartusBinDirectory
$QuartusSh = Resolve-ToolExecutable `
    -Name "quartus_sh.exe" `
    -CandidatePaths @((Join-Path $QuartusBin "quartus_sh.exe"))
$OutputDir = Join-Path $ProjectRoot "quartus\output_files"
$Subst = Resolve-ToolExecutable -Name "subst.exe" -CandidatePaths @()

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "rtl") `
    -Destination (Join-Path $BuildRoot "rtl") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "quartus") `
    -Destination (Join-Path $BuildRoot "quartus") -Recurse -Force

$QuartusBuildDir = Join-Path $BuildRoot "quartus"
$IncrementalDbDir = Join-Path $QuartusBuildDir "incremental_db"
$CompiledPartitionsDir = Join-Path $IncrementalDbDir "compiled_partitions"
New-Item -ItemType Directory -Force -Path $CompiledPartitionsDir | Out-Null

@(
    Get-Item -LiteralPath $BuildRoot -Force
    Get-ChildItem -LiteralPath $BuildRoot -Recurse -Force
) | ForEach-Object {
    if ($_.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
        $_.Attributes = $_.Attributes -bxor [System.IO.FileAttributes]::ReadOnly
    }
}

$MappedDrive = $null
$BuildSucceeded = $false
foreach ($CodePoint in 90..68) {
    $CandidateDrive = "{0}:" -f [char]$CodePoint
    if (-not (Test-Path -LiteralPath "$CandidateDrive\")) {
        & $Subst $CandidateDrive $BuildRoot
        if ($LASTEXITCODE -eq 0) {
            $MappedDrive = $CandidateDrive
            break
        }
    }
}
if ($null -eq $MappedDrive) {
    throw "无法为Quartus临时构建目录分配空闲盘符"
}

# Quartus 25.1 can remove hidden path components such as AppData while
# normalizing a project path.  A temporary SUBST drive also isolates Quartus
# from Chinese usernames and source paths.  All QSF paths remain relative.
$QuartusBuildDir = Join-Path "$MappedDrive\" "quartus"
$LocationPushed = $false
try {
    Push-Location $QuartusBuildDir
    $LocationPushed = $true

    & $QuartusSh --flow compile fpga_v4
    if ($LASTEXITCODE -ne 0) {
        throw "Quartus compile failed with exit code $LASTEXITCODE"
    }

    $SofPath = ".\output_files\fpga_v4.sof"
    $FitSummaryPath = ".\output_files\fpga_v4.fit.summary"
    $StaSummaryPath = ".\output_files\fpga_v4.sta.summary"
    foreach ($RequiredArtifact in @(
        $SofPath,
        $FitSummaryPath,
        $StaSummaryPath
    )) {
        if (-not (Test-Path -LiteralPath $RequiredArtifact -PathType Leaf)) {
            throw "Quartus did not produce required artifact: $RequiredArtifact"
        }
    }

    $FitSummary = Get-Content -LiteralPath $FitSummaryPath -Raw
    $BlockMemoryMatch = [regex]::Match(
        $FitSummary,
        "Total block memory bits\s*:\s*([\d,]+)"
    )
    if (-not $BlockMemoryMatch.Success) {
        throw "Unable to verify block-memory use from the Fitter summary"
    }
    $BlockMemoryBits = [int](
        $BlockMemoryMatch.Groups[1].Value.Replace(",", "")
    )
    if ($BlockMemoryBits -lt 4608) {
        throw (
            "Statistics RAM was not mapped to the required 128x36-bit " +
            "block memory; fitted block-memory bits: $BlockMemoryBits"
        )
    }

    $StaSummary = Get-Content -LiteralPath $StaSummaryPath -Raw
    $TimingChecks = [regex]::Matches(
        $StaSummary,
        "(?ms)^Type\s*:\s*.*?\s+(Setup|Hold)\s+'.*?'\r?\n" +
        "Slack\s*:\s*[-+\d.]+\r?\nTNS\s*:\s*([-+\d.]+)"
    )
    if ($TimingChecks.Count -eq 0) {
        throw "Unable to find setup/hold TNS entries in the STA summary"
    }
    $CheckedTimingCount = 0
    foreach ($TimingCheck in $TimingChecks) {
        $TimingText = $TimingCheck.Groups[0].Value
        if ($TimingText -match "altera_reserved_tck") {
            continue
        }

        $CheckedTimingCount += 1
        $Tns = [double]::Parse(
            $TimingCheck.Groups[2].Value,
            [Globalization.CultureInfo]::InvariantCulture
        )
        if ([math]::Abs($Tns) -gt 0.0000001) {
            throw (
                "Non-zero setup/hold TNS found: " +
                $TimingText
            )
        }
    }
    if ($CheckedTimingCount -eq 0) {
        throw "Unable to find user setup/hold TNS entries in the STA summary"
    }

    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    Copy-Item -LiteralPath $SofPath `
        -Destination (Join-Path $OutputDir "fpga_v4.sof") -Force
    Copy-Item -LiteralPath $FitSummaryPath `
        -Destination (Join-Path $OutputDir "fpga_v4.fit.summary") -Force
    Copy-Item -LiteralPath $StaSummaryPath `
        -Destination (Join-Path $OutputDir "fpga_v4.sta.summary") -Force

    Write-Host (
        "FPGA V5 COMPATIBLE V4 QUARTUS BUILD PASS: " +
        "$BlockMemoryBits block-memory bits; " +
        "$CheckedTimingCount user setup/hold checks with TNS=0"
    )
    $BuildSucceeded = $true
}
finally {
    if ($LocationPushed) {
        Pop-Location
    }
    if ($null -ne $MappedDrive) {
        & $Subst $MappedDrive /D | Out-Null
    }

    if ($AutomaticBuildRoot) {
        $FullTempRoot = [System.IO.Path]::GetFullPath(
            [System.IO.Path]::GetTempPath()
        )
        $FullBuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
        $BuildLeaf = Split-Path -Leaf $FullBuildRoot
        $IsSafeAutomaticPath = $FullBuildRoot.StartsWith(
            $FullTempRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and $BuildLeaf.StartsWith(
            "fpga_v4_build_",
            [System.StringComparison]::OrdinalIgnoreCase
        )

        if (-not $IsSafeAutomaticPath) {
            Write-Warning "Refusing to clean an unexpected path: $FullBuildRoot"
        }
        elseif ($BuildSucceeded) {
            Remove-Item -LiteralPath $FullBuildRoot -Recurse -Force
            Write-Host "Removed temporary build directory: $FullBuildRoot"
        }
        else {
            Write-Warning "Build failed; retained: $FullBuildRoot"
        }
    }
}
