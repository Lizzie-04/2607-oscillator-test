if {$argc < 1 || $argc > 2} {
    puts stderr "Usage: quartus_stp -t issp_write.tcl <0..1023> ?deferred:0|1?"
    exit 2
}

set value_text [lindex $argv 0]
if {[catch {expr {$value_text + 0}} value]} {
    puts stderr "ERROR: invalid integer '$value_text'"
    exit 2
}
if {$value < 0 || $value > 1023} {
    puts stderr "ERROR: value must be between 0 and 1023"
    exit 2
}

set deferred 0
if {$argc == 2} {
    set deferred [lindex $argv 1]
    if {$deferred ni {0 1}} {
        puts stderr "ERROR: deferred must be 0 or 1"
        exit 2
    }
}

set matching_hardware {}
foreach hardware [get_hardware_names] {
    if {[regexp {^DE-SoC \[USB-[^]]+\]$} $hardware]} {
        lappend matching_hardware $hardware
    }
}
if {[llength $matching_hardware] != 1} {
    puts stderr "ERROR: expected one DE-SoC cable, found [llength $matching_hardware]"
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
    puts stderr "ERROR: expected one Cyclone V FPGA, found [llength $matching_devices]"
    exit 3
}
set device [lindex $matching_devices 0]

# The programmed image contains exactly one Source/Probe node.  Using the
# debug-node index reported by jtagconfig avoids opening a second discovery
# session while the transaction session is active.
set instance_index 0
set session_open 0
if {[catch {
    start_insystem_source_probe -hardware_name $hardware -device_name $device
    set session_open 1

    set current_hex [read_source_data -instance_index $instance_index -value_in_hex]
    scan $current_hex %x current_source
    set next_request [expr {(($current_source >> 11) & 1) ^ 1}]
    set source_word [expr {($next_request << 11) | ($deferred << 10) | $value}]

    write_source_data -instance_index $instance_index \
        -value [format "%03X" $source_word] -value_in_hex

    set complete 0
    set probe_word 0
    for {set attempt 0} {$attempt < 400} {incr attempt} {
        set probe_hex [read_probe_data -instance_index $instance_index -value_in_hex]
        scan $probe_hex %x probe_word
        set ack [expr {($probe_word >> 11) & 1}]
        set busy [expr {($probe_word >> 10) & 1}]
        set readback [expr {$probe_word & 0x3FF}]
        if {$ack == $next_request && !$busy} {
            set complete 1
            break
        }
        after 25
    }

    if {!$complete} {
        error "Timed out waiting for FPGA acknowledgement"
    }
    if {$readback != $value} {
        error "Readback mismatch: wrote $value, read $readback"
    }

    puts [format "RESULT value=%d hex=0x%03X binary=%010b ack=%d busy=%d" \
        $readback $readback $readback $ack $busy]
} error_message]} {
    if {$session_open} {
        catch {end_insystem_source_probe}
    }
    puts stderr "ERROR: $error_message"
    exit 4
}

if {$session_open} {
    end_insystem_source_probe
}
exit 0
