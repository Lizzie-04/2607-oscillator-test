module divider #(
    parameter DATA_WIDTH = 18
)(
    input rst,
    input clk,
    input valid_in,
    input [DATA_WIDTH-1:0] data_in,
    input  [DATA_WIDTH-1:0] A,

    output reg valid_out,
    output reg [DATA_WIDTH-1:0] poly_out 
);
    reg  [DATA_WIDTH+11:0] mult_out;
    reg  [DATA_WIDTH-1:0] poly;
    reg  [DATA_WIDTH-1:0] A_reg;
    reg  [DATA_WIDTH-1:0] B_reg;
    reg [2:0] state;


	always @(posedge clk or posedge rst) begin
        if (rst) begin
            valid_out <= 0;
            poly_out <= 0;
            mult_out <= 0;
            poly <= 0;
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
                      poly <= B_reg+A_reg ;
                      state <= 2;
                end
                2:begin
                      mult_out <= {A_reg, 12'b0};
                      state <= 3;
                end
                3:begin
                      mult_out <= mult_out/poly[DATA_WIDTH-1:4] ;
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
