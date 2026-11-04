module filter_down_two_clock #(
    parameter int unsigned DATA_WIDTH = 18
) (
    input  logic                      rst_sample,
    input  logic                      clk_sample,
    input  logic                      rst_data,
    input  logic                      clk_data,
    input  logic                      bs_in,

    output logic [DATA_WIDTH-1:0]     out,
    output logic                      valid
);

    logic [10:0] counter;
    logic bs_delayed;
    logic direction;
    logic [20:0] accumulator;

    logic [DATA_WIDTH-1:0] sample_result_hold;
    logic result_toggle_sample;

    logic result_toggle_meta;
    logic result_toggle_sync;
    logic result_toggle_delay;
    logic [DATA_WIDTH-1:0] result_meta;
    logic [DATA_WIDTH-1:0] result_sync;

    // This is the fixed-N on-chip-compatible sampling path.  The arithmetic,
    // delayed BS bit and accumulator slice are intentionally identical to
    // rtl/on_chip/filter_down.sv.  Only the completed-result transfer to
    // clk_data is new.
    always_ff @(posedge clk_sample or posedge rst_sample) begin
        if (rst_sample) begin
            counter <= '0;
            bs_delayed <= 1'b0;
            direction <= 1'b0;
            accumulator <= '0;
            sample_result_hold <= '0;
            result_toggle_sample <= 1'b0;
        end else begin
            bs_delayed <= bs_in;
            accumulator <=
                accumulator + (bs_delayed ? counter : 11'd0);

            if (!direction) begin
                counter <= counter + 1'b1;
                if (counter == 1023) begin
                    direction <= 1'b1;
                end
            end else begin
                counter <= counter - 1'b1;
                if (counter == 1) begin
                    direction <= 1'b0;
                    sample_result_hold <= {
                        1'b0,
                        accumulator[20:4]
                    };
                    result_toggle_sample <= ~result_toggle_sample;
                    accumulator <= '0;
                end
            end
        end
    end

    // CDC mailbox: the payload is stable for a complete 2048-sample window.
    // valid is a one-clk_data-cycle pulse for downstream 2 MHz logic.
    always_ff @(posedge clk_data or posedge rst_data) begin
        if (rst_data) begin
            result_toggle_meta <= 1'b0;
            result_toggle_sync <= 1'b0;
            result_toggle_delay <= 1'b0;
            result_meta <= '0;
            result_sync <= '0;
            out <= '0;
            valid <= 1'b0;
        end else begin
            result_toggle_meta <= result_toggle_sample;
            result_toggle_sync <= result_toggle_meta;
            result_toggle_delay <= result_toggle_sync;
            result_meta <= sample_result_hold;
            result_sync <= result_meta;
            valid <= 1'b0;

            if (result_toggle_sync != result_toggle_delay) begin
                out <= result_sync;
                valid <= 1'b1;
            end
        end
    end

endmodule
