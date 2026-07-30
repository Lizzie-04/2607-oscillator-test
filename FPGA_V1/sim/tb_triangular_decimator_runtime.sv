`timescale 1ns/1ps

module tb_triangular_decimator_runtime;

    logic sample_clk = 1'b0;
    logic rst = 1'b1;
    logic bs_t = 1'b1;
    logic bs_c = 1'b0;
    logic [15:0] configured_length = 16'd32;

    logic [17:0] mu1;
    logic [17:0] mu2;
    logic valid;
    logic [15:0] active_length;

    int valid_count;
    int sample_clock_count;
    int last_valid_clock;
    int last_window_length;
    int interval;

    always #10 sample_clk = ~sample_clk;

    triangular_decimator_runtime #(
        .OUTPUT_WIDTH(18),
        .DEFAULT_LENGTH(32),
        .MIN_LENGTH(32),
        .MAX_LENGTH(4096)
    ) dut (
        .rst(rst),
        .sample_clk(sample_clk),
        .bs_t(bs_t),
        .bs_c(bs_c),
        .configured_length_async(configured_length),
        .mu1(mu1),
        .mu2(mu2),
        .valid(valid),
        .active_length(active_length)
    );

    always @(posedge sample_clk) begin
        sample_clock_count++;
        if (valid) begin
            valid_count++;
            if (last_valid_clock != 0) begin
                interval = sample_clock_count - last_valid_clock;
                if (interval != 2 * last_window_length) begin
                    $fatal(
                        1,
                        "continuous output interval mismatch: N=%0d interval=%0d",
                        last_window_length,
                        interval
                    );
                end
            end
            last_valid_clock = sample_clock_count;
            last_window_length = active_length;
        end
    end

    initial begin
        valid_count = 0;
        sample_clock_count = 0;
        last_valid_clock = 0;
        last_window_length = 0;
        interval = 0;

        repeat (3) @(posedge sample_clk);
        rst = 1'b0;

        wait (valid_count == 2);
        @(negedge sample_clk);
        if (mu1 != 18'd65_536 || mu2 != 0) begin
            $fatal(1, "constant stream normalization failed: mu1=%0d mu2=%0d", mu1, mu2);
        end

        configured_length = 16'd40;
        wait (active_length == 40);
        wait (valid_count == 4);
        @(negedge sample_clk);
        if (mu1 != 18'd65_536 || mu2 != 0) begin
            $fatal(1, "runtime length update changed normalized values");
        end

        $display(
            "TEST PASS: continuous triangular decimation and runtime N update"
        );
        $finish;
    end

endmodule
