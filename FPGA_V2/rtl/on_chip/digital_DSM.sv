module digital_DSM #(
    parameter DATA_WIDTH = 29
)(
    input rst,
    input clk,
    input signed [DATA_WIDTH-1:0] data_in,
    
    output reg dsm_out
);
    reg signed [DATA_WIDTH+10 :0] integrator;
    wire signed [DATA_WIDTH+10 :0] next_integrator;
    wire signed [DATA_WIDTH-1 :0] feedback;
    wire signed [DATA_WIDTH-1 :0] error;

    assign feedback = dsm_out ? (2**(DATA_WIDTH-14)+1) : - (2**(DATA_WIDTH-14) +1); 
   
    assign error = data_in - feedback;
    assign next_integrator = integrator + error;
   
    
    always @(posedge clk or posedge rst) begin
        if (rst) begin
            dsm_out <= 0;
            integrator <= 0;
        end else begin 
            integrator <= next_integrator;
            dsm_out <= next_integrator > 0 ? 1'b1 : 1'b0;
        end
    end
endmodule
