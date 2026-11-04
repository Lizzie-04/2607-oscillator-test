module filter_up #(
    parameter DATA_WIDTH = 29
)(
    input rst,
    input clk_DATA,
    input clk_DSM,
    input valid,
    input signed [DATA_WIDTH-1:0] data_in,
    output reg signed [DATA_WIDTH-1:0] data_out,
    output reg signed [DATA_WIDTH-1:0] data   
     
);
  
   
    always @(posedge clk_DATA or posedge rst) begin
        if (rst) begin
            data <= 0;
        end else begin
            if (valid) begin
                data <= data_in;
            end 
        end
    end

    always @(posedge clk_DSM or posedge rst) begin
        if (rst) begin
            data_out <= 0;
        end else begin
            data_out <= data;
        end
    end

endmodule
