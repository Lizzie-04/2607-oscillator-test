`timescale 1ns/1ps

module tb_sar_calibrator;

    localparam int DATA_WIDTH = 29;
    localparam int FRACTION_BITS = 16;
    localparam int FULL_SCALE = 1 << FRACTION_BITS;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic start = 1'b0;
    logic compare_valid = 1'b0;
    logic bit_in = 1'b0;

    logic signed [DATA_WIDTH-1:0] code;
    logic trial_valid;
    logic compare_ready;
    logic busy;
    logic done;
    logic [7:0] step_index;

    always #5 clk = ~clk;

    sar_calibrator #(
        .DATA_WIDTH(DATA_WIDTH),
        .FRACTION_BITS(FRACTION_BITS)
    ) dut (
        .rst(rst),
        .clk(clk),
        .start(start),
        .compare_valid(compare_valid),
        .bit_in(bit_in),
        .code(code),
        .trial_valid(trial_valid),
        .compare_ready(compare_ready),
        .busy(busy),
        .done(done),
        .step_index(step_index)
    );

    task automatic pulse_start;
        begin
            @(negedge clk);
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;

            if (!busy || !trial_valid || done ||
                    code != (FULL_SCALE / 2)) begin
                $fatal(
                    1,
                    "start failed: busy=%0b trial_valid=%0b code=%0d",
                    busy,
                    trial_valid,
                    code
                );
            end

            repeat (2) @(negedge clk);
            if (!busy || !trial_valid || done) begin
                $fatal(1, "trial status was not held for host polling");
            end
        end
    endtask

    task automatic send_comparison(input logic comparison_bit);
        begin
            @(negedge clk);
            if (!compare_ready) begin
                $fatal(1, "compare_ready was low while search was busy");
            end

            bit_in = comparison_bit;
            compare_valid = 1'b1;
            @(negedge clk);
            compare_valid = 1'b0;
            bit_in = 1'b0;
        end
    endtask

    task automatic run_search(input int target_code);
        int trial_code;
        int comparison_count;
        int result_code;
        begin
            pulse_start();
            comparison_count = 0;

            while (busy) begin
                trial_code = code;
                send_comparison(trial_code < target_code);
                comparison_count++;

                if (comparison_count > FRACTION_BITS) begin
                    $fatal(1, "SAR search did not terminate");
                end
            end

            if (comparison_count != FRACTION_BITS) begin
                $fatal(
                    1,
                    "expected one comparison per bit: count=%0d",
                    comparison_count
                );
            end

            if (!done || trial_valid) begin
                $fatal(
                    1,
                    "final status missing: done=%0b trial_valid=%0b",
                    done,
                    trial_valid
                );
            end

            repeat (3) @(negedge clk);
            if (!done || busy || trial_valid) begin
                $fatal(1, "final status was not held for host polling");
            end

            result_code = code;
            if ((result_code < target_code - 1) ||
                    (result_code > target_code + 1)) begin
                $fatal(
                    1,
                    "search result out of tolerance: target=%0d result=%0d",
                    target_code,
                    result_code
                );
            end
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);

        run_search(0);
        if (code != 0) begin
            $fatal(1, "lower boundary failed: result=%0d", code);
        end

        run_search(FULL_SCALE);
        if (code != FULL_SCALE) begin
            $fatal(1, "inclusive 1.0 boundary failed: result=%0d", code);
        end

        run_search(45875);
        if ((code < 45874) || (code > 45876)) begin
            $fatal(1, "single-bit search failed: result=%0d", code);
        end

        pulse_start();
        send_comparison(1'b1);
        pulse_start();
        if (!busy || done || !trial_valid ||
                code != (FULL_SCALE / 2) || step_index != 0) begin
            $fatal(1, "restart did not restore the initial trial");
        end

        $display(
            "TEST PASS: single-bit SAR, A/B format, range and restart"
        );
        $finish;
    end

endmodule
