package fpga_v1_protocol_pkg;

    localparam logic [3:0] OP_WRITE_CONTROL_WORD = 4'h1;
    localparam logic [3:0] OP_COMMIT_AND_START   = 4'h2;
    localparam logic [3:0] OP_SET_DECIMATION    = 4'h3;
    localparam logic [3:0] OP_SAR_START         = 4'h4;
    localparam logic [3:0] OP_SAR_COMPARE       = 4'h5;
    localparam logic [3:0] OP_CLEAR_ERROR       = 4'h6;
    localparam logic [3:0] OP_READ_REGISTER     = 4'h8;

    localparam logic [7:0] ERROR_NONE                = 8'h00;
    localparam logic [7:0] ERROR_CONTROL_INDEX       = 8'h01;
    localparam logic [7:0] ERROR_CONTROL_INCOMPLETE  = 8'h02;
    localparam logic [7:0] ERROR_DRIVER_BUSY         = 8'h03;
    localparam logic [7:0] ERROR_DECIMATION_RANGE    = 8'h04;
    localparam logic [7:0] ERROR_SAR_NOT_READY       = 8'h05;
    localparam logic [7:0] ERROR_UNKNOWN_OPCODE      = 8'h06;

endpackage
