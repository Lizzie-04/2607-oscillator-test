$ErrorActionPreference = "Stop"
$HostRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "host"
$PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
$PythonArguments = @()
if ($null -eq $PythonCommand) {
    $PythonCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue
    $PythonArguments = @("-3")
}
if ($null -eq $PythonCommand) {
    throw "Python 3 was not found. Add python.exe or py.exe to PATH."
}

Push-Location $HostRoot
try {
    & $PythonCommand.Source @PythonArguments ".\gui.py"
}
finally {
    Pop-Location
}
