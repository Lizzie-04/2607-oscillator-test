if {$argc < 3 || $argc > 4} {
    puts stderr "Usage: quartus_stp -t signaltap_capture.tcl <session.stp> <out.csv> <out.vcd> ?timeout_seconds?"
    exit 2
}

set stp_file [file normalize [lindex $argv 0]]
set csv_file [file normalize [lindex $argv 1]]
set vcd_file [file normalize [lindex $argv 2]]
set timeout_seconds 10
if {$argc == 4} {
    set timeout_seconds [lindex $argv 3]
}
if {![file exists $stp_file]} {
    puts stderr "ERROR: STP file not found: $stp_file"
    exit 2
}
file mkdir [file dirname $csv_file]
file mkdir [file dirname $vcd_file]

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
    open_session -name $stp_file
    set session_open 1
    run -hardware_name $hardware -device_name $device \
        -instance led_capture \
        -signal_set led_signal_set \
        -trigger update_trigger \
        -data_log full_flow \
        -timeout $timeout_seconds
    export_data_log -instance led_capture \
        -signal_set led_signal_set \
        -trigger update_trigger \
        -data_log full_flow \
        -filename $csv_file -format csv
    export_data_log -instance led_capture \
        -signal_set led_signal_set \
        -trigger update_trigger \
        -data_log full_flow \
        -filename $vcd_file -format vcd
    puts "CAPTURE csv=$csv_file vcd=$vcd_file"
} message]} {
    if {$session_open} {
        catch {close_session}
    }
    puts stderr "ERROR: $message"
    exit 4
}

close_session
exit 0
