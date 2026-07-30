create_clock -name CLOCK_50 -period 20.000 [get_ports {CLOCK_50}]
create_clock -name FS_DATA_OUT33 -period 2000.000 [get_ports {FS_DATA_OUT33}]

create_generated_clock -name DSM_CLK_INT \
    -source [get_ports {CLOCK_50}] \
    -divide_by 25 \
    [get_nets {*dsm_clock|clk_2mhz}]

set_clock_groups -asynchronous \
    -group [get_clocks {CLOCK_50 DSM_CLK_INT}] \
    -group [get_clocks {FS_DATA_OUT33}]

set_false_path -from [get_ports {PC_DATA[*] PC_INDEX[*] PC_OPCODE[*]}]
set_false_path -from [get_ports {PC_REQUEST_TOGGLE PC_WRITE}]
derive_clock_uncertainty
