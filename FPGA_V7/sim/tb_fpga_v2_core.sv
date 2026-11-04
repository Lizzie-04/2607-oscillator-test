`timescale 1ns/1ps

module tb_fpga_v2_core;

    import fpga_v2_protocol_pkg::*;

    localparam int HALF_PERIOD_CYCLES = 1;
    localparam int CONTROL_SAMPLE_COUNT = 980;
    localparam logic [28:0] DSM_OVERRIDE_TEST = 29'h1ABCDE;
    localparam logic [28:0] SNAPSHOT_FREQ_TEST = 29'h1F12345;
    localparam logic [28:0] SNAPSHOT_SYSTEM_TEST = 29'h0123456;
    localparam logic [28:0] SNAPSHOT_SAR_TEST = 29'h1FFF000;
    localparam logic [28:0] SNAPSHOT_DSM_TEST = 29'h000C000;

    logic clk_50mhz = 1'b0;
    logic rst = 1'b1;
    logic fs_data_out33 = 1'b0;
    logic fs_running = 1'b1;
    logic bs_t_out33 = 1'b1;
    logic bs_c_out33 = 1'b0;

    logic command_valid = 1'b0;
    logic [3:0] command_opcode = '0;
    logic [5:0] command_index = '0;
    logic [15:0] command_data = '0;
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

    logic [479:0] padded_control_word;
    int sr_rising_edges;
    int live_snapshot_sequence_test;
    int live_snapshot_onchip_sequence_test;
    int calibration_snapshot_sequence_test;

    always #5 clk_50mhz = ~clk_50mhz;
    always #1000 begin
        if (fs_running) begin
            fs_data_out33 = ~fs_data_out33;
        end else begin
            fs_data_out33 = 1'b0;
        end
    end

    fpga_v2_core #(
        .HALF_PERIOD_CYCLES(HALF_PERIOD_CYCLES),
        .DEFAULT_DAQ_SAMPLES(CONTROL_SAMPLE_COUNT),
        .DEFAULT_DECIMATION(32),
        .MIN_DECIMATION(32),
        .MAX_DECIMATION(4096)
    ) dut (
        .clk_50mhz(clk_50mhz),
        .rst(rst),
        .fs_data_out33(fs_data_out33),
        .bs_t_out33(bs_t_out33),
        .bs_c_out33(bs_c_out33),
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
        .statistics_captured_count(statistics_captured_count)
    );

    always @(posedge sr_clk) begin
        sr_rising_edges++;
    end

    task automatic send_command(
        input logic [3:0] opcode,
        input logic [5:0] index,
        input logic [15:0] data
    );
        begin
            @(negedge clk_50mhz);
            command_opcode = opcode;
            command_index = index;
            command_data = data;
            command_valid = 1'b1;
            @(negedge clk_50mhz);
            command_valid = 1'b0;
            wait (command_done);
            @(negedge clk_50mhz);
        end
    endtask

    initial begin
        int i;
        sr_rising_edges = 0;
        padded_control_word = '0;

        for (i = 0; i < 474; i++) begin
            padded_control_word[i] = (i % 7) == 0;
        end

        repeat (5) @(posedge clk_50mhz);
        rst = 1'b0;
        repeat (5) @(posedge clk_50mhz);

        send_command(OP_SET_DECIMATION, 0, 16'd32);
        send_command(OP_SET_CONTROL_SAMPLES, 0, 16'(CONTROL_SAMPLE_COUNT));
        send_command(OP_READ_REGISTER, 17, 0);
        if (command_response != 16'(CONTROL_SAMPLE_COUNT)) begin
            $fatal(
                1,
                "control sample count register mismatch: %0d",
                command_response
            );
        end

        send_command(
            OP_SET_DSM_OVERRIDE,
            0,
            DSM_OVERRIDE_TEST[15:0]
        );
        send_command(
            OP_SET_DSM_OVERRIDE,
            1,
            {3'b000, DSM_OVERRIDE_TEST[28:16]}
        );
        send_command(OP_SET_DSM_OVERRIDE, 2, 16'h0001);
        send_command(OP_READ_REGISTER, 18, 0);
        if (command_response != 16'h0001) begin
            $fatal(1, "DSM override enable register mismatch");
        end
        send_command(OP_READ_REGISTER, 19, 0);
        if (command_response != DSM_OVERRIDE_TEST[15:0]) begin
            $fatal(1, "DSM override low word mismatch");
        end
        send_command(OP_READ_REGISTER, 20, 0);
        if (command_response != {3'b000, DSM_OVERRIDE_TEST[28:16]}) begin
            $fatal(1, "DSM override high word mismatch");
        end

        wait (dut.calibration_sample_sequence >= 1);
        send_command(OP_LATCH_CALIBRATION_SAMPLE, 0, 0);
        send_command(OP_READ_REGISTER, 38, 0);
        if (command_response != DSM_OVERRIDE_TEST[15:0]) begin
            $fatal(1, "calibration snapshot DSM input low mismatch");
        end
        send_command(OP_READ_REGISTER, 39, 0);
        if (command_response != {3'b000, DSM_OVERRIDE_TEST[28:16]}) begin
            $fatal(1, "calibration snapshot DSM input high mismatch");
        end
        send_command(OP_READ_REGISTER, 40, 0);
        calibration_snapshot_sequence_test = command_response;
        if (calibration_snapshot_sequence_test == 0) begin
            $fatal(1, "calibration snapshot sequence was zero");
        end
        send_command(OP_READ_REGISTER, 41, 0);
        if (command_response[1:0] != 2'b11) begin
            $fatal(1, "calibration snapshot valid/override flags mismatch");
        end

        send_command(OP_SET_DSM_OVERRIDE, 2, 16'h0000);
        wait (
            dut.calibration_sample_sequence >
            calibration_snapshot_sequence_test
        );
        send_command(OP_LATCH_CALIBRATION_SAMPLE, 0, 0);
        send_command(OP_READ_REGISTER, 41, 0);
        if (command_response[1:0] != 2'b10) begin
            $fatal(1, "calibration snapshot did not record restored loop");
        end

        // Freeze a coherent destination-domain mailbox image to verify all
        // four signed 29-bit register pairs and one-shot latch atomicity.
        force dut.calibration_poly_freq = SNAPSHOT_FREQ_TEST;
        force dut.calibration_poly_system = SNAPSHOT_SYSTEM_TEST;
        force dut.calibration_sar_correction = SNAPSHOT_SAR_TEST;
        force dut.calibration_dsm_input = SNAPSHOT_DSM_TEST;
        force dut.calibration_sample_sequence = 16'h1234;
        force dut.calibration_sample_valid = 1'b1;
        force dut.calibration_override_enabled = 1'b0;
        send_command(OP_LATCH_CALIBRATION_SAMPLE, 0, 0);
        release dut.calibration_poly_freq;
        release dut.calibration_poly_system;
        release dut.calibration_sar_correction;
        release dut.calibration_dsm_input;
        release dut.calibration_sample_sequence;
        release dut.calibration_sample_valid;
        release dut.calibration_override_enabled;

        send_command(OP_READ_REGISTER, 32, 0);
        if (command_response != SNAPSHOT_FREQ_TEST[15:0]) begin
            $fatal(1, "calibration poly_freq low register mismatch");
        end
        send_command(OP_READ_REGISTER, 33, 0);
        if (command_response != {3'b000, SNAPSHOT_FREQ_TEST[28:16]}) begin
            $fatal(1, "calibration poly_freq high register mismatch");
        end
        send_command(OP_READ_REGISTER, 34, 0);
        if (command_response != SNAPSHOT_SYSTEM_TEST[15:0]) begin
            $fatal(1, "calibration poly_system low register mismatch");
        end
        send_command(OP_READ_REGISTER, 35, 0);
        if (command_response != {3'b000, SNAPSHOT_SYSTEM_TEST[28:16]}) begin
            $fatal(1, "calibration poly_system high register mismatch");
        end
        send_command(OP_READ_REGISTER, 36, 0);
        if (command_response != SNAPSHOT_SAR_TEST[15:0]) begin
            $fatal(1, "calibration SAR low register mismatch");
        end
        send_command(OP_READ_REGISTER, 37, 0);
        if (command_response != {3'b000, SNAPSHOT_SAR_TEST[28:16]}) begin
            $fatal(1, "calibration SAR high register mismatch");
        end
        send_command(OP_READ_REGISTER, 38, 0);
        if (command_response != SNAPSHOT_DSM_TEST[15:0]) begin
            $fatal(1, "calibration DSM low register mismatch");
        end
        send_command(OP_READ_REGISTER, 39, 0);
        if (command_response != {3'b000, SNAPSHOT_DSM_TEST[28:16]}) begin
            $fatal(1, "calibration DSM high register mismatch");
        end
        send_command(OP_READ_REGISTER, 40, 0);
        if (command_response != 16'h1234) begin
            $fatal(1, "calibration sequence register mismatch");
        end
        send_command(OP_READ_REGISTER, 41, 0);
        if (command_response[1:0] != 2'b10) begin
            $fatal(1, "calibration flags register mismatch");
        end
        send_command(OP_READ_REGISTER, 18, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "DSM override did not disable");
        end

        for (i = 0; i < 30; i++) begin
            send_command(
                OP_WRITE_CONTROL_WORD,
                i,
                padded_control_word[i*16 +: 16]
            );
        end

        if (!control_words_complete) begin
            $fatal(1, "control-word completeness bitmap did not fill");
        end

        send_command(OP_COMMIT_AND_START, 0, 16'h0002);
        if (control_words_complete) begin
            $fatal(
                1,
                "successful commit left stale control-word validity bits"
            );
        end
        wait (driver_busy);
        wait (driver_done);
        @(negedge clk_50mhz);

        if (sr_rising_edges != 474 || address != 3'd2) begin
            $fatal(
                1,
                "integrated control driver failed: edges=%0d address=%0d",
                sr_rising_edges,
                address
            );
        end
        if (!start_dll || !start_ana) begin
            $fatal(1, "START outputs did not remain high after first commit");
        end

        // A later batch must be complete on its own; it cannot inherit the
        // validity bitmap from the previous successful configuration.
        for (i = 0; i < 29; i++) begin
            send_command(
                OP_WRITE_CONTROL_WORD,
                i,
                ~padded_control_word[i*16 +: 16]
            );
        end
        if (control_words_complete) begin
            $fatal(1, "partial second control word appeared complete");
        end
        send_command(OP_COMMIT_AND_START, 0, 16'h0001);
        if (error_code != ERROR_CONTROL_INCOMPLETE || driver_busy) begin
            $fatal(
                1,
                "incomplete control word was not rejected atomically"
            );
        end

        // Even after all valid blocks arrive, a sticky transfer error must
        // inhibit COMMIT until the host explicitly clears it.
        send_command(OP_CLEAR_ERROR, 0, 16'h0000);
        send_command(OP_WRITE_CONTROL_WORD, 31, 16'hDEAD);
        send_command(
            OP_WRITE_CONTROL_WORD,
            29,
            ~padded_control_word[29*16 +: 16]
        );
        if (!control_words_complete) begin
            $fatal(1, "complete retry did not fill validity bitmap");
        end
        send_command(OP_COMMIT_AND_START, 0, 16'h0001);
        if (error_code != ERROR_CONTROL_INDEX || driver_busy) begin
            $fatal(1, "sticky transfer error did not inhibit COMMIT");
        end

        send_command(OP_CLEAR_ERROR, 0, 16'h0000);
        send_command(OP_COMMIT_AND_START, 0, 16'h0001);
        wait (driver_busy);
        wait (driver_done);
        @(negedge clk_50mhz);
        if (sr_rising_edges != 948 || address != 3'd1) begin
            $fatal(
                1,
                "second complete control word failed: edges=%0d address=%0d",
                sr_rising_edges,
                address
            );
        end

        wait (sample_sequence >= 2);
        if (mu1 != 18'd65_536 || mu2 != 0) begin
            $fatal(1, "continuous mu readout failed: mu1=%0d mu2=%0d", mu1, mu2);
        end

        wait (dut.onchip_sample_sequence >= 1);
        send_command(OP_LATCH_LIVE_SAMPLE, 0, 0);
        send_command(OP_READ_REGISTER, 21, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "live snapshot mu1 low mismatch");
        end
        send_command(OP_READ_REGISTER, 22, 0);
        if (command_response != 16'h0001) begin
            $fatal(1, "live snapshot mu1 high mismatch");
        end
        send_command(OP_READ_REGISTER, 23, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "live snapshot mu2 low mismatch");
        end
        send_command(OP_READ_REGISTER, 24, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "live snapshot mu2 high mismatch");
        end
        send_command(OP_READ_REGISTER, 25, 0);
        // The fixed on-chip-compatible filter deliberately slices
        // accumulator[20:4], so an all-one window is 0x0FFFF rather than the
        // separately implemented UI decimator's saturated 0x10000.
        if (command_response != 16'hffff) begin
            $fatal(
                1,
                "live snapshot on-chip mu1 low mismatch: %h",
                command_response
            );
        end
        send_command(OP_READ_REGISTER, 26, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "live snapshot on-chip mu1 high mismatch");
        end
        send_command(OP_READ_REGISTER, 27, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "live snapshot on-chip mu2 low mismatch");
        end
        send_command(OP_READ_REGISTER, 28, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "live snapshot on-chip mu2 high mismatch");
        end
        send_command(OP_READ_REGISTER, 29, 0);
        live_snapshot_sequence_test = command_response;
        if (live_snapshot_sequence_test == 0) begin
            $fatal(1, "live snapshot sequence was zero");
        end
        send_command(OP_READ_REGISTER, 30, 0);
        if (command_response != active_decimation_length) begin
            $fatal(1, "live snapshot Filter length mismatch");
        end
        send_command(OP_READ_REGISTER, 31, 0);
        live_snapshot_onchip_sequence_test = command_response;
        if (live_snapshot_onchip_sequence_test == 0) begin
            $fatal(1, "live snapshot on-chip sequence was zero");
        end
        wait (sample_sequence > live_snapshot_sequence_test);
        send_command(OP_READ_REGISTER, 29, 0);
        if (command_response != 16'(live_snapshot_sequence_test)) begin
            $fatal(1, "live snapshot changed without a new latch");
        end

        send_command(OP_READ_REGISTER, 14, 0);
        if (command_response != PROTOCOL_VERSION) begin
            $fatal(
                1,
                "V4 protocol version mismatch: %h",
                command_response
            );
        end
        send_command(OP_READ_REGISTER, 16, 0);
        if (command_response != IMPLEMENTATION_REVISION) begin
            $fatal(
                1,
                "V4 implementation revision mismatch: %h",
                command_response
            );
        end

        send_command(OP_STATISTICS_START, 0, 0);
        wait (statistics_busy);
        send_command(OP_SET_DECIMATION, 0, 16'd64);
        if (error_code != ERROR_STATISTICS_BUSY ||
                decimation_length != 32) begin
            $fatal(1, "Filter changed during statistics capture");
        end
        send_command(OP_CLEAR_ERROR, 0, 0);
        wait (statistics_done);
        wait (statistics_captured_count == STATISTICS_SAMPLES);
        if (statistics_busy || statistics_completion_toggle != 1'b1) begin
            $fatal(1, "integrated statistics capture did not complete");
        end

        send_command(OP_STATISTICS_READ, 0, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "statistics mu1 low word mismatch");
        end
        send_command(OP_STATISTICS_READ, 1, 0);
        if (command_response != 16'h0001) begin
            $fatal(1, "statistics packed middle word mismatch");
        end
        send_command(OP_STATISTICS_READ, 2, 0);
        if (command_response != 16'h0000) begin
            $fatal(1, "statistics packed high word mismatch");
        end

        send_command(OP_STATISTICS_READ, 0, 100);
        if (error_code != ERROR_STATISTICS_ADDRESS) begin
            $fatal(1, "statistics invalid address was not rejected");
        end
        send_command(OP_CLEAR_ERROR, 0, 0);

        // A second batch must use the same Filter setting and must flip the
        // completion bit again, which is how the host distinguishes batches.
        wait (!statistics_busy);
        repeat (8) @(posedge clk_50mhz);
        send_command(OP_STATISTICS_START, 0, 0);
        wait (statistics_busy);
        wait (!statistics_done);
        if (decimation_length != 32) begin
            $fatal(1, "Filter was not locked across statistics batches");
        end
        wait (statistics_done);
        wait (statistics_captured_count == STATISTICS_SAMPLES);
        if (statistics_busy || statistics_completion_toggle != 1'b0) begin
            $fatal(1, "second statistics batch did not toggle completion");
        end

        // SAR and the digital core use the FPGA-generated 2 MHz clock.
        // Stopping FS must pause bitstream sampling without blocking SAR.
        fs_running = 1'b0;
        repeat (8) @(posedge clk_3p3);
        send_command(OP_SAR_START, 0, 16'h0001);
        wait (sar_busy);
        if (sar_code != 0) begin
            $fatal(
                1,
                "SAR did not start from centered code zero while FS was stopped: %0d",
                sar_code
            );
        end

        for (i = 0; i < 16; i++) begin
            send_command(OP_SAR_COMPARE, 0, 16'h0000);
            if (i < 15) begin
                wait (sar_step_index >= i + 1);
            end
        end

        wait (sar_done);
        if (sar_code != -29'sd32_768 || sar_step_index != 16) begin
            $fatal(
                1,
                "integrated single-bit SAR failed: code=%0d step=%0d",
                sar_code,
                sar_step_index
            );
        end
        send_command(OP_READ_REGISTER, 6, 0);
        if (command_response != 16'h8000) begin
            $fatal(1, "signed SAR low register mismatch");
        end
        send_command(OP_READ_REGISTER, 7, 0);
        if (command_response != 16'h1fff) begin
            $fatal(1, "signed SAR high register mismatch");
        end

        for (i = 0; i < 30; i++) begin
            send_command(
                OP_WRITE_CONTROL_WORD,
                i,
                padded_control_word[i*16 +: 16]
            );
        end
        send_command(OP_COMMIT_AND_START, 0, 16'h0001);
        wait (driver_busy);
        wait (chip_reset);
        if (!start_dll || !start_ana) begin
            $fatal(1, "chip reset no longer coincides with START high");
        end
        wait (!chip_reset);
        repeat (4) @(posedge clk_3p3);
        repeat (4) @(posedge clk_50mhz);
        if (sar_busy || sar_done || sar_code != 0) begin
            $fatal(
                1,
                "chip reset did not reset 2 MHz digital SAR state: busy=%b done=%b code=%0d",
                sar_busy,
                sar_done,
                sar_code
            );
        end
        if (dut.active_control_word_2mhz !== dut.active_control_word) begin
            $fatal(1, "chip reset cleared the synchronized control word");
        end
        wait (driver_done);

        send_command(OP_READ_REGISTER, 5, 0);
        if (command_response == 0) begin
            $fatal(1, "sample sequence readback was zero");
        end

        if (error_code != ERROR_NONE) begin
            $fatal(1, "unexpected core error: 0x%02x", error_code);
        end

        $display(
            "TEST PASS: UI command core, control driver, continuous mu and SAR"
        );
        $finish;
    end

endmodule
