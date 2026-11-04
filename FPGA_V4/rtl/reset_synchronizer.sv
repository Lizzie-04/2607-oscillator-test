module reset_synchronizer (
    input  logic clk,
    input  logic async_reset,
    output logic sync_reset
);

    logic reset_meta;

    always_ff @(posedge clk or posedge async_reset) begin
        if (async_reset) begin
            reset_meta <= 1'b1;
            sync_reset <= 1'b1;
        end else begin
            reset_meta <= 1'b0;
            sync_reset <= reset_meta;
        end
    end

endmodule
