import unittest
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading

from control_word import (
    COEFFICIENT_Q16_WIDTH,
    ControlWord,
    FpgaAlgorithmCoefficients,
    parse_alpha_decimal,
    parse_coefficient_decimal,
    signed_q16_to_decimal_text,
    unsigned_q16_to_decimal_text,
)
from gui import (
    ControlApp,
    DEFAULT_CHIP_NUMBER,
    DEFAULT_CONTROL_FLAGS,
    MULTI_DEFAULT_CONTROL_SAMPLE_COUNT,
    MultiTemperatureTestSettings,
    control_word_config_json_dict,
    load_control_word_config,
    multi_temperature_test_bypass_values,
    parse_bit_string,
    parse_dsm_override_value,
    parse_filter_total_sample_count,
)
from jtag_transport import (
    Command,
    DEFAULT_CONTROL_SAMPLE_COUNT,
    DSM_OVERRIDE_RAW_MAX,
    DsmOverrideStatus,
    FILTER_TOTAL_SAMPLES_DEFAULT,
    FILTER_TOTAL_SAMPLES_MAX,
    MIN_CONTROL_SAMPLE_COUNT,
    OP_CLEAR_ERROR,
    OP_COMMIT_AND_START,
    OP_READ_REGISTER,
    OP_SET_CONTROL_SAMPLES,
    OP_SET_DSM_OVERRIDE,
    OP_WRITE_CONTROL_WORD,
    Probe,
    QuartusJtagTransport,
    describe_fpga_error,
    dsm_signed_to_twos,
    validate_control_sample_count,
    validate_filter_total_samples,
)


class StaticVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class ControlWordTest(unittest.TestCase):
    def test_v5_default_control_flags_and_chip(self) -> None:
        self.assertEqual(
            DEFAULT_CONTROL_FLAGS,
            {
                "bypass": 1,
                "en_bias": 1,
                "en_dllbuf": 0,
                "en_intbuf": 0,
                "en_sensor": 1,
                "en_xo": 1,
                "en_xobufa": 0,
                "su": 1,
                "polarity": 0,
            },
        )
        self.assertEqual(DEFAULT_CHIP_NUMBER, 2)

    def test_multi_temperature_default_control_word_and_shutdown(self) -> None:
        app = object.__new__(ControlApp)
        app.multi_default_flag_vars = {
            key: StaticVar(value)
            for key, value in DEFAULT_CONTROL_FLAGS.items()
        }
        app.multi_default_field_vars = {
            "cs": StaticVar("10"),
            "trim_c_fine": StaticVar("111"),
            "trim_r_xo": StaticVar("1000"),
            "alpha": StaticVar("0.0"),
        }
        app.multi_default_batch_vars = [StaticVar("0.0") for _ in range(7)]
        app.multi_default_poly_vars = [StaticVar("0.0") for _ in range(8)]

        active = ControlApp._multi_default_control_word(
            app,
            "101",
            en_xo=True,
        )
        shutdown = ControlApp._multi_default_control_word(
            app,
            "101",
            en_xo=False,
        )

        self.assertEqual(active.cs, 0b10)
        self.assertEqual(active.trim_c_coarse, 0b101)
        self.assertEqual(active.trim_c_fine, 0b111)
        self.assertEqual(active.trim_r_xo, 0b1000)
        self.assertEqual(active.alpha, 0)
        self.assertEqual(active.batch, [0] * 7)
        self.assertEqual(active.poly, [0] * 8)
        self.assertEqual(active.en_xo, 1)
        self.assertEqual(shutdown.en_xo, 0)

    def test_multi_temperature_control_sample_default_is_20000(self) -> None:
        self.assertEqual(MULTI_DEFAULT_CONTROL_SAMPLE_COUNT, 20000)

    def test_multi_temperature_control_word_write_uses_configured_sample_count(self) -> None:
        class FakeTransport:
            def __init__(self) -> None:
                self.calls = []

            def configure_and_start(
                self,
                words,
                coefficient_words,
                selection,
                *,
                control_sample_count,
            ) -> None:
                self.calls.append(
                    (words, coefficient_words, selection, control_sample_count)
                )

        app = object.__new__(ControlApp)
        app.multi_default_flag_vars = {
            key: StaticVar(value)
            for key, value in DEFAULT_CONTROL_FLAGS.items()
        }
        app.multi_default_field_vars = {
            "cs": StaticVar("10"),
            "trim_c_fine": StaticVar("111"),
            "trim_r_xo": StaticVar("1000"),
            "alpha": StaticVar("0.0"),
        }
        app.multi_default_batch_vars = [StaticVar("0.0") for _ in range(7)]
        app.multi_default_poly_vars = [StaticVar("0.0") for _ in range(8)]
        transport = FakeTransport()

        ControlApp._write_multi_temperature_control_word(
            app,
            transport,
            chip=2,
            trim_c_coarse="101",
            en_xo=True,
            control_sample_count=20000,
        )

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][2:], (2, 20000))

    def test_multi_temperature_test_overrides_trim_bypass_and_en_xo(self) -> None:
        class FakeTransport:
            def __init__(self) -> None:
                self.calls = []

            def configure_and_start(
                self,
                words,
                coefficient_words,
                selection,
                *,
                control_sample_count,
            ) -> None:
                self.calls.append(
                    (words, coefficient_words, selection, control_sample_count)
                )

        base = ControlWord(
            bypass=1,
            cs=2,
            en_bias=1,
            en_dllbuf=0,
            en_intbuf=1,
            en_sensor=1,
            en_xo=1,
            en_xobufa=0,
            su=1,
            trim_c_coarse=0,
            trim_c_fine=6,
            trim_r_xo=9,
            alpha=12345,
            batch=[index - 3 for index in range(7)],
            polarity=1,
            poly=[index * 11 for index in range(8)],
        )
        settings = MultiTemperatureTestSettings(
            temperature_points=1,
            wait_seconds=0.0,
            test_seconds=0.0,
            chips=(2,),
            config_paths={(2, 3): Path("chip2_config3.json")},
            control_words={(2, 3): base},
            trim_c_coarse_selections=((1, "000"), (2, "101"), (3, "111")),
            run_bypass_variants=True,
            frequency_repeats=10,
            output_directory=Path("."),
            file_stem="test",
            startup_min_frequency_mhz=1.9,
            startup_max_frequency_mhz=2.1,
            startup_settle_seconds=1.0,
            startup_attempts=1,
            control_sample_counts={(2, 3): 20000},
        )
        transport = FakeTransport()
        app = object.__new__(ControlApp)

        ControlApp._write_multi_temperature_test_control_word(
            app,
            transport,
            settings=settings,
            chip=2,
            config_slot=3,
            bypass=0,
            trim_c_coarse="101",
            en_xo=True,
        )
        ControlApp._write_multi_temperature_test_control_word(
            app,
            transport,
            settings=settings,
            chip=2,
            config_slot=3,
            bypass=0,
            trim_c_coarse="101",
            en_xo=False,
        )

        active = replace(base, trim_c_coarse=0b101, bypass=0, en_xo=1)
        shutdown = replace(base, trim_c_coarse=0b101, bypass=0, en_xo=0)
        self.assertEqual(
            transport.calls,
            [
                (
                    active.words16(),
                    active.fpga_coefficients().words16(),
                    2,
                    20000,
                ),
                (
                    shutdown.words16(),
                    shutdown.fpga_coefficients().words16(),
                    2,
                    20000,
                ),
            ],
        )

    def test_multi_temperature_test_bypass_modes(self) -> None:
        self.assertEqual(multi_temperature_test_bypass_values(1, False), (1,))
        self.assertEqual(multi_temperature_test_bypass_values(0, False), (0,))
        self.assertEqual(multi_temperature_test_bypass_values(1, True), (1, 0))
        with self.assertRaises(ValueError):
            multi_temperature_test_bypass_values(2, False)

    def test_saved_control_word_json_uses_v12_schema(self) -> None:
        word = ControlWord(trim_c_coarse=0b101)
        values = control_word_config_json_dict(word, 23456)
        self.assertEqual(values["schema_version"], 2)
        self.assertEqual(values["project"], "FPGA_V12")
        self.assertEqual(values["n_daq"], 23456)
        self.assertEqual(values["pcb_control"]["trim_c_coarse"], 0b101)
        self.assertNotIn("alpha", values["pcb_control"])
        self.assertEqual(
            values["fpga_algorithm_coefficients"]["alpha"],
            word.alpha,
        )

    def test_v12_loader_accepts_v11_schema_two_config(self) -> None:
        word = ControlWord(trim_c_coarse=0b110, en_xo=1)
        values = control_word_config_json_dict(word, 24567)
        values["project"] = "FPGA_V11"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v11_config.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            loaded = load_control_word_config(
                path,
                require_control_sample_count=True,
            )
        self.assertEqual(loaded.word, word)
        self.assertEqual(loaded.control_sample_count, 24567)

    def test_legacy_v10_json_migrates_signed_29_bit_coefficients(self) -> None:
        legacy = ControlWord(
            alpha=0x10000,
            batch=[(1 << 29) - 16_384] + [0] * 6,
            poly=[(1 << 29) - 1] + [0] * 7,
        ).to_json_dict()
        legacy["n_daq"] = 968
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            loaded = load_control_word_config(path, require_control_sample_count=True)
        self.assertEqual(loaded.word.alpha, 0x10000)
        self.assertEqual(loaded.word.batch[0], -16_384)
        self.assertEqual(loaded.word.poly[0], -1)
        self.assertEqual(loaded.control_sample_count, 968)

    def test_control_word_start_helper_uses_same_transport_api(self) -> None:
        class FakeTransport:
            def __init__(self) -> None:
                self.calls = []

            def configure_and_start(
                self,
                words,
                coefficient_words,
                selection,
                *,
                control_sample_count,
            ) -> None:
                self.calls.append(
                    (words, coefficient_words, selection, control_sample_count)
                )

        word = ControlWord()
        transport = FakeTransport()
        ControlApp._configure_and_start_control_word(
            transport,
            word,
            0x02,
            DEFAULT_CONTROL_SAMPLE_COUNT,
        )
        self.assertEqual(
            transport.calls,
            [(
                word.words16(),
                word.fpga_coefficients().words16(),
                0x02,
                DEFAULT_CONTROL_SAMPLE_COUNT,
            )],
        )

    def test_calibration_display_omits_raw_words(self) -> None:
        self.assertEqual(ControlApp._format_calibration_value(-0.25), "-0.250000")
        status_text = ControlApp._format_dsm_status(
            DsmOverrideStatus(
                enabled=True,
                raw_value=dsm_signed_to_twos(-16_384),
            )
        )
        self.assertEqual(status_text, "覆盖已启用；Q16=-0.250000")
        self.assertNotIn("raw", status_text.lower())

    def test_matlab_flip_and_width(self) -> None:
        word = ControlWord(bypass=1, poly=[0] * 8)
        logical = word.logical_bits()
        serial = word.serial_bits()
        self.assertEqual(len(logical), 474)
        self.assertEqual(serial[473], 1)
        self.assertEqual(serial, list(reversed(logical)))
        self.assertEqual(len(word.words16()), 30)
        self.assertEqual(word.words16()[-1] & 0xFC00, 0)

    def test_field_locations(self) -> None:
        word = ControlWord(
            alpha=-1,
            batch=[1] + [0] * 6,
            polarity=1,
            poly=[-1] + [0] * 7,
        )
        serial = word.serial_bits()
        self.assertEqual(serial[0:232], [0] * 232)
        self.assertEqual(serial[232], 1)
        self.assertEqual(serial[233:436], [0] * 203)
        self.assertEqual(serial[436:454], [0] * 18)

    def test_signed_coefficient(self) -> None:
        word = ControlWord(batch=[-1] + [0] * 6)
        self.assertEqual(word.logical_bits()[38:67], [0] * 29)
        words = word.fpga_coefficients().words16()
        self.assertEqual(words[3:6], [0xFFFF, 0xFFFF, 0x001F])

    def test_exact_width_binary_trim_fields(self) -> None:
        self.assertEqual(parse_bit_string("10", 2, "CS"), 2)

    def test_binary_trim_field_rejects_bad_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "恰好输入2位"):
            parse_bit_string("1", 2, "CS")
        with self.assertRaisesRegex(ValueError, "只能包含字符0和1"):
            parse_bit_string("0x1", 2, "CS")
        with self.assertRaisesRegex(ValueError, "只能包含字符0和1"):
            parse_bit_string("10_01", 4, "TRIM_R_XO")

    def test_decimal_q16_ui_fields(self) -> None:
        self.assertEqual(parse_alpha_decimal("1.0"), 0x10000)
        self.assertEqual(
            parse_alpha_decimal("3.9999847412109375"),
            (1 << 18) - 1,
        )
        self.assertEqual(parse_coefficient_decimal("-0.25", "batch_a0"), -16_384)
        self.assertEqual(
            parse_coefficient_decimal("1048575.9999847412109375", "poly_a0"),
            (1 << 36) - 1,
        )
        self.assertEqual(
            parse_coefficient_decimal("-1048576.0", "poly_a0"),
            -(1 << 36),
        )
        self.assertEqual(unsigned_q16_to_decimal_text(0x10000), "1.0")
        self.assertEqual(signed_q16_to_decimal_text(-16_384), "-0.25")
        self.assertEqual(
            signed_q16_to_decimal_text((1 << 37) - 16_384),
            "-0.25",
        )

    def test_decimal_q16_field_rejects_bad_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "超出Q16范围"):
            parse_alpha_decimal("-0.0001")
        with self.assertRaisesRegex(ValueError, "超出Q16范围"):
            parse_alpha_decimal("4.0")
        with self.assertRaisesRegex(ValueError, "超出Q16范围"):
            parse_coefficient_decimal("1048576.0", "poly_a0")
        with self.assertRaisesRegex(ValueError, "有限十进制数值"):
            parse_coefficient_decimal("NaN", "poly_a0")
        with self.assertRaisesRegex(ValueError, "必须为十进制数值"):
            parse_coefficient_decimal("0x1", "poly_a0")

    def test_decimal_ui_values_enter_fpga_coefficient_window_only(self) -> None:
        coefficient_value = parse_coefficient_decimal("-0.25", "batch_a0")
        word = ControlWord(
            cs=parse_bit_string("10", 2, "CS"),
            trim_c_coarse=parse_bit_string("101", 3, "TRIM_C_COARSE"),
            trim_c_fine=parse_bit_string("011", 3, "TRIM_C_FINE"),
            trim_r_xo=parse_bit_string("1011", 4, "TRIM_R_XO"),
            alpha=parse_coefficient_decimal("-2.5", "ALPHA"),
            batch=[
                coefficient_value
            ] + [0] * 6,
            poly=[
                coefficient_value
            ] + [0] * 7,
        )
        logical = word.logical_bits()
        self.assertEqual(logical[1:3], [1, 0])
        self.assertEqual(logical[10:13], [1, 0, 1])
        self.assertEqual(logical[13:16], [0, 1, 1])
        self.assertEqual(logical[16:20], [1, 0, 1, 1])
        self.assertEqual(logical[20:38], [0] * 18)
        self.assertEqual(logical[38:241], [0] * 203)
        self.assertEqual(logical[242:474], [0] * 232)
        chunks = word.fpga_coefficients().words16()
        alpha_raw = (-163_840) & ((1 << COEFFICIENT_Q16_WIDTH) - 1)
        self.assertEqual(chunks[0:3], [alpha_raw & 0xFFFF,
                                      (alpha_raw >> 16) & 0xFFFF,
                                      (alpha_raw >> 32) & 0x1F])

    def test_system_console_tcl_uses_tcp_and_avalon_without_runtime_files(self) -> None:
        script = Path(__file__).with_name("system_console_server.tcl").read_text(
            encoding="utf-8"
        )
        self.assertIn("socket -server accept_client -myaddr 127.0.0.1 0", script)
        self.assertIn("master_read_32", script)
        self.assertIn("master_write_32", script)
        self.assertIn("HELLO", script)
        self.assertIn("FPGA_V12_TOKEN", script)
        self.assertIn("FPGA_V12_PARENT_PID", script)
        self.assertIn("maximum_words 256", script)
        self.assertNotIn("request.txt", script)
        self.assertNotIn("response.txt", script)
        self.assertNotIn("quartus_stp", script)

    def test_fpga_error_description(self) -> None:
        self.assertEqual(
            describe_fpga_error(0x03),
            "0x03（芯片启动驱动器正忙）",
        )

    def test_batch_api_uses_30_plus_48_blocks_and_waits_for_done(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        actions: list[tuple[int, int]] = []
        writes: list[tuple[int, list[int]]] = []
        transport._write_action = (
            lambda address, data=0, **_kwargs: actions.append((address, data)) or len(actions)
        )
        transport._write32 = (
            lambda address, values: writes.append((address, list(values)))
        )
        transport._read32 = lambda address, count=1: (
            [1 << 2] if address == 0x0010 else [0] * count
        )
        result = transport.configure_and_start(
            list(range(30)),
            [0] * 48,
            selection=0xA,
            control_sample_count=1600,
        )
        self.assertEqual(actions, [(0x0060, 0), (0x0040, 1600), (0x0054, 0xA)])
        self.assertEqual(
            writes,
            [(0x0100, list(range(30))), (0x0800, [0] * 48)],
        )
        self.assertTrue(result.driver_done)
        self.assertFalse(result.driver_busy)

    def test_control_sample_count_validation(self) -> None:
        self.assertEqual(
            validate_control_sample_count(DEFAULT_CONTROL_SAMPLE_COUNT),
            DEFAULT_CONTROL_SAMPLE_COUNT,
        )
        with self.assertRaises(ValueError):
            validate_control_sample_count(MIN_CONTROL_SAMPLE_COUNT - 1)
        with self.assertRaises(ValueError):
            validate_control_sample_count(0x1_0000)

    def test_filter_total_sample_range_is_2048_to_100000(self) -> None:
        self.assertEqual(
            validate_filter_total_samples(FILTER_TOTAL_SAMPLES_DEFAULT),
            2048,
        )
        self.assertEqual(
            parse_filter_total_sample_count(str(FILTER_TOTAL_SAMPLES_MAX)),
            100000,
        )
        with self.assertRaises(ValueError):
            parse_filter_total_sample_count("2047")
        with self.assertRaises(ValueError):
            parse_filter_total_sample_count("100001")
        with self.assertRaises(ValueError):
            parse_filter_total_sample_count("2049")

    def test_dsm_override_value_parser_accepts_q16_and_raw_words(self) -> None:
        self.assertEqual(parse_dsm_override_value("0.5"), 32_768)
        self.assertEqual(parse_dsm_override_value("0.75"), 49_152)
        self.assertEqual(
            parse_dsm_override_value("-0.25"),
            dsm_signed_to_twos(-16_384),
        )
        self.assertEqual(
            parse_dsm_override_value("-0.75"),
            dsm_signed_to_twos(-49_152),
        )
        self.assertEqual(
            parse_dsm_override_value("0x1fffffff"),
            DSM_OVERRIDE_RAW_MAX,
        )
        self.assertEqual(
            parse_dsm_override_value("1" * 29),
            DSM_OVERRIDE_RAW_MAX,
        )

    def test_dsm_override_api_writes_low_high_then_apply(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        actions: list[tuple[int, int]] = []
        writes: list[tuple[int, list[int]]] = []
        raw_value = 0x1ABCDE
        transport._write_action = (
            lambda address, data=0: actions.append((address, data))
        )
        transport._write32 = (
            lambda address, values: writes.append((address, list(values)))
        )
        transport.read_registers = lambda _indices: {
            18: 1,
            19: raw_value & 0xFFFF,
            20: (raw_value >> 16) & 0x1FFF,
        }
        status = transport.set_dsm_override(raw_value, enabled=True)
        self.assertEqual(actions, [(0x0060, 0), (0x0050, 1)])
        self.assertEqual(
            writes,
            [(0x0048, [raw_value & 0xFFFF, (raw_value >> 16) & 0x1FFF])],
        )
        self.assertTrue(status.enabled)
        self.assertEqual(status.raw_value, raw_value)


if __name__ == "__main__":
    unittest.main()
