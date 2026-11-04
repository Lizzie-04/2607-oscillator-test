module sar_calibrator #(
    parameter integer DATA_WIDTH = 29,
    parameter integer FRACTION_BITS = 16
)(
    input  wire                         rst,
    input  wire                         clk,

    // All command inputs must be synchronous to clk.
    input  wire                         start,
    input  wire                         compare_valid,

    // One comparison bit is consumed for each SAR step:
    //   1: measured frequency is too low, keep/increase the trial code.
    //   0: measured frequency is too high, clear/decrease the trial code.
    input  wire                         bit_in,

    // Same signed fixed-point format as adder inputs A and B.
    // Only the non-negative interval from 0.0 to 1.0 is generated.
    output reg signed [DATA_WIDTH-1:0]  code,
    output reg                          trial_valid,
    output wire                         compare_ready,
    output reg                          busy,
    output reg                          done,
    output reg  [7:0]                   step_index
);

    function integer clog2;
        input integer value;
        integer temporary;
        begin
            temporary = value - 1;
            clog2 = 0;
            while (temporary > 0) begin
                temporary = temporary >> 1;
                clog2 = clog2 + 1;
            end
        end
    endfunction

    localparam integer BIT_INDEX_WIDTH =
        (FRACTION_BITS <= 1) ? 1 : clog2(FRACTION_BITS);

    localparam signed [DATA_WIDTH-1:0] ONE_LSB =
        {{(DATA_WIDTH-1){1'b0}}, 1'b1};
    localparam signed [DATA_WIDTH-1:0] FULL_SCALE =
        ONE_LSB <<< FRACTION_BITS;
    localparam signed [DATA_WIDTH-1:0] HALF_SCALE =
        ONE_LSB <<< (FRACTION_BITS - 1);

    reg [BIT_INDEX_WIDTH-1:0] current_bit;

    wire signed [DATA_WIDTH-1:0] current_weight;
    wire signed [DATA_WIDTH-1:0] next_weight;

    assign current_weight = ONE_LSB <<< current_bit;
    assign next_weight =
        (current_bit == 0)
            ? {DATA_WIDTH{1'b0}}
            : (ONE_LSB <<< (current_bit - 1'b1));

    assign compare_ready = busy;

`ifndef SYNTHESIS
    initial begin
        if (FRACTION_BITS < 1) begin
            $error("sar_calibrator FRACTION_BITS must be at least 1");
        end
        if (DATA_WIDTH < (FRACTION_BITS + 2)) begin
            $error(
                "sar_calibrator DATA_WIDTH must include sign, integer and fractional bits"
            );
        end
    end
`endif

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            code        <= {DATA_WIDTH{1'b0}};
            trial_valid <= 1'b0;
            busy        <= 1'b0;
            done        <= 1'b0;
            step_index  <= 8'd0;
            current_bit <= {BIT_INDEX_WIDTH{1'b0}};
        end else if (start) begin
            // First trial: 0.5. A new start also aborts an unfinished search.
            code        <= HALF_SCALE;
            trial_valid <= 1'b1;
            busy        <= 1'b1;
            done        <= 1'b0;
            step_index  <= 8'd0;
            current_bit <= FRACTION_BITS - 1;
        end else if (busy && compare_valid) begin
            if (current_bit == 0) begin
                // The current LSB is the final trial bit.
                if (bit_in) begin
                    // All fractional bits high and still too low means that
                    // the inclusive upper endpoint 1.0 is the final result.
                    if (code == (FULL_SCALE - ONE_LSB)) begin
                        code <= FULL_SCALE;
                    end
                end else begin
                    code <= code & ~current_weight;
                end

                trial_valid <= 1'b0;
                busy        <= 1'b0;
                done        <= 1'b1;
                step_index  <= step_index + 1'b1;
            end else begin
                // Keep or clear the current trial bit according to the single
                // comparison result, then set the next-lower trial bit.
                if (bit_in) begin
                    code <= code | next_weight;
                end else begin
                    code <= (code & ~current_weight) | next_weight;
                end

                trial_valid <= 1'b1;
                current_bit <= current_bit - 1'b1;
                step_index  <= step_index + 1'b1;
            end
        end
    end

endmodule
