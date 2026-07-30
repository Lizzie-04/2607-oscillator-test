module triangular_decimator_runtime #(
    parameter int unsigned OUTPUT_WIDTH = 18,
    parameter int unsigned DEFAULT_LENGTH = 1024,
    parameter int unsigned MIN_LENGTH = 32,
    parameter int unsigned MAX_LENGTH = 4096
) (
    input  logic                      rst,
    input  logic                      sample_clk,
    input  logic                      bs_t,
    input  logic                      bs_c,
    input  logic [15:0]               configured_length_async,

    output logic [OUTPUT_WIDTH-1:0]   mu1,
    output logic [OUTPUT_WIDTH-1:0]   mu2,
    output logic                      valid,
    output logic [15:0]               active_length
);

    localparam int unsigned ACCUMULATOR_WIDTH = 48;

    logic [15:0] length_meta;
    logic [15:0] length_sync;
    logic [15:0] weight;
    logic descending;
    logic bs_t_delayed;
    logic bs_c_delayed;
    logic [ACCUMULATOR_WIDTH-1:0] accumulator_t;
    logic [ACCUMULATOR_WIDTH-1:0] accumulator_c;

    logic [ACCUMULATOR_WIDTH-1:0] sum_t_with_current;
    logic [ACCUMULATOR_WIDTH-1:0] sum_c_with_current;
    logic [31:0] normalization_denominator;
    logic [63:0] numerator_t;
    logic [63:0] numerator_c;
    logic [63:0] normalized_t;
    logic [63:0] normalized_c;

    function automatic logic [15:0] clamp_length(input logic [15:0] value);
        begin
            if (value < MIN_LENGTH) begin
                clamp_length = 16'(MIN_LENGTH);
            end else if (value > MAX_LENGTH) begin
                clamp_length = 16'(MAX_LENGTH);
            end else begin
                clamp_length = value;
            end
        end
    endfunction

    always_comb begin
        sum_t_with_current =
            accumulator_t + (bs_t_delayed ? weight : 16'd0);
        sum_c_with_current =
            accumulator_c + (bs_c_delayed ? weight : 16'd0);
        normalization_denominator = active_length * active_length;
        numerator_t =
            {{(64-ACCUMULATOR_WIDTH){1'b0}}, sum_t_with_current} << 16;
        numerator_c =
            {{(64-ACCUMULATOR_WIDTH){1'b0}}, sum_c_with_current} << 16;
        normalized_t = numerator_t / normalization_denominator;
        normalized_c = numerator_c / normalization_denominator;
    end

    always_ff @(posedge sample_clk or posedge rst) begin
        if (rst) begin
            length_meta <= 16'(DEFAULT_LENGTH);
            length_sync <= 16'(DEFAULT_LENGTH);
            active_length <= 16'(DEFAULT_LENGTH);
            weight <= '0;
            descending <= 1'b0;
            bs_t_delayed <= 1'b0;
            bs_c_delayed <= 1'b0;
            accumulator_t <= '0;
            accumulator_c <= '0;
            mu1 <= '0;
            mu2 <= '0;
            valid <= 1'b0;
        end else begin
            length_meta <= configured_length_async;
            length_sync <= length_meta;
            bs_t_delayed <= bs_t;
            bs_c_delayed <= bs_c;
            valid <= 1'b0;

            accumulator_t <= sum_t_with_current;
            accumulator_c <= sum_c_with_current;

            if (!descending) begin
                if (weight == active_length - 1'b1) begin
                    weight <= active_length;
                    descending <= 1'b1;
                end else begin
                    weight <= weight + 1'b1;
                end
            end else if (weight == 1) begin
                mu1 <= (normalized_t > 65_536)
                    ? OUTPUT_WIDTH'(65_536)
                    : normalized_t[OUTPUT_WIDTH-1:0];
                mu2 <= (normalized_c > 65_536)
                    ? OUTPUT_WIDTH'(65_536)
                    : normalized_c[OUTPUT_WIDTH-1:0];
                valid <= 1'b1;
                accumulator_t <= '0;
                accumulator_c <= '0;
                weight <= '0;
                descending <= 1'b0;
                active_length <= clamp_length(length_sync);
            end else begin
                weight <= weight - 1'b1;
            end
        end
    end

endmodule
