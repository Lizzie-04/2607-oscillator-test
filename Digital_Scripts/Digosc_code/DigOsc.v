module DigOsc#(
	parameter DATA_WIDTH1 = 18,
	parameter DATA_WIDTH2 = 29
)(
	input rst,
	input clk,
	input clk_DSM,
	input BS0,
	input BS1,
	input polarity,

	input SAR_enable,
	input SAR_start,
	input SAR_compare_valid,
	input SAR_bit_in,
	
	input [DATA_WIDTH2-1:0] batch_a6,
	input [DATA_WIDTH2-1:0] batch_a5,
	input [DATA_WIDTH2-1:0] batch_a4,
	input [DATA_WIDTH2-1:0] batch_a3,
	input [DATA_WIDTH2-1:0] batch_a2,
	input [DATA_WIDTH2-1:0] batch_a1,
	input [DATA_WIDTH2-1:0] batch_a0,

        input [DATA_WIDTH2-1:0] poly_a7,
	input [DATA_WIDTH2-1:0] poly_a6,
        input [DATA_WIDTH2-1:0] poly_a5,
	input [DATA_WIDTH2-1:0] poly_a4,
	input [DATA_WIDTH2-1:0] poly_a3,
	input [DATA_WIDTH2-1:0] poly_a2,
	input [DATA_WIDTH2-1:0] poly_a1,
	input [DATA_WIDTH2-1:0] poly_a0,
        input [DATA_WIDTH1-1:0] alpha,

	output DSM,

        output [DATA_WIDTH1-1:0] div_out_test,
	output [DATA_WIDTH2-1:0] poly_system_result_test,
	output [DATA_WIDTH2-1:0] poly_freq_result_test,
	output [DATA_WIDTH2-1:0] phase_diff_test,
	output phase_diff_valid_out,
	output [DATA_WIDTH2-1:0] difference_test,
	output [DATA_WIDTH2-1:0] calibrated_error_test,
	output signed [DATA_WIDTH2-1:0] SAR_code,
	output SAR_trial_valid,
	output SAR_compare_ready,
	output SAR_busy,
	output SAR_done,
	output [7:0] SAR_step_index,
	output [DATA_WIDTH2-1:0] UP,
	output [DATA_WIDTH2-1:0] data
   
);

	localparam integer DATA_FRACTION_BITS = 16;

	wire [DATA_WIDTH1-1:0] Dec_out0, Dec_out1;
	wire Dec_valid0, Dec_valid1;

	wire [DATA_WIDTH1-1:0] div_out;
	wire div_valid;

	wire [DATA_WIDTH2-1:0] poly_system_result;
	wire poly_system_valid;
	
	wire [DATA_WIDTH2-1:0] poly_freq_result;
	wire poly_freq_valid;

	wire [DATA_WIDTH2-1:0] phase_diff;
        wire phase_diff_valid;
        wire [DATA_WIDTH2-1:0] difference;
        wire [DATA_WIDTH2-1:0] calibrated_error;

        wire signed [DATA_WIDTH2-1:0] sar_code_internal;
        wire signed [DATA_WIDTH2-1:0] sar_correction;

   
        wire [DATA_WIDTH2-1:0] Up_out;
        wire [DATA_WIDTH2-1:0] data_in;

	wire dsm_out;

	assign sar_correction = SAR_enable
		? sar_code_internal
		: {DATA_WIDTH2{1'b0}};

	sar_calibrator #(
		.DATA_WIDTH(DATA_WIDTH2),
		.FRACTION_BITS(DATA_FRACTION_BITS)
	) sar_calibration (
		.rst(rst),
		.clk(clk),
		.start(SAR_start),
		.compare_valid(SAR_compare_valid),
		.bit_in(SAR_bit_in),
		.code(sar_code_internal),
		.trial_valid(SAR_trial_valid),
		.compare_ready(SAR_compare_ready),
		.busy(SAR_busy),
		.done(SAR_done),
		.step_index(SAR_step_index)
	);

	assign SAR_code = sar_code_internal;
	
	
	filter_down #(
		.DATA_WIDTH(DATA_WIDTH1)
	) fd0(
		.rst(rst),
		.clk(clk),
		.bs_in(BS0),
		.out(Dec_out0),
		.valid(Dec_valid0)
	);
	
	filter_down #(
		.DATA_WIDTH(DATA_WIDTH1)
	) fd1(
		.rst(rst),
		.clk(clk),
		.bs_in(BS1),
		.out(Dec_out1),
		.valid(Dec_valid1)
	);
	

	divider #(
		.DATA_WIDTH(DATA_WIDTH1)
	) div(
		.rst(rst),
		.clk(clk),
		.valid_in(Dec_valid0),
		.data_in(Dec_out0),
		.A(alpha),
		.valid_out(div_valid),
		.poly_out(div_out)
	);
	assign div_out_test = div_out;

	poly_engine_system #(
		.DATA_WIDTH(DATA_WIDTH2)
	) poly_system(
		.rst(rst),
		.clk(clk),
		.valid_in(div_valid),
		.data_in(div_out),
		.a6(batch_a6),
		.a5(batch_a5),
		.a4(batch_a4),
		.a3(batch_a3),
		.a2(batch_a2),
		.a1(batch_a1),
		.a0(batch_a0),
		.valid_out(poly_system_valid),
		.poly_out(poly_system_result)
	);
	assign poly_system_result_test = poly_system_result;

	poly_engine_freq #(
		.DATA_WIDTH(DATA_WIDTH2)
	) poly_freq(
		.rst(rst),
		.clk(clk),
		.valid_in(Dec_valid1),
		.data_in(Dec_out1),
                .a7(poly_a7),
		.a6(poly_a6),
		.a5(poly_a5),
		.a4(poly_a4),
		.a3(poly_a3),
		.a2(poly_a2),
		.a1(poly_a1),
		.a0(poly_a0),
		.valid_out(poly_freq_valid),
		.poly_out(poly_freq_result)
	);
	
assign poly_freq_result_test = poly_freq_result;

	adder #(
		.DATA_WIDTH(DATA_WIDTH2)
	) adder(
		.rst(rst),
		.clk(clk),
		.A(poly_system_result),
		.B(poly_freq_result),
		.calibration(sar_correction),
		.valid_in(poly_system_valid),
		.polar(polarity),
		.D(phase_diff),
		.F(difference),
		.calibrated_error(calibrated_error),
		.valid_out(phase_diff_valid)
	);
	
assign phase_diff_test = phase_diff;
assign difference_test = difference;
assign calibrated_error_test = calibrated_error;
assign phase_diff_valid_out = phase_diff_valid;

    filter_up #(
		.DATA_WIDTH(DATA_WIDTH2)
	) fu(
		.rst(rst),
		.clk_DATA(clk),
		.clk_DSM(clk_DSM),
		.valid(phase_diff_valid),
		.data_in(phase_diff),
		.data_out(Up_out),
		.data(data_in)
	);
 assign UP = Up_out;
 assign data = data_in;

	digital_DSM #(
		.DATA_WIDTH(DATA_WIDTH2)
	) dsm(
		.rst(rst),
		.clk(clk_DSM),
		.data_in(Up_out),
		.dsm_out(dsm_out)
	);
   assign DSM = dsm_out;
   
endmodule
