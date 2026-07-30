$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Sof = Join-Path $ProjectRoot "quartus\output_files\jtag_led_demo.sof"
$QuartusBin = "C:\altera_lite\25.1std\quartus\bin64"

if (-not (Test-Path -LiteralPath $Sof)) {
    throw "SOF not found. Run scripts\build.ps1 first: $Sof"
}

$Chain = & "$QuartusBin\jtagconfig.exe"
if ($LASTEXITCODE -ne 0) { throw "jtagconfig failed" }

$CableMatch = $Chain | Select-String -Pattern '^\d+\)\s+(DE-SoC \[USB-[^\]]+\])$'
if ($CableMatch.Count -ne 1) {
    throw "Expected exactly one DE-SoC cable, found $($CableMatch.Count)."
}
if (-not ($Chain -match '02D120DD')) {
    throw "Cyclone V FPGA ID 02D120DD was not found in the JTAG chain."
}
if (-not ($Chain -match '4BA00477')) {
    throw "Cyclone V HPS TAP ID 4BA00477 was not found in the JTAG chain."
}

$Cable = $CableMatch.Matches[0].Groups[1].Value
Write-Host "Programming Cyclone V through $Cable"
& "$QuartusBin\quartus_pgm.exe" -c $Cable -m JTAG -o "p;$Sof@2"
if ($LASTEXITCODE -ne 0) {
    throw "FPGA programming failed with exit code $LASTEXITCODE"
}
Write-Host "PROGRAM PASS"
