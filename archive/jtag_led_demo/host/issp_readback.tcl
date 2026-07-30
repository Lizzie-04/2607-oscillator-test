if {$argc != 2} {
    puts stderr "Usage: quartus_stp -t issp_readback.tcl <expected_value> <expected_request>"
    exit 2
}

set expected_value [lindex $argv 0]
set expected_request [lindex $argv 1]
if {[catch {expr {$expected_value + 0}} expected_value] ||
        $expected_value < 0 || $expected_value > 1023 ||
        $expected_request ni {0 1}} {
    puts stderr "ERROR: invalid expected value or request toggle"
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

    set complete 0
    for {set attempt 0} {$attempt < 200} {incr attempt} {
        set probe_hex [read_probe_data -instance_index 0 -value_in_hex]
        scan $probe_hex %x probe_word
        set value [expr {$probe_word & 0x3FF}]
        set busy [expr {($probe_word >> 10) & 1}]
        set ack [expr {($probe_word >> 11) & 1}]
        if {!$busy && $ack == $expected_request} {
            set complete 1
            break
        }
        after 25
    }
    if {!$complete} {
        error "timed out waiting for ack=$expected_request and busy=0"
    }
    if {$value != $expected_value} {
        error "readback mismatch: expected $expected_value, got $value"
    }
    puts [format "READBACK value=%d hex=0x%03X binary=%010b ack=%d busy=%d" \
        $value $value $value $ack $busy]
} message]} {
    if {$session_open} {
        catch {end_insystem_source_probe}
    }
    puts stderr "ERROR: $message"
    exit 4
}

end_insystem_source_probe
exit 0
