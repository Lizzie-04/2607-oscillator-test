module clock_generator_2mhz (
    input  logic clk_50mhz,
    input  logic rst,
    output logic clk_2mhz
);

    logic [4:0] divider_count;

    always_ff @(posedge clk_50mhz or posedge rst) begin
        if (rst) begin
            divider_count <= '0;
            clk_2mhz <= 1'b0;
        end else if (divider_count == 24) begin
            divider_count <= '0;
            clk_2mhz <= 1'b1;
        end else begin
            divider_count <= divider_count + 1'b1;
            clk_2mhz <= (divider_count < 11);
        end
    end

    // Registered output: 12 high clocks and 13 low clocks, exactly 2 MHz
    // average frequency, without a combinational clock waveform.

endmodule
