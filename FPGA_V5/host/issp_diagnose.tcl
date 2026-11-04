set matching_hardware {}
foreach hardware [get_hardware_names] {
    if {[regexp {^DE-SoC \[USB-[^]]+\]$} $hardware]} {
        lappend matching_hardware $hardware
    }
}
if {[llength $matching_hardware] != 1} {
    puts stderr "错误：DE-SoC下载器数量=[llength $matching_hardware]"
    exit 3
}
set hardware [lindex $matching_hardware 0]

set matching_devices {}
foreach device [get_device_names -hardware_name $hardware] {
    if {[string match "*5CSE*" $device]} {
        lappend matching_devices $device
    }
}
if {[llength $matching_devices] != 1} {
    puts stderr "错误：Cyclone V FPGA数量=[llength $matching_devices]"
    exit 3
}
set device [lindex $matching_devices 0]

set session_open 0
if {[catch {
    start_insystem_source_probe -hardware_name $hardware -device_name $device
    set session_open 1
    set source_hex [read_source_data -instance_index 0 -value_in_hex]
    set probe_hex [read_probe_data -instance_index 0 -value_in_hex]
    puts "DIAG hardware=$hardware"
    puts "DIAG device=$device"
    puts "DIAG source_hex=$source_hex source_hex_digits=[string length $source_hex]"
    puts "DIAG probe_hex=$probe_hex probe_hex_digits=[string length $probe_hex]"
} message]} {
    if {$session_open} {
        catch {end_insystem_source_probe}
    }
    puts stderr "错误：$message"
    exit 4
}
end_insystem_source_probe
exit 0
