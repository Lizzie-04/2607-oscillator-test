# FPGA V11 System Console TCP bridge.  Runtime requests and responses travel
# only over the loopback socket; this tracked script is never rewritten.

set protocol_version 1
set expected_magic 0x4F534356
set maximum_words 256
set avalon_span_bytes 4096
set token $::env(FPGA_V11_TOKEN)
set parent_pid $::env(FPGA_V11_PARENT_PID)
set client_channel ""
set server_channel ""
set server_done 0
set master_path ""
set hello_complete 0

proc message_hex {message} {
    return [binary encode hex [encoding convertto utf-8 $message]]
}

proc send_error {channel tcp_id class message} {
    puts $channel "RES $tcp_id ERR $class [message_hex $message]"
    flush $channel
}

proc parse_u32 {text} {
    if {![regexp {^(0[xX][0-9a-fA-F]+|[0-9]+)$} $text]} {
        error "not an unsigned integer: $text"
    }
    set value [expr {wide($text)}]
    if {$value < 0 || $value > 0xFFFFFFFF} {
        error "32-bit value out of range: $text"
    }
    return $value
}

proc close_master {} {
    global master_path
    if {$master_path ne ""} {
        catch {close_service master $master_path}
        set master_path ""
    }
}

proc shutdown_server {} {
    global client_channel server_channel server_done
    close_master
    if {$client_channel ne ""} {
        catch {close $client_channel}
        set client_channel ""
    }
    if {$server_channel ne ""} {
        catch {close $server_channel}
        set server_channel ""
    }
    set server_done 1
}

proc scan_v8_masters {} {
    global expected_magic
    set matches {}
    foreach path [get_service_paths master] {
        if {[catch {open_service master $path}]} {
            continue
        }
        set matched 0
        if {![catch {set values [master_read_32 $path 0 1]}] &&
                [llength $values] == 1} {
            set value [expr {wide([lindex $values 0]) & 0xFFFFFFFF}]
            if {$value == $expected_magic} {
                set matched 1
            }
        }
        catch {close_service master $path}
        if {$matched} {
            lappend matches $path
        }
    }
    return $matches
}

proc find_v8_master {} {
    set deadline [expr {[clock milliseconds] + 30000}]
    while {1} {
        set matches [scan_v8_masters]
        if {[llength $matches] == 1} {
            set selected [lindex $matches 0]
            open_service master $selected
            return $selected
        }
        if {[llength $matches] > 1} {
            error "expected exactly one FPGA V11 Avalon master, found [llength $matches]"
        }
        if {[clock milliseconds] >= $deadline} {
            error "expected exactly one FPGA V11 Avalon master, found 0 after 30 seconds"
        }
        after 500
    }
}

proc handle_request {channel line} {
    global token protocol_version maximum_words avalon_span_bytes
    global master_path hello_complete

    set fields [split [string trim $line]]
    if {!$hello_complete} {
        if {[llength $fields] != 3 || [lindex $fields 0] ne "HELLO"} {
            send_error $channel 0 AUTH "HELLO required"
            shutdown_server
            return
        }
        if {[lindex $fields 1] ne $token} {
            send_error $channel 0 AUTH "invalid token"
            shutdown_server
            return
        }
        if {[lindex $fields 2] ne "$protocol_version"} {
            send_error $channel 0 VERSION "unsupported TCP protocol"
            shutdown_server
            return
        }
        if {[catch {set master_path [find_v8_master]} message]} {
            send_error $channel 0 JTAG $message
            shutdown_server
            return
        }
        set hello_complete 1
        puts $channel "RES 0 OK 1 $protocol_version"
        flush $channel
        return
    }

    if {[llength $fields] < 5 || [lindex $fields 0] ne "REQ"} {
        send_error $channel 0 PROTOCOL "malformed request"
        return
    }
    if {[catch {set tcp_id [parse_u32 [lindex $fields 1]]} message]} {
        send_error $channel 0 PROTOCOL $message
        return
    }
    if {$tcp_id == 0} {
        send_error $channel 0 PROTOCOL "request ID zero is reserved"
        return
    }
    set operation [lindex $fields 2]
    if {[catch {
        set address [parse_u32 [lindex $fields 3]]
        set count [parse_u32 [lindex $fields 4]]
    } message]} {
        send_error $channel $tcp_id PROTOCOL $message
        return
    }
    if {$address % 4 != 0 || $count < 1 || $count > $maximum_words ||
            $address >= $avalon_span_bytes ||
            $address + 4 * $count > $avalon_span_bytes} {
        send_error $channel $tcp_id RANGE "invalid address or word count"
        return
    }

    if {$operation eq "READ32"} {
        if {[llength $fields] != 5} {
            send_error $channel $tcp_id PROTOCOL "READ32 has extra fields"
            return
        }
        if {[catch {set values [master_read_32 $master_path $address $count]} message]} {
            send_error $channel $tcp_id JTAG $message
            return
        }
        if {[llength $values] != $count} {
            send_error $channel $tcp_id JTAG "read returned wrong word count"
            return
        }
        set response [list RES $tcp_id OK $count]
        foreach value $values {
            lappend response [format 0x%08X [expr {wide($value) & 0xFFFFFFFF}]]
        }
        puts $channel [join $response " "]
        flush $channel
        return
    }

    if {$operation eq "WRITE32"} {
        if {[llength $fields] != 5 + $count} {
            send_error $channel $tcp_id PROTOCOL "WRITE32 word count mismatch"
            return
        }
        set values {}
        for {set index 0} {$index < $count} {incr index} {
            if {[catch {
                set value [parse_u32 [lindex $fields [expr {5 + $index}]]]
            } message]} {
                send_error $channel $tcp_id PROTOCOL $message
                return
            }
            lappend values $value
        }
        if {[catch {master_write_32 $master_path $address $values} message]} {
            send_error $channel $tcp_id JTAG $message
            return
        }
        puts $channel "RES $tcp_id OK 0"
        flush $channel
        return
    }

    send_error $channel $tcp_id PROTOCOL "unknown operation"
}

proc client_readable {channel} {
    if {[eof $channel]} {
        shutdown_server
        return
    }
    while {[gets $channel line] >= 0} {
        if {[string length [string trim $line]] > 0} {
            handle_request $channel $line
        }
    }
}

proc accept_client {channel address port} {
    global client_channel hello_complete
    if {$client_channel ne ""} {
        catch {close $channel}
        return
    }
    set client_channel $channel
    set hello_complete 0
    fconfigure $channel -blocking 0 -buffering line -translation lf -encoding utf-8
    fileevent $channel readable [list client_readable $channel]
}

proc parent_watchdog {} {
    global parent_pid
    if {$::tcl_platform(platform) eq "windows"} {
        if {[catch {exec tasklist /FI "PID eq $parent_pid" /NH} result] ||
                [string first "$parent_pid" $result] < 0} {
            shutdown_server
            return
        }
    }
    after 5000 parent_watchdog
}

set server_channel [socket -server accept_client -myaddr 127.0.0.1 0]
set socket_name [fconfigure $server_channel -sockname]
set server_port [lindex $socket_name end]
puts "SERVER_READY port=$server_port"
flush stdout
after 5000 parent_watchdog
vwait server_done
