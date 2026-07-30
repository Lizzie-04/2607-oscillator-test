module jtag_led_top (
    input  logic       CLOCK_50,
    input  logic [0:0] KEY,
    output logic [9:0] LEDR
);

    (* keep = "true", preserve = "true" *) logic [11:0] jtag_source;
    (* keep = "true", preserve = "true" *) logic [11:0] jtag_probe;
    (* keep = "true", preserve = "true" *) logic [9:0]  led_value;
    (* keep = "true", preserve = "true" *) logic [9:0]  pending_value;
    (* keep = "true", preserve = "true" *) logic        busy;
    (* keep = "true", preserve = "true" *) logic        update_pulse;
    (* keep = "true", preserve = "true" *) logic        ack_toggle;
    (* keep = "true", preserve = "true" *) logic [44:0] signaltap_data;

    altsource_probe #(
        .sld_auto_instance_index("YES"),
        .instance_id("LED1"),
        .probe_width(12),
        .source_width(12),
        .source_initial_value("000"),
        .enable_metastability("NO")
    ) led_control_issp (
        .probe(jtag_probe),
        .source(jtag_source),
        .source_clk(CLOCK_50),
        .source_ena(1'b1)
    );

    jtag_command_receiver #(
        .DEFER_CYCLES(250_000_000)
    ) receiver (
        .clk(CLOCK_50),
        .reset_n(KEY[0]),
        .source_async(jtag_source),
        .probe_data(jtag_probe),
        .led_value(led_value),
        .pending_value(pending_value),
        .busy(busy),
        .update_pulse(update_pulse),
        .ack_toggle(ack_toggle)
    );

    assign LEDR = led_value;
    assign signaltap_data = {
        jtag_source,
        pending_value,
        busy,
        ack_toggle,
        led_value,
        LEDR,
        update_pulse
    };

    // HDL-instantiated SignalTap keeps the debug core self-contained.  The
    // matching .stp session is generated from this node after compilation.
    sld_signaltap #(
        .SLD_NODE_INFO(806_383_104),
        .SLD_SECTION_ID("led_capture"),
        .SLD_DATA_BITS(45),
        .SLD_TRIGGER_BITS(1),
        .SLD_NODE_CRC_HIWORD(12_345),
        .SLD_NODE_CRC_LOWORD(19_899),
        .SLD_SAMPLE_DEPTH(512),
        .SLD_MEM_ADDRESS_BITS(9),
        .SLD_DATA_BIT_CNTR_BITS(7),
        .SLD_TRIGGER_LEVEL(1),
        .SLD_BUFFER_FULL_STOP(1)
    ) led_signaltap (
        .acq_clk(CLOCK_50),
        .acq_data_in(signaltap_data),
        .acq_trigger_in(update_pulse)
    );

endmodule
