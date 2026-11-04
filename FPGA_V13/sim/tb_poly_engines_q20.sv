`timescale 1ns/1ps

module tb_poly_engines_q20;
    localparam int DATA_WIDTH = 37;
    localparam int INPUT_WIDTH = 18;
    localparam logic signed [DATA_WIDTH-1:0] LARGE_POSITIVE =
        37'sd32768016384; // +500000.25 in Q20.16
    localparam logic signed [DATA_WIDTH-1:0] LARGE_NEGATIVE =
        -37'sd39321632768; // -600000.5 in Q20.16

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic freq_valid_in = 1'b0;
    logic system_valid_in = 1'b0;
    logic signed [INPUT_WIDTH-1:0] freq_data = '0;
    logic signed [INPUT_WIDTH-1:0] system_data = '0;
    logic signed [DATA_WIDTH-1:0] freq_a7 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a6 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a5 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a4 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a3 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a2 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a1 = '0;
    logic signed [DATA_WIDTH-1:0] freq_a0 = '0;
    logic signed [DATA_WIDTH-1:0] system_a6 = '0;
    logic signed [DATA_WIDTH-1:0] system_a5 = '0;
    logic signed [DATA_WIDTH-1:0] system_a4 = '0;
    logic signed [DATA_WIDTH-1:0] system_a3 = '0;
    logic signed [DATA_WIDTH-1:0] system_a2 = '0;
    logic signed [DATA_WIDTH-1:0] system_a1 = '0;
    logic signed [DATA_WIDTH-1:0] system_a0 = '0;
    logic freq_valid_out;
    logic system_valid_out;
    logic signed [DATA_WIDTH-1:0] freq_out;
    logic signed [DATA_WIDTH-1:0] system_out;

    always #5 clk = ~clk;

    poly_engine_freq #(
        .DATA_WIDTH(DATA_WIDTH),
        .INPUT_WIDTH(INPUT_WIDTH),
        .FRACTION_BITS(16)
    ) freq_dut (
        .rst(rst), .clk(clk), .valid_in(freq_valid_in),
        .data_in(freq_data), .a7(freq_a7), .a6(freq_a6),
        .a5(freq_a5), .a4(freq_a4), .a3(freq_a3), .a2(freq_a2),
        .a1(freq_a1), .a0(freq_a0), .valid_out(freq_valid_out),
        .poly_out(freq_out)
    );

    poly_engine_system #(
        .DATA_WIDTH(DATA_WIDTH),
        .INPUT_WIDTH(INPUT_WIDTH),
        .FRACTION_BITS(16)
    ) system_dut (
        .rst(rst), .clk(clk), .valid_in(system_valid_in),
        .data_in(system_data), .a6(system_a6), .a5(system_a5),
        .a4(system_a4), .a3(system_a3), .a2(system_a2),
        .a1(system_a1), .a0(system_a0),
        .valid_out(system_valid_out), .poly_out(system_out)
    );

    task automatic run_freq(
        input logic signed [INPUT_WIDTH-1:0] input_data,
        input logic signed [DATA_WIDTH-1:0] expected
    );
        begin
            @(negedge clk);
            freq_data = input_data;
            freq_valid_in = 1'b1;
            @(negedge clk);
            freq_valid_in = 1'b0;
            wait (freq_valid_out);
            #1;
            if (freq_out !== expected) begin
                $fatal(1, "frequency polynomial mismatch: %0d/%0d", freq_out, expected);
            end
        end
    endtask

    task automatic run_system(
        input logic signed [INPUT_WIDTH-1:0] input_data,
        input logic signed [DATA_WIDTH-1:0] expected
    );
        begin
            @(negedge clk);
            system_data = input_data;
            system_valid_in = 1'b1;
            @(negedge clk);
            system_valid_in = 1'b0;
            wait (system_valid_out);
            #1;
            if (system_out !== expected) begin
                $fatal(1, "system polynomial mismatch: %0d/%0d", system_out, expected);
            end
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst = 1'b0;

        // Constant terms exceed the old signed Q12.16 range in both signs.
        freq_a0 = LARGE_POSITIVE;
        run_freq(18'sd16384, LARGE_POSITIVE);
        system_a0 = LARGE_NEGATIVE;
        run_system(-18'sd16384, LARGE_NEGATIVE);

        // Exercise the parameterized 74-bit product and arithmetic Q16
        // rescale: -2.0 * +0.25 = -0.5.
        freq_a0 = '0;
        freq_a1 = -37'sd131072;
        run_freq(18'sd16384, -37'sd32768);
        system_a0 = '0;
        system_a1 = -37'sd131072;
        run_system(18'sd16384, -37'sd32768);

        $display("TEST PASS: 37-bit signed Q20.16 polynomial constants and products");
        $finish;
    end
endmodule
