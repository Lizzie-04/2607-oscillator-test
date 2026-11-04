`timescale 1ns/1ps

module tb_statistics_capture_buffer;

    localparam int SAMPLE_COUNT = 100;

    logic source_clk = 1'b0;
    logic destination_clk = 1'b0;
    logic source_rst = 1'b1;
    logic source_start = 1'b0;
    logic source_sample_valid = 1'b0;
    logic [17:0] source_mu1 = '0;
    logic [17:0] source_mu2 = '0;
    logic source_busy;
    logic source_done;
    logic source_completion_toggle;
    logic [15:0] source_captured_count;
    logic destination_read_enable = 1'b0;
    logic [6:0] destination_read_address = '0;
    logic [35:0] destination_read_data;

    always #7 source_clk = ~source_clk;
    always #5 destination_clk = ~destination_clk;

    statistics_capture_buffer #(
        .DATA_WIDTH(18),
        .SAMPLE_COUNT(SAMPLE_COUNT),
        .MEMORY_DEPTH(128),
        .ADDRESS_WIDTH(7)
    ) dut (
        .source_clk(source_clk),
        .source_rst(source_rst),
        .source_start(source_start),
        .source_sample_valid(source_sample_valid),
        .source_mu1(source_mu1),
        .source_mu2(source_mu2),
        .source_busy(source_busy),
        .source_done(source_done),
        .source_completion_toggle(source_completion_toggle),
        .source_captured_count(source_captured_count),
        .destination_clk(destination_clk),
        .destination_read_enable(destination_read_enable),
        .destination_read_address(destination_read_address),
        .destination_read_data(destination_read_data)
    );

    task automatic pulse_start;
        begin
            @(negedge source_clk);
            source_start = 1'b1;
            @(negedge source_clk);
            source_start = 1'b0;
        end
    endtask

    task automatic push_sample(input int mu1_value, input int mu2_value);
        begin
            @(negedge source_clk);
            source_mu1 = mu1_value[17:0];
            source_mu2 = mu2_value[17:0];
            source_sample_valid = 1'b1;
            @(negedge source_clk);
            source_sample_valid = 1'b0;
            // Invalid source clocks must not advance the capture address.
            @(negedge source_clk);
        end
    endtask

    task automatic check_sample(input int address);
        logic [35:0] expected;
        begin
            expected = {
                18'(address + 1000),
                18'(address)
            };
            @(negedge destination_clk);
            destination_read_address = address[6:0];
            destination_read_enable = 1'b1;
            @(posedge destination_clk);
            #1;
            destination_read_enable = 1'b0;
            if (destination_read_data !== expected) begin
                $fatal(
                    1,
                    "statistics RAM mismatch at %0d: expected=%h actual=%h",
                    address,
                    expected,
                    destination_read_data
                );
            end
        end
    endtask

    initial begin
        int i;

        repeat (4) @(posedge source_clk);
        source_rst = 1'b0;
        repeat (3) @(posedge source_clk);

        pulse_start();
        if (!source_busy || source_done || source_captured_count != 0) begin
            $fatal(1, "statistics capture did not enter armed state");
        end

        // This result belongs to the triangular window that was already in
        // progress when start arrived, and therefore must be discarded.
        push_sample(18'h3AAAA, 18'h15555);
        if (source_captured_count != 0) begin
            $fatal(1, "pre-start triangular window was not discarded");
        end

        for (i = 0; i < SAMPLE_COUNT; i++) begin
            push_sample(i, i + 1000);
        end

        if (source_busy || !source_done ||
                source_captured_count != SAMPLE_COUNT ||
                source_completion_toggle != 1'b1) begin
            $fatal(
                1,
                "statistics completion failed: busy=%0b done=%0b count=%0d",
                source_busy,
                source_done,
                source_captured_count
            );
        end

        check_sample(0);
        check_sample(37);
        check_sample(99);

        // A new request clears DONE and begins a fresh batch without
        // inheriting the previous captured count.
        pulse_start();
        if (!source_busy || source_done || source_captured_count != 0) begin
            $fatal(1, "statistics restart did not reset batch state");
        end

        $display(
            "TEST PASS: 100-point statistics capture, alignment and dual-clock RAM"
        );
        $finish;
    end

endmodule
