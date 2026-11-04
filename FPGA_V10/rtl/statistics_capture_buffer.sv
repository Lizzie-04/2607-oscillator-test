module statistics_capture_buffer #(
    parameter int unsigned DATA_WIDTH = 18,
    parameter int unsigned SAMPLE_COUNT = 100,
    parameter int unsigned MEMORY_DEPTH = 128,
    parameter int unsigned ADDRESS_WIDTH = 7
) (
    input  logic                          source_clk,
    input  logic                          source_rst,
    input  logic                          source_start,
    input  logic                          source_abort,
    input  logic                          source_sample_valid,
    input  logic [DATA_WIDTH-1:0]         source_mu1,
    input  logic [DATA_WIDTH-1:0]         source_mu2,
    output logic                          source_busy,
    output logic                          source_done,
    output logic                          source_completion_toggle,
    output logic [15:0]                   source_captured_count,

    input  logic                          destination_clk,
    input  logic                          destination_read_enable,
    input  logic [ADDRESS_WIDTH-1:0]      destination_read_address,
    output logic [(2*DATA_WIDTH)-1:0]     destination_read_data
);

    localparam int unsigned PACKED_WIDTH = 2 * DATA_WIDTH;

    (* ramstyle = "M10K" *)
    logic [PACKED_WIDTH-1:0] memory [0:MEMORY_DEPTH-1];

    logic armed;
    logic [ADDRESS_WIDTH-1:0] write_address;

`ifndef SYNTHESIS
    initial begin
        if (SAMPLE_COUNT < 1 || SAMPLE_COUNT > MEMORY_DEPTH) begin
            $error("statistics_capture_buffer SAMPLE_COUNT exceeds memory");
        end
        if ((1 << ADDRESS_WIDTH) < MEMORY_DEPTH) begin
            $error("statistics_capture_buffer ADDRESS_WIDTH is too small");
        end
    end
`endif

    // A start can arrive part-way through a triangular window.  The first
    // valid result after start belongs to that already-running window, so it
    // is discarded.  The following SAMPLE_COUNT results are complete,
    // consecutive windows that all started after the statistics request.
    always_ff @(posedge source_clk or posedge source_rst) begin
        if (source_rst) begin
            armed <= 1'b0;
            write_address <= '0;
            source_busy <= 1'b0;
            source_done <= 1'b0;
            source_completion_toggle <= 1'b0;
            source_captured_count <= '0;
        end else if (source_abort) begin
            armed <= 1'b0;
            write_address <= '0;
            source_busy <= 1'b0;
            source_done <= 1'b0;
            source_captured_count <= '0;
        end else if (source_start) begin
            armed <= 1'b1;
            write_address <= '0;
            source_busy <= 1'b1;
            source_done <= 1'b0;
            source_captured_count <= '0;
        end else if (source_busy && source_sample_valid) begin
            if (armed) begin
                armed <= 1'b0;
            end else begin
                memory[write_address] <= {source_mu2, source_mu1};
                source_captured_count <= source_captured_count + 1'b1;

                if (write_address == SAMPLE_COUNT - 1) begin
                    source_busy <= 1'b0;
                    source_done <= 1'b1;
                    source_completion_toggle <=
                        ~source_completion_toggle;
                end else begin
                    write_address <= write_address + 1'b1;
                end
            end
        end
    end

    // Independent read port for the 50 MHz host-command clock domain.
    always_ff @(posedge destination_clk) begin
        if (destination_read_enable) begin
            destination_read_data <= memory[destination_read_address];
        end
    end

endmodule
