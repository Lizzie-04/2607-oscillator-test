package fpga_v2_protocol_pkg;

    localparam logic [3:0] OP_WRITE_CONTROL_WORD = 4'h1;
    localparam logic [3:0] OP_COMMIT_AND_START   = 4'h2;
    localparam logic [3:0] OP_SET_DECIMATION    = 4'h3;
    localparam logic [3:0] OP_SAR_START         = 4'h4;
    localparam logic [3:0] OP_SAR_COMPARE       = 4'h5;
    localparam logic [3:0] OP_CLEAR_ERROR       = 4'h6;
    localparam logic [3:0] OP_STATISTICS_START  = 4'h7;
    localparam logic [3:0] OP_READ_REGISTER     = 4'h8;
    localparam logic [3:0] OP_STATISTICS_READ   = 4'h9;
    localparam logic [3:0] OP_SET_CONTROL_SAMPLES = 4'ha;
    localparam logic [3:0] OP_SET_DSM_OVERRIDE  = 4'hb;
    localparam logic [3:0] OP_LATCH_LIVE_SAMPLE = 4'hc;

    localparam logic [7:0] ERROR_NONE                = 8'h00;
    localparam logic [7:0] ERROR_CONTROL_INDEX       = 8'h01;
    localparam logic [7:0] ERROR_CONTROL_INCOMPLETE  = 8'h02;
    localparam logic [7:0] ERROR_DRIVER_BUSY         = 8'h03;
    localparam logic [7:0] ERROR_DECIMATION_RANGE    = 8'h04;
    localparam logic [7:0] ERROR_SAR_NOT_READY       = 8'h05;
    localparam logic [7:0] ERROR_UNKNOWN_OPCODE      = 8'h06;
    localparam logic [7:0] ERROR_STATISTICS_BUSY     = 8'h07;
    localparam logic [7:0] ERROR_STATISTICS_NOT_READY = 8'h08;
    localparam logic [7:0] ERROR_STATISTICS_ADDRESS  = 8'h09;
    localparam logic [7:0] ERROR_CONTROL_SAMPLE_COUNT = 8'h0a;
    localparam logic [7:0] ERROR_DSM_OVERRIDE_VALUE = 8'h0b;

    localparam logic [15:0] PROTOCOL_VERSION = 16'h0002;
    // Revision 4 adds host-latched live mu snapshots to avoid torn reads.
    localparam logic [15:0] IMPLEMENTATION_REVISION = 16'h0004;
    localparam int unsigned STATISTICS_SAMPLES = 100;
    localparam int unsigned DEFAULT_CONTROL_SAMPLES = 1500;
    localparam int unsigned MIN_CONTROL_SAMPLES = 968;
    localparam int unsigned START_ASSERT_SAMPLES = 10;
    localparam int unsigned DEFAULT_FILTER_HALF_LENGTH = 1024;
    localparam int unsigned MIN_FILTER_HALF_LENGTH = 1024;
    localparam int unsigned MAX_FILTER_HALF_LENGTH = 50000;
    localparam int unsigned DSM_OVERRIDE_WIDTH = 29;

endpackage
