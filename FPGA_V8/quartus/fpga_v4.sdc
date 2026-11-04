create_clock -name CLOCK_50 -period 20.000 [get_ports {CLOCK_50}]
create_clock -name FS_SAMPLE_CLK -period 2000.000 \
    [get_ports {FS_DATA_OUT33}]

create_generated_clock -name CORE_CLK_2MHZ \
    -source [get_ports {CLOCK_50}] \
    -divide_by 25 \
    [get_nets {*dsm_clock|clk_2mhz}]

set_clock_groups -asynchronous \
    -group [get_clocks {CLOCK_50}] \
    -group [get_clocks {CORE_CLK_2MHZ}] \
    -group [get_clocks {FS_SAMPLE_CLK}]

set_false_path -from [get_ports {PC_DATA[*] PC_INDEX[*] PC_OPCODE[*]}]
set_false_path -from [get_ports {PC_REQUEST_TOGGLE PC_WRITE}]
derive_clock_uncertainty
