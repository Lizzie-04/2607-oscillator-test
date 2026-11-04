module host_command_arbiter (
    input  logic        clk,
    input  logic        rst,

    input  logic        jtag_valid,
    input  logic [3:0]  jtag_opcode,
    input  logic [5:0]  jtag_index,
    input  logic [15:0] jtag_data,
    output logic        jtag_done,
    output logic [15:0] jtag_response,

    input  logic        gpio_valid,
    input  logic [3:0]  gpio_opcode,
    input  logic [5:0]  gpio_index,
    input  logic [15:0] gpio_data,
    output logic        gpio_done,
    output logic [15:0] gpio_response,

    output logic        command_valid,
    output logic [3:0]  command_opcode,
    output logic [5:0]  command_index,
    output logic [15:0] command_data,
    input  logic        command_done,
    input  logic [15:0] command_response
);

    logic active;
    logic owner_gpio;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            active <= 1'b0;
            owner_gpio <= 1'b0;
            jtag_done <= 1'b0;
            gpio_done <= 1'b0;
            jtag_response <= '0;
            gpio_response <= '0;
            command_valid <= 1'b0;
            command_opcode <= '0;
            command_index <= '0;
            command_data <= '0;
        end else begin
            jtag_done <= 1'b0;
            gpio_done <= 1'b0;
            command_valid <= 1'b0;

            if (!active) begin
                // A bridge observes done and drops its level-valid on this
                // same clock edge.  Gate acceptance with the previous done
                // pulse so that held valid cannot be consumed a second time.
                if (jtag_valid && !jtag_done) begin
                    owner_gpio <= 1'b0;
                    command_opcode <= jtag_opcode;
                    command_index <= jtag_index;
                    command_data <= jtag_data;
                    command_valid <= 1'b1;
                    active <= 1'b1;
                end else if (gpio_valid && !gpio_done) begin
                    owner_gpio <= 1'b1;
                    command_opcode <= gpio_opcode;
                    command_index <= gpio_index;
                    command_data <= gpio_data;
                    command_valid <= 1'b1;
                    active <= 1'b1;
                end
            end else if (command_done) begin
                if (owner_gpio) begin
                    gpio_response <= command_response;
                    gpio_done <= 1'b1;
                end else begin
                    jtag_response <= command_response;
                    jtag_done <= 1'b1;
                end
                active <= 1'b0;
            end
        end
    end

endmodule
