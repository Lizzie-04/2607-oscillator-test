module fpga_v2_top (
    input  logic        CLOCK_50,
    input  logic [0:0]  KEY,
    output logic [9:0]  LEDR,

    inout  wire [15:0]  PC_DATA,
    input  logic [5:0]  PC_INDEX,
    input  logic [3:0]  PC_OPCODE,
    input  logic        PC_REQUEST_TOGGLE,
    input  logic        PC_WRITE,
    output logic        PC_ACK_TOGGLE,
    output logic        PC_PENDING,
    output logic        PC_ERROR,
    output logic        PC_SAMPLE_TOGGLE,
    output logic        PC_DRIVER_BUSY,
    output logic        PC_SAR_BUSY,
    output logic        PC_SAR_DONE,
    output logic        PC_CONTROL_COMPLETE,

    output logic        Address1,
    output logic        Address2,
    output logic        Address3,
    output logic        SR_CLK,
    output logic        SR_IN,
    output logic        SR_EN1,
    output logic        SR_EN2,
    output logic        SR_EN3,
    output logic        SR_EN4,
    output logic        SR_EN5,
    output logic        START_DLL,
    output logic        START_ANA,
    input  logic        FS_DATA_OUT33,
    output logic        RESET,
    output logic        CLK_3P3,
    input  logic        BS_T_OUT33,
    output logic        DSM_3P3,
    input  logic        BS_C_OUT33,
    input  logic        OSC_OUT33_TO_FPGA
);

    logic rst;

    logic [31:0] avalon_address;
    logic [3:0] avalon_byteenable;
    logic avalon_read;
    logic avalon_write;
    logic [31:0] avalon_writedata;
    logic avalon_waitrequest;
    logic [31:0] avalon_readdata;
    logic avalon_readdatavalid;
    logic avalon_master_reset_unused;
    logic jtag_request_valid;
    logic [3:0] jtag_opcode;
    logic [5:0] jtag_index;
    logic [15:0] jtag_data;
    logic jtag_request_done;
    logic [15:0] jtag_response;
    logic avalon_statistics_abort;
    logic avalon_statistics_read_valid;
    logic [6:0] avalon_statistics_read_address;
    logic avalon_statistics_read_ready;
    logic [35:0] avalon_statistics_read_data;
    logic avalon_statistics_read_data_valid;

    logic gpio_request_valid;
    logic [3:0] gpio_opcode;
    logic [5:0] gpio_index;
    logic [15:0] gpio_data;
    logic gpio_request_done;
    logic [15:0] gpio_response;
    logic gpio_ack;
    logic gpio_pending;

    logic command_valid;
    logic [3:0] command_opcode;
    logic [5:0] command_index;
    logic [15:0] command_data;
    logic command_done;
    logic [15:0] command_response;

    logic [2:0] address;
    logic sr_clk;
    logic sr_in;
    logic [4:0] sr_enable;
    logic chip_reset;
    logic start_dll;
    logic start_ana;
    logic clk_3p3;
    logic dsm_3p3;
    logic [17:0] mu1;
    logic [17:0] mu2;
    logic [15:0] sample_sequence;
    logic sample_toggle;
    logic [15:0] decimation_length;
    logic [15:0] active_decimation_length;
    logic [7:0] error_code;
    logic control_words_complete;
    logic driver_busy;
    logic driver_done;
    logic sar_busy;
    logic sar_done;
    logic [7:0] sar_step_index;
    logic signed [28:0] sar_code;
    logic statistics_busy;
    logic statistics_done;
    logic statistics_completion_toggle;
    logic [15:0] statistics_captured_count;

    assign rst = ~KEY[0];

    oscv_jtag_master jtag_to_avalon (
        .clk_clk(CLOCK_50),
        .reset_reset(rst),
        .jtag_avalon_address(avalon_address),
        .jtag_avalon_readdata(avalon_readdata),
        .jtag_avalon_read(avalon_read),
        .jtag_avalon_write(avalon_write),
        .jtag_avalon_writedata(avalon_writedata),
        .jtag_avalon_waitrequest(avalon_waitrequest),
        .jtag_avalon_readdatavalid(avalon_readdatavalid),
        .jtag_avalon_byteenable(avalon_byteenable),
        .jtag_avalon_reset_reset(avalon_master_reset_unused)
    );

    avalon_host_bridge avalon_receiver (
        .clk(CLOCK_50),
        .rst(rst),
        .avalon_address(avalon_address),
        .avalon_byteenable(avalon_byteenable),
        .avalon_read(avalon_read),
        .avalon_write(avalon_write),
        .avalon_writedata(avalon_writedata),
        .avalon_waitrequest(avalon_waitrequest),
        .avalon_readdata(avalon_readdata),
        .avalon_readdatavalid(avalon_readdatavalid),
        .core_request_valid(jtag_request_valid),
        .core_request_opcode(jtag_opcode),
        .core_request_index(jtag_index),
        .core_request_data(jtag_data),
        .core_request_done(jtag_request_done),
        .core_response_data(jtag_response),
        .core_error_code(error_code),
        .core_control_words_complete(control_words_complete),
        .core_driver_busy(driver_busy),
        .core_driver_done(driver_done),
        .core_sar_busy(sar_busy),
        .core_sar_done(sar_done),
        .core_statistics_busy(statistics_busy),
        .core_statistics_done(statistics_done),
        .core_statistics_completion_toggle(
            statistics_completion_toggle
        ),
        .core_statistics_captured_count(statistics_captured_count),
        .statistics_abort(avalon_statistics_abort),
        .statistics_read_valid(avalon_statistics_read_valid),
        .statistics_read_address(avalon_statistics_read_address),
        .statistics_read_ready(avalon_statistics_read_ready),
        .statistics_read_data(avalon_statistics_read_data),
        .statistics_read_data_valid(avalon_statistics_read_data_valid)
    );

    gpio_host_bridge gpio_receiver (
        .clk(CLOCK_50),
        .rst(rst),
        .host_data(PC_DATA),
        .host_index(PC_INDEX),
        .host_opcode(PC_OPCODE),
        .host_request_toggle(PC_REQUEST_TOGGLE),
        .host_write(PC_WRITE),
        .request_valid(gpio_request_valid),
        .request_opcode(gpio_opcode),
        .request_index(gpio_index),
        .request_data(gpio_data),
        .request_done(gpio_request_done),
        .response_data(gpio_response),
        .ack_toggle(gpio_ack),
        .pending(gpio_pending)
    );

    assign PC_ACK_TOGGLE = gpio_ack;
    assign PC_PENDING = gpio_pending;
    assign PC_ERROR = |error_code;
    assign PC_SAMPLE_TOGGLE = sample_toggle;
    assign PC_DRIVER_BUSY = driver_busy;
    assign PC_SAR_BUSY = sar_busy;
    assign PC_SAR_DONE = sar_done;
    assign PC_CONTROL_COMPLETE = control_words_complete;

    host_command_arbiter command_arbiter (
        .clk(CLOCK_50),
        .rst(rst),
        .jtag_valid(jtag_request_valid),
        .jtag_opcode(jtag_opcode),
        .jtag_index(jtag_index),
        .jtag_data(jtag_data),
        .jtag_done(jtag_request_done),
        .jtag_response(jtag_response),
        .gpio_valid(gpio_request_valid),
        .gpio_opcode(gpio_opcode),
        .gpio_index(gpio_index),
        .gpio_data(gpio_data),
        .gpio_done(gpio_request_done),
        .gpio_response(gpio_response),
        .command_valid(command_valid),
        .command_opcode(command_opcode),
        .command_index(command_index),
        .command_data(command_data),
        .command_done(command_done),
        .command_response(command_response)
    );

    fpga_v2_core core (
        .clk_50mhz(CLOCK_50),
        .rst(rst),
        .fs_data_out33(FS_DATA_OUT33),
        .bs_t_out33(BS_T_OUT33),
        .bs_c_out33(BS_C_OUT33),
        .command_valid(command_valid),
        .command_opcode(command_opcode),
        .command_index(command_index),
        .command_data(command_data),
        .command_done(command_done),
        .command_response(command_response),
        .address(address),
        .sr_clk(sr_clk),
        .sr_in(sr_in),
        .sr_enable(sr_enable),
        .chip_reset(chip_reset),
        .start_dll(start_dll),
        .start_ana(start_ana),
        .clk_3p3(clk_3p3),
        .dsm_3p3(dsm_3p3),
        .mu1(mu1),
        .mu2(mu2),
        .sample_sequence(sample_sequence),
        .sample_toggle(sample_toggle),
        .decimation_length(decimation_length),
        .active_decimation_length(active_decimation_length),
        .error_code(error_code),
        .control_words_complete(control_words_complete),
        .driver_busy(driver_busy),
        .driver_done(driver_done),
        .sar_busy(sar_busy),
        .sar_done(sar_done),
        .sar_step_index(sar_step_index),
        .sar_code(sar_code),
        .statistics_busy(statistics_busy),
        .statistics_done(statistics_done),
        .statistics_completion_toggle(
            statistics_completion_toggle
        ),
        .statistics_captured_count(statistics_captured_count),
        .avalon_statistics_abort(avalon_statistics_abort),
        .avalon_statistics_read_valid(avalon_statistics_read_valid),
        .avalon_statistics_read_address(avalon_statistics_read_address),
        .avalon_statistics_read_ready(avalon_statistics_read_ready),
        .avalon_statistics_read_data(avalon_statistics_read_data),
        .avalon_statistics_read_data_valid(
            avalon_statistics_read_data_valid
        )
    );

    // GPIO2 is connected to the PCB H1 2x20 header.
    assign Address1 = address[0];
    assign Address2 = address[1];
    assign Address3 = address[2];
    assign SR_CLK = sr_clk;
    assign SR_IN = sr_in;
    assign SR_EN1 = sr_enable[0];
    assign SR_EN2 = sr_enable[1];
    assign SR_EN3 = sr_enable[2];
    assign SR_EN4 = sr_enable[3];
    assign SR_EN5 = sr_enable[4];
    assign START_DLL = start_dll;
    assign START_ANA = start_ana;
    assign RESET = chip_reset;
    assign CLK_3P3 = clk_3p3;
    assign DSM_3P3 = dsm_3p3;

    // Keep the presently unused oscillator input as an explicit PCB port.
    logic oscillator_input_unused;
    assign oscillator_input_unused = OSC_OUT33_TO_FPGA;

    assign LEDR = {
        error_code != 0,
        control_words_complete,
        driver_busy,
        driver_done,
        sar_busy,
        sar_done,
        sample_toggle,
        address
    };

endmodule
