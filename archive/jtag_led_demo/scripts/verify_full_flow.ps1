param(
    [ValidateRange(0, 1023)]
    [int]$Value = 1,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$QuartusStp = "C:\altera_lite\25.1std\quartus\bin64\quartus_stp.exe"
$Python = "python"
$Artifacts = Join-Path $ProjectRoot "artifacts"
$CanonicalStp = Join-Path $ProjectRoot "quartus\jtag_led_demo.stp"
$RuntimeStp = Join-Path $Artifacts "full_flow_runtime.stp"
$Csv = Join-Path $Artifacts "full_flow.csv"
$Vcd = Join-Path $Artifacts "full_flow.vcd"

New-Item -ItemType Directory -Path $Artifacts -Force | Out-Null

function Invoke-Checked {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    Write-Host "`n=== $Label ==="
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Invoke-Checked "Questa simulation" {
    powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "simulate.ps1")
}

if (-not $SkipBuild) {
    Invoke-Checked "Quartus full compilation" {
        powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build.ps1")
    }
}

Invoke-Checked "JTAG chain" {
    powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "jtag_check.ps1")
}
Invoke-Checked "SOF programming" {
    powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "program.ps1")
}
Invoke-Checked "Programmed debug nodes" {
    powershell.exe -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "jtag_check.ps1") -RequireDebugNodes
}

Copy-Item -LiteralPath $CanonicalStp -Destination $RuntimeStp -Force

Write-Host "`n=== Deferred ISSP command ==="
$SavedErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$IssueOutput = @(
    & $QuartusStp -t (Join-Path $ProjectRoot "host\issp_issue.tcl") $Value 2>&1
)
$IssueExitCode = $LASTEXITCODE
$ErrorActionPreference = $SavedErrorPreference
$IssueOutput | Write-Host
if ($IssueExitCode -ne 0) {
    throw "Deferred ISSP command failed with exit code $IssueExitCode"
}
$IssueText = $IssueOutput -join "`n"
$RequestMatch = [regex]::Match($IssueText, 'ISSUED value=\d+ request=([01])')
if (-not $RequestMatch.Success) {
    throw "Could not parse request toggle from ISSP output."
}
$Request = [int]$RequestMatch.Groups[1].Value

Invoke-Checked "SignalTap capture" {
    & $QuartusStp -t (Join-Path $ProjectRoot "host\signaltap_capture.tcl") `
        $RuntimeStp $Csv $Vcd 10
}
Invoke-Checked "ISSP readback" {
    & $QuartusStp -t (Join-Path $ProjectRoot "host\issp_readback.tcl") `
        $Value $Request
}
Invoke-Checked "Waveform assertions" {
    & $Python (Join-Path $ProjectRoot "host\verify_capture.py") $Csv $Value
}

if (-not (Test-Path -LiteralPath $Vcd)) {
    throw "SignalTap VCD was not generated: $Vcd"
}
Write-Host "`nFULL FLOW PASS value=$Value"
Write-Host "CSV: $Csv"
Write-Host "VCD: $Vcd"
