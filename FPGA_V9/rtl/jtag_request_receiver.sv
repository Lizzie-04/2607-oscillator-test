module jtag_request_receiver (
    input  logic        clk,
    input  logic        rst,
    input  logic [63:0] source_async,

    output logic        request_valid,
    output logic [3:0]  request_opcode,
    output logic [5:0]  request_index,
    output logic [15:0] request_data,
    input  logic        request_done,
    input  logic [15:0] response_data,

    output logic        ack_toggle,
    output logic        pending,
    output logic [15:0] last_response
);

    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    logic [63:0] source_meta;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    logic [63:0] source_sync;
    logic request_toggle_latched;
    logic [62:0] payload_candidate;
    logic payload_stable_once;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            source_meta <= '0;
            source_sync <= '0;
            request_valid <= 1'b0;
            request_opcode <= '0;
            request_index <= '0;
            request_data <= '0;
            request_toggle_latched <= 1'b0;
            payload_candidate <= '0;
            payload_stable_once <= 1'b0;
            ack_toggle <= 1'b0;
            pending <= 1'b0;
            last_response <= '0;
        end else begin
            source_meta <= source_async;
            source_sync <= source_meta;

            if (!pending && (source_sync[63] != ack_toggle)) begin
                // The source register is a multi-bit asynchronous mailbox.
                // Detect its toggle first, then wait until the complete
                // payload is identical on consecutive CLOCK_50 samples.
                // Latching toggle and payload on the same sample can combine
                // old/new index bits (for example 23 -> 24 can appear as 31).
                request_toggle_latched <= source_sync[63];
                payload_candidate <= source_sync[62:0];
                payload_stable_once <= 1'b0;
                request_valid <= 1'b0;
                pending <= 1'b1;
            end else if (pending && !request_valid) begin
                if ((source_sync[63] != request_toggle_latched) ||
                    (source_sync[62:0] != payload_candidate)) begin
                    request_toggle_latched <= source_sync[63];
                    payload_candidate <= source_sync[62:0];
                    payload_stable_once <= 1'b0;
                end else if (!payload_stable_once) begin
                    payload_stable_once <= 1'b1;
                end else begin
                    request_opcode <= payload_candidate[62:59];
                    request_index <= payload_candidate[58:53];
                    request_data <= payload_candidate[52:37];
                    request_valid <= 1'b1;
                end
            end else if (pending && request_valid && request_done) begin
                last_response <= response_data;
                ack_toggle <= request_toggle_latched;
                request_valid <= 1'b0;
                pending <= 1'b0;
            end
        end
    end

endmodule
