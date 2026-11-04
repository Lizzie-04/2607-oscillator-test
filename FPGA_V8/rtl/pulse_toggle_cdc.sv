module pulse_toggle_cdc (
    input  logic source_clk,
    input  logic source_rst,
    input  logic source_pulse,
    input  logic destination_clk,
    input  logic destination_rst,
    output logic destination_pulse
);

    logic source_toggle;
    logic destination_meta;
    logic destination_sync;
    logic destination_delay;

    always_ff @(posedge source_clk or posedge source_rst) begin
        if (source_rst) begin
            source_toggle <= 1'b0;
        end else if (source_pulse) begin
            source_toggle <= ~source_toggle;
        end
    end

    always_ff @(posedge destination_clk or posedge destination_rst) begin
        if (destination_rst) begin
            destination_meta <= 1'b0;
            destination_sync <= 1'b0;
            destination_delay <= 1'b0;
        end else begin
            destination_meta <= source_toggle;
            destination_sync <= destination_meta;
            destination_delay <= destination_sync;
        end
    end

    assign destination_pulse = destination_sync ^ destination_delay;

endmodule
