create_clock -name CLOCK_50 -period 20.000 [get_ports {CLOCK_50}]

set_false_path -from [get_ports {KEY[0]}]
set_false_path -to [get_registers {*|source_meta[*]}]
set_false_path -to [get_ports {LEDR[*]}]
