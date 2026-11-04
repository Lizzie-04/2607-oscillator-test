`timescale 1ns/1ps

module tb_avalon_host_bridge;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic [31:0] address = '0;
    logic [3:0] byteenable = 4'hF;
    logic read = 1'b0;
    logic write = 1'b0;
    logic [31:0] writedata = '0;
    logic waitrequest;
    logic [31:0] readdata;
    logic readdatavalid;
    logic core_valid;
    logic [3:0] core_opcode;
    logic [5:0] core_index;
    logic [15:0] core_data;
    logic core_done = 1'b0;
    logic [15:0] core_response = '0;
    logic [7:0] core_error = '0;
    logic core_control_complete = 1'b0;
    logic core_driver_busy = 1'b0;
    logic core_driver_done = 1'b0;
    logic core_sar_busy = 1'b0;
    logic core_sar_done = 1'b0;
    logic core_statistics_busy = 1'b0;
    logic core_statistics_done = 1'b0;
    logic core_statistics_toggle = 1'b0;
    logic [15:0] core_statistics_count = '0;
    logic statistics_abort;
    logic statistics_read_valid;
    logic [6:0] statistics_read_address;
    logic statistics_read_ready = 1'b1;
    logic [35:0] statistics_read_data = '0;
    logic statistics_read_data_valid = 1'b0;
    int core_command_count = 0;
    int control_command_count = 0;

    always #5 clk = ~clk;

    avalon_host_bridge dut (
        .clk(clk),
        .rst(rst),
        .avalon_address(address),
        .avalon_byteenable(byteenable),
        .avalon_read(read),
        .avalon_write(write),
        .avalon_writedata(writedata),
        .avalon_waitrequest(waitrequest),
        .avalon_readdata(readdata),
        .avalon_readdatavalid(readdatavalid),
        .core_request_valid(core_valid),
        .core_request_opcode(core_opcode),
        .core_request_index(core_index),
        .core_request_data(core_data),
        .core_request_done(core_done),
        .core_response_data(core_response),
        .core_error_code(core_error),
        .core_control_words_complete(core_control_complete),
        .core_driver_busy(core_driver_busy),
        .core_driver_done(core_driver_done),
        .core_sar_busy(core_sar_busy),
        .core_sar_done(core_sar_done),
        .core_statistics_busy(core_statistics_busy),
        .core_statistics_done(core_statistics_done),
        .core_statistics_completion_toggle(core_statistics_toggle),
        .core_statistics_captured_count(core_statistics_count),
        .statistics_abort(statistics_abort),
        .statistics_read_valid(statistics_read_valid),
        .statistics_read_address(statistics_read_address),
        .statistics_read_ready(statistics_read_ready),
        .statistics_read_data(statistics_read_data),
        .statistics_read_data_valid(statistics_read_data_valid)
    );

    always @(posedge clk) begin
        core_done <= core_valid;
        statistics_read_data_valid <= 1'b0;
        if (core_valid) begin
            core_command_count <= core_command_count + 1;
            core_response <= {6'd0, core_index, core_opcode};
            if (core_opcode == 4'h1) begin
                control_command_count <= control_command_count + 1;
                if (core_index != control_command_count[5:0]) begin
                    $fatal(1, "control block order mismatch");
                end
            end
        end
        if (statistics_read_valid && statistics_read_ready) begin
            statistics_read_data <= {
                18'(statistics_read_address + 1000),
                18'(statistics_read_address)
            };
            statistics_read_data_valid <= 1'b1;
        end
    end

    task automatic write32(input logic [31:0] bus_address, input logic [31:0] data);
        begin
            @(negedge clk);
            address = bus_address;
            writedata = data;
            write = 1'b1;
            do @(posedge clk); while (waitrequest);
            @(negedge clk);
            write = 1'b0;
        end
    endtask

    task automatic read32(input logic [31:0] bus_address, output logic [31:0] data);
        begin
            @(negedge clk);
            address = bus_address;
            read = 1'b1;
            do @(posedge clk); while (waitrequest);
            if (!readdatavalid) begin
                wait (readdatavalid);
            end
            data = readdata;
            @(negedge clk);
            read = 1'b0;
        end
    endtask

    initial begin
        logic [31:0] value;
        int index;
        int commands_before_duplicate;

        repeat (5) @(posedge clk);
        rst = 1'b0;
        repeat (3) @(posedge clk);

        read32(32'h0000, value);
        if (value != 32'h4F534356) begin
            $fatal(1, "MAGIC mismatch: %h", value);
        end

        for (index = 0; index < 30; index++) begin
            write32(32'h0100 + index * 4, 16'h8000 + index);
        end
        write32(32'h0018, 32'd1);
        write32(32'h0054, 32'h000A);
        if (control_command_count != 30 || core_command_count != 31) begin
            $fatal(
                1,
                "commit command count mismatch: control=%0d total=%0d",
                control_command_count,
                core_command_count
            );
        end

        commands_before_duplicate = core_command_count;
        write32(32'h0018, 32'd1);
        write32(32'h0054, 32'h000A);
        if (core_command_count != commands_before_duplicate) begin
            $fatal(1, "completed request ID executed twice");
        end

        write32(32'h0018, 32'd2);
        write32(32'h0064, 32'd0);
        core_statistics_toggle = ~core_statistics_toggle;
        repeat (3) @(posedge clk);
        read32(32'h0030, value);
        if (value != 0) begin
            $fatal(1, "statistics CDC skew published an incomplete batch");
        end
        core_statistics_count = 16'd100;
        core_statistics_done = 1'b1;
        repeat (3) @(posedge clk);
        read32(32'h0030, value);
        if (value != 1) begin
            $fatal(1, "statistics batch did not become ready");
        end
        read32(32'h0034, value);
        if (value != 2) begin
            $fatal(1, "statistics capture ID mismatch");
        end
        read32(32'h0400 + 37 * 8, value);
        if (value != 37) begin
            $fatal(1, "mu1 address order mismatch: %0d", value);
        end
        read32(32'h0404 + 37 * 8, value);
        if (value != 1037) begin
            $fatal(1, "mu2 pairing mismatch: %0d", value);
        end

        write32(32'h0018, 32'd3);
        write32(32'h0070, 32'd2);
        read32(32'h0030, value);
        if (value != 0) begin
            $fatal(1, "statistics release did not clear ready");
        end

        write32(32'h0018, 32'd4);
        write32(32'h0064, 32'd0);
        write32(32'h0018, 32'd5);
        write32(32'h0074, 32'd4);
        if (!statistics_abort) begin
            @(posedge clk);
        end
        read32(32'h0030, value);
        if (value != 0) begin
            $fatal(1, "statistics abort left batch ready");
        end

        $display(
            "TEST PASS: Avalon address decode, exactly-once actions and frozen statistics batch"
        );
        $finish;
    end

endmodule
