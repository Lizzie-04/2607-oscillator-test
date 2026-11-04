function Resolve-ToolExecutable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string[]]$CandidatePaths = @()
    )

    $FromPath = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $FromPath) {
        return $FromPath.Source
    }

    foreach ($Candidate in $CandidatePaths) {
        if (-not [string]::IsNullOrWhiteSpace($Candidate) -and
                (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    throw (
        "Cannot find {0}. Add its directory to PATH or set the appropriate " +
        "toolchain environment variable described in README.md." -f $Name
    )
}

function Get-QuartusBinDirectory {
    $Roots = @(
        $env:QUARTUS_ROOTDIR,
        $env:QUARTUS_ROOTDIR_OVERRIDE
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    foreach ($Root in $Roots) {
        $Bin = Join-Path $Root "bin64"
        if (Test-Path -LiteralPath $Bin -PathType Container) {
            return (Resolve-Path -LiteralPath $Bin).Path
        }
    }

    $QuartusSh = Get-Command "quartus_sh.exe" -ErrorAction SilentlyContinue
    if ($null -ne $QuartusSh) {
        return (Split-Path -Parent $QuartusSh.Source)
    }

    throw (
        "Quartus was not found. Run this script from the Quartus Prime " +
        "Command Prompt, add quartus\bin64 to PATH, or set QUARTUS_ROOTDIR."
    )
}

function Get-QuestaBinDirectory {
    $DirectRoots = @(
        $env:QUESTA_FSE_ROOT,
        $env:QUESTA_ROOT
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    foreach ($Root in $DirectRoots) {
        foreach ($Candidate in @($Root, (Join-Path $Root "win64"))) {
            if (Test-Path -LiteralPath (Join-Path $Candidate "vsim.exe")) {
                return (Resolve-Path -LiteralPath $Candidate).Path
            }
        }
    }

    $FromPath = Get-Command "vsim.exe" -ErrorAction SilentlyContinue
    if ($null -ne $FromPath) {
        return (Split-Path -Parent $FromPath.Source)
    }

    $QuartusRoots = @(
        $env:QUARTUS_ROOTDIR,
        $env:QUARTUS_ROOTDIR_OVERRIDE
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    $QuartusFromPath = Get-Command "quartus_sh.exe" -ErrorAction SilentlyContinue
    if ($null -ne $QuartusFromPath) {
        $QuartusBinFromPath = Split-Path -Parent $QuartusFromPath.Source
        $QuartusRoots += Split-Path -Parent $QuartusBinFromPath
    }

    foreach ($QuartusRoot in $QuartusRoots) {
        $InstallRoot = Split-Path -Parent $QuartusRoot
        $Candidate = Join-Path $InstallRoot "questa_fse\win64"
        if (Test-Path -LiteralPath (Join-Path $Candidate "vsim.exe")) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    throw (
        "Questa was not found. Add its win64 directory to PATH, set " +
        "QUESTA_FSE_ROOT/QUESTA_ROOT, or set QUARTUS_ROOTDIR when Questa " +
        "is installed beside Quartus."
    )
}
