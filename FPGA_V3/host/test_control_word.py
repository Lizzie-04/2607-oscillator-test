import unittest
from pathlib import Path
import threading

from control_word import ControlWord
from gui import (
    parse_bit_string,
    parse_dsm_override_value,
    parse_filter_total_sample_count,
)
from jtag_transport import (
    DEFAULT_CONTROL_SAMPLE_COUNT,
    DSM_OVERRIDE_RAW_MAX,
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


class ControlWordTest(unittest.TestCase):
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
        word = ControlWord(alpha=1, batch=[1] + [0] * 6, poly=[0] * 8)
        logical = word.logical_bits()
        self.assertEqual(logical[37], 1)
        self.assertEqual(logical[66], 1)

    def test_signed_coefficient(self) -> None:
        word = ControlWord(batch=[-1] + [0] * 6)
        self.assertEqual(word.logical_bits()[38:67], [1] * 29)

    def test_exact_width_binary_ui_fields(self) -> None:
        self.assertEqual(parse_bit_string("10", 2, "CS"), 2)
        self.assertEqual(
            parse_bit_string("010000000000000000", 18, "ALPHA"),
            0x10000,
        )
        self.assertEqual(parse_bit_string("1" * 29, 29, "batch_a0"), (1 << 29) - 1)

    def test_binary_ui_field_rejects_bad_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "恰好输入2位"):
            parse_bit_string("1", 2, "CS")
        with self.assertRaisesRegex(ValueError, "只能包含字符0和1"):
            parse_bit_string("0x1", 2, "CS")
        with self.assertRaisesRegex(ValueError, "只能包含字符0和1"):
            parse_bit_string("10_01", 4, "TRIM_R_XO")

    def test_binary_ui_bits_enter_packer_unchanged(self) -> None:
        alpha_bits = "101010101010101010"
        coefficient_bits = "10101010101010101010101010101"
        word = ControlWord(
            cs=parse_bit_string("10", 2, "CS"),
            trim_c_coarse=parse_bit_string("101", 3, "TRIM_C_COARSE"),
            trim_c_fine=parse_bit_string("011", 3, "TRIM_C_FINE"),
            trim_r_xo=parse_bit_string("1011", 4, "TRIM_R_XO"),
            alpha=parse_bit_string(alpha_bits, 18, "ALPHA"),
            batch=[
                parse_bit_string(coefficient_bits, 29, "batch_a0")
            ] + [0] * 6,
            poly=[
                parse_bit_string(coefficient_bits, 29, "poly_a0")
            ] + [0] * 7,
        )
        logical = word.logical_bits()
        self.assertEqual(logical[1:3], [1, 0])
        self.assertEqual(logical[10:13], [1, 0, 1])
        self.assertEqual(logical[13:16], [0, 1, 1])
        self.assertEqual(logical[16:20], [1, 0, 1, 1])
        self.assertEqual(logical[20:38], [int(bit) for bit in alpha_bits])
        self.assertEqual(
            logical[38:67],
            [int(bit) for bit in coefficient_bits],
        )
        self.assertEqual(
            logical[242:271],
            [int(bit) for bit in coefficient_bits],
        )

    def test_jtag_tcl_uses_64_bit_scan_and_format(self) -> None:
        script = Path(__file__).with_name("issp_batch.tcl").read_text(
            encoding="utf-8"
        )
        self.assertIn("scan $probe_hex %llx probe_word", script)
        self.assertIn('format "%016llX" $source_word', script)
        self.assertIn('format "RESULT %d %016llX"', script)
        self.assertNotIn("scan $probe_hex %x probe_word", script)

    def test_fpga_error_description(self) -> None:
        self.assertEqual(
            describe_fpga_error(0x03),
            "0x03（芯片启动驱动器正忙）",
        )

    def test_batch_api_uses_30_blocks_and_waits_for_done(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        calls: list[tuple[list, float]] = []

        def fake_transact(commands, timeout):
            calls.append((commands, timeout))
            if len(calls) == 1:
                return [Probe(0) for _ in commands]
            return [Probe(1 << 52)]

        transport._transact_unlocked = fake_transact
        result = transport.configure_and_start(
            list(range(30)),
            selection=0xA,
            control_sample_count=1600,
        )

        commands = calls[0][0]
        self.assertEqual(len(commands), 33)
        self.assertEqual(commands[0].opcode, OP_CLEAR_ERROR)
        self.assertEqual(commands[1].opcode, OP_SET_CONTROL_SAMPLES)
        self.assertEqual(commands[1].data, 1600)
        self.assertEqual(
            [command.index for command in commands[2:32]],
            list(range(30)),
        )
        self.assertTrue(
            all(
                command.opcode == OP_WRITE_CONTROL_WORD
                for command in commands[2:32]
            )
        )
        self.assertEqual(commands[-1].opcode, OP_COMMIT_AND_START)
        self.assertEqual(commands[-1].data, 0xA)
        self.assertEqual(calls[1][0][0].opcode, OP_READ_REGISTER)
        self.assertTrue(result.driver_done)
        self.assertFalse(result.driver_busy)

    def test_batch_api_reports_exact_failed_block(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()

        def fake_transact(commands, timeout):
            probes = [Probe(0) for _ in commands]
            probes[26] = Probe(1 << 54)
            return probes

        transport._transact_unlocked = fake_transact
        with self.assertRaisesRegex(
            RuntimeError,
            "第27条协议命令（控制字块24）失败",
        ):
            transport.configure_and_start(list(range(30)), selection=0)

    def test_control_sample_count_validation(self) -> None:
        self.assertEqual(
            validate_control_sample_count(DEFAULT_CONTROL_SAMPLE_COUNT),
            DEFAULT_CONTROL_SAMPLE_COUNT,
        )
        with self.assertRaisesRegex(ValueError, "不小于"):
            validate_control_sample_count(MIN_CONTROL_SAMPLE_COUNT - 1)
        with self.assertRaisesRegex(ValueError, "不能超过"):
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
        self.assertEqual(
            parse_dsm_override_value("-0.25"),
            dsm_signed_to_twos(-16_384),
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
        calls: list[list] = []
        raw_value = 0x1ABCDE

        def probe_with_response(response: int) -> Probe:
            return Probe((response & 0xFFFF) << 17)

        def fake_transact(commands, timeout):
            calls.append(commands)
            if len(calls) == 1:
                return [Probe(0) for _ in commands]
            return [
                probe_with_response(1),
                probe_with_response(raw_value & 0xFFFF),
                probe_with_response((raw_value >> 16) & 0x1FFF),
            ]

        transport._transact_unlocked = fake_transact
        status = transport.set_dsm_override(raw_value, enabled=True)

        commands = calls[0]
        self.assertEqual(
            [command.opcode for command in commands],
            [
                OP_CLEAR_ERROR,
                OP_SET_DSM_OVERRIDE,
                OP_SET_DSM_OVERRIDE,
                OP_SET_DSM_OVERRIDE,
            ],
        )
        self.assertEqual(
            [(command.index, command.data) for command in commands[1:]],
            [
                (0, raw_value & 0xFFFF),
                (1, (raw_value >> 16) & 0x1FFF),
                (2, 1),
            ],
        )
        self.assertEqual(
            [(command.opcode, command.index) for command in calls[1]],
            [
                (OP_READ_REGISTER, 18),
                (OP_READ_REGISTER, 19),
                (OP_READ_REGISTER, 20),
            ],
        )
        self.assertTrue(status.enabled)
        self.assertEqual(status.raw_value, raw_value)


if __name__ == "__main__":
    unittest.main()
