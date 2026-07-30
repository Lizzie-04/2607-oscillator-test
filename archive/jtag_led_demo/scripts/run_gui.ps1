$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
python (Join-Path $ProjectRoot "host\jtag_led_gui.py")
