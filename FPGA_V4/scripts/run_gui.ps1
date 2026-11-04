$ErrorActionPreference = "Stop"
$HostRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "host"

$PythonCandidates = @()
$PythonOverrides = @(
    $env:FPGA_V4_PYTHON,
    [Environment]::GetEnvironmentVariable("FPGA_V4_PYTHON", "User"),
    [Environment]::GetEnvironmentVariable("FPGA_V4_PYTHON", "Machine"),
    $env:FPGA_V3_PYTHON,
    [Environment]::GetEnvironmentVariable("FPGA_V3_PYTHON", "User"),
    [Environment]::GetEnvironmentVariable("FPGA_V3_PYTHON", "Machine"),
    $env:FPGA_V2_PYTHON,
    [Environment]::GetEnvironmentVariable("FPGA_V2_PYTHON", "User"),
    [Environment]::GetEnvironmentVariable("FPGA_V2_PYTHON", "Machine")
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    Select-Object -Unique
foreach ($PythonOverride in $PythonOverrides) {
    if (-not (Test-Path -LiteralPath $PythonOverride -PathType Leaf)) {
        continue
    }
    $PythonCandidates += @{
        Command = (Resolve-Path -LiteralPath $PythonOverride).Path
        Arguments = @()
    }
}
$Python = Get-Command "python.exe" -ErrorAction SilentlyContinue
if ($null -ne $Python) {
    $PythonCandidates += @{
        Command = $Python.Source
        Arguments = @()
    }
}
$PyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
if ($null -ne $PyLauncher) {
    $PythonCandidates += @{
        Command = $PyLauncher.Source
        Arguments = @("-3")
    }
}

$SelectedPython = $null
foreach ($Candidate in $PythonCandidates) {
    $CandidateCommand = $Candidate.Command
    $CandidateArguments = @($Candidate.Arguments)
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $CandidateCommand @CandidateArguments -c `
        "import tkinter as tk; t=tk.Tcl(); t.eval('info patchlevel')" `
        *> $null
    $CandidateExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($CandidateExitCode -eq 0) {
        $SelectedPython = $Candidate
        break
    }
}

if ($null -eq $SelectedPython) {
    throw (
        "Python 3 with a working Tcl/Tk component was not found. " +
        "Install Python 3.10+ from python.org with tcl/tk and IDLE enabled, " +
        "or set FPGA_V4_PYTHON to the full python.exe path."
    )
}

Push-Location $HostRoot
try {
    $SelectedCommand = $SelectedPython.Command
    $SelectedArguments = @($SelectedPython.Arguments)
    & $SelectedCommand @SelectedArguments ".\gui.py"
}
finally {
    Pop-Location
}
