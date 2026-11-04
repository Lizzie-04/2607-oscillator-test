module altsource_probe #(
    parameter string sld_auto_instance_index = "YES",
    parameter string instance_id = "NONE",
    parameter int probe_width = 1,
    parameter int source_width = 1,
    parameter string source_initial_value = "0",
    parameter string enable_metastability = "NO"
) (
    input  wire [probe_width-1:0] probe,
    output wire [source_width-1:0] source,
    input  wire source_clk,
    input  wire source_ena
);

    assign source = {source_width{1'b0}};

endmodule
