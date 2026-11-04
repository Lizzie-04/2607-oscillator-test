module control_word_unpack (
    input  logic [473:0] serial_bits,

    output logic [17:0] alpha,
    output logic        polarity,
    output logic [28:0] batch_a0,
    output logic [28:0] batch_a1,
    output logic [28:0] batch_a2,
    output logic [28:0] batch_a3,
    output logic [28:0] batch_a4,
    output logic [28:0] batch_a5,
    output logic [28:0] batch_a6,
    output logic [28:0] poly_a0,
    output logic [28:0] poly_a1,
    output logic [28:0] poly_a2,
    output logic [28:0] poly_a3,
    output logic [28:0] poly_a4,
    output logic [28:0] poly_a5,
    output logic [28:0] poly_a6,
    output logic [28:0] poly_a7
);

    logic [473:0] logical_bits;

    function automatic logic [28:0] msb_field29(
        input logic [28:0] field_bits
    );
        int i;
        begin
            for (i = 0; i < 29; i++) begin
                msb_field29[28-i] = field_bits[i];
            end
        end
    endfunction

    function automatic logic [17:0] msb_field18(
        input logic [17:0] field_bits
    );
        int i;
        begin
            for (i = 0; i < 18; i++) begin
                msb_field18[17-i] = field_bits[i];
            end
        end
    endfunction

    genvar bit_number;
    generate
        for (bit_number = 0; bit_number < 474; bit_number++) begin : reverse
            assign logical_bits[bit_number] = serial_bits[473-bit_number];
        end
    endgenerate

    always_comb begin
        alpha = msb_field18(logical_bits[20 +: 18]);

        batch_a0 = msb_field29(logical_bits[38 +: 29]);
        batch_a1 = msb_field29(logical_bits[67 +: 29]);
        batch_a2 = msb_field29(logical_bits[96 +: 29]);
        batch_a3 = msb_field29(logical_bits[125 +: 29]);
        batch_a4 = msb_field29(logical_bits[154 +: 29]);
        batch_a5 = msb_field29(logical_bits[183 +: 29]);
        batch_a6 = msb_field29(logical_bits[212 +: 29]);

        polarity = logical_bits[241];

        poly_a0 = msb_field29(logical_bits[242 +: 29]);
        poly_a1 = msb_field29(logical_bits[271 +: 29]);
        poly_a2 = msb_field29(logical_bits[300 +: 29]);
        poly_a3 = msb_field29(logical_bits[329 +: 29]);
        poly_a4 = msb_field29(logical_bits[358 +: 29]);
        poly_a5 = msb_field29(logical_bits[387 +: 29]);
        poly_a6 = msb_field29(logical_bits[416 +: 29]);
        poly_a7 = msb_field29(logical_bits[445 +: 29]);
    end

endmodule
