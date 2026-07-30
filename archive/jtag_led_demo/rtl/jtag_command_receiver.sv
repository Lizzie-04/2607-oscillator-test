module jtag_command_receiver #(
    parameter int unsigned DEFER_CYCLES = 250_000_000
) (
    input  logic        clk,
    input  logic        reset_n,
    input  logic [11:0] source_async,
    output logic [11:0] probe_data,
    output logic [9:0]  led_value,
    output logic [9:0]  pending_value,
    output logic        busy,
    output logic        update_pulse,
    output logic        ack_toggle
);

    localparam int unsigned COUNTER_WIDTH =
        (DEFER_CYCLES <= 1) ? 1 : $clog2(DEFER_CYCLES);

    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    logic [11:0] source_meta;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    logic [11:0] source_sync;

    logic       pending_request;
    logic [COUNTER_WIDTH-1:0] delay_counter;

    assign probe_data = {ack_toggle, busy, led_value};

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            source_meta    <= '0;
            source_sync    <= '0;
            pending_value  <= '0;
            pending_request <= 1'b0;
            delay_counter  <= '0;
            led_value      <= '0;
            busy           <= 1'b0;
            update_pulse   <= 1'b0;
            ack_toggle     <= 1'b0;
        end else begin
            source_meta  <= source_async;
            source_sync  <= source_meta;
            update_pulse <= 1'b0;

            if (busy) begin
                if (delay_counter == '0) begin
                    led_value    <= pending_value;
                    ack_toggle   <= pending_request;
                    busy         <= 1'b0;
                    update_pulse <= 1'b1;
                end else begin
                    delay_counter <= delay_counter - 1'b1;
                end
            end else if (source_sync[11] != ack_toggle) begin
                pending_value   <= source_sync[9:0];
                pending_request <= source_sync[11];

                if (source_sync[10] && (DEFER_CYCLES > 0)) begin
                    busy <= 1'b1;
                    delay_counter <= COUNTER_WIDTH'(DEFER_CYCLES - 1);
                end else begin
                    led_value    <= source_sync[9:0];
                    ack_toggle   <= source_sync[11];
                    update_pulse <= 1'b1;
                end
            end
        end
    end

endmodule
