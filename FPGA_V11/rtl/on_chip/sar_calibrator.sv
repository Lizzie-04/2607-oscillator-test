module sar_calibrator #(
    parameter integer DATA_WIDTH = 37,
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
    // The inclusive search interval is -0.5 to +0.5.
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

    localparam [FRACTION_BITS:0] ONE_LSB_OFFSET =
        {{FRACTION_BITS{1'b0}}, 1'b1};
    localparam [FRACTION_BITS:0] FULL_SCALE_OFFSET =
        ONE_LSB_OFFSET << FRACTION_BITS;
    localparam [FRACTION_BITS:0] HALF_SCALE_OFFSET =
        ONE_LSB_OFFSET << (FRACTION_BITS - 1);

    reg [BIT_INDEX_WIDTH-1:0] current_bit;
    reg [FRACTION_BITS:0] offset_code;

    wire [FRACTION_BITS:0] current_weight;
    wire [FRACTION_BITS:0] next_weight;
    wire [FRACTION_BITS:0] next_offset;

    assign current_weight = ONE_LSB_OFFSET << current_bit;
    assign next_weight =
        (current_bit == 0)
            ? {(FRACTION_BITS+1){1'b0}}
            : (ONE_LSB_OFFSET << (current_bit - 1'b1));
    assign next_offset = bit_in
        ? (offset_code | next_weight)
        : ((offset_code & ~current_weight) | next_weight);

    function signed [DATA_WIDTH-1:0] offset_to_centered_code;
        input [FRACTION_BITS:0] offset_value;
        reg signed [DATA_WIDTH:0] offset_extended;
        reg signed [DATA_WIDTH:0] half_extended;
        begin
            offset_extended = '0;
            offset_extended[FRACTION_BITS:0] = offset_value;
            half_extended = '0;
            half_extended[FRACTION_BITS:0] = HALF_SCALE_OFFSET;
            offset_to_centered_code = offset_extended - half_extended;
        end
    endfunction

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
            offset_code <= {(FRACTION_BITS+1){1'b0}};
        end else if (start) begin
            // The midpoint of [-0.5, +0.5] is the first trial.  A new start
            // also aborts an unfinished search.
            code        <= {DATA_WIDTH{1'b0}};
            trial_valid <= 1'b1;
            busy        <= 1'b1;
            done        <= 1'b0;
            step_index  <= 8'd0;
            current_bit <= FRACTION_BITS - 1;
            offset_code <= HALF_SCALE_OFFSET;
        end else if (busy && compare_valid) begin
            if (current_bit == 0) begin
                // The current LSB is the final trial bit.
                if (bit_in) begin
                    // All offset bits high and still too low selects the
                    // inclusive centered upper endpoint +0.5.
                    if (offset_code == (FULL_SCALE_OFFSET - ONE_LSB_OFFSET)) begin
                        offset_code <= FULL_SCALE_OFFSET;
                        code <= offset_to_centered_code(FULL_SCALE_OFFSET);
                    end
                end else begin
                    offset_code <= offset_code & ~current_weight;
                    code <= offset_to_centered_code(
                        offset_code & ~current_weight
                    );
                end

                trial_valid <= 1'b0;
                busy        <= 1'b0;
                done        <= 1'b1;
                step_index  <= step_index + 1'b1;
            end else begin
                // Search the unsigned [0, 1] offset, then translate it to the
                // externally visible centered interval [-0.5, +0.5].
                offset_code <= next_offset;
                code <= offset_to_centered_code(next_offset);

                trial_valid <= 1'b1;
                current_bit <= current_bit - 1'b1;
                step_index  <= step_index + 1'b1;
            end
        end
    end

endmodule
