`timescale 1ns/1ps

module tb_triangular_decimator_runtime;

    logic clk_sample = 1'b0;
    logic clk_data = 1'b0;
    logic rst_sample = 1'b1;
    logic rst_data = 1'b1;
    logic sample_running = 1'b1;
    logic bs_t = 1'b1;
    logic bs_c = 1'b0;
    logic [15:0] configured_length = 16'd32;

    logic [17:0] mu1;
    logic [17:0] mu2;
    logic valid;
    logic [15:0] active_length;

    int valid_count;
    int sample_count;
    int last_valid_clock;
    int last_window_length;
    int interval;

    always #13 clk_data = ~clk_data;
    always #47 begin
        if (sample_running) begin
            clk_sample = ~clk_sample;
        end
    end

    triangular_decimator_runtime #(
        .OUTPUT_WIDTH(18),
        .DEFAULT_LENGTH(32),
        .MIN_LENGTH(32),
        .MAX_LENGTH(4096)
    ) dut (
        .rst_sample(rst_sample),
        .clk_sample(clk_sample),
        .rst_data(rst_data),
        .clk_data(clk_data),
        .bs_t(bs_t),
        .bs_c(bs_c),
        .configured_length_async(configured_length),
        .mu1(mu1),
        .mu2(mu2),
        .valid(valid),
        .active_length(active_length)
    );

    always @(posedge clk_sample) begin
        sample_count++;
    end

    always @(posedge clk_data) begin
        if (valid) begin
            valid_count++;
            if (last_valid_clock != 0) begin
                interval = sample_count - last_valid_clock;
                if (interval != 2 * last_window_length) begin
                    $fatal(
                        1,
                        "continuous output interval mismatch: N=%0d interval=%0d",
                        last_window_length,
                        interval
                    );
                end
            end
            last_valid_clock = sample_count;
            last_window_length = active_length;
        end
    end

    initial begin
        valid_count = 0;
        sample_count = 0;
        last_valid_clock = 0;
        last_window_length = 0;
        interval = 0;

        repeat (3) @(posedge clk_data);
        rst_data = 1'b0;
        repeat (3) @(posedge clk_sample);
        rst_sample = 1'b0;

        wait (valid_count == 2);
        @(negedge clk_data);
        if (mu1 != 18'd65_536 || mu2 != 0) begin
            $fatal(1, "constant stream normalization failed: mu1=%0d mu2=%0d", mu1, mu2);
        end

        sample_running = 1'b0;
        repeat (100) @(posedge clk_data);
        if (valid_count != 2) begin
            $fatal(1, "decimator advanced while FS was stopped");
        end
        sample_running = 1'b1;

        configured_length = 16'd40;
        wait (active_length == 40);
        wait (valid_count == 4);
        @(negedge clk_data);
        if (mu1 != 18'd65_536 || mu2 != 0) begin
            $fatal(1, "runtime length update changed normalized values");
        end

        $display(
            "TEST PASS: continuous triangular decimation and runtime N update"
        );
        $finish;
    end

endmodule
