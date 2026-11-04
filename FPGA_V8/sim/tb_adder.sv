`timescale 1ns/1ps

module tb_adder;
    localparam int DATA_WIDTH = 29;
    localparam signed [DATA_WIDTH-1:0] MAX_VALUE =
        {1'b0, {(DATA_WIDTH-1){1'b1}}};
    localparam signed [DATA_WIDTH-1:0] MIN_VALUE =
        {1'b1, {(DATA_WIDTH-1){1'b0}}};

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic signed [DATA_WIDTH-1:0] A = '0;
    logic signed [DATA_WIDTH-1:0] B = '0;
    logic signed [DATA_WIDTH-1:0] calibration = '0;
    logic valid_in = 1'b0;
    logic polar = 1'b0;
    logic signed [DATA_WIDTH-1:0] D;
    logic signed [DATA_WIDTH-1:0] F;
    logic signed [DATA_WIDTH-1:0] calibrated_error;
    logic valid_out;

    always #5 clk = ~clk;

    adder #(
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .rst(rst),
        .clk(clk),
        .A(A),
        .B(B),
        .calibration(calibration),
        .valid_in(valid_in),
        .polar(polar),
        .D(D),
        .F(F),
        .calibrated_error(calibrated_error),
        .valid_out(valid_out)
    );

    task automatic reset_adder;
        begin
            rst = 1'b1;
            valid_in = 1'b0;
            repeat (2) @(posedge clk);
            #1;
            rst = 1'b0;
        end
    endtask

    task automatic submit_and_check(
        input signed [DATA_WIDTH-1:0] input_a,
        input signed [DATA_WIDTH-1:0] input_b,
        input signed [DATA_WIDTH-1:0] input_calibration,
        input logic input_polar,
        input signed [DATA_WIDTH-1:0] expected_f,
        input signed [DATA_WIDTH-1:0] expected_error,
        input signed [DATA_WIDTH-1:0] expected_d
    );
        int latency;
        begin
            @(negedge clk);
            A = input_a;
            B = input_b;
            calibration = input_calibration;
            polar = input_polar;
            valid_in = 1'b1;

            @(posedge clk);
            #1;
            valid_in = 1'b0;
            latency = 0;
            while (!valid_out && latency < 4) begin
                @(posedge clk);
                #1;
                latency = latency + 1;
            end

            if (latency != 2) begin
                $fatal(1, "adder valid pipeline expected 2 cycles, actual=%0d", latency);
            end
            if ((F !== expected_f) ||
                    (calibrated_error !== expected_error) ||
                    (D !== expected_d)) begin
                $fatal(
                    1,
                    "adder mismatch F=%0d/%0d E=%0d/%0d D=%0d/%0d",
                    F,
                    expected_f,
                    calibrated_error,
                    expected_error,
                    D,
                    expected_d
                );
            end

            @(posedge clk);
            #1;
            if (valid_out) begin
                $fatal(1, "adder valid_out did not return low after one result");
            end
        end
    endtask

    initial begin
        reset_adder();
        submit_and_check(29'sd100, 29'sd40, 29'sd10, 1'b0,
                         29'sd60, 29'sd70, 29'sd70);
        submit_and_check(29'sd20, 29'sd5, 29'sd0, 1'b0,
                         29'sd15, 29'sd15, 29'sd85);
        submit_and_check(29'sd30, 29'sd10, 29'sd5, 1'b1,
                         29'sd20, 29'sd25, 29'sd60);

        repeat (4) @(posedge clk);
        #1;
        if (D !== 29'sd60) begin
            $fatal(1, "adder accumulator changed without valid input");
        end

        reset_adder();
        submit_and_check(MAX_VALUE, MIN_VALUE, MAX_VALUE, 1'b0,
                         MAX_VALUE, MAX_VALUE, MAX_VALUE);
        reset_adder();
        submit_and_check(MIN_VALUE, MAX_VALUE, MIN_VALUE, 1'b0,
                         MIN_VALUE, MIN_VALUE, MIN_VALUE);

        $display("TEST PASS: adder difference, calibrated integration and saturation");
        $finish;
    end

endmodule
