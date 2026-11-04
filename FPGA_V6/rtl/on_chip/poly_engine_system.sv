module poly_engine_system #(
    parameter DATA_WIDTH = 29
)(
    input rst,
    input clk,
    input valid_in,
    input signed [18-1:0] data_in,
    input signed [DATA_WIDTH-1:0] a6,
    input signed [DATA_WIDTH-1:0] a5,
    input signed [DATA_WIDTH-1:0] a4,
    input signed [DATA_WIDTH-1:0] a3,
    input signed [DATA_WIDTH-1:0] a2,
    input signed [DATA_WIDTH-1:0] a1,
    input signed [DATA_WIDTH-1:0] a0,
    output reg valid_out,
    output reg signed[DATA_WIDTH-1:0] poly_out 
);

    reg signed [2*DATA_WIDTH-1:0] mult_out;
    reg signed [DATA_WIDTH-1:0] poly;
    reg signed [DATA_WIDTH-1:0] data;
    reg [4:0] state;

    reg signed [DATA_WIDTH-1:0] ai6;
    reg signed [DATA_WIDTH-1:0] ai5;
    reg signed [DATA_WIDTH-1:0] ai4;
    reg signed [DATA_WIDTH-1:0] ai3;
    reg signed [DATA_WIDTH-1:0] ai2;
    reg signed [DATA_WIDTH-1:0] ai1;
    reg signed [DATA_WIDTH-1:0] ai0;

	always @(posedge clk or posedge rst) begin
        if (rst) begin
            valid_out <= 0;
            poly_out <= 0;
            mult_out <= 0;
            poly <= 0;
            data <= 0;
            state <= 0;
            ai6 <= 0;
            ai5 <= 0;
            ai4 <= 0;
            ai3 <= 0;
            ai2 <= 0;
            ai1 <= 0;
            ai0 <= 0;

        end else begin
            case (state)
                0:begin
                    valid_out <= 0;
                    if (valid_in) begin
                        poly <= { 11'b00000000000, data_in };
                        data <= { 11'b00000000000, data_in };
                        state <= 1;
                        ai6 <= a6;
                        ai5 <= a5;
                        ai4 <= a4;
                        ai3 <= a3;
                        ai2 <= a2;
                        ai1 <= a1;
                        ai0 <= a0;
                    end
                end
                1:begin
                        mult_out <= poly * ai6;
                        state <= 2;
                    end
                2:begin
                    poly <= $signed(mult_out[44:16]) + ai5;
                    state <= 3;
                end
                3:begin
                    mult_out <= poly * data;
                    state <= 4;
                end
                4:begin
                    poly <= $signed(mult_out[44:16]) + ai4;
                    state <= 5;
                end
                5:begin
                    mult_out <= poly * data;
                    state <= 6;
                end
                6:begin
                    poly <= $signed(mult_out[44:16]) + ai3;
                    state <= 7;
                end
                7:begin
                    mult_out <= poly * data;
                    state <= 8;
                end
                8:begin
                    poly <= $signed(mult_out[44:16]) + ai2;
                    state <= 9;
                end
                9:begin
                    mult_out <= poly * data;
                    state <= 10;
                end
                10:begin
                    poly <= $signed(mult_out[44:16]) + ai1;
                    state <= 11;
                end
                11:begin
                    mult_out <= poly * data;
                    state <= 12;
                end
                12:begin
                    poly <= $signed(mult_out[44:16]) + ai0;
                    state <= 13;
                end
                13:begin
                    valid_out <= 1;
                    poly_out <= poly;
                    state <= 0;
                end

                default: begin
              state <= 0;
                end
            endcase
        end
    end

endmodule
