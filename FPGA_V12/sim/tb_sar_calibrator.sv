`timescale 1ns/1ps

module tb_sar_calibrator;
    localparam int DATA_WIDTH = 37;
    localparam int FRACTION_BITS = 16;
    localparam signed [DATA_WIDTH-1:0] NEGATIVE_HALF = -29'sd32768;
    localparam signed [DATA_WIDTH-1:0] POSITIVE_HALF = 29'sd32768;

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

    task automatic start_search;
        begin
            @(negedge clk);
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;
            #1;
            if (code !== 0 || !busy || done || !trial_valid ||
                    !compare_ready || step_index !== 0) begin
                $fatal(1, "SAR did not start at centered code zero");
            end
        end
    endtask

    task automatic decide(input logic decision);
        begin
            @(negedge clk);
            bit_in = decision;
            compare_valid = 1'b1;
            @(negedge clk);
            compare_valid = 1'b0;
            #1;
            if (code < NEGATIVE_HALF || code > POSITIVE_HALF) begin
                $fatal(1, "SAR code escaped [-0.5,+0.5]: %0d", code);
            end
        end
    endtask

    integer index;
    initial begin
        repeat (3) @(posedge clk);
        rst = 1'b0;

        start_search();
        decide(1'b1);
        if (code !== 29'sd16384 || step_index !== 1) begin
            $fatal(1, "first increase trial must be +0.25: %0d", code);
        end
        start_search();
        decide(1'b0);
        if (code !== -29'sd16384 || step_index !== 1) begin
            $fatal(1, "first decrease trial must be -0.25: %0d", code);
        end

        start_search();
        for (index = 0; index < FRACTION_BITS; index = index + 1) begin
            decide(1'b0);
        end
        if (code !== NEGATIVE_HALF || busy || !done || trial_valid ||
                compare_ready || step_index !== FRACTION_BITS) begin
            $fatal(1, "all-low decisions must finish at -0.5: %0d", code);
        end

        start_search();
        for (index = 0; index < FRACTION_BITS; index = index + 1) begin
            decide(1'b1);
        end
        if (code !== POSITIVE_HALF || busy || !done || trial_valid ||
                compare_ready || step_index !== FRACTION_BITS) begin
            $fatal(1, "all-high decisions must finish at +0.5: %0d", code);
        end

        // compare_valid outside an active search must not change the result.
        decide(1'b0);
        if (code !== POSITIVE_HALF || step_index !== FRACTION_BITS) begin
            $fatal(1, "completed SAR did not hold its result");
        end

        $display("TEST PASS: centered SAR range, endpoints and hold behavior");
        $finish;
    end

endmodule
