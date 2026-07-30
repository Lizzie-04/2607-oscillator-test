`timescale 1ns/1ps

module tb_fpga_v1_core;

    import fpga_v1_protocol_pkg::*;

    localparam int HALF_PERIOD_CYCLES = 1;
    localparam int TOTAL_CYCLES = 980;

    logic clk_50mhz = 1'b0;
    logic rst = 1'b1;
    logic fs_data_out33 = 1'b0;
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
    logic [7:0] error_code;
    logic control_words_complete;
    logic driver_busy;
    logic driver_done;
    logic sar_busy;
    logic sar_done;
    logic [7:0] sar_step_index;
    logic signed [28:0] sar_code;

    logic [479:0] padded_control_word;
    int sr_rising_edges;

    always #5 clk_50mhz = ~clk_50mhz;
    always #25 fs_data_out33 = ~fs_data_out33;

    fpga_v1_core #(
        .HALF_PERIOD_CYCLES(HALF_PERIOD_CYCLES),
        .TOTAL_CYCLES(TOTAL_CYCLES),
        .DEFAULT_DECIMATION(32)
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
        .error_code(error_code),
        .control_words_complete(control_words_complete),
        .driver_busy(driver_busy),
        .driver_done(driver_done),
        .sar_busy(sar_busy),
        .sar_done(sar_done),
        .sar_step_index(sar_step_index),
        .sar_code(sar_code)
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

        send_command(OP_SAR_START, 0, 16'h0001);
        wait (sar_busy);

        for (i = 0; i < 16; i++) begin
            send_command(OP_SAR_COMPARE, 0, 16'h0000);
            if (i < 15) begin
                wait (sar_step_index >= i + 1);
            end
        end

        wait (sar_done);
        if (sar_code != 0 || sar_step_index != 16) begin
            $fatal(
                1,
                "integrated single-bit SAR failed: code=%0d step=%0d",
                sar_code,
                sar_step_index
            );
        end

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
