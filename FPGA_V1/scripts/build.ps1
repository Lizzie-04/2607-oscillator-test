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
        ("fpga_v1_build_{0}" -f $PID)
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

    & $QuartusSh --flow compile fpga_v1
    if ($LASTEXITCODE -ne 0) {
        throw "Quartus compile failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    Copy-Item -LiteralPath ".\output_files\fpga_v1.sof" `
        -Destination (Join-Path $OutputDir "fpga_v1.sof") -Force
    Copy-Item -LiteralPath ".\output_files\fpga_v1.fit.summary" `
        -Destination (Join-Path $OutputDir "fpga_v1.fit.summary") -Force
    Copy-Item -LiteralPath ".\output_files\fpga_v1.sta.summary" `
        -Destination (Join-Path $OutputDir "fpga_v1.sta.summary") -Force

    Write-Host "FPGA V1 QUARTUS BUILD PASS"
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
            "fpga_v1_build_",
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
