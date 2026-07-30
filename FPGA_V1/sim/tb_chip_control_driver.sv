`timescale 1ns/1ps

module tb_chip_control_driver;

    localparam int HALF_PERIOD_CYCLES = 2;
    localparam int TOTAL_CYCLES = 1940;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic start = 1'b0;
    logic [473:0] serial_bits;
    logic [2:0] chip_number = 3'd2;
    logic broadcast = 1'b0;

    logic [2:0] address;
    logic sr_clk;
    logic sr_in;
    logic [4:0] sr_enable;
    logic chip_reset;
    logic start_dll;
    logic start_ana;
    logic busy;
    logic done;
    logic [9:0] bit_index;
    logic [3:0] state_debug;

    int rising_edges;
    int enable_cycles;
    int reset_cycles;
    int busy_cycles;
    logic [473:0] captured_bits;

    always #5 clk = ~clk;

    chip_control_driver #(
        .CONTROL_BITS(474),
        .HALF_PERIOD_CYCLES(HALF_PERIOD_CYCLES),
        .TOTAL_CYCLES(TOTAL_CYCLES)
    ) dut (
        .clk(clk),
        .rst(rst),
        .start(start),
        .serial_bits(serial_bits),
        .chip_number(chip_number),
        .broadcast(broadcast),
        .address(address),
        .sr_clk(sr_clk),
        .sr_in(sr_in),
        .sr_enable(sr_enable),
        .chip_reset(chip_reset),
        .start_dll(start_dll),
        .start_ana(start_ana),
        .busy(busy),
        .done(done),
        .bit_index(bit_index),
        .state_debug(state_debug)
    );

    always @(posedge sr_clk) begin
        captured_bits[rising_edges] = sr_in;
        rising_edges++;
    end

    always @(posedge clk) begin
        if (busy) begin
            busy_cycles++;
        end
        if (sr_enable != 0) begin
            enable_cycles++;
            if (sr_enable != 5'b0_0100) begin
                $fatal(1, "wrong enable mask: %b", sr_enable);
            end
        end
        if (chip_reset) begin
            reset_cycles++;
            if (!start_dll || !start_ana) begin
                $fatal(1, "reset was not aligned with both START signals");
            end
        end
        if (busy && (address != chip_number)) begin
            $fatal(1, "address changed while transaction was active");
        end
    end

    initial begin
        int i;
        serial_bits = '0;
        captured_bits = '0;
        rising_edges = 0;
        enable_cycles = 0;
        reset_cycles = 0;
        busy_cycles = 0;

        for (i = 0; i < 474; i++) begin
            serial_bits[i] = ((i % 5) == 0) || (i == 473);
        end

        repeat (3) @(posedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);

        @(negedge clk);
        start = 1'b1;
        @(negedge clk);
        start = 1'b0;

        wait (busy);
        wait (done);
        @(negedge clk);

        if (rising_edges != 474) begin
            $fatal(1, "expected 474 SR_CLK rising edges, got %0d", rising_edges);
        end
        if (captured_bits !== serial_bits) begin
            $fatal(1, "serialized data did not match the 474-bit input");
        end
        if (enable_cycles != HALF_PERIOD_CYCLES) begin
            $fatal(1, "enable width mismatch: %0d", enable_cycles);
        end
        if (reset_cycles != HALF_PERIOD_CYCLES) begin
            $fatal(1, "reset width mismatch: %0d", reset_cycles);
        end
        if (busy_cycles != TOTAL_CYCLES) begin
            $fatal(
                1,
                "total transaction length mismatch: expected=%0d got=%0d",
                TOTAL_CYCLES,
                busy_cycles
            );
        end
        if (sr_clk || sr_in || (sr_enable != 0) || chip_reset ||
                start_dll || start_ana) begin
            $fatal(1, "outputs did not return to the safe idle state");
        end

        $display(
            "TEST PASS: 474-bit driver edges, data, enable and startup timing"
        );
        $finish;
    end

endmodule
