module fpga_v1_core #(
    parameter int unsigned HALF_PERIOD_CYCLES = 25_000,
    parameter int unsigned TOTAL_CYCLES = 37_500_000,
    parameter int unsigned DEFAULT_DECIMATION = 1024
) (
    input  logic        clk_50mhz,
    input  logic        rst,

    input  logic        fs_data_out33,
    input  logic        bs_t_out33,
    input  logic        bs_c_out33,

    input  logic        command_valid,
    input  logic [3:0]  command_opcode,
    input  logic [5:0]  command_index,
    input  logic [15:0] command_data,
    output logic        command_done,
    output logic [15:0] command_response,

    output logic [2:0]  address,
    output logic        sr_clk,
    output logic        sr_in,
    output logic [4:0]  sr_enable,
    output logic        chip_reset,
    output logic        start_dll,
    output logic        start_ana,
    output logic        clk_3p3,
    output logic        dsm_3p3,

    output logic [17:0] mu1,
    output logic [17:0] mu2,
    output logic [15:0] sample_sequence,
    output logic        sample_toggle,
    output logic [15:0] decimation_length,

    output logic [7:0]  error_code,
    output logic        control_words_complete,
    output logic        driver_busy,
    output logic        driver_done,
    output logic        sar_busy,
    output logic        sar_done,
    output logic [7:0]  sar_step_index,
    output logic signed [28:0] sar_code
);

    import fpga_v1_protocol_pkg::*;

    logic [479:0] shadow_control_word;
    logic [473:0] active_control_word;
    logic [29:0] control_word_valid;
    logic [2:0] selected_chip;
    logic broadcast;
    logic driver_start_pulse;
    logic [9:0] driver_bit_index;
    logic [3:0] driver_state;

    logic [17:0] alpha;
    logic polarity;
    logic [28:0] batch_a0;
    logic [28:0] batch_a1;
    logic [28:0] batch_a2;
    logic [28:0] batch_a3;
    logic [28:0] batch_a4;
    logic [28:0] batch_a5;
    logic [28:0] batch_a6;
    logic [28:0] poly_a0;
    logic [28:0] poly_a1;
    logic [28:0] poly_a2;
    logic [28:0] poly_a3;
    logic [28:0] poly_a4;
    logic [28:0] poly_a5;
    logic [28:0] poly_a6;
    logic [28:0] poly_a7;

    logic [15:0] read_response_mux;

    logic rst_fs;
    logic sar_enable_source;
    logic sar_start_source_pulse;
    logic sar_compare_source_pulse;
    logic sar_bit_source;
    logic sar_start_fs_pulse;
    logic sar_compare_fs_pulse;
    logic sar_enable_meta;
    logic sar_enable_fs;
    logic sar_bit_meta;
    logic sar_bit_fs;

    logic sar_busy_fs;
    logic sar_done_fs;
    logic sar_trial_valid_fs;
    logic sar_compare_ready_fs;
    logic [7:0] sar_step_index_fs;
    logic signed [28:0] sar_code_fs;

    logic sar_busy_meta;
    logic sar_done_meta;
    logic [7:0] sar_step_meta;
    logic signed [28:0] sar_code_meta;

    logic [17:0] mu1_fs;
    logic [17:0] mu2_fs;
    logic mu_valid_fs;
    logic [15:0] active_decimation_fs;

    logic [17:0] div_out_unused;
    logic [28:0] poly_system_unused;
    logic [28:0] poly_freq_unused;
    logic [28:0] phase_diff_unused;
    logic phase_diff_valid_unused;
    logic [28:0] difference_unused;
    logic [28:0] calibrated_error_unused;
    logic [28:0] up_unused;
    logic [28:0] data_unused;

    assign control_words_complete = &control_word_valid;

    always_comb begin
        unique case (command_index)
            6'd0: read_response_mux = {
                error_code,
                control_words_complete,
                sar_done,
                sar_busy,
                driver_done,
                driver_busy,
                sample_toggle,
                1'b0
            };
            6'd1: read_response_mux = mu1[15:0];
            6'd2: read_response_mux = {14'b0, mu1[17:16]};
            6'd3: read_response_mux = mu2[15:0];
            6'd4: read_response_mux = {14'b0, mu2[17:16]};
            6'd5: read_response_mux = sample_sequence;
            6'd6: read_response_mux = sar_code[15:0];
            6'd7: read_response_mux = {15'b0, sar_code[16]};
            6'd8: read_response_mux = {8'b0, sar_step_index};
            6'd9: read_response_mux = decimation_length;
            6'd10: read_response_mux = {6'b0, driver_bit_index};
            6'd11: read_response_mux = {12'b0, driver_state};
            default: read_response_mux = 16'h0000;
        endcase
    end

    always_ff @(posedge clk_50mhz or posedge rst) begin
        if (rst) begin
            shadow_control_word <= '0;
            active_control_word <= '0;
            control_word_valid <= '0;
            selected_chip <= '0;
            broadcast <= 1'b0;
            driver_start_pulse <= 1'b0;
            decimation_length <= 16'(DEFAULT_DECIMATION);
            sar_enable_source <= 1'b0;
            sar_start_source_pulse <= 1'b0;
            sar_compare_source_pulse <= 1'b0;
            sar_bit_source <= 1'b0;
            error_code <= ERROR_NONE;
            command_done <= 1'b0;
            command_response <= '0;
        end else begin
            driver_start_pulse <= 1'b0;
            sar_start_source_pulse <= 1'b0;
            sar_compare_source_pulse <= 1'b0;
            command_done <= 1'b0;

            if (command_valid) begin
                command_response <= 16'h0000;

                unique case (command_opcode)
                    OP_WRITE_CONTROL_WORD: begin
                        if (command_index < 30) begin
                            shadow_control_word[
                                command_index * 16 +: 16
                            ] <= command_data;
                            control_word_valid[command_index] <= 1'b1;
                        end else begin
                            error_code <= ERROR_CONTROL_INDEX;
                        end
                    end

                    OP_COMMIT_AND_START: begin
                        // A sticky error from any preceding block write must
                        // prevent a mixed old/new control word from reaching
                        // the chip.  The host starts every batch with
                        // OP_CLEAR_ERROR and retries the complete 30 blocks.
                        if (error_code == ERROR_NONE) begin
                            if (driver_busy) begin
                                error_code <= ERROR_DRIVER_BUSY;
                            end else if (!control_words_complete) begin
                                error_code <= ERROR_CONTROL_INCOMPLETE;
                            end else begin
                                active_control_word <=
                                    shadow_control_word[473:0];
                                selected_chip <= command_data[2:0];
                                broadcast <= command_data[3];
                                driver_start_pulse <= 1'b1;
                                // Make completeness describe the next
                                // transaction, not a stale word left by an
                                // earlier successful commit.
                                control_word_valid <= '0;
                            end
                        end
                    end

                    OP_SET_DECIMATION: begin
                        if ((command_data < 32) || (command_data > 4096)) begin
                            error_code <= ERROR_DECIMATION_RANGE;
                        end else begin
                            decimation_length <= command_data;
                        end
                    end

                    OP_SAR_START: begin
                        sar_enable_source <= command_data[0];
                        sar_start_source_pulse <= 1'b1;
                    end

                    OP_SAR_COMPARE: begin
                        if (sar_busy) begin
                            sar_bit_source <= command_data[0];
                            sar_compare_source_pulse <= 1'b1;
                        end else begin
                            error_code <= ERROR_SAR_NOT_READY;
                        end
                    end

                    OP_CLEAR_ERROR: begin
                        error_code <= ERROR_NONE;
                    end

                    OP_READ_REGISTER: begin
                        command_response <= read_response_mux;
                    end

                    default: begin
                        error_code <= ERROR_UNKNOWN_OPCODE;
                    end
                endcase

                command_done <= 1'b1;
            end
        end
    end

    chip_control_driver #(
        .CONTROL_BITS(474),
        .HALF_PERIOD_CYCLES(HALF_PERIOD_CYCLES),
        .TOTAL_CYCLES(TOTAL_CYCLES)
    ) control_driver (
        .clk(clk_50mhz),
        .rst(rst),
        .start(driver_start_pulse),
        .serial_bits(active_control_word),
        .chip_number(selected_chip),
        .broadcast(broadcast),
        .address(address),
        .sr_clk(sr_clk),
        .sr_in(sr_in),
        .sr_enable(sr_enable),
        .chip_reset(chip_reset),
        .start_dll(start_dll),
        .start_ana(start_ana),
        .busy(driver_busy),
        .done(driver_done),
        .bit_index(driver_bit_index),
        .state_debug(driver_state)
    );

    control_word_unpack unpack (
        .serial_bits(active_control_word),
        .alpha(alpha),
        .polarity(polarity),
        .batch_a0(batch_a0),
        .batch_a1(batch_a1),
        .batch_a2(batch_a2),
        .batch_a3(batch_a3),
        .batch_a4(batch_a4),
        .batch_a5(batch_a5),
        .batch_a6(batch_a6),
        .poly_a0(poly_a0),
        .poly_a1(poly_a1),
        .poly_a2(poly_a2),
        .poly_a3(poly_a3),
        .poly_a4(poly_a4),
        .poly_a5(poly_a5),
        .poly_a6(poly_a6),
        .poly_a7(poly_a7)
    );

    clock_generator_2mhz dsm_clock (
        .clk_50mhz(clk_50mhz),
        .rst(rst),
        .clk_2mhz(clk_3p3)
    );

    reset_synchronizer fs_reset (
        .clk(fs_data_out33),
        .async_reset(rst),
        .sync_reset(rst_fs)
    );

    pulse_toggle_cdc sar_start_cdc (
        .source_clk(clk_50mhz),
        .source_rst(rst),
        .source_pulse(sar_start_source_pulse),
        .destination_clk(fs_data_out33),
        .destination_rst(rst_fs),
        .destination_pulse(sar_start_fs_pulse)
    );

    pulse_toggle_cdc sar_compare_cdc (
        .source_clk(clk_50mhz),
        .source_rst(rst),
        .source_pulse(sar_compare_source_pulse),
        .destination_clk(fs_data_out33),
        .destination_rst(rst_fs),
        .destination_pulse(sar_compare_fs_pulse)
    );

    always_ff @(posedge fs_data_out33 or posedge rst_fs) begin
        if (rst_fs) begin
            sar_enable_meta <= 1'b0;
            sar_enable_fs <= 1'b0;
            sar_bit_meta <= 1'b0;
            sar_bit_fs <= 1'b0;
        end else begin
            sar_enable_meta <= sar_enable_source;
            sar_enable_fs <= sar_enable_meta;
            sar_bit_meta <= sar_bit_source;
            sar_bit_fs <= sar_bit_meta;
        end
    end

    triangular_decimator_runtime #(
        .OUTPUT_WIDTH(18),
        .DEFAULT_LENGTH(DEFAULT_DECIMATION),
        .MIN_LENGTH(32),
        .MAX_LENGTH(4096)
    ) ui_decimator (
        .rst(rst_fs),
        .sample_clk(fs_data_out33),
        .bs_t(bs_t_out33),
        .bs_c(bs_c_out33),
        .configured_length_async(decimation_length),
        .mu1(mu1_fs),
        .mu2(mu2_fs),
        .valid(mu_valid_fs),
        .active_length(active_decimation_fs)
    );

    sample_mailbox_cdc #(
        .DATA_WIDTH(18)
    ) ui_sample_mailbox (
        .source_clk(fs_data_out33),
        .source_rst(rst_fs),
        .source_mu1(mu1_fs),
        .source_mu2(mu2_fs),
        .source_valid(mu_valid_fs),
        .destination_clk(clk_50mhz),
        .destination_rst(rst),
        .destination_mu1(mu1),
        .destination_mu2(mu2),
        .sample_sequence(sample_sequence),
        .sample_toggle(sample_toggle)
    );

    DigOsc on_chip_calibration (
        .rst(rst_fs),
        .clk(fs_data_out33),
        .clk_DSM(clk_3p3),
        .BS0(bs_t_out33),
        .BS1(bs_c_out33),
        .polarity(polarity),
        .SAR_enable(sar_enable_fs),
        .SAR_start(sar_start_fs_pulse),
        .SAR_compare_valid(sar_compare_fs_pulse),
        .SAR_bit_in(sar_bit_fs),
        .batch_a6(batch_a6),
        .batch_a5(batch_a5),
        .batch_a4(batch_a4),
        .batch_a3(batch_a3),
        .batch_a2(batch_a2),
        .batch_a1(batch_a1),
        .batch_a0(batch_a0),
        .poly_a7(poly_a7),
        .poly_a6(poly_a6),
        .poly_a5(poly_a5),
        .poly_a4(poly_a4),
        .poly_a3(poly_a3),
        .poly_a2(poly_a2),
        .poly_a1(poly_a1),
        .poly_a0(poly_a0),
        .alpha(alpha),
        .DSM(dsm_3p3),
        .div_out_test(div_out_unused),
        .poly_system_result_test(poly_system_unused),
        .poly_freq_result_test(poly_freq_unused),
        .phase_diff_test(phase_diff_unused),
        .phase_diff_valid_out(phase_diff_valid_unused),
        .difference_test(difference_unused),
        .calibrated_error_test(calibrated_error_unused),
        .SAR_code(sar_code_fs),
        .SAR_trial_valid(sar_trial_valid_fs),
        .SAR_compare_ready(sar_compare_ready_fs),
        .SAR_busy(sar_busy_fs),
        .SAR_done(sar_done_fs),
        .SAR_step_index(sar_step_index_fs),
        .UP(up_unused),
        .data(data_unused)
    );

    always_ff @(posedge clk_50mhz or posedge rst) begin
        if (rst) begin
            sar_busy_meta <= 1'b0;
            sar_busy <= 1'b0;
            sar_done_meta <= 1'b0;
            sar_done <= 1'b0;
            sar_step_meta <= '0;
            sar_step_index <= '0;
            sar_code_meta <= '0;
            sar_code <= '0;
        end else begin
            sar_busy_meta <= sar_busy_fs;
            sar_busy <= sar_busy_meta;
            sar_done_meta <= sar_done_fs;
            sar_done <= sar_done_meta;
            sar_step_meta <= sar_step_index_fs;
            sar_step_index <= sar_step_meta;
            sar_code_meta <= sar_code_fs;
            sar_code <= sar_code_meta;
        end
    end

endmodule
