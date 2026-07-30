$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildDir = Join-Path $ProjectRoot "sim\work"
$QuestaRoot = "C:\altera_lite\25.1std\questa_fse\win64"

$env:SALT_LICENSE_SERVER = [Environment]::GetEnvironmentVariable(
    "SALT_LICENSE_SERVER", "Machine"
)

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
Push-Location $BuildDir
try {
    if (Test-Path "work") {
        Remove-Item -Recurse -Force "work"
    }

    & "$QuestaRoot\vlib.exe" work
    if ($LASTEXITCODE -ne 0) { throw "vlib failed" }

    & "$QuestaRoot\vlog.exe" -sv `
        (Join-Path $ProjectRoot "rtl\jtag_command_receiver.sv") `
        (Join-Path $ProjectRoot "sim\tb_jtag_command_receiver.sv")
    if ($LASTEXITCODE -ne 0) { throw "vlog failed" }

    & "$QuestaRoot\vsim.exe" -c tb_jtag_command_receiver `
        -do "run -all; quit -f"
    if ($LASTEXITCODE -ne 0) { throw "Questa simulation failed" }
}
finally {
    Pop-Location
}
