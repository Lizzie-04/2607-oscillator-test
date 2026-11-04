module fpga_v2_core #(
    parameter int unsigned HALF_PERIOD_CYCLES = 25_000,
    parameter int unsigned DEFAULT_DAQ_SAMPLES =
        fpga_v2_protocol_pkg::DEFAULT_CONTROL_SAMPLES,
    parameter int unsigned DEFAULT_DECIMATION =
        fpga_v2_protocol_pkg::DEFAULT_FILTER_HALF_LENGTH,
    parameter int unsigned MIN_DECIMATION =
        fpga_v2_protocol_pkg::MIN_FILTER_HALF_LENGTH,
    parameter int unsigned MAX_DECIMATION =
        fpga_v2_protocol_pkg::MAX_FILTER_HALF_LENGTH
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
    output logic [15:0] active_decimation_length,

    output logic [7:0]  error_code,
    output logic        control_words_complete,
    output logic        driver_busy,
    output logic        driver_done,
    output logic        sar_busy,
    output logic        sar_done,
    output logic [7:0]  sar_step_index,
    output logic signed [28:0] sar_code,
    output logic        statistics_busy,
    output logic        statistics_done,
    output logic        statistics_completion_toggle,
    output logic [15:0] statistics_captured_count
);

    import fpga_v2_protocol_pkg::*;

    logic [479:0] shadow_control_word;
    logic [473:0] active_control_word;
    logic [473:0] control_word_meta_2mhz;
    logic [473:0] control_word_sync_2mhz;
    logic [473:0] active_control_word_2mhz;
    logic configuration_toggle_source;
    logic configuration_toggle_meta;
    logic configuration_toggle_sync;
    logic configuration_toggle_delay;
    logic configuration_toggle_capture_delay;
    logic [29:0] control_word_valid;
    logic [2:0] selected_chip;
    logic broadcast;
    logic [15:0] control_sample_count;
    logic driver_start_pulse;
    logic [9:0] driver_bit_index;
    logic [3:0] driver_state;
    logic signed [DSM_OVERRIDE_WIDTH-1:0] dsm_override_shadow;
    logic signed [DSM_OVERRIDE_WIDTH-1:0] dsm_override_value_source;
    logic dsm_override_enable_source;
    logic dsm_override_toggle_source;
    logic [17:0] live_snapshot_mu1;
    logic [17:0] live_snapshot_mu2;
    logic [15:0] live_snapshot_sequence;
    logic [15:0] live_snapshot_decimation_length;
    logic [17:0] live_snapshot_onchip_mu1;
    logic [17:0] live_snapshot_onchip_mu2;
    logic [15:0] live_snapshot_onchip_sequence;

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

    logic rst_2mhz;
    logic digital_reset_async;
    logic digital_rst_2mhz;
    logic digital_rst_sample;
    logic sar_enable_source;
    logic sar_start_source_pulse;
    logic sar_compare_source_pulse;
    logic sar_bit_source;
    logic sar_start_2mhz_pulse;
    logic sar_compare_2mhz_pulse;
    logic sar_enable_meta;
    logic sar_enable_2mhz;
    logic sar_bit_meta;
    logic sar_bit_2mhz;

    logic sar_busy_2mhz;
    logic sar_done_2mhz;
    logic sar_trial_valid_2mhz;
    logic sar_compare_ready_2mhz;
    logic [7:0] sar_step_index_2mhz;
    logic signed [28:0] sar_code_2mhz;

    logic sar_busy_meta;
    logic sar_done_meta;
    logic [7:0] sar_step_meta;
    logic signed [28:0] sar_code_meta;

    logic [17:0] mu1_2mhz;
    logic [17:0] mu2_2mhz;
    logic mu_valid_2mhz;
    logic [15:0] active_decimation_2mhz;
    logic [15:0] active_decimation_meta;
    logic [17:0] onchip_mu1_2mhz;
    logic [17:0] onchip_mu2_2mhz;
    logic onchip_mu_valid_2mhz;
    logic [17:0] onchip_mu1;
    logic [17:0] onchip_mu2;
    logic [15:0] onchip_sample_sequence;
    logic onchip_sample_toggle;

    logic statistics_start_source_pulse;
    logic statistics_start_2mhz_pulse;
    logic statistics_busy_2mhz;
    logic statistics_done_2mhz;
    logic statistics_completion_toggle_2mhz;
    logic [15:0] statistics_captured_count_2mhz;
    logic statistics_busy_meta;
    logic statistics_done_meta;
    logic statistics_completion_toggle_meta;
    logic [15:0] statistics_captured_count_meta;
    logic statistics_request_active;
    logic statistics_start_toggle_baseline;

    logic statistics_memory_read_enable;
    logic [6:0] statistics_memory_read_address;
    logic [35:0] statistics_memory_read_data;
    logic statistics_read_pending;
    logic statistics_read_wait;
    logic [1:0] statistics_read_part;

    logic dsm_override_enable_meta;
    logic dsm_override_enable_sync;
    logic dsm_override_enable_2mhz;
    logic signed [DSM_OVERRIDE_WIDTH-1:0] dsm_override_value_meta;
    logic signed [DSM_OVERRIDE_WIDTH-1:0] dsm_override_value_sync;
    logic signed [DSM_OVERRIDE_WIDTH-1:0] dsm_override_value_2mhz;
    logic dsm_override_toggle_meta;
    logic dsm_override_toggle_sync;
    logic dsm_override_toggle_delay;
    logic dsm_override_toggle_capture_delay;
    logic dsm_override_applied_2mhz_pulse;

    logic signed [28:0] calibration_poly_freq_2mhz;
    logic signed [28:0] calibration_poly_system_2mhz;
    logic signed [28:0] calibration_sar_correction_2mhz;
    logic signed [28:0] calibration_dsm_input_2mhz;
    logic calibration_algorithm_valid_2mhz;
    logic signed [28:0] calibration_poly_freq;
    logic signed [28:0] calibration_poly_system;
    logic signed [28:0] calibration_sar_correction;
    logic signed [28:0] calibration_dsm_input;
    logic calibration_override_enabled;
    logic calibration_sample_valid;
    logic [15:0] calibration_sample_sequence;
    logic signed [28:0] calibration_snapshot_poly_freq;
    logic signed [28:0] calibration_snapshot_poly_system;
    logic signed [28:0] calibration_snapshot_sar_correction;
    logic signed [28:0] calibration_snapshot_dsm_input;
    logic calibration_snapshot_override_enabled;
    logic calibration_snapshot_valid;
    logic [15:0] calibration_snapshot_sequence;

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
            6'd7: read_response_mux = {3'b000, sar_code[28:16]};
            6'd8: read_response_mux = {8'b0, sar_step_index};
            6'd9: read_response_mux = decimation_length;
            6'd10: read_response_mux = {6'b0, driver_bit_index};
            6'd11: read_response_mux = {12'b0, driver_state};
            6'd12: read_response_mux = {
                12'b0,
                statistics_request_active,
                statistics_busy,
                statistics_done,
                statistics_completion_toggle
            };
            6'd13: read_response_mux = statistics_captured_count;
            6'd14: read_response_mux = PROTOCOL_VERSION;
            6'd15: read_response_mux = active_decimation_length;
            6'd16: read_response_mux = IMPLEMENTATION_REVISION;
            6'd17: read_response_mux = control_sample_count;
            6'd18: read_response_mux = {15'b0, dsm_override_enable_source};
            6'd19: read_response_mux = dsm_override_value_source[15:0];
            6'd20: read_response_mux = {
                3'b000,
                dsm_override_value_source[DSM_OVERRIDE_WIDTH-1:16]
            };
            6'd21: read_response_mux = live_snapshot_mu1[15:0];
            6'd22: read_response_mux = {14'b0, live_snapshot_mu1[17:16]};
            6'd23: read_response_mux = live_snapshot_mu2[15:0];
            6'd24: read_response_mux = {14'b0, live_snapshot_mu2[17:16]};
            6'd25: read_response_mux = live_snapshot_onchip_mu1[15:0];
            6'd26: read_response_mux = {14'b0, live_snapshot_onchip_mu1[17:16]};
            6'd27: read_response_mux = live_snapshot_onchip_mu2[15:0];
            6'd28: read_response_mux = {14'b0, live_snapshot_onchip_mu2[17:16]};
            6'd29: read_response_mux = live_snapshot_sequence;
            6'd30: read_response_mux = live_snapshot_decimation_length;
            6'd31: read_response_mux = live_snapshot_onchip_sequence;
            6'd32: read_response_mux = calibration_snapshot_poly_freq[15:0];
            6'd33: read_response_mux = {
                3'b000,
                calibration_snapshot_poly_freq[28:16]
            };
            6'd34: read_response_mux = calibration_snapshot_poly_system[15:0];
            6'd35: read_response_mux = {
                3'b000,
                calibration_snapshot_poly_system[28:16]
            };
            6'd36: read_response_mux =
                calibration_snapshot_sar_correction[15:0];
            6'd37: read_response_mux = {
                3'b000,
                calibration_snapshot_sar_correction[28:16]
            };
            6'd38: read_response_mux = calibration_snapshot_dsm_input[15:0];
            6'd39: read_response_mux = {
                3'b000,
                calibration_snapshot_dsm_input[28:16]
            };
            6'd40: read_response_mux = calibration_snapshot_sequence;
            6'd41: read_response_mux = {
                14'b0,
                calibration_snapshot_valid,
                calibration_snapshot_override_enabled
            };
            default: read_response_mux = 16'h0000;
        endcase
    end

    always_ff @(posedge clk_50mhz or posedge rst) begin
        if (rst) begin
            shadow_control_word <= '0;
            active_control_word <= '0;
            configuration_toggle_source <= 1'b0;
            control_word_valid <= '0;
            selected_chip <= '0;
            broadcast <= 1'b0;
            control_sample_count <= 16'(DEFAULT_DAQ_SAMPLES);
            driver_start_pulse <= 1'b0;
            decimation_length <= 16'(DEFAULT_DECIMATION);
            sar_enable_source <= 1'b0;
            sar_start_source_pulse <= 1'b0;
            sar_compare_source_pulse <= 1'b0;
            sar_bit_source <= 1'b0;
            statistics_start_source_pulse <= 1'b0;
            statistics_request_active <= 1'b0;
            statistics_start_toggle_baseline <= 1'b0;
            statistics_memory_read_enable <= 1'b0;
            statistics_memory_read_address <= '0;
            statistics_read_pending <= 1'b0;
            statistics_read_wait <= 1'b0;
            statistics_read_part <= '0;
            dsm_override_shadow <= '0;
            dsm_override_value_source <= '0;
            dsm_override_enable_source <= 1'b0;
            dsm_override_toggle_source <= 1'b0;
            live_snapshot_mu1 <= '0;
            live_snapshot_mu2 <= '0;
            live_snapshot_sequence <= '0;
            live_snapshot_decimation_length <= 16'(DEFAULT_DECIMATION);
            live_snapshot_onchip_mu1 <= '0;
            live_snapshot_onchip_mu2 <= '0;
            live_snapshot_onchip_sequence <= '0;
            calibration_snapshot_poly_freq <= '0;
            calibration_snapshot_poly_system <= '0;
            calibration_snapshot_sar_correction <= '0;
            calibration_snapshot_dsm_input <= '0;
            calibration_snapshot_override_enabled <= 1'b0;
            calibration_snapshot_valid <= 1'b0;
            calibration_snapshot_sequence <= '0;
            error_code <= ERROR_NONE;
            command_done <= 1'b0;
            command_response <= '0;
        end else begin
            driver_start_pulse <= 1'b0;
            sar_start_source_pulse <= 1'b0;
            sar_compare_source_pulse <= 1'b0;
            statistics_start_source_pulse <= 1'b0;
            statistics_memory_read_enable <= 1'b0;
            command_done <= 1'b0;

            if (statistics_request_active &&
                    (statistics_completion_toggle !=
                     statistics_start_toggle_baseline)) begin
                statistics_request_active <= 1'b0;
            end

            if (statistics_read_pending) begin
                if (statistics_read_wait) begin
                    unique case (statistics_read_part)
                        2'd0: command_response <=
                            statistics_memory_read_data[15:0];
                        2'd1: command_response <=
                            statistics_memory_read_data[31:16];
                        default: command_response <= {
                            12'b0,
                            statistics_memory_read_data[35:32]
                        };
                    endcase
                    command_done <= 1'b1;
                    statistics_read_pending <= 1'b0;
                    statistics_read_wait <= 1'b0;
                end else begin
                    // The inferred M10K read port is synchronous.  Wait one
                    // extra host clock after asserting read_enable before
                    // returning its registered output to the arbiter.
                    statistics_read_wait <= 1'b1;
                end
            end else if (command_valid &&
                    command_opcode == OP_STATISTICS_READ) begin
                command_response <= 16'h0000;

                if (statistics_request_active || statistics_busy ||
                        !statistics_done) begin
                    error_code <= ERROR_STATISTICS_NOT_READY;
                    command_done <= 1'b1;
                end else if ((command_data >= STATISTICS_SAMPLES) ||
                        (command_index > 2)) begin
                    error_code <= ERROR_STATISTICS_ADDRESS;
                    command_done <= 1'b1;
                end else begin
                    statistics_memory_read_address <= command_data[6:0];
                    statistics_memory_read_enable <= 1'b1;
                    statistics_read_part <= command_index[1:0];
                    statistics_read_pending <= 1'b1;
                    statistics_read_wait <= 1'b0;
                end
            end else if (command_valid) begin
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
                                configuration_toggle_source <=
                                    ~configuration_toggle_source;
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
                        if (statistics_request_active || statistics_busy) begin
                            error_code <= ERROR_STATISTICS_BUSY;
                        end else if ((command_data < MIN_DECIMATION) ||
                                (command_data > MAX_DECIMATION)) begin
                            error_code <= ERROR_DECIMATION_RANGE;
                        end else begin
                            decimation_length <= command_data;
                        end
                    end

                    OP_SET_CONTROL_SAMPLES: begin
                        if (driver_busy) begin
                            error_code <= ERROR_DRIVER_BUSY;
                        end else if (command_data < 16'(MIN_CONTROL_SAMPLES)) begin
                            error_code <= ERROR_CONTROL_SAMPLE_COUNT;
                        end else begin
                            control_sample_count <= command_data;
                        end
                    end

                    OP_SET_DSM_OVERRIDE: begin
                        unique case (command_index)
                            6'd0: begin
                                dsm_override_shadow[15:0] <= command_data;
                            end

                            6'd1: begin
                                if (command_data[15:13] != 3'b000) begin
                                    error_code <= ERROR_DSM_OVERRIDE_VALUE;
                                end else begin
                                    dsm_override_shadow[
                                        DSM_OVERRIDE_WIDTH-1:16
                                    ] <= command_data[12:0];
                                end
                            end

                            6'd2: begin
                                if (command_data[15:1] != 15'b0) begin
                                    error_code <= ERROR_DSM_OVERRIDE_VALUE;
                                end else begin
                                    dsm_override_value_source <=
                                        dsm_override_shadow;
                                    dsm_override_enable_source <=
                                        command_data[0];
                                    dsm_override_toggle_source <=
                                        ~dsm_override_toggle_source;
                                end
                            end

                            default: begin
                                error_code <= ERROR_DSM_OVERRIDE_VALUE;
                            end
                        endcase
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

                    OP_STATISTICS_START: begin
                        if (statistics_request_active || statistics_busy) begin
                            error_code <= ERROR_STATISTICS_BUSY;
                        end else begin
                            statistics_start_toggle_baseline <=
                                statistics_completion_toggle;
                            statistics_request_active <= 1'b1;
                            statistics_start_source_pulse <= 1'b1;
                        end
                    end

                    OP_READ_REGISTER: begin
                        command_response <= read_response_mux;
                    end

                    OP_LATCH_LIVE_SAMPLE: begin
                        live_snapshot_mu1 <= mu1;
                        live_snapshot_mu2 <= mu2;
                        live_snapshot_onchip_mu1 <= onchip_mu1;
                        live_snapshot_onchip_mu2 <= onchip_mu2;
                        live_snapshot_sequence <= sample_sequence;
                        live_snapshot_decimation_length <=
                            active_decimation_length;
                        live_snapshot_onchip_sequence <=
                            onchip_sample_sequence;
                    end

                    OP_LATCH_CALIBRATION_SAMPLE: begin
                        calibration_snapshot_poly_freq <= calibration_poly_freq;
                        calibration_snapshot_poly_system <=
                            calibration_poly_system;
                        calibration_snapshot_sar_correction <=
                            calibration_sar_correction;
                        calibration_snapshot_dsm_input <= calibration_dsm_input;
                        calibration_snapshot_override_enabled <=
                            calibration_override_enabled;
                        calibration_snapshot_valid <= calibration_sample_valid;
                        calibration_snapshot_sequence <=
                            calibration_sample_sequence;
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
        .DEFAULT_SAMPLE_COUNT(DEFAULT_DAQ_SAMPLES),
        .MIN_SAMPLE_COUNT(MIN_CONTROL_SAMPLES),
        .START_ASSERT_SAMPLES(START_ASSERT_SAMPLES)
    ) control_driver (
        .clk(clk_50mhz),
        .rst(rst),
        .start(driver_start_pulse),
        .serial_bits(active_control_word),
        .chip_number(selected_chip),
        .broadcast(broadcast),
        .sample_count(control_sample_count),
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
        .serial_bits(active_control_word_2mhz),
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

    assign digital_reset_async = rst | chip_reset;

    clock_generator_2mhz dsm_clock (
        .clk_50mhz(clk_50mhz),
        .rst(rst),
        .clk_2mhz(clk_3p3)
    );

    reset_synchronizer core_2mhz_reset (
        .clk(clk_3p3),
        .async_reset(rst),
        .sync_reset(rst_2mhz)
    );

    // The chip RESET pulse generated by the 474-bit control transaction is
    // also the run reset for the on-chip digital algorithms.  Keep the host
    // command path and synchronized control-word payload on the board reset
    // above so the just-committed coefficients survive this pulse.
    reset_synchronizer digital_core_2mhz_reset (
        .clk(clk_3p3),
        .async_reset(digital_reset_async),
        .sync_reset(digital_rst_2mhz)
    );

    reset_synchronizer digital_sample_reset (
        .clk(fs_data_out33),
        .async_reset(digital_reset_async),
        .sync_reset(digital_rst_sample)
    );

    pulse_toggle_cdc sar_start_cdc (
        .source_clk(clk_50mhz),
        .source_rst(rst),
        .source_pulse(sar_start_source_pulse),
        .destination_clk(clk_3p3),
        .destination_rst(rst_2mhz),
        .destination_pulse(sar_start_2mhz_pulse)
    );

    pulse_toggle_cdc sar_compare_cdc (
        .source_clk(clk_50mhz),
        .source_rst(rst),
        .source_pulse(sar_compare_source_pulse),
        .destination_clk(clk_3p3),
        .destination_rst(rst_2mhz),
        .destination_pulse(sar_compare_2mhz_pulse)
    );

    pulse_toggle_cdc statistics_start_cdc (
        .source_clk(clk_50mhz),
        .source_rst(rst),
        .source_pulse(statistics_start_source_pulse),
        .destination_clk(clk_3p3),
        .destination_rst(rst_2mhz),
        .destination_pulse(statistics_start_2mhz_pulse)
    );

    always_ff @(posedge clk_3p3 or posedge rst_2mhz) begin
        if (rst_2mhz) begin
            sar_enable_meta <= 1'b0;
            sar_enable_2mhz <= 1'b0;
            sar_bit_meta <= 1'b0;
            sar_bit_2mhz <= 1'b0;
            control_word_meta_2mhz <= '0;
            control_word_sync_2mhz <= '0;
            active_control_word_2mhz <= '0;
            configuration_toggle_meta <= 1'b0;
            configuration_toggle_sync <= 1'b0;
            configuration_toggle_delay <= 1'b0;
            configuration_toggle_capture_delay <= 1'b0;
            dsm_override_enable_meta <= 1'b0;
            dsm_override_enable_sync <= 1'b0;
            dsm_override_enable_2mhz <= 1'b0;
            dsm_override_value_meta <= '0;
            dsm_override_value_sync <= '0;
            dsm_override_value_2mhz <= '0;
            dsm_override_toggle_meta <= 1'b0;
            dsm_override_toggle_sync <= 1'b0;
            dsm_override_toggle_delay <= 1'b0;
            dsm_override_toggle_capture_delay <= 1'b0;
            dsm_override_applied_2mhz_pulse <= 1'b0;
        end else begin
            dsm_override_applied_2mhz_pulse <= 1'b0;
            sar_enable_meta <= sar_enable_source;
            sar_enable_2mhz <= sar_enable_meta;
            sar_bit_meta <= sar_bit_source;
            sar_bit_2mhz <= sar_bit_meta;
            control_word_meta_2mhz <= active_control_word;
            control_word_sync_2mhz <= control_word_meta_2mhz;
            configuration_toggle_meta <= configuration_toggle_source;
            configuration_toggle_sync <= configuration_toggle_meta;
            configuration_toggle_delay <= configuration_toggle_sync;
            configuration_toggle_capture_delay <=
                configuration_toggle_delay;
            dsm_override_enable_meta <= dsm_override_enable_source;
            dsm_override_enable_sync <= dsm_override_enable_meta;
            dsm_override_value_meta <= dsm_override_value_source;
            dsm_override_value_sync <= dsm_override_value_meta;
            dsm_override_toggle_meta <= dsm_override_toggle_source;
            dsm_override_toggle_sync <= dsm_override_toggle_meta;
            dsm_override_toggle_delay <= dsm_override_toggle_sync;
            dsm_override_toggle_capture_delay <=
                dsm_override_toggle_delay;

            // The complete 474-bit payload has passed through two 2 MHz
            // sampling stages before the synchronized event is observed.
            // Capture all fields together so Polynomial/Batch/ALPHA/polarity
            // never see a torn control-word update.
            if (configuration_toggle_delay !=
                    configuration_toggle_capture_delay) begin
                active_control_word_2mhz <= control_word_sync_2mhz;
            end

            if (dsm_override_toggle_delay !=
                    dsm_override_toggle_capture_delay) begin
                dsm_override_enable_2mhz <= dsm_override_enable_sync;
                dsm_override_value_2mhz <= dsm_override_value_sync;
                dsm_override_applied_2mhz_pulse <= 1'b1;
            end
        end
    end

    triangular_decimator_runtime #(
        .OUTPUT_WIDTH(18),
        .DEFAULT_LENGTH(DEFAULT_DECIMATION),
        .MIN_LENGTH(MIN_DECIMATION),
        .MAX_LENGTH(MAX_DECIMATION)
    ) ui_decimator (
        .rst_sample(digital_rst_sample),
        .clk_sample(fs_data_out33),
        .rst_data(digital_rst_2mhz),
        .clk_data(clk_3p3),
        .bs_t(bs_t_out33),
        .bs_c(bs_c_out33),
        .configured_length_async(decimation_length),
        .mu1(mu1_2mhz),
        .mu2(mu2_2mhz),
        .valid(mu_valid_2mhz),
        .active_length(active_decimation_2mhz)
    );

    sample_mailbox_cdc #(
        .DATA_WIDTH(18)
    ) ui_sample_mailbox (
        .source_clk(clk_3p3),
        .source_rst(rst_2mhz),
        .source_mu1(mu1_2mhz),
        .source_mu2(mu2_2mhz),
        .source_valid(mu_valid_2mhz),
        .destination_clk(clk_50mhz),
        .destination_rst(rst),
        .destination_mu1(mu1),
        .destination_mu2(mu2),
        .sample_sequence(sample_sequence),
        .sample_toggle(sample_toggle)
    );

    sample_mailbox_cdc #(
        .DATA_WIDTH(18)
    ) onchip_sample_mailbox (
        .source_clk(clk_3p3),
        .source_rst(rst_2mhz),
        .source_mu1(onchip_mu1_2mhz),
        .source_mu2(onchip_mu2_2mhz),
        .source_valid(onchip_mu_valid_2mhz),
        .destination_clk(clk_50mhz),
        .destination_rst(rst),
        .destination_mu1(onchip_mu1),
        .destination_mu2(onchip_mu2),
        .sample_sequence(onchip_sample_sequence),
        .sample_toggle(onchip_sample_toggle)
    );

    calibration_mailbox_cdc #(
        .DATA_WIDTH(29)
    ) calibration_sample_mailbox (
        .source_clk(clk_3p3),
        .source_rst(rst_2mhz),
        .source_poly_freq(calibration_poly_freq_2mhz),
        .source_poly_system(calibration_poly_system_2mhz),
        .source_sar_correction(calibration_sar_correction_2mhz),
        .source_dsm_input(calibration_dsm_input_2mhz),
        .source_override_enabled(dsm_override_enable_2mhz),
        .source_valid(
            calibration_algorithm_valid_2mhz |
            dsm_override_applied_2mhz_pulse
        ),
        .destination_clk(clk_50mhz),
        .destination_rst(rst),
        .destination_poly_freq(calibration_poly_freq),
        .destination_poly_system(calibration_poly_system),
        .destination_sar_correction(calibration_sar_correction),
        .destination_dsm_input(calibration_dsm_input),
        .destination_override_enabled(calibration_override_enabled),
        .destination_valid(calibration_sample_valid),
        .sample_sequence(calibration_sample_sequence)
    );

    statistics_capture_buffer #(
        .DATA_WIDTH(18),
        .SAMPLE_COUNT(STATISTICS_SAMPLES),
        .MEMORY_DEPTH(128),
        .ADDRESS_WIDTH(7)
    ) statistics_buffer (
        .source_clk(clk_3p3),
        .source_rst(rst_2mhz),
        .source_start(statistics_start_2mhz_pulse),
        .source_sample_valid(mu_valid_2mhz),
        .source_mu1(mu1_2mhz),
        .source_mu2(mu2_2mhz),
        .source_busy(statistics_busy_2mhz),
        .source_done(statistics_done_2mhz),
        .source_completion_toggle(statistics_completion_toggle_2mhz),
        .source_captured_count(statistics_captured_count_2mhz),
        .destination_clk(clk_50mhz),
        .destination_read_enable(statistics_memory_read_enable),
        .destination_read_address(statistics_memory_read_address),
        .destination_read_data(statistics_memory_read_data)
    );

    DigOsc on_chip_calibration (
        .rst_DATA(digital_rst_2mhz),
        .rst_SAMPLE(digital_rst_sample),
        .clk_DATA(clk_3p3),
        .clk_SAMPLE(fs_data_out33),
        .clk_DSM(clk_3p3),
        .BS0(bs_t_out33),
        .BS1(bs_c_out33),
        .polarity(polarity),
        .SAR_enable(sar_enable_2mhz),
        .SAR_start(sar_start_2mhz_pulse),
        .SAR_compare_valid(sar_compare_2mhz_pulse),
        .SAR_bit_in(sar_bit_2mhz),
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
        .dsm_override_enable(dsm_override_enable_2mhz),
        .dsm_override_value(dsm_override_value_2mhz),
        .DSM(dsm_3p3),
        .div_out_test(div_out_unused),
        .poly_system_result_test(poly_system_unused),
        .poly_freq_result_test(poly_freq_unused),
        .phase_diff_test(phase_diff_unused),
        .phase_diff_valid_out(phase_diff_valid_unused),
        .difference_test(difference_unused),
        .calibrated_error_test(calibrated_error_unused),
        .SAR_code(sar_code_2mhz),
        .SAR_trial_valid(sar_trial_valid_2mhz),
        .SAR_compare_ready(sar_compare_ready_2mhz),
        .SAR_busy(sar_busy_2mhz),
        .SAR_done(sar_done_2mhz),
        .SAR_step_index(sar_step_index_2mhz),
        .UP(up_unused),
        .data(data_unused),
        .onchip_mu1(onchip_mu1_2mhz),
        .onchip_mu2(onchip_mu2_2mhz),
        .onchip_mu_valid(onchip_mu_valid_2mhz),
        .calibration_poly_freq(calibration_poly_freq_2mhz),
        .calibration_poly_system(calibration_poly_system_2mhz),
        .calibration_sar_correction(calibration_sar_correction_2mhz),
        .calibration_dsm_input(calibration_dsm_input_2mhz),
        .calibration_sample_valid(calibration_algorithm_valid_2mhz)
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
            statistics_busy_meta <= 1'b0;
            statistics_busy <= 1'b0;
            statistics_done_meta <= 1'b0;
            statistics_done <= 1'b0;
            statistics_completion_toggle_meta <= 1'b0;
            statistics_completion_toggle <= 1'b0;
            statistics_captured_count_meta <= '0;
            statistics_captured_count <= '0;
            active_decimation_meta <= 16'(DEFAULT_DECIMATION);
            active_decimation_length <= 16'(DEFAULT_DECIMATION);
        end else begin
            sar_busy_meta <= sar_busy_2mhz;
            sar_busy <= sar_busy_meta;
            sar_done_meta <= sar_done_2mhz;
            sar_done <= sar_done_meta;
            sar_step_meta <= sar_step_index_2mhz;
            sar_step_index <= sar_step_meta;
            sar_code_meta <= sar_code_2mhz;
            sar_code <= sar_code_meta;
            statistics_busy_meta <= statistics_busy_2mhz;
            statistics_busy <= statistics_busy_meta;
            statistics_done_meta <= statistics_done_2mhz;
            statistics_done <= statistics_done_meta;
            statistics_completion_toggle_meta <=
                statistics_completion_toggle_2mhz;
            statistics_completion_toggle <=
                statistics_completion_toggle_meta;
            statistics_captured_count_meta <=
                statistics_captured_count_2mhz;
            statistics_captured_count <=
                statistics_captured_count_meta;
            active_decimation_meta <= active_decimation_2mhz;
            active_decimation_length <= active_decimation_meta;
        end
    end

endmodule
