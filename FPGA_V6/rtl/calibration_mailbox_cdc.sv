module calibration_mailbox_cdc #(
    parameter int unsigned DATA_WIDTH = 29
) (
    input  logic                         source_clk,
    input  logic                         source_rst,
    input  logic signed [DATA_WIDTH-1:0] source_poly_freq,
    input  logic signed [DATA_WIDTH-1:0] source_poly_system,
    input  logic signed [DATA_WIDTH-1:0] source_sar_correction,
    input  logic signed [DATA_WIDTH-1:0] source_dsm_input,
    input  logic                         source_override_enabled,
    input  logic                         source_valid,

    input  logic                         destination_clk,
    input  logic                         destination_rst,
    output logic signed [DATA_WIDTH-1:0] destination_poly_freq,
    output logic signed [DATA_WIDTH-1:0] destination_poly_system,
    output logic signed [DATA_WIDTH-1:0] destination_sar_correction,
    output logic signed [DATA_WIDTH-1:0] destination_dsm_input,
    output logic                         destination_override_enabled,
    output logic                         destination_valid,
    output logic [15:0]                  sample_sequence
);

    localparam int unsigned PAYLOAD_WIDTH = (4 * DATA_WIDTH) + 1;

    logic source_toggle;
    logic [PAYLOAD_WIDTH-1:0] source_payload_hold;
    logic toggle_meta;
    logic toggle_sync;
    logic toggle_delay;
    logic [PAYLOAD_WIDTH-1:0] payload_meta;
    logic [PAYLOAD_WIDTH-1:0] payload_sync;

    always_ff @(posedge source_clk or posedge source_rst) begin
        if (source_rst) begin
            source_toggle <= 1'b0;
            source_payload_hold <= '0;
        end else if (source_valid) begin
            source_payload_hold <= {
                source_override_enabled,
                source_dsm_input,
                source_sar_correction,
                source_poly_system,
                source_poly_freq
            };
            source_toggle <= ~source_toggle;
        end
    end

    always_ff @(posedge destination_clk or posedge destination_rst) begin
        if (destination_rst) begin
            toggle_meta <= 1'b0;
            toggle_sync <= 1'b0;
            toggle_delay <= 1'b0;
            payload_meta <= '0;
            payload_sync <= '0;
            destination_poly_freq <= '0;
            destination_poly_system <= '0;
            destination_sar_correction <= '0;
            destination_dsm_input <= '0;
            destination_override_enabled <= 1'b0;
            destination_valid <= 1'b0;
            sample_sequence <= '0;
        end else begin
            toggle_meta <= source_toggle;
            toggle_sync <= toggle_meta;
            toggle_delay <= toggle_sync;
            payload_meta <= source_payload_hold;
            payload_sync <= payload_meta;

            if (toggle_sync != toggle_delay) begin
                {
                    destination_override_enabled,
                    destination_dsm_input,
                    destination_sar_correction,
                    destination_poly_system,
                    destination_poly_freq
                } <= payload_sync;
                destination_valid <= 1'b1;
                sample_sequence <= sample_sequence + 1'b1;
            end
        end
    end

endmodule
