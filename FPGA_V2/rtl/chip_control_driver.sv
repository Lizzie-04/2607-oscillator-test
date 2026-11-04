module chip_control_driver #(
    parameter int unsigned CONTROL_BITS = 474,
    parameter int unsigned HALF_PERIOD_CYCLES = 25_000,
    parameter int unsigned DEFAULT_SAMPLE_COUNT = 1500,
    parameter int unsigned MIN_SAMPLE_COUNT = 968,
    parameter int unsigned START_ASSERT_SAMPLES = 10
) (
    input  logic                    clk,
    input  logic                    rst,
    input  logic                    start,
    input  logic [CONTROL_BITS-1:0] serial_bits,
    input  logic [2:0]              chip_number,
    input  logic                    broadcast,
    input  logic [15:0]             sample_count,

    output logic [2:0]              address,
    output logic                    sr_clk,
    output logic                    sr_in,
    output logic [4:0]              sr_enable,
    output logic                    chip_reset,
    output logic                    start_dll,
    output logic                    start_ana,
    output logic                    busy,
    output logic                    done,
    output logic [9:0]              bit_index,
    output logic [3:0]              state_debug
);

    localparam int unsigned GUARD_CYCLES = 7 * HALF_PERIOD_CYCLES;

    typedef enum logic [3:0] {
        IDLE,
        PREFIX_IDLE,
        BIT_LOW,
        BIT_HIGH,
        POST_SHIFT_IDLE,
        ENABLE_HIGH,
        ENABLE_GUARD,
        RESET_START_HIGH,
        START_WAIT
    } state_t;

    state_t state;

    logic [CONTROL_BITS-1:0] serial_latched;
    logic [2:0] chip_latched;
    logic broadcast_latched;
    logic [31:0] state_counter;
    logic [31:0] total_counter;
    logic [31:0] total_cycles_latched;
    logic [31:0] start_assert_cycle_latched;
    logic start_latched_high;

    function automatic [15:0] sanitize_sample_count(
        input logic [15:0] requested
    );
        if (requested < 16'(MIN_SAMPLE_COUNT)) begin
            sanitize_sample_count = 16'(MIN_SAMPLE_COUNT);
        end else begin
            sanitize_sample_count = requested;
        end
    endfunction

    function automatic [31:0] cycles_from_samples(
        input logic [15:0] samples
    );
        cycles_from_samples = 32'(samples) * HALF_PERIOD_CYCLES;
    endfunction

    logic [15:0] effective_sample_count;
    logic start_assert_active;

    always_comb begin
        effective_sample_count = sanitize_sample_count(sample_count);
        start_assert_active = (
            (state != IDLE) &&
            (total_counter >= start_assert_cycle_latched)
        );
    end

    always_comb begin
        address = chip_latched;
        sr_clk = 1'b0;
        sr_in = 1'b0;
        sr_enable = 5'b0;
        chip_reset = 1'b0;
        start_dll = start_latched_high || start_assert_active;
        start_ana = start_latched_high || start_assert_active;
        busy = (state != IDLE);
        state_debug = state;

        if ((state == BIT_LOW) || (state == BIT_HIGH)) begin
            sr_in = serial_latched[bit_index];
        end

        if (state == BIT_HIGH) begin
            sr_clk = 1'b1;
        end

        if (state == ENABLE_HIGH) begin
            if (broadcast_latched) begin
                sr_enable = 5'b1_1111;
            end else if (chip_latched < 5) begin
                sr_enable[chip_latched] = 1'b1;
            end
        end

        if (state == RESET_START_HIGH) begin
            chip_reset = 1'b1;
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state <= IDLE;
            serial_latched <= '0;
            chip_latched <= '0;
            broadcast_latched <= 1'b0;
            state_counter <= '0;
            total_counter <= '0;
            total_cycles_latched <= cycles_from_samples(
                16'(DEFAULT_SAMPLE_COUNT)
            );
            start_assert_cycle_latched <= cycles_from_samples(
                16'(DEFAULT_SAMPLE_COUNT - START_ASSERT_SAMPLES)
            );
            start_latched_high <= 1'b0;
            bit_index <= '0;
            done <= 1'b0;
        end else begin
            if (state == IDLE) begin
                if (start) begin
                    serial_latched <= serial_bits;
                    chip_latched <= chip_number;
                    broadcast_latched <= broadcast;
                    state_counter <= '0;
                    total_counter <= '0;
                    total_cycles_latched <= cycles_from_samples(
                        effective_sample_count
                    );
                    start_assert_cycle_latched <= cycles_from_samples(
                        effective_sample_count - 16'(START_ASSERT_SAMPLES)
                    );
                    start_latched_high <= 1'b0;
                    bit_index <= '0;
                    done <= 1'b0;
                    state <= PREFIX_IDLE;
                end
            end else begin
                total_counter <= total_counter + 1'b1;

                unique case (state)
                    PREFIX_IDLE: begin
                        if (state_counter == HALF_PERIOD_CYCLES - 1) begin
                            state_counter <= '0;
                            state <= BIT_LOW;
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    BIT_LOW: begin
                        if (state_counter == HALF_PERIOD_CYCLES - 1) begin
                            state_counter <= '0;
                            state <= BIT_HIGH;
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    BIT_HIGH: begin
                        if (state_counter == HALF_PERIOD_CYCLES - 1) begin
                            state_counter <= '0;
                            if (bit_index == CONTROL_BITS - 1) begin
                                state <= POST_SHIFT_IDLE;
                            end else begin
                                bit_index <= bit_index + 1'b1;
                                state <= BIT_LOW;
                            end
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    POST_SHIFT_IDLE: begin
                        if (state_counter == HALF_PERIOD_CYCLES - 1) begin
                            state_counter <= '0;
                            state <= ENABLE_HIGH;
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    ENABLE_HIGH: begin
                        if (state_counter == HALF_PERIOD_CYCLES - 1) begin
                            state_counter <= '0;
                            state <= ENABLE_GUARD;
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    ENABLE_GUARD: begin
                        if (state_counter == GUARD_CYCLES - 1) begin
                            state_counter <= '0;
                            state <= RESET_START_HIGH;
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    RESET_START_HIGH: begin
                        if (state_counter == HALF_PERIOD_CYCLES - 1) begin
                            state_counter <= '0;
                            state <= START_WAIT;
                        end else begin
                            state_counter <= state_counter + 1'b1;
                        end
                    end

                    START_WAIT: begin
                        if (total_counter >= total_cycles_latched - 1) begin
                            state <= IDLE;
                            done <= 1'b1;
                            start_latched_high <= 1'b1;
                        end
                    end

                    default: begin
                        state <= IDLE;
                    end
                endcase
            end
        end
    end

endmodule
