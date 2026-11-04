module digital_DSM #(
    parameter integer DATA_WIDTH = 37,
    parameter integer FRACTION_BITS = 16
)(
    input  wire                         rst,
    input  wire                         clk,
    input  wire signed [DATA_WIDTH-1:0] data_in,

    output reg                          dsm_out
);

    // The signed input represents the centered DSM range.  -0.5 maps to a
    // zero density and +0.5 maps to a one density.  Interior values are
    // translated to an unsigned Q0.FRACTION_BITS density and accumulated
    // modulo 1.0; the carry is the PDM/DSM output.  The bounded phase
    // accumulator cannot grow or wrap outside its intentional modulo range.
    localparam signed [DATA_WIDTH-1:0] HALF_SCALE =
        {{(DATA_WIDTH-FRACTION_BITS){1'b0}},
         1'b1,
         {(FRACTION_BITS-1){1'b0}}};

    reg [FRACTION_BITS-1:0] phase_accumulator;

    wire signed [DATA_WIDTH:0] density_extended;
    wire [FRACTION_BITS:0] phase_sum;

    assign density_extended =
        {data_in[DATA_WIDTH-1], data_in} +
        {HALF_SCALE[DATA_WIDTH-1], HALF_SCALE};
    assign phase_sum =
        {1'b0, phase_accumulator} +
        density_extended[FRACTION_BITS:0];

`ifndef SYNTHESIS
    initial begin
        if (FRACTION_BITS < 1) begin
            $error("digital_DSM FRACTION_BITS must be at least 1");
        end
        if (DATA_WIDTH <= FRACTION_BITS) begin
            $error("digital_DSM DATA_WIDTH must include signed integer bits");
        end
    end
`endif

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            phase_accumulator <= {FRACTION_BITS{1'b0}};
            dsm_out <= 1'b0;
        end else if (data_in <= -HALF_SCALE) begin
            // The lower endpoint and every lower out-of-range value must be
            // all-zero immediately, without residual phase history.
            phase_accumulator <= {FRACTION_BITS{1'b0}};
            dsm_out <= 1'b0;
        end else if (data_in >= HALF_SCALE) begin
            // The upper endpoint and every higher out-of-range value must be
            // all-one immediately, without residual phase history.
            phase_accumulator <= {FRACTION_BITS{1'b0}};
            dsm_out <= 1'b1;
        end else begin
            phase_accumulator <= phase_sum[FRACTION_BITS-1:0];
            dsm_out <= phase_sum[FRACTION_BITS];
        end
    end

endmodule
