param(
    [switch]$RequireDebugNodes
)

$ErrorActionPreference = "Stop"
$QuartusBin = "C:\altera_lite\25.1std\quartus\bin64"
$Output = & "$QuartusBin\jtagconfig.exe" --debug 2>&1
$Output | Write-Host
if ($LASTEXITCODE -ne 0) {
    throw "jtagconfig failed with exit code $LASTEXITCODE"
}

$CableMatches = @($Output | Select-String -Pattern '^\d+\)\s+(DE-SoC \[USB-[^\]]+\])$')
if ($CableMatches.Count -ne 1) {
    throw "Expected exactly one DE-SoC cable, found $($CableMatches.Count)."
}
if (-not ($Output -match '4BA00477\s+SOCVHPS')) {
    throw "HPS TAP (ID 4BA00477) was not found."
}
if (-not ($Output -match '02D120DD\s+5CSE')) {
    throw "Cyclone V FPGA TAP (ID 02D120DD) was not found."
}
if ($RequireDebugNodes) {
    if (-not ($Output -match 'Source/Probe #0')) {
        throw "ISSP Source/Probe #0 is absent; program the matching SOF."
    }
    if (-not ($Output -match 'Node 30106E00')) {
        throw "SignalTap node is absent; program the matching SOF."
    }
}

$Cable = $CableMatches[0].Matches[0].Groups[1].Value
Write-Host "JTAG PASS cable=$Cable HPS=4BA00477 FPGA=02D120DD debug=$RequireDebugNodes"
