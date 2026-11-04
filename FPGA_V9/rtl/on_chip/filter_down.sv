module filter_down #(
	parameter DATA_WIDTH = 18
)(
	input rst,
	input clk,
	input bs_in,
	
	output reg [DATA_WIDTH-1:0] out,
	output reg valid
);
	reg [10:0] counter;
        reg bs;
	reg direction;
	reg [20:0] accumulator;


	always @(posedge clk or posedge rst) begin
		if (rst) begin
			counter <= 0;
			direction <= 0;
			accumulator <= 0;
			valid <= 0;
			out <= 0;
                        bs <= 0;
		end else begin
                        bs <= bs_in;
			accumulator <= accumulator + (bs ? counter : 0);
			if (direction == 0) begin
				counter <= counter + 1;
				valid <= 0;
				if (counter == 1023)
					direction <= 1;
			end else begin
				counter <= counter - 1;
				if (counter == 1) begin
					direction <= 0;
					valid <= 1;
					out <= { 1'b0, accumulator[20:4] };//
                                accumulator <= 0;
				end
			end
		end
		
	end
endmodule
