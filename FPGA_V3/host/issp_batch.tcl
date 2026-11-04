if {$argc != 1} {
    puts stderr "用法：quartus_stp -t issp_batch.tcl <事务文件>"
    exit 2
}

set transaction_file [lindex $argv 0]
if {![file isfile $transaction_file]} {
    puts stderr "错误：未找到事务文件：$transaction_file"
    exit 2
}

set matching_hardware {}
foreach hardware [get_hardware_names] {
    if {[regexp {^DE-SoC \[USB-[^]]+\]$} $hardware]} {
        lappend matching_hardware $hardware
    }
}
if {[llength $matching_hardware] != 1} {
    puts stderr "错误：应检测到1个DE-SoC下载器，实际为[llength $matching_hardware]个"
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
    puts stderr "错误：应检测到1个Cyclone V FPGA，实际为[llength $matching_devices]个"
    exit 3
}
set device [lindex $matching_devices 0]

set session_open 0
if {[catch {
    start_insystem_source_probe -hardware_name $hardware -device_name $device
    set session_open 1
    set instance_index 0

    set source_hex [read_source_data \
        -instance_index $instance_index -value_in_hex]
    set initial_probe_hex [read_probe_data \
        -instance_index $instance_index -value_in_hex]
    if {[string length $source_hex] != 16 ||
        [string length $initial_probe_hex] != 16} {
        error [format \
            "ISSP位宽不匹配：当前镜像为%d-bit source/%d-bit probe；FPGA V3要求64-bit/64-bit。请加载quartus/output_files/fpga_v3.sof。" \
            [expr {[string length $source_hex] * 4}] \
            [expr {[string length $initial_probe_hex] * 4}]]
    }
    # %x is only 32-bit in the Quartus Tcl runtime and saturates a 64-bit
    # value at 0x7FFFFFFF.  The request/ack bits live at bit 63, so every
    # source/probe word must be parsed with the 64-bit "ll" modifier.
    scan $source_hex %llx source_word
    set request_toggle [expr {($source_word >> 63) & 1}]

    # Opening an ISSP session restores source_initial_value.  If a previous
    # session stopped while its toggle was 1, allow the FPGA receiver to
    # consume that falling edge and return to source/ack agreement before
    # issuing a real command.
    set synchronized 0
    for {set attempt 0} {$attempt < 400} {incr attempt} {
        set probe_hex [read_probe_data \
            -instance_index $instance_index -value_in_hex]
        scan $probe_hex %llx probe_word
        set ack [expr {($probe_word >> 63) & 1}]
        set pending [expr {($probe_word >> 62) & 1}]
        if {$ack == $request_toggle && !$pending} {
            set synchronized 1
            break
        }
        after 5
    }
    if {!$synchronized} {
        error "JTAG会话初始化时request/ack同步超时"
    }

    set input_channel [open $transaction_file r]
    set transaction_index 0
    while {[gets $input_channel line] >= 0} {
        set line [string trim $line]
        if {$line eq "" || [string index $line 0] eq "#"} {
            continue
        }
        if {[scan $line "%i %i %i" opcode index data] != 3} {
            error "无效的事务行：$line"
        }
        if {$opcode < 0 || $opcode > 15 ||
            $index < 0 || $index > 63 ||
            $data < 0 || $data > 65535} {
            error "事务数值超出范围：$line"
        }

        set request_toggle [expr {$request_toggle ^ 1}]
        set source_word [expr {
            ($request_toggle << 63) |
            ($opcode << 59) |
            ($index << 53) |
            ($data << 37)
        }]
        write_source_data -instance_index $instance_index \
            -value [format "%016llX" $source_word] -value_in_hex

        set complete 0
        for {set attempt 0} {$attempt < 400} {incr attempt} {
            set probe_hex [read_probe_data \
                -instance_index $instance_index -value_in_hex]
            scan $probe_hex %llx probe_word
            set ack [expr {($probe_word >> 63) & 1}]
            set pending [expr {($probe_word >> 62) & 1}]
            if {$ack == $request_toggle && !$pending} {
                set complete 1
                break
            }
            after 5
        }
        if {!$complete} {
            error [format \
                "等待第%d个事务应答超时；source=%016llX probe=%s expected_ack=%d actual_ack=%d pending=%d" \
                $transaction_index $source_word $probe_hex $request_toggle \
                $ack $pending]
        }
        puts [format "RESULT %d %016llX" $transaction_index $probe_word]
        incr transaction_index
    }
    close $input_channel

    # Normalize every completed session to toggle=0.  This keeps reopen,
    # reset and FPGA reprogram paths deterministic and prevents a residual
    # edge from being decoded as a spurious request.  A hidden read is
    # side-effect free and is not emitted as a RESULT line.
    if {$request_toggle != 0} {
        set request_toggle 0
        set source_word [expr {(8 << 59)}]
        write_source_data -instance_index $instance_index \
            -value [format "%016llX" $source_word] -value_in_hex

        set cleanup_complete 0
        for {set attempt 0} {$attempt < 400} {incr attempt} {
            set probe_hex [read_probe_data \
                -instance_index $instance_index -value_in_hex]
            scan $probe_hex %llx probe_word
            set ack [expr {($probe_word >> 63) & 1}]
            set pending [expr {($probe_word >> 62) & 1}]
            if {$ack == 0 && !$pending} {
                set cleanup_complete 1
                break
            }
            after 5
        }
        if {!$cleanup_complete} {
            error "JTAG会话结束时request/ack归零超时"
        }
    }
} error_message]} {
    if {$session_open} {
        catch {end_insystem_source_probe}
    }
    puts stderr "错误：$error_message"
    exit 4
}

if {$session_open} {
    end_insystem_source_probe
}
exit 0
