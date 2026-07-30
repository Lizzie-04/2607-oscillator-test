`timescale 1ns/1ps

module tb_adder_calibration;

    localparam int DATA_WIDTH = 8;

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

    task automatic apply_and_check(
        input int a_value,
        input int b_value,
        input int calibration_value,
        input logic polarity,
        input int expected_d,
        input int expected_f,
        input int expected_calibrated_error
    );
        int timeout;
        begin
            @(negedge clk);
            A = a_value;
            B = b_value;
            calibration = calibration_value;
            polar = polarity;
            valid_in = 1'b1;
            @(negedge clk);
            valid_in = 1'b0;

            timeout = 0;
            while (!valid_out && timeout < 10) begin
                @(negedge clk);
                timeout++;
            end
            if (timeout >= 10) begin
                $fatal(1, "adder valid_out timeout");
            end

            if ($signed(D) != expected_d ||
                    $signed(F) != expected_f ||
                    $signed(calibrated_error) !=
                        expected_calibrated_error) begin
                $fatal(
                    1,
                    "adder mismatch D=%0d/%0d F=%0d/%0d CE=%0d/%0d",
                    $signed(D),
                    expected_d,
                    $signed(F),
                    expected_f,
                    $signed(calibrated_error),
                    expected_calibrated_error
                );
            end
        end
    endtask

    task automatic reset_dut;
        begin
            @(negedge clk);
            rst = 1'b1;
            repeat (2) @(negedge clk);
            rst = 1'b0;
            repeat (2) @(negedge clk);
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);

        apply_and_check(10, 4, 2, 1'b0, 8, 6, 8);
        apply_and_check(10, 4, 2, 1'b0, 16, 6, 8);
        apply_and_check(10, 4, 2, 1'b1, 8, 6, 8);

        apply_and_check(100, -100, 50, 1'b0, 127, 127, 127);

        reset_dut();
        apply_and_check(-100, 100, 0, 1'b0, -128, -128, -128);

        $display(
            "TEST PASS: calibrated error, polarity and signed saturation"
        );
        $finish;
    end

endmodule
