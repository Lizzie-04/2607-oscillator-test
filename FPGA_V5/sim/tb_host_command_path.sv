`timescale 1ns/1ps

module tb_host_command_path;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic [63:0] source_async = '0;

    logic jtag_valid;
    logic [3:0] jtag_opcode;
    logic [5:0] jtag_index;
    logic [15:0] jtag_data;
    logic jtag_done;
    logic [15:0] jtag_response;
    logic ack_toggle;
    logic pending;
    logic [15:0] last_response;

    logic command_valid;
    logic [3:0] command_opcode;
    logic [5:0] command_index;
    logic [15:0] command_data;
    logic command_done;
    logic [15:0] command_response;

    int command_count;

    always #5 clk = ~clk;

    jtag_request_receiver receiver (
        .clk(clk),
        .rst(rst),
        .source_async(source_async),
        .request_valid(jtag_valid),
        .request_opcode(jtag_opcode),
        .request_index(jtag_index),
        .request_data(jtag_data),
        .request_done(jtag_done),
        .response_data(jtag_response),
        .ack_toggle(ack_toggle),
        .pending(pending),
        .last_response(last_response)
    );

    host_command_arbiter arbiter (
        .clk(clk),
        .rst(rst),
        .jtag_valid(jtag_valid),
        .jtag_opcode(jtag_opcode),
        .jtag_index(jtag_index),
        .jtag_data(jtag_data),
        .jtag_done(jtag_done),
        .jtag_response(jtag_response),
        .gpio_valid(1'b0),
        .gpio_opcode('0),
        .gpio_index('0),
        .gpio_data('0),
        .gpio_done(),
        .gpio_response(),
        .command_valid(command_valid),
        .command_opcode(command_opcode),
        .command_index(command_index),
        .command_data(command_data),
        .command_done(command_done),
        .command_response(command_response)
    );

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            command_done <= 1'b0;
            command_response <= '0;
            command_count <= 0;
        end else begin
            command_done <= command_valid;
            if (command_valid) begin
                command_response <= {6'b0, command_index, command_opcode};
                command_count <= command_count + 1;
            end
        end
    end

    task automatic issue_request(
        input logic toggle,
        input logic [3:0] opcode,
        input logic [5:0] index,
        input logic [15:0] data
    );
        int count_before;
        begin
            count_before = command_count;
            source_async = {toggle, opcode, index, data, 37'b0};
            wait (ack_toggle == toggle && !pending);
            @(negedge clk);
            if (command_count != count_before + 1) begin
                $fatal(
                    1,
                    "one toggle executed %0d commands",
                    command_count - count_before
                );
            end
            if (last_response != {6'b0, index, opcode}) begin
                $fatal(
                    1,
                    "request payload mismatch: expected index=%0d opcode=%0h, response=%04h",
                    index,
                    opcode,
                    last_response
                );
            end
        end
    endtask

    task automatic issue_request_with_bus_skew(
        input logic toggle,
        input logic [3:0] opcode,
        input logic [5:0] index,
        input logic [15:0] data
    );
        int count_before;
        begin
            count_before = command_count;

            // Model a request-toggle bit arriving before the rest of the
            // asynchronous mailbox.  Index 31 is a deliberate intermediate
            // value for the real 23 -> 24 multi-bit boundary failure.
            source_async = {
                toggle,
                opcode,
                6'd31,
                ~data,
                37'b0
            };
            @(posedge clk);
            #1 source_async = {toggle, opcode, index, data, 37'b0};

            wait (ack_toggle == toggle && !pending);
            @(negedge clk);
            if (command_count != count_before + 1) begin
                $fatal(
                    1,
                    "skewed toggle executed %0d commands",
                    command_count - count_before
                );
            end
            if (last_response != {6'b0, index, opcode}) begin
                $fatal(
                    1,
                    "skewed mailbox tore payload: expected index=%0d opcode=%0h, response=%04h",
                    index,
                    opcode,
                    last_response
                );
            end
        end
    endtask

    initial begin
        logic next_toggle;
        int round;
        int index;

        repeat (4) @(posedge clk);
        rst = 1'b0;
        repeat (4) @(posedge clk);

        issue_request(1'b1, 4'h2, 6'd7, 16'h1234);
        if (command_count != 1) begin
            $fatal(
                1,
                "one JTAG toggle executed %0d core commands",
                command_count
            );
        end

        issue_request(1'b0, 4'h1, 6'd9, 16'hABCD);
        if (command_count != 2) begin
            $fatal(
                1,
                "two JTAG toggles executed %0d core commands",
                command_count
            );
        end

        next_toggle = 1'b1;
        for (round = 0; round < 40; round++) begin
            for (index = 0; index < 30; index++) begin
                issue_request_with_bus_skew(
                    next_toggle,
                    4'h1,
                    index,
                    (round << 8) ^ index
                );
                next_toggle = ~next_toggle;
            end
        end

        if (command_count != 1202) begin
            $fatal(
                1,
                "stress run executed %0d commands instead of 1202",
                command_count
            );
        end

        $display(
            "TEST PASS: each held-valid host request executes exactly once"
        );
        $finish;
    end

endmodule
