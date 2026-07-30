module fpga_v1_top (
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

    logic [63:0] jtag_source;
    logic [63:0] jtag_probe;
    logic jtag_request_valid;
    logic [3:0] jtag_opcode;
    logic [5:0] jtag_index;
    logic [15:0] jtag_data;
    logic jtag_request_done;
    logic [15:0] jtag_response;
    logic jtag_ack;
    logic jtag_pending;

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
    logic [7:0] error_code;
    logic control_words_complete;
    logic driver_busy;
    logic driver_done;
    logic sar_busy;
    logic sar_done;
    logic [7:0] sar_step_index;
    logic signed [28:0] sar_code;

    assign rst = ~KEY[0];

    altsource_probe #(
        .sld_auto_instance_index("YES"),
        .instance_id("OSCV"),
        .probe_width(64),
        .source_width(64),
        .source_initial_value("0000000000000000"),
        .enable_metastability("NO")
    ) host_issp (
        .probe(jtag_probe),
        .source(jtag_source),
        .source_clk(CLOCK_50),
        .source_ena(1'b1)
    );

    jtag_request_receiver jtag_receiver (
        .clk(CLOCK_50),
        .rst(rst),
        .source_async(jtag_source),
        .request_valid(jtag_request_valid),
        .request_opcode(jtag_opcode),
        .request_index(jtag_index),
        .request_data(jtag_data),
        .request_done(jtag_request_done),
        .response_data(jtag_response),
        .ack_toggle(jtag_ack),
        .pending(jtag_pending),
        .last_response()
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

    fpga_v1_core core (
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
        .error_code(error_code),
        .control_words_complete(control_words_complete),
        .driver_busy(driver_busy),
        .driver_done(driver_done),
        .sar_busy(sar_busy),
        .sar_done(sar_done),
        .sar_step_index(sar_step_index),
        .sar_code(sar_code)
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

    assign jtag_probe = {
        jtag_ack,
        jtag_pending,
        error_code,
        driver_busy,
        driver_done,
        sar_busy,
        sar_done,
        sample_toggle,
        sample_sequence,
        jtag_response,
        sar_code[16:0]
    };

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
