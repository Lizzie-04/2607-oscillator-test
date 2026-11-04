module gpio_host_bridge (
    input  logic        clk,
    input  logic        rst,
    inout  wire [15:0]  host_data,
    input  logic [5:0]  host_index,
    input  logic [3:0]  host_opcode,
    input  logic        host_request_toggle,
    input  logic        host_write,

    output logic        request_valid,
    output logic [3:0]  request_opcode,
    output logic [5:0]  request_index,
    output logic [15:0] request_data,
    input  logic        request_done,
    input  logic [15:0] response_data,

    output logic        ack_toggle,
    output logic        pending
);

    logic request_meta;
    logic request_sync;
    logic [15:0] data_meta;
    logic [15:0] data_sync;
    logic [5:0] index_meta;
    logic [5:0] index_sync;
    logic [3:0] opcode_meta;
    logic [3:0] opcode_sync;
    logic write_meta;
    logic write_sync;
    logic request_toggle_latched;
    logic [15:0] last_response;

    assign host_data = host_write ? 16'hzzzz : last_response;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            request_meta <= 1'b0;
            request_sync <= 1'b0;
            data_meta <= '0;
            data_sync <= '0;
            index_meta <= '0;
            index_sync <= '0;
            opcode_meta <= '0;
            opcode_sync <= '0;
            write_meta <= 1'b1;
            write_sync <= 1'b1;
            request_valid <= 1'b0;
            request_opcode <= '0;
            request_index <= '0;
            request_data <= '0;
            request_toggle_latched <= 1'b0;
            ack_toggle <= 1'b0;
            pending <= 1'b0;
            last_response <= '0;
        end else begin
            request_meta <= host_request_toggle;
            request_sync <= request_meta;
            data_meta <= host_data;
            data_sync <= data_meta;
            index_meta <= host_index;
            index_sync <= index_meta;
            opcode_meta <= host_opcode;
            opcode_sync <= opcode_meta;
            write_meta <= host_write;
            write_sync <= write_meta;

            if (!pending && (request_sync != ack_toggle)) begin
                request_toggle_latched <= request_sync;
                request_opcode <= write_sync
                    ? opcode_sync
                    : fpga_v2_protocol_pkg::OP_READ_REGISTER;
                request_index <= index_sync;
                request_data <= write_sync ? data_sync : 16'h0000;
                request_valid <= 1'b1;
                pending <= 1'b1;
            end

            if (pending && request_done) begin
                last_response <= response_data;
                ack_toggle <= request_toggle_latched;
                request_valid <= 1'b0;
                pending <= 1'b0;
            end
        end
    end

endmodule
