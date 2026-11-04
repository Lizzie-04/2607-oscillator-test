# Read-only System Console service inventory for FPGA V8 hardware diagnosis.

puts "DIAGNOSTIC_BEGIN"

if {![catch {set service_types [get_service_types]} type_error]} {
    puts "SERVICE_TYPES [join $service_types {|}]"
    foreach service_type $service_types {
        if {![catch {set service_paths [get_service_paths $service_type]}]} {
            puts "SERVICE_COUNT $service_type [llength $service_paths]"
        }
    }
} else {
    puts "SERVICE_TYPES_ERROR $type_error"
}

set master_paths [get_service_paths master]
puts "MASTER_COUNT [llength $master_paths]"
set index 0
foreach path $master_paths {
    incr index
    puts "MASTER_PATH_BEGIN $index {$path}"
    if {[catch {open_service master $path} open_error]} {
        puts "MASTER_OPEN_ERROR $index {$open_error}"
        continue
    }
    if {[catch {set values [master_read_32 $path 0 4]} read_error]} {
        puts "MASTER_READ_ERROR $index {$read_error}"
    } else {
        set formatted {}
        foreach value $values {
            lappend formatted [format 0x%08X [expr {wide($value) & 0xFFFFFFFF}]]
        }
        puts "MASTER_READ_OK $index [join $formatted { }]"
    }
    catch {close_service master $path}
    puts "MASTER_PATH_END $index"
}

puts "DIAGNOSTIC_END"
flush stdout
