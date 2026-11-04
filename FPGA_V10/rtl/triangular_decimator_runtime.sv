module triangular_decimator_runtime #(
    parameter int unsigned OUTPUT_WIDTH = 18,
    parameter int unsigned DEFAULT_LENGTH = 1024,
    parameter int unsigned MIN_LENGTH = 1024,
    parameter int unsigned MAX_LENGTH = 50000
) (
    input  logic                      rst_sample,
    input  logic                      clk_sample,
    input  logic                      rst_data,
    input  logic                      clk_data,
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
    logic [15:0] active_length_sample;
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

    logic [OUTPUT_WIDTH-1:0] mu1_sample_hold;
    logic [OUTPUT_WIDTH-1:0] mu2_sample_hold;
    logic result_toggle_sample;

    logic result_toggle_meta;
    logic result_toggle_sync;
    logic result_toggle_delay;
    logic [OUTPUT_WIDTH-1:0] mu1_meta;
    logic [OUTPUT_WIDTH-1:0] mu1_sync;
    logic [OUTPUT_WIDTH-1:0] mu2_meta;
    logic [OUTPUT_WIDTH-1:0] mu2_sync;

    logic [15:0] active_length_meta;
    logic [15:0] active_length_sync;

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
        normalization_denominator =
            active_length_sample * active_length_sample;
        numerator_t =
            {{(64-ACCUMULATOR_WIDTH){1'b0}}, sum_t_with_current} << 16;
        numerator_c =
            {{(64-ACCUMULATOR_WIDTH){1'b0}}, sum_c_with_current} << 16;
        normalized_t = numerator_t / normalization_denominator;
        normalized_c = numerator_c / normalization_denominator;
    end

    // clk_sample is the external FS reference.  Only the BS sampling and
    // triangular accumulators advance on this clock, matching the
    // clk_SAMPLE role in the supplied filter_down_two_channel reference.
    always_ff @(posedge clk_sample or posedge rst_sample) begin
        if (rst_sample) begin
            length_meta <= 16'(DEFAULT_LENGTH);
            length_sync <= 16'(DEFAULT_LENGTH);
            active_length_sample <= 16'(DEFAULT_LENGTH);
            weight <= '0;
            descending <= 1'b0;
            bs_t_delayed <= 1'b0;
            bs_c_delayed <= 1'b0;
            accumulator_t <= '0;
            accumulator_c <= '0;
            mu1_sample_hold <= '0;
            mu2_sample_hold <= '0;
            result_toggle_sample <= 1'b0;
        end else begin
            length_meta <= configured_length_async;
            length_sync <= length_meta;
            bs_t_delayed <= bs_t;
            bs_c_delayed <= bs_c;
            accumulator_t <= sum_t_with_current;
            accumulator_c <= sum_c_with_current;

            if (!descending) begin
                if (weight == active_length_sample - 1'b1) begin
                    weight <= active_length_sample;
                    descending <= 1'b1;
                end else begin
                    weight <= weight + 1'b1;
                end
            end else if (weight == 1) begin
                mu1_sample_hold <= (normalized_t > 65_536)
                    ? OUTPUT_WIDTH'(65_536)
                    : normalized_t[OUTPUT_WIDTH-1:0];
                mu2_sample_hold <= (normalized_c > 65_536)
                    ? OUTPUT_WIDTH'(65_536)
                    : normalized_c[OUTPUT_WIDTH-1:0];
                result_toggle_sample <= ~result_toggle_sample;
                accumulator_t <= '0;
                accumulator_c <= '0;
                weight <= '0;
                descending <= 1'b0;
                active_length_sample <= clamp_length(length_sync);
            end else begin
                weight <= weight - 1'b1;
            end
        end
    end

    // Completed-window values are held in the FS domain until the next
    // window.  Synchronizing the toggle first and delaying event detection
    // gives the multi-bit data two clk_data cycles to settle before capture.
    always_ff @(posedge clk_data or posedge rst_data) begin
        if (rst_data) begin
            result_toggle_meta <= 1'b0;
            result_toggle_sync <= 1'b0;
            result_toggle_delay <= 1'b0;
            mu1_meta <= '0;
            mu1_sync <= '0;
            mu2_meta <= '0;
            mu2_sync <= '0;
            active_length_meta <= 16'(DEFAULT_LENGTH);
            active_length_sync <= 16'(DEFAULT_LENGTH);
            active_length <= 16'(DEFAULT_LENGTH);
            mu1 <= '0;
            mu2 <= '0;
            valid <= 1'b0;
        end else begin
            result_toggle_meta <= result_toggle_sample;
            result_toggle_sync <= result_toggle_meta;
            result_toggle_delay <= result_toggle_sync;
            mu1_meta <= mu1_sample_hold;
            mu1_sync <= mu1_meta;
            mu2_meta <= mu2_sample_hold;
            mu2_sync <= mu2_meta;
            active_length_meta <= active_length_sample;
            active_length_sync <= active_length_meta;
            active_length <= active_length_sync;
            valid <= 1'b0;

            if (result_toggle_sync != result_toggle_delay) begin
                mu1 <= mu1_sync;
                mu2 <= mu2_sync;
                valid <= 1'b1;
            end
        end
    end

endmodule
