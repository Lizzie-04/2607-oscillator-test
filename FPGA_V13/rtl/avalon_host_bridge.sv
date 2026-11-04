module avalon_host_bridge (
    input  logic        clk,
    input  logic        rst,

    input  logic [31:0] avalon_address,
    input  logic [3:0]  avalon_byteenable,
    input  logic        avalon_read,
    input  logic        avalon_write,
    input  logic [31:0] avalon_writedata,
    output logic        avalon_waitrequest,
    output logic [31:0] avalon_readdata,
    output logic        avalon_readdatavalid,

    output logic        core_request_valid,
    output logic [3:0]  core_request_opcode,
    output logic [5:0]  core_request_index,
    output logic [15:0] core_request_data,
    input  logic        core_request_done,
    input  logic [15:0] core_response_data,

    input  logic [7:0]  core_error_code,
    input  logic        core_control_words_complete,
    input  logic        core_driver_busy,
    input  logic        core_driver_done,
    input  logic        core_sar_busy,
    input  logic        core_sar_done,
    input  logic        core_statistics_busy,
    input  logic        core_statistics_done,
    input  logic        core_statistics_completion_toggle,
    input  logic [15:0] core_statistics_captured_count,

    output logic        statistics_abort,
    output logic        statistics_read_valid,
    output logic [6:0]  statistics_read_address,
    input  logic        statistics_read_ready,
    input  logic [35:0] statistics_read_data,
    input  logic        statistics_read_data_valid
);

    import fpga_v2_protocol_pkg::*;

    localparam logic [31:0] MAGIC = 32'h4F53_4356;
    localparam logic [31:0] AVALON_ABI_VERSION = 32'd2;
    // Bit 7 advertises the V13 48-fragment FPGA coefficient window.
    localparam logic [31:0] CAPABILITIES = 32'h0000_00FF;

    localparam logic [7:0] BRIDGE_ERROR_NONE = 8'h00;
    localparam logic [7:0] BRIDGE_ERROR_ADDRESS = 8'h01;
    localparam logic [7:0] BRIDGE_ERROR_ACCESS = 8'h02;
    localparam logic [7:0] BRIDGE_ERROR_REQUEST_ID = 8'h03;
    localparam logic [7:0] BRIDGE_ERROR_BUSY = 8'h04;
    localparam logic [7:0] BRIDGE_ERROR_CONTROL_INCOMPLETE = 8'h05;
    localparam logic [7:0] BRIDGE_ERROR_STATISTICS_NOT_READY = 8'h06;
    localparam logic [7:0] BRIDGE_ERROR_CAPTURE_ID = 8'h07;
    localparam logic [7:0] BRIDGE_ERROR_CORE = 8'h08;
    localparam logic [7:0] BRIDGE_ERROR_COEFFICIENT_INCOMPLETE = 8'h09;

    localparam logic [4:0] ACTION_NONE = 5'd0;
    localparam logic [4:0] ACTION_CORE_READ = 5'd1;
    localparam logic [4:0] ACTION_GENERIC = 5'd2;
    localparam logic [4:0] ACTION_CONTROL_COMMIT = 5'd3;
    localparam logic [4:0] ACTION_DSM_COMMIT = 5'd4;
    localparam logic [4:0] ACTION_STATISTICS_START = 5'd5;

    localparam logic [5:0] OPERATION_CONTROL_COMMIT = 6'd1;
    localparam logic [5:0] OPERATION_SET_N = 6'd2;
    localparam logic [5:0] OPERATION_SET_FILTER = 6'd3;
    localparam logic [5:0] OPERATION_DSM_COMMIT = 6'd4;
    localparam logic [5:0] OPERATION_SAR_START = 6'd5;
    localparam logic [5:0] OPERATION_SAR_COMPARE = 6'd6;
    localparam logic [5:0] OPERATION_CLEAR_ERROR = 6'd7;
    localparam logic [5:0] OPERATION_STATISTICS_START = 6'd8;
    localparam logic [5:0] OPERATION_LATCH_LIVE = 6'd9;
    localparam logic [5:0] OPERATION_LATCH_CALIBRATION = 6'd10;
    localparam logic [5:0] OPERATION_STATISTICS_RELEASE = 6'd11;
    localparam logic [5:0] OPERATION_STATISTICS_ABORT = 6'd12;
    localparam logic [5:0] OPERATION_READ_REGISTER = 6'd13;

    typedef enum logic [2:0] {
        BUS_IDLE,
        BUS_ACTION_WAIT,
        BUS_STATISTICS_ACCEPT,
        BUS_STATISTICS_DATA,
        BUS_RESPONSE
    } bus_state_t;

    typedef enum logic [1:0] {
        ACTION_IDLE,
        ACTION_ISSUE,
        ACTION_WAIT,
        ACTION_SETTLE
    } action_state_t;

    bus_state_t bus_state;
    action_state_t action_state;
    logic [4:0] action_kind;
    logic [5:0] action_operation;
    logic [6:0] action_step;
    logic [3:0] generic_opcode;
    logic [5:0] generic_index;
    logic [15:0] generic_data;
    logic action_has_request_id;
    logic [31:0] action_request_id;
    logic action_complete_pulse;
    logic [15:0] action_read_response;

    logic [15:0] control_words [0:29];
    logic [29:0] control_word_valid;
    logic [15:0] fpga_coefficient_words [0:FPGA_COEFFICIENT_WORDS-1];
    logic [FPGA_COEFFICIENT_WORDS-1:0] fpga_coefficient_valid;
    logic [15:0] dsm_low_shadow;
    logic [12:0] dsm_high_shadow;
    logic dsm_enable_shadow;

    logic [31:0] request_id_shadow;
    logic [31:0] last_accepted_request_id;
    logic [31:0] last_completed_request_id;
    logic [5:0] last_operation;
    logic [7:0] last_bridge_error;
    logic [7:0] last_core_error;
    logic [15:0] last_core_response;
    logic [7:0] bridge_error_code;
    logic bridge_address_error;
    logic bridge_access_error;

    logic statistics_ready;
    logic statistics_capture_active;
    logic statistics_completion_baseline;
    logic [31:0] statistics_capture_id;
    logic [31:0] active_statistics_capture_id;
    logic statistics_read_mu2;

    integer word_index;

    function automatic logic valid_action_request_id;
        input logic [31:0] candidate;
        begin
            valid_action_request_id = candidate != 32'd0;
        end
    endfunction

    task automatic finish_bus_error(input logic [7:0] error_value);
        begin
            bridge_error_code <= error_value;
            last_bridge_error <= error_value;
            avalon_readdata <= {24'd0, error_value};
            avalon_waitrequest <= 1'b0;
            avalon_readdatavalid <= avalon_read;
            bus_state <= BUS_RESPONSE;
        end
    endtask

    task automatic begin_action(
        input logic [4:0] kind,
        input logic [5:0] operation,
        input logic [3:0] opcode,
        input logic [5:0] index,
        input logic [15:0] data,
        input logic with_request_id
    );
        begin
            action_kind <= kind;
            action_operation <= operation;
            action_step <= '0;
            generic_opcode <= opcode;
            generic_index <= index;
            generic_data <= data;
            action_has_request_id <= with_request_id;
            action_request_id <= request_id_shadow;
            action_state <= ACTION_ISSUE;
            if (with_request_id) begin
                last_accepted_request_id <= request_id_shadow;
            end
            last_operation <= operation;
            last_bridge_error <= BRIDGE_ERROR_NONE;
            last_core_error <= ERROR_NONE;
            last_core_response <= '0;
            bus_state <= BUS_ACTION_WAIT;
        end
    endtask

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            bus_state <= BUS_IDLE;
            action_state <= ACTION_IDLE;
            action_kind <= ACTION_NONE;
            action_operation <= '0;
            action_step <= '0;
            generic_opcode <= '0;
            generic_index <= '0;
            generic_data <= '0;
            action_has_request_id <= 1'b0;
            action_request_id <= '0;
            action_complete_pulse <= 1'b0;
            action_read_response <= '0;
            core_request_valid <= 1'b0;
            core_request_opcode <= '0;
            core_request_index <= '0;
            core_request_data <= '0;
            avalon_waitrequest <= 1'b1;
            avalon_readdata <= '0;
            avalon_readdatavalid <= 1'b0;
            control_word_valid <= '0;
            fpga_coefficient_valid <= '0;
            for (word_index = 0; word_index < 30; word_index++) begin
                control_words[word_index] <= '0;
            end
            for (word_index = 0;
                    word_index < FPGA_COEFFICIENT_WORDS;
                    word_index = word_index + 1) begin
                fpga_coefficient_words[word_index] <= '0;
            end
            dsm_low_shadow <= '0;
            dsm_high_shadow <= '0;
            dsm_enable_shadow <= 1'b0;
            request_id_shadow <= '0;
            last_accepted_request_id <= '0;
            last_completed_request_id <= '0;
            last_operation <= '0;
            last_bridge_error <= BRIDGE_ERROR_NONE;
            last_core_error <= ERROR_NONE;
            last_core_response <= '0;
            bridge_error_code <= BRIDGE_ERROR_NONE;
            bridge_address_error <= 1'b0;
            bridge_access_error <= 1'b0;
            statistics_ready <= 1'b0;
            statistics_capture_active <= 1'b0;
            statistics_completion_baseline <= 1'b0;
            statistics_capture_id <= '0;
            active_statistics_capture_id <= '0;
            statistics_abort <= 1'b0;
            statistics_read_valid <= 1'b0;
            statistics_read_address <= '0;
            statistics_read_mu2 <= 1'b0;
        end else begin
            core_request_valid <= 1'b0;
            action_complete_pulse <= 1'b0;
            avalon_readdatavalid <= 1'b0;
            statistics_abort <= 1'b0;

            // The completion toggle, done flag, and count cross clock domains
            // independently.  Keep waiting until the complete tuple is stable;
            // observing the toggle alone must not turn CDC skew into a failure.
            if (statistics_capture_active &&
                    (core_statistics_completion_toggle !=
                     statistics_completion_baseline) &&
                    core_statistics_done &&
                    (core_statistics_captured_count == STATISTICS_SAMPLES)) begin
                statistics_capture_active <= 1'b0;
                statistics_ready <= 1'b1;
                statistics_capture_id <= active_statistics_capture_id;
            end

            unique case (action_state)
                ACTION_IDLE: begin
                end

                ACTION_ISSUE: begin
                    unique case (action_kind)
                        ACTION_CORE_READ: begin
                            core_request_opcode <= OP_READ_REGISTER;
                            core_request_index <= generic_index;
                            core_request_data <= 16'd0;
                        end

                        ACTION_GENERIC,
                        ACTION_STATISTICS_START: begin
                            core_request_opcode <= generic_opcode;
                            core_request_index <= generic_index;
                            core_request_data <= generic_data;
                        end

                        ACTION_CONTROL_COMMIT: begin
                            if (action_step < 30) begin
                                core_request_opcode <= OP_WRITE_CONTROL_WORD;
                                core_request_index <= action_step[5:0];
                                core_request_data <= control_words[action_step];
                            end else if (action_step <
                                    30 + FPGA_COEFFICIENT_WORDS) begin
                                core_request_opcode <=
                                    OP_WRITE_FPGA_COEFFICIENT;
                                core_request_index <= action_step - 30;
                                core_request_data <= fpga_coefficient_words[
                                    action_step - 30
                                ];
                            end else begin
                                core_request_opcode <= OP_COMMIT_AND_START;
                                core_request_index <= 6'd0;
                                core_request_data <= generic_data;
                            end
                        end

                        ACTION_DSM_COMMIT: begin
                            core_request_opcode <= OP_SET_DSM_OVERRIDE;
                            core_request_index <= action_step;
                            unique case (action_step)
                                6'd0: core_request_data <= dsm_low_shadow;
                                6'd1: core_request_data <= {3'd0, dsm_high_shadow};
                                default: core_request_data <=
                                    {15'd0, dsm_enable_shadow};
                            endcase
                        end

                        default: begin
                            core_request_opcode <= '0;
                            core_request_index <= '0;
                            core_request_data <= '0;
                        end
                    endcase
                    core_request_valid <= 1'b1;
                    action_state <= ACTION_WAIT;
                end

                ACTION_WAIT: begin
                    if (core_request_done) begin
                        last_core_response <= core_response_data;
                        if (action_kind == ACTION_CORE_READ) begin
                            action_read_response <= core_response_data;
                        end
                        action_state <= ACTION_SETTLE;
                    end
                end

                ACTION_SETTLE: begin
                    last_core_error <= core_error_code;
                    if ((core_error_code != ERROR_NONE) &&
                            action_kind != ACTION_CORE_READ) begin
                        bridge_error_code <= BRIDGE_ERROR_CORE;
                        last_bridge_error <= BRIDGE_ERROR_CORE;
                        if (action_has_request_id) begin
                            last_completed_request_id <= action_request_id;
                        end
                        action_state <= ACTION_IDLE;
                        action_complete_pulse <= 1'b1;
                    end else if (action_kind == ACTION_CONTROL_COMMIT &&
                            action_step < 30 + FPGA_COEFFICIENT_WORDS) begin
                        action_step <= action_step + 1'b1;
                        action_state <= ACTION_ISSUE;
                    end else if (action_kind == ACTION_DSM_COMMIT &&
                            action_step < 2) begin
                        action_step <= action_step + 1'b1;
                        action_state <= ACTION_ISSUE;
                    end else begin
                        if (action_kind == ACTION_STATISTICS_START) begin
                            statistics_ready <= 1'b0;
                            statistics_capture_active <= 1'b1;
                            statistics_completion_baseline <=
                                core_statistics_completion_toggle;
                            active_statistics_capture_id <= action_request_id;
                        end
                        if (action_kind == ACTION_CONTROL_COMMIT) begin
                            control_word_valid <= '0;
                            fpga_coefficient_valid <= '0;
                        end
                        if (action_has_request_id) begin
                            last_completed_request_id <= action_request_id;
                        end
                        action_state <= ACTION_IDLE;
                        action_complete_pulse <= 1'b1;
                    end
                end

                default: action_state <= ACTION_IDLE;
            endcase

            unique case (bus_state)
                BUS_IDLE: begin
                    avalon_waitrequest <= 1'b1;
                    statistics_read_valid <= 1'b0;

                    if (avalon_read || avalon_write) begin
                        if (avalon_read && avalon_write) begin
                            bridge_access_error <= 1'b1;
                            finish_bus_error(BRIDGE_ERROR_ACCESS);
                        end else if ((avalon_address[1:0] != 2'b00) ||
                                (avalon_byteenable != 4'hF)) begin
                            bridge_access_error <= 1'b1;
                            finish_bus_error(BRIDGE_ERROR_ACCESS);
                        end else if (avalon_read) begin
                            unique case (avalon_address)
                                32'h0000: avalon_readdata <= MAGIC;
                                32'h0004: avalon_readdata <= AVALON_ABI_VERSION;
                                32'h0008: avalon_readdata <= {
                                    IMPLEMENTATION_REVISION,
                                    PROTOCOL_VERSION
                                };
                                32'h000C: avalon_readdata <= CAPABILITIES;
                                32'h0010: avalon_readdata <= {
                                    16'd0,
                                    core_error_code,
                                    1'b0,
                                    core_statistics_done,
                                    core_statistics_busy,
                                    core_sar_done,
                                    core_sar_busy,
                                    core_driver_done,
                                    core_driver_busy,
                                    core_control_words_complete
                                };
                                32'h0014: avalon_readdata <= {
                                    16'd0,
                                    bridge_error_code,
                                    5'd0,
                                    bridge_access_error,
                                    bridge_address_error,
                                    action_state != ACTION_IDLE
                                };
                                32'h0018: avalon_readdata <= request_id_shadow;
                                32'h001C: avalon_readdata <=
                                    last_accepted_request_id;
                                32'h0020: avalon_readdata <=
                                    last_completed_request_id;
                                32'h0024: avalon_readdata <=
                                    {26'd0, last_operation};
                                32'h0028: avalon_readdata <= {
                                    last_bridge_error,
                                    last_core_error,
                                    last_core_response
                                };
                                32'h0030: avalon_readdata <=
                                    {31'd0, statistics_ready};
                                32'h0034: avalon_readdata <=
                                    statistics_capture_id;
                                32'h0038: avalon_readdata <=
                                    {16'd0, core_statistics_captured_count};
                                default: begin
                                    if ((avalon_address >= 32'h0200) &&
                                            (avalon_address <= 32'h02B4)) begin
                                        begin_action(
                                            ACTION_CORE_READ,
                                            OPERATION_READ_REGISTER,
                                            OP_READ_REGISTER,
                                            (avalon_address - 32'h0200) >> 2,
                                            16'd0,
                                            1'b0
                                        );
                                    end else if ((avalon_address >= 32'h0400) &&
                                            (avalon_address <= 32'h071C)) begin
                                        if (!statistics_ready) begin
                                            finish_bus_error(
                                                BRIDGE_ERROR_STATISTICS_NOT_READY
                                            );
                                        end else begin
                                            statistics_read_address <=
                                                (avalon_address - 32'h0400) >> 3;
                                            statistics_read_mu2 <= avalon_address[2];
                                            statistics_read_valid <= 1'b1;
                                            bus_state <= BUS_STATISTICS_ACCEPT;
                                        end
                                    end else begin
                                        bridge_address_error <= 1'b1;
                                        finish_bus_error(BRIDGE_ERROR_ADDRESS);
                                    end
                                end
                            endcase

                            if ((avalon_address <= 32'h0038) &&
                                    (bus_state == BUS_IDLE)) begin
                                avalon_waitrequest <= 1'b0;
                                avalon_readdatavalid <= 1'b1;
                                bus_state <= BUS_RESPONSE;
                            end
                        end else begin
                            if (avalon_address == 32'h0018) begin
                                request_id_shadow <= avalon_writedata;
                                avalon_waitrequest <= 1'b0;
                                bus_state <= BUS_RESPONSE;
                            end else if ((avalon_address >= 32'h0100) &&
                                    (avalon_address <= 32'h0174)) begin
                                control_words[
                                    (avalon_address - 32'h0100) >> 2
                                ] <= avalon_writedata[15:0];
                                control_word_valid[
                                    (avalon_address - 32'h0100) >> 2
                                ] <= 1'b1;
                                avalon_waitrequest <= 1'b0;
                                bus_state <= BUS_RESPONSE;
                            end else if ((avalon_address >= 32'h0800) &&
                                    (avalon_address <= 32'h08BC)) begin
                                fpga_coefficient_words[
                                    (avalon_address - 32'h0800) >> 2
                                ] <= avalon_writedata[15:0];
                                fpga_coefficient_valid[
                                    (avalon_address - 32'h0800) >> 2
                                ] <= 1'b1;
                                avalon_waitrequest <= 1'b0;
                                bus_state <= BUS_RESPONSE;
                            end else if (avalon_address == 32'h0048) begin
                                dsm_low_shadow <= avalon_writedata[15:0];
                                avalon_waitrequest <= 1'b0;
                                bus_state <= BUS_RESPONSE;
                            end else if (avalon_address == 32'h004C) begin
                                dsm_high_shadow <= avalon_writedata[12:0];
                                avalon_waitrequest <= 1'b0;
                                bus_state <= BUS_RESPONSE;
                            end else if (!valid_action_request_id(
                                    request_id_shadow)) begin
                                finish_bus_error(BRIDGE_ERROR_REQUEST_ID);
                            end else if (request_id_shadow ==
                                    last_completed_request_id) begin
                                avalon_waitrequest <= 1'b0;
                                bus_state <= BUS_RESPONSE;
                            end else if (action_state != ACTION_IDLE) begin
                                finish_bus_error(BRIDGE_ERROR_BUSY);
                            end else begin
                                unique case (avalon_address)
                                    32'h0040: begin_action(
                                        ACTION_GENERIC,
                                        OPERATION_SET_N,
                                        OP_SET_CONTROL_SAMPLES,
                                        6'd0,
                                        avalon_writedata[15:0],
                                        1'b1
                                    );
                                    32'h0044: begin_action(
                                        ACTION_GENERIC,
                                        OPERATION_SET_FILTER,
                                        OP_SET_DECIMATION,
                                        6'd0,
                                        avalon_writedata[15:0],
                                        1'b1
                                    );
                                    32'h0050: begin
                                        dsm_enable_shadow <=
                                            avalon_writedata[0];
                                        begin_action(
                                            ACTION_DSM_COMMIT,
                                            OPERATION_DSM_COMMIT,
                                            OP_SET_DSM_OVERRIDE,
                                            6'd0,
                                            16'd0,
                                            1'b1
                                        );
                                    end
                                    32'h0054: begin
                                        if (!(&control_word_valid) ||
                                                core_error_code != ERROR_NONE) begin
                                            last_accepted_request_id <=
                                                request_id_shadow;
                                            last_completed_request_id <=
                                                request_id_shadow;
                                            last_operation <=
                                                OPERATION_CONTROL_COMMIT;
                                            last_bridge_error <=
                                                BRIDGE_ERROR_CONTROL_INCOMPLETE;
                                            bridge_error_code <=
                                                BRIDGE_ERROR_CONTROL_INCOMPLETE;
                                            avalon_waitrequest <= 1'b0;
                                            bus_state <= BUS_RESPONSE;
                                        end else if (!(&fpga_coefficient_valid)) begin
                                            last_accepted_request_id <=
                                                request_id_shadow;
                                            last_completed_request_id <=
                                                request_id_shadow;
                                            last_operation <=
                                                OPERATION_CONTROL_COMMIT;
                                            last_bridge_error <=
                                                BRIDGE_ERROR_COEFFICIENT_INCOMPLETE;
                                            bridge_error_code <=
                                                BRIDGE_ERROR_COEFFICIENT_INCOMPLETE;
                                            avalon_waitrequest <= 1'b0;
                                            bus_state <= BUS_RESPONSE;
                                        end else begin
                                            begin_action(
                                                ACTION_CONTROL_COMMIT,
                                                OPERATION_CONTROL_COMMIT,
                                                OP_COMMIT_AND_START,
                                                6'd0,
                                                avalon_writedata[15:0],
                                                1'b1
                                            );
                                        end
                                    end
                                    32'h0058: begin_action(
                                        ACTION_GENERIC,
                                        OPERATION_SAR_START,
                                        OP_SAR_START,
                                        6'd0,
                                        avalon_writedata[15:0],
                                        1'b1
                                    );
                                    32'h005C: begin_action(
                                        ACTION_GENERIC,
                                        OPERATION_SAR_COMPARE,
                                        OP_SAR_COMPARE,
                                        6'd0,
                                        avalon_writedata[15:0],
                                        1'b1
                                    );
                                    32'h0060: begin
                                        bridge_error_code <= BRIDGE_ERROR_NONE;
                                        bridge_address_error <= 1'b0;
                                        bridge_access_error <= 1'b0;
                                        begin_action(
                                            ACTION_GENERIC,
                                            OPERATION_CLEAR_ERROR,
                                            OP_CLEAR_ERROR,
                                            6'd0,
                                            16'd0,
                                            1'b1
                                        );
                                    end
                                    32'h0064: begin
                                        if (statistics_ready ||
                                                statistics_capture_active ||
                                                core_statistics_busy) begin
                                            finish_bus_error(BRIDGE_ERROR_BUSY);
                                        end else begin
                                            begin_action(
                                                ACTION_STATISTICS_START,
                                                OPERATION_STATISTICS_START,
                                                OP_STATISTICS_START,
                                                6'd0,
                                                16'd0,
                                                1'b1
                                            );
                                        end
                                    end
                                    32'h0068: begin_action(
                                        ACTION_GENERIC,
                                        OPERATION_LATCH_LIVE,
                                        OP_LATCH_LIVE_SAMPLE,
                                        6'd0,
                                        16'd0,
                                        1'b1
                                    );
                                    32'h006C: begin_action(
                                        ACTION_GENERIC,
                                        OPERATION_LATCH_CALIBRATION,
                                        OP_LATCH_CALIBRATION_SAMPLE,
                                        6'd0,
                                        16'd0,
                                        1'b1
                                    );
                                    32'h0070: begin
                                        last_accepted_request_id <=
                                            request_id_shadow;
                                        last_completed_request_id <=
                                            request_id_shadow;
                                        last_operation <=
                                            OPERATION_STATISTICS_RELEASE;
                                        if (!statistics_ready ||
                                                avalon_writedata !=
                                                statistics_capture_id) begin
                                            bridge_error_code <=
                                                BRIDGE_ERROR_CAPTURE_ID;
                                            last_bridge_error <=
                                                BRIDGE_ERROR_CAPTURE_ID;
                                        end else begin
                                            statistics_ready <= 1'b0;
                                            last_bridge_error <=
                                                BRIDGE_ERROR_NONE;
                                        end
                                        avalon_waitrequest <= 1'b0;
                                        bus_state <= BUS_RESPONSE;
                                    end
                                    32'h0074: begin
                                        last_accepted_request_id <=
                                            request_id_shadow;
                                        last_completed_request_id <=
                                            request_id_shadow;
                                        last_operation <=
                                            OPERATION_STATISTICS_ABORT;
                                        statistics_abort <= 1'b1;
                                        statistics_ready <= 1'b0;
                                        statistics_capture_active <= 1'b0;
                                        statistics_capture_id <= '0;
                                        last_bridge_error <=
                                            BRIDGE_ERROR_NONE;
                                        avalon_waitrequest <= 1'b0;
                                        bus_state <= BUS_RESPONSE;
                                    end
                                    default: begin
                                        bridge_address_error <= 1'b1;
                                        finish_bus_error(BRIDGE_ERROR_ADDRESS);
                                    end
                                endcase
                            end
                        end
                    end
                end

                BUS_ACTION_WAIT: begin
                    if (action_complete_pulse) begin
                        if (action_kind == ACTION_CORE_READ) begin
                            avalon_readdata <= {16'd0, action_read_response};
                            avalon_readdatavalid <= 1'b1;
                        end
                        avalon_waitrequest <= 1'b0;
                        bus_state <= BUS_RESPONSE;
                    end
                end

                BUS_STATISTICS_ACCEPT: begin
                    statistics_read_valid <= 1'b1;
                    if (statistics_read_ready) begin
                        statistics_read_valid <= 1'b0;
                        bus_state <= BUS_STATISTICS_DATA;
                    end
                end

                BUS_STATISTICS_DATA: begin
                    if (statistics_read_data_valid) begin
                        if (statistics_read_mu2) begin
                            avalon_readdata <= {
                                14'd0,
                                statistics_read_data[35:18]
                            };
                        end else begin
                            avalon_readdata <= {
                                14'd0,
                                statistics_read_data[17:0]
                            };
                        end
                        avalon_readdatavalid <= 1'b1;
                        avalon_waitrequest <= 1'b0;
                        bus_state <= BUS_RESPONSE;
                    end
                end

                BUS_RESPONSE: begin
                    avalon_waitrequest <= 1'b1;
                    bus_state <= BUS_IDLE;
                end

                default: bus_state <= BUS_IDLE;
            endcase
        end
    end

endmodule
