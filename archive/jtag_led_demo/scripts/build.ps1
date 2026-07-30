$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$QuartusDir = Join-Path $ProjectRoot "quartus"
$QuartusSh = "C:\altera_lite\25.1std\quartus\bin64\quartus_sh.exe"
$Sof = Join-Path $QuartusDir "output_files\jtag_led_demo.sof"
$FitSummary = Join-Path $QuartusDir "output_files\jtag_led_demo.fit.summary"
$MapReport = Join-Path $QuartusDir "output_files\jtag_led_demo.map.rpt"

Push-Location $QuartusDir
try {
    & $QuartusSh --flow compile jtag_led_demo
    if ($LASTEXITCODE -ne 0) {
        throw "Quartus compilation failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $Sof)) {
        throw "Compilation reported success but no SOF was generated: $Sof"
    }
    $FitText = Get-Content -LiteralPath $FitSummary -Raw
    $MapText = Get-Content -LiteralPath $MapReport -Raw
    if ($FitText -notmatch 'Fitter Status\s*:\s*Successful') {
        throw "Quartus Fitter did not report success."
    }
    if ($FitText -notmatch 'Total block memory bits\s*:\s*23,040') {
        throw "Expected 23,040 SignalTap memory bits were not implemented."
    }
    if ($MapText -notmatch 'signaltap_megafunction_0') {
        throw "SignalTap HDL instance is absent from the synthesis report."
    }
    Write-Host "BUILD PASS: $Sof"
}
finally {
    Pop-Location
}
