`timescale 1ns/1ps

module tb_adder;
    localparam int DATA_WIDTH = 37;
    localparam signed [DATA_WIDTH-1:0] HALF_SCALE = 37'sd32768;
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
        submit_and_check(37'sd100, 37'sd40, 37'sd10, 1'b0,
                         37'sd60, 37'sd70, 37'sd70);
        submit_and_check(37'sd20, 37'sd5, 37'sd0, 1'b0,
                         37'sd15, 37'sd15, 37'sd85);
        submit_and_check(37'sd30, 37'sd10, 37'sd5, 1'b1,
                         37'sd20, 37'sd25, 37'sd60);

        repeat (4) @(posedge clk);
        #1;
        if (D !== 37'sd60) begin
            $fatal(1, "adder accumulator changed without valid input");
        end

        reset_adder();
        submit_and_check(MAX_VALUE, MIN_VALUE, MAX_VALUE, 1'b0,
                         HALF_SCALE, HALF_SCALE, HALF_SCALE);
        reset_adder();
        submit_and_check(MIN_VALUE, MAX_VALUE, MIN_VALUE, 1'b0,
                         -HALF_SCALE, -HALF_SCALE, -HALF_SCALE);

        // F remains in range while F+SAR and the integrated D clip high.
        reset_adder();
        submit_and_check(37'sd30000, 37'sd0, 37'sd10000, 1'b0,
                         37'sd30000, HALF_SCALE, HALF_SCALE);
        // The opposite polarity clips the accumulator low without clipping
        // the raw calibrated error before it is integrated.
        reset_adder();
        submit_and_check(37'sd30000, 37'sd0, 37'sd10000, 1'b1,
                         37'sd30000, HALF_SCALE, -HALF_SCALE);

        $display("TEST PASS: adder difference, calibrated integration and saturation");
        $finish;
    end

endmodule
