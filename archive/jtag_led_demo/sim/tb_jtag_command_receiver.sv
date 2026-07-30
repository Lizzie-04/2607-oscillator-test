`timescale 1ns/1ps

module tb_jtag_command_receiver;

    localparam int DEFER_CYCLES = 4;

    logic        clk = 1'b0;
    logic        reset_n = 1'b0;
    logic [11:0] source_async = '0;
    logic [11:0] probe_data;
    logic [9:0]  led_value;
    logic [9:0]  pending_value;
    logic        busy;
    logic        update_pulse;
    logic        ack_toggle;

    int pulse_count = 0;

    always #10 clk = ~clk;

    always @(posedge clk) begin
        if (update_pulse)
            pulse_count <= pulse_count + 1;
    end

    jtag_command_receiver #(
        .DEFER_CYCLES(DEFER_CYCLES)
    ) dut (
        .clk(clk),
        .reset_n(reset_n),
        .source_async(source_async),
        .probe_data(probe_data),
        .led_value(led_value),
        .pending_value(pending_value),
        .busy(busy),
        .update_pulse(update_pulse),
        .ack_toggle(ack_toggle)
    );

    task automatic wait_cycles(input int count);
        repeat (count) @(posedge clk);
    endtask

    task automatic send_command(
        input logic [9:0] value,
        input logic       deferred
    );
        logic request;
        int timeout;
        begin
            request = ~source_async[11];
            source_async = {request, deferred, value};
            timeout = 0;
            while ((ack_toggle !== request) && (timeout < 100)) begin
                @(posedge clk);
                timeout++;
            end
            if (timeout >= 100)
                $fatal(1, "Timeout waiting for acknowledgement");
            if (led_value !== value)
                $fatal(1, "LED mismatch: expected %0d, got %0d", value, led_value);
            if (probe_data[9:0] !== value || probe_data[11] !== request)
                $fatal(1, "Probe mismatch after command");
        end
    endtask

    initial begin
        $display("Starting jtag_command_receiver tests");
        wait_cycles(3);
        reset_n = 1'b1;
        wait_cycles(3);

        if (led_value !== 10'd0 || busy !== 1'b0 || ack_toggle !== 1'b0)
            $fatal(1, "Reset state is incorrect");

        send_command(10'd1, 1'b0);
        wait_cycles(2);
        if (pulse_count != 1)
            $fatal(1, "Expected one update pulse after value 1");

        source_async[9:0] = 10'd55;
        wait_cycles(6);
        if (led_value !== 10'd1 || pulse_count != 1)
            $fatal(1, "Data changed without request toggle");

        send_command(10'd3, 1'b0);
        wait_cycles(2);
        if (pulse_count != 2)
            $fatal(1, "Expected second update pulse");

        send_command(10'd1023, 1'b0);
        wait_cycles(2);
        if (pulse_count != 3)
            $fatal(1, "Expected third update pulse");

        fork
            begin
                send_command(10'd341, 1'b1);
            end
            begin
                wait (busy === 1'b1);
                if (led_value !== 10'd1023)
                    $fatal(1, "Deferred command changed LEDs too early");
                wait_cycles(DEFER_CYCLES - 1);
                if (led_value !== 10'd1023)
                    $fatal(1, "Deferred command changed LEDs before delay elapsed");
            end
        join

        wait_cycles(2);
        if (pulse_count != 4 || busy !== 1'b0)
            $fatal(1, "Deferred command completion is incorrect");

        send_command(10'd0, 1'b0);
        wait_cycles(2);
        if (pulse_count != 5)
            $fatal(1, "Back-to-back command test failed");

        $display("TEST PASS: all jtag_command_receiver cases passed");
        $finish;
    end

endmodule
