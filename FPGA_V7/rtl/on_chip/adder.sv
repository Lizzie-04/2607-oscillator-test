module adder #(
    parameter integer DATA_WIDTH = 29
)(
    input  wire                              rst,
    input  wire                              clk,
    input  wire signed [DATA_WIDTH-1:0]       A,
    input  wire signed [DATA_WIDTH-1:0]       B,
    input  wire signed [DATA_WIDTH-1:0]       calibration,
    input  wire                              valid_in,
    input  wire                              polar,

    output reg  signed [DATA_WIDTH-1:0]       D,
    output reg  signed [DATA_WIDTH-1:0]       F,
    output reg  signed [DATA_WIDTH-1:0]       calibrated_error,
    output reg                               valid_out
);

    reg signed [DATA_WIDTH-1:0] A_reg;
    reg signed [DATA_WIDTH-1:0] B_reg;
    reg signed [DATA_WIDTH-1:0] calibration_reg;
    reg signed [DATA_WIDTH-1:0] D_reg;
    reg signed [DATA_WIDTH-1:0] F_reg;
    reg signed [DATA_WIDTH-1:0] calibrated_error_reg;
    reg                         polar_reg;
    reg                         valid_stage;
    reg                         valid_result;

    wire signed [DATA_WIDTH:0] raw_difference_ext;
    wire signed [DATA_WIDTH+1:0] calibrated_error_ext;
    wire signed [DATA_WIDTH+2:0] raw_difference_for_saturation;
    wire signed [DATA_WIDTH+2:0] calibrated_error_for_saturation;
    wire signed [DATA_WIDTH+2:0] accumulator_ext;

    assign raw_difference_ext =
        {A_reg[DATA_WIDTH-1], A_reg} -
        {B_reg[DATA_WIDTH-1], B_reg};

    assign calibrated_error_ext =
        {{1{raw_difference_ext[DATA_WIDTH]}}, raw_difference_ext} +
        {{2{calibration_reg[DATA_WIDTH-1]}}, calibration_reg};

    assign raw_difference_for_saturation =
        {{2{raw_difference_ext[DATA_WIDTH]}}, raw_difference_ext};

    assign calibrated_error_for_saturation =
        {{1{calibrated_error_ext[DATA_WIDTH+1]}}, calibrated_error_ext};

    assign accumulator_ext =
        polar_reg
            ? ({{3{D_reg[DATA_WIDTH-1]}}, D_reg} -
               calibrated_error_for_saturation)
            : ({{3{D_reg[DATA_WIDTH-1]}}, D_reg} +
               calibrated_error_for_saturation);

    function signed [DATA_WIDTH-1:0] saturate_to_data_width;
        input signed [DATA_WIDTH+2:0] value;
        reg signed [DATA_WIDTH+2:0] maximum_value;
        reg signed [DATA_WIDTH+2:0] minimum_value;
        begin
            maximum_value =
                {3'b000, 1'b0, {(DATA_WIDTH-1){1'b1}}};
            minimum_value =
                {3'b111, 1'b1, {(DATA_WIDTH-1){1'b0}}};

            if (value > maximum_value) begin
                saturate_to_data_width =
                    {1'b0, {(DATA_WIDTH-1){1'b1}}};
            end else if (value < minimum_value) begin
                saturate_to_data_width =
                    {1'b1, {(DATA_WIDTH-1){1'b0}}};
            end else begin
                saturate_to_data_width = value[DATA_WIDTH-1:0];
            end
        end
    endfunction

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            D                <= {DATA_WIDTH{1'b0}};
            F                <= {DATA_WIDTH{1'b0}};
            calibrated_error <= {DATA_WIDTH{1'b0}};
            valid_out        <= 1'b0;
        end else begin
            D                <= D_reg;
            F                <= F_reg;
            calibrated_error <= calibrated_error_reg;
            valid_out        <= valid_result;
        end
    end

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            A_reg                 <= {DATA_WIDTH{1'b0}};
            B_reg                 <= {DATA_WIDTH{1'b0}};
            calibration_reg       <= {DATA_WIDTH{1'b0}};
            D_reg                 <= {DATA_WIDTH{1'b0}};
            F_reg                 <= {DATA_WIDTH{1'b0}};
            calibrated_error_reg  <= {DATA_WIDTH{1'b0}};
            polar_reg             <= 1'b0;
            valid_stage           <= 1'b0;
            valid_result          <= 1'b0;
        end else begin
            valid_stage  <= valid_in;
            valid_result <= valid_stage;

            if (valid_in) begin
                A_reg           <= A;
                B_reg           <= B;
                calibration_reg <= calibration;
                polar_reg       <= polar;
            end

            if (valid_stage) begin
                F_reg <= saturate_to_data_width(
                    raw_difference_for_saturation
                );
                calibrated_error_reg <= saturate_to_data_width(
                    calibrated_error_for_saturation
                );
                D_reg <= saturate_to_data_width(accumulator_ext);
            end
        end
    end

endmodule
