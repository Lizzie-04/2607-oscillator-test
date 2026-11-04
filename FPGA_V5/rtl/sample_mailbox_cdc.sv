module sample_mailbox_cdc #(
    parameter int unsigned DATA_WIDTH = 18
) (
    input  logic                      source_clk,
    input  logic                      source_rst,
    input  logic [DATA_WIDTH-1:0]     source_mu1,
    input  logic [DATA_WIDTH-1:0]     source_mu2,
    input  logic                      source_valid,

    input  logic                      destination_clk,
    input  logic                      destination_rst,
    output logic [DATA_WIDTH-1:0]     destination_mu1,
    output logic [DATA_WIDTH-1:0]     destination_mu2,
    output logic [15:0]               sample_sequence,
    output logic                      sample_toggle
);

    logic source_toggle;
    logic [DATA_WIDTH-1:0] source_mu1_hold;
    logic [DATA_WIDTH-1:0] source_mu2_hold;

    logic toggle_meta;
    logic toggle_sync;
    logic toggle_delay;
    logic [DATA_WIDTH-1:0] mu1_meta;
    logic [DATA_WIDTH-1:0] mu1_sync;
    logic [DATA_WIDTH-1:0] mu2_meta;
    logic [DATA_WIDTH-1:0] mu2_sync;

    always_ff @(posedge source_clk or posedge source_rst) begin
        if (source_rst) begin
            source_toggle <= 1'b0;
            source_mu1_hold <= '0;
            source_mu2_hold <= '0;
        end else if (source_valid) begin
            source_mu1_hold <= source_mu1;
            source_mu2_hold <= source_mu2;
            source_toggle <= ~source_toggle;
        end
    end

    always_ff @(posedge destination_clk or posedge destination_rst) begin
        if (destination_rst) begin
            toggle_meta <= 1'b0;
            toggle_sync <= 1'b0;
            toggle_delay <= 1'b0;
            mu1_meta <= '0;
            mu1_sync <= '0;
            mu2_meta <= '0;
            mu2_sync <= '0;
            destination_mu1 <= '0;
            destination_mu2 <= '0;
            sample_sequence <= '0;
            sample_toggle <= 1'b0;
        end else begin
            toggle_meta <= source_toggle;
            toggle_sync <= toggle_meta;
            toggle_delay <= toggle_sync;
            mu1_meta <= source_mu1_hold;
            mu1_sync <= mu1_meta;
            mu2_meta <= source_mu2_hold;
            mu2_sync <= mu2_meta;

            if (toggle_sync != toggle_delay) begin
                destination_mu1 <= mu1_sync;
                destination_mu2 <= mu2_sync;
                sample_sequence <= sample_sequence + 1'b1;
                sample_toggle <= ~sample_toggle;
            end
        end
    end

endmodule
