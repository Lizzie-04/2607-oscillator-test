if {$argc != 1} {
    puts stderr "Usage: quartus_stp -t issp_issue.tcl <0..1023>"
    exit 2
}

set value_text [lindex $argv 0]
if {[catch {expr {$value_text + 0}} value] || $value < 0 || $value > 1023} {
    puts stderr "ERROR: value must be an integer from 0 through 1023"
    exit 2
}

set matching_hardware {}
foreach hardware_name [get_hardware_names] {
    if {[regexp {^DE-SoC \[USB-[^]]+\]$} $hardware_name]} {
        lappend matching_hardware $hardware_name
    }
}
if {[llength $matching_hardware] != 1} {
    puts stderr "ERROR: expected one DE-SoC cable, found [llength $matching_hardware]"
    exit 3
}
set hardware [lindex $matching_hardware 0]

set matching_devices {}
foreach device_name [get_device_names -hardware_name $hardware] {
    if {[string match "*5CSE*" $device_name]} {
        lappend matching_devices $device_name
    }
}
if {[llength $matching_devices] != 1} {
    puts stderr "ERROR: expected one Cyclone V FPGA, found [llength $matching_devices]"
    exit 3
}
set device [lindex $matching_devices 0]

set session_open 0
if {[catch {
    start_insystem_source_probe -hardware_name $hardware -device_name $device
    set session_open 1

    set current_hex [read_source_data -instance_index 0 -value_in_hex]
    scan $current_hex %x current_source
    set request [expr {(($current_source >> 11) & 1) ^ 1}]
    set source_word [expr {($request << 11) | (1 << 10) | $value}]
    write_source_data -instance_index 0 \
        -value [format "%03X" $source_word] -value_in_hex
    puts [format "ISSUED value=%d request=%d source=0x%03X" \
        $value $request $source_word]
} message]} {
    if {$session_open} {
        catch {end_insystem_source_probe}
    }
    puts stderr "ERROR: $message"
    exit 4
}

end_insystem_source_probe
exit 0
