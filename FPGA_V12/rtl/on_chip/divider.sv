module divider #(
    parameter DATA_WIDTH = 18,
    parameter COEFFICIENT_WIDTH = 37
)(
    input rst,
    input clk,
    input valid_in,
    input [DATA_WIDTH-1:0] data_in,
    input signed [COEFFICIENT_WIDTH-1:0] A,

    output reg valid_out,
    output reg signed [DATA_WIDTH-1:0] poly_out 
);
    reg signed [COEFFICIENT_WIDTH+11:0] mult_out;
    reg signed [COEFFICIENT_WIDTH:0] denominator;
    reg signed [COEFFICIENT_WIDTH-1:0] A_reg;
    reg  [DATA_WIDTH-1:0] B_reg;
    reg [2:0] state;


	always @(posedge clk or posedge rst) begin
        if (rst) begin
            valid_out <= 0;
            poly_out <= 0;
            mult_out <= 0;
            denominator <= 0;
            state <= 0;
            A_reg <= 0;
            B_reg <= 0;
        
        end else begin
            case (state)
                0:begin
                    valid_out <= 0;
                    if (valid_in) begin
                        A_reg <= A;
                        B_reg <= data_in;
                        state <= 1;
                    end else begin
                        state <= 0;
                    end
                end
                 1:begin
                      denominator <=
                          {A_reg[COEFFICIENT_WIDTH-1], A_reg} +
                          $signed({1'b0, {
                              (COEFFICIENT_WIDTH-DATA_WIDTH){1'b0}
                          }, B_reg});
                      state <= 2;
                end
                2:begin
                      // Concatenation deliberately widens before shifting;
                      // a bare SystemVerilog shift would retain A_reg's
                      // 37-bit expression width and discard upper bits.
                      mult_out <= {A_reg, 12'b0};
                      state <= 3;
                end
                3:begin
                      if ($signed(denominator[COEFFICIENT_WIDTH:4]) == 0) begin
                          mult_out <= '0;
                      end else begin
                          mult_out <= mult_out /
                              $signed(denominator[COEFFICIENT_WIDTH:4]);
                      end
                      state <= 4;
                end
                
                4:begin
                        poly_out <= mult_out[DATA_WIDTH-1:0];
                        valid_out <= 1;
                         state <= 0;
                end

              default: begin
              state <= 0;
                end
            
            endcase
        end
    end

endmodule
