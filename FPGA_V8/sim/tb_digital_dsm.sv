`timescale 1ns/1ps

module tb_digital_dsm;
    localparam int DATA_WIDTH = 29;
    localparam int FRACTION_BITS = 16;
    localparam signed [DATA_WIDTH-1:0] HALF_SCALE = 29'sd32768;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic signed [DATA_WIDTH-1:0] data_in = '0;
    logic dsm_out;

    always #5 clk = ~clk;

    digital_DSM #(
        .DATA_WIDTH(DATA_WIDTH),
        .FRACTION_BITS(FRACTION_BITS)
    ) dut (
        .rst(rst),
        .clk(clk),
        .data_in(data_in),
        .dsm_out(dsm_out)
    );

    task automatic reset_dsm;
        begin
            rst = 1'b1;
            repeat (2) @(posedge clk);
            #1;
            rst = 1'b0;
        end
    endtask

    task automatic check_constant(
        input signed [DATA_WIDTH-1:0] value,
        input logic expected,
        input int cycles
    );
        int index;
        begin
            @(negedge clk);
            data_in = value;
            for (index = 0; index < cycles; index = index + 1) begin
                @(posedge clk);
                #1;
                if (dsm_out !== expected) begin
                    $fatal(
                        1,
                        "constant DSM mismatch value=%0d cycle=%0d expected=%0b actual=%0b",
                        value,
                        index,
                        expected,
                        dsm_out
                    );
                end
            end
        end
    endtask

    task automatic check_density(
        input signed [DATA_WIDTH-1:0] value,
        input int cycles,
        input int expected_ones
    );
        int index;
        int ones;
        begin
            reset_dsm();
            @(negedge clk);
            data_in = value;
            ones = 0;
            for (index = 0; index < cycles; index = index + 1) begin
                @(posedge clk);
                #1;
                if (dsm_out) begin
                    ones = ones + 1;
                end
            end
            if (ones != expected_ones) begin
                $fatal(
                    1,
                    "DSM density mismatch value=%0d cycles=%0d expected=%0d actual=%0d",
                    value,
                    cycles,
                    expected_ones,
                    ones
                );
            end
        end
    endtask

    initial begin
        reset_dsm();
        check_constant(-HALF_SCALE, 1'b0, 64);
        check_constant(-29'sd65536, 1'b0, 64);
        check_constant(HALF_SCALE, 1'b1, 64);
        check_constant(29'sd65536, 1'b1, 64);

        check_density(29'sd0, 256, 128);
        check_density(29'sd16384, 256, 192);
        check_density(-29'sd16384, 256, 64);

        // A full modulo period proves exact Q16 density and bounded long-run
        // behavior for a non-power-of-two interior input.
        check_density(29'sd12345, 65536, 32768 + 12345);
        check_density(-29'sd12345, 65536, 32768 - 12345);

        // Saturation clears phase history, so transitions away from overflow
        // restart deterministically without a residual all-one/all-zero tail.
        reset_dsm();
        check_constant(29'sd100000, 1'b1, 32);
        check_constant(-29'sd100000, 1'b0, 32);
        check_density(29'sd0, 256, 128);

        $display("TEST PASS: bounded digital DSM endpoints, density and overflow protection");
        $finish;
    end

endmodule
