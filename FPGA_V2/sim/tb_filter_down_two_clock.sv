`timescale 1ns/1ps

module tb_filter_down_two_clock;

    logic clk_sample = 1'b0;
    logic clk_data = 1'b0;
    logic rst_sample = 1'b1;
    logic rst_data = 1'b1;
    logic sample_running = 1'b1;
    logic data_running = 1'b1;
    logic bs_in = 1'b1;

    logic [17:0] out;
    logic valid;

    int valid_count;

    always #11 begin
        if (data_running) begin
            clk_data = ~clk_data;
        end
    end

    always #37 begin
        if (sample_running) begin
            clk_sample = ~clk_sample;
        end
    end

    filter_down_two_clock dut (
        .rst_sample(rst_sample),
        .clk_sample(clk_sample),
        .rst_data(rst_data),
        .clk_data(clk_data),
        .bs_in(bs_in),
        .out(out),
        .valid(valid)
    );

    always @(posedge clk_data) begin
        if (valid) begin
            valid_count++;
        end
    end

    initial begin
        valid_count = 0;

        repeat (3) @(posedge clk_data);
        rst_data = 1'b0;
        repeat (3) @(posedge clk_sample);
        rst_sample = 1'b0;

        wait (valid_count == 1);
        if (out != 18'h0FFFF) begin
            $fatal(1, "constant-one fixed filter output mismatch: %h", out);
        end

        // FS alone controls BS sampling.  A running 2 MHz data clock must not
        // create another filtered result while FS is absent.
        sample_running = 1'b0;
        repeat (200) @(posedge clk_data);
        if (valid_count != 1) begin
            $fatal(1, "data clock advanced the FS sampling accumulator");
        end

        // Conversely, the FS accumulator can finish while clk_data is
        // paused; its held result must be delivered once clk_data resumes.
        data_running = 1'b0;
        sample_running = 1'b1;
        repeat (2048) @(posedge clk_sample);
        if (valid_count != 1) begin
            $fatal(1, "valid changed without a clk_data edge");
        end
        data_running = 1'b1;
        wait (valid_count == 2);

        $display(
            "TEST PASS: FS sampling and 2 MHz data processing are separated"
        );
        $finish;
    end

endmodule
