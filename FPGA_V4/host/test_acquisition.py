import csv
from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from acquisition import (
    AtomicStatisticsCsv,
    CalibrationTrendHistory,
    TrendHistory,
    calibration_axis_bounds,
    validate_data_count,
    validate_file_stem,
)
from gui import parse_target_frequency
from jtag_transport import (
    Command,
    CalibrationSnapshot,
    IMPLEMENTATION_REVISION,
    JtagDiagnostic,
    MuSample,
    MIN_POLL_TRANSACTION_TIMEOUT,
    OP_CLEAR_ERROR,
    OP_LATCH_LIVE_SAMPLE,
    OP_LATCH_CALIBRATION_SAMPLE,
    OP_READ_REGISTER,
    OP_SAR_COMPARE,
    OP_SAR_START,
    Probe,
    PROTOCOL_VERSION,
    QuartusJtagTransport,
    SAR_INITIAL_CODE_Q16,
    SarStatus,
    STATISTICS_SAMPLES,
    poll_transaction_timeout,
    dsm_signed_to_twos,
)


def probe_with_response(response: int) -> Probe:
    return Probe((response & 0xFFFF) << 17)


def sar_status_probes(
    code_raw: int,
    step: int,
    *,
    busy: bool,
    done: bool,
    error: int = 0,
) -> list[Probe]:
    status_bits = (
        (int(busy) << 51)
        | (int(done) << 50)
        | ((error & 0xFF) << 54)
    )
    return [
        Probe(status_bits | ((code_raw & 0xFFFF) << 17)),
        Probe(status_bits | (((code_raw >> 16) & 0x1FFF) << 17)),
        Probe(status_bits | ((step & 0xFFFF) << 17)),
    ]


class AcquisitionTest(unittest.TestCase):
    def test_data_count_requires_positive_hundreds(self) -> None:
        self.assertEqual(validate_data_count(100), 100)
        self.assertEqual(validate_data_count(1200), 1200)
        for invalid in (0, -100, 1, 150):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_data_count(invalid)

    def test_filename_validation(self) -> None:
        self.assertEqual(validate_file_stem(" 晶振A "), "晶振A")
        for invalid in ("", "bad/name", "CON", "tail."):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_file_stem(invalid)

    def test_trend_history_keeps_latest_1000_points(self) -> None:
        history = TrendHistory(1000)
        history.extend(
            MuSample(index % 65537, (index + 1) % 65537)
            for index in range(1100)
        )
        mu1, mu2, onchip_mu1, onchip_mu2 = history.values()
        self.assertEqual(history.size, 1000)
        self.assertAlmostEqual(mu1[0], 100 / 65536)
        self.assertAlmostEqual(mu2[-1], 1100 / 65536)
        self.assertEqual(onchip_mu1[-1], None)
        self.assertEqual(onchip_mu2[-1], None)

    def test_multi_batch_csv_is_atomic_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            started = datetime(2026, 7, 30, 15, 0, 1)
            batch1 = [MuSample(index, index + 1) for index in range(100)]
            batch2 = [
                MuSample(index + 100, index + 101)
                for index in range(100)
            ]
            with AtomicStatisticsCsv(
                output, "test", started
            ) as writer:
                temporary_path = writer.temporary_path
                writer.append_batch(1, batch1)
                writer.append_batch(2, batch2)
                final_path = writer.publish(200)

            self.assertFalse(temporary_path.exists())
            self.assertEqual(
                final_path.name,
                "test_20260730_150001.csv",
            )
            with final_path.open(
                "r", encoding="utf-8", newline=""
            ) as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 200)
            self.assertEqual(rows[0]["batch"], "1")
            self.assertEqual(rows[99]["batch"], "1")
            self.assertEqual(rows[100]["batch"], "2")
            self.assertEqual(rows[-1]["batch"], "2")

    def test_failed_csv_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            writer = AtomicStatisticsCsv(
                output,
                "failed",
                datetime(2026, 7, 30, 15, 0, 2),
            )
            temporary_path = writer.temporary_path
            writer.append_batch(
                1,
                [MuSample(0, 0) for _ in range(100)],
            )
            writer.abort()
            self.assertFalse(temporary_path.exists())
            self.assertFalse(writer.final_path.exists())

    def test_statistics_batch_unpacks_300_words(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        call_number = 0

        expected = [
            MuSample(index, 1000 + index)
            for index in range(STATISTICS_SAMPLES)
        ]

        def fake_transact(commands, timeout):
            nonlocal call_number
            call_number += 1
            if call_number == 1:
                return [Probe(0)]
            if call_number == 2:
                return [
                    probe_with_response(0),
                    probe_with_response(0),
                ]
            if call_number == 3:
                return [Probe(0)]
            if call_number == 4:
                return [
                    probe_with_response(0b0011),
                    probe_with_response(STATISTICS_SAMPLES),
                ]
            self.assertEqual(len(commands), 300)
            self.assertEqual(
                [
                    (command.index, command.data)
                    for command in commands
                ],
                [
                    (part, sample_index)
                    for sample_index in range(STATISTICS_SAMPLES)
                    for part in range(3)
                ],
            )
            probes = []
            for sample in expected:
                packed = (
                    sample.mu1_raw
                    | (sample.mu2_raw << 18)
                )
                probes.extend(
                    [
                        probe_with_response(packed & 0xFFFF),
                        probe_with_response((packed >> 16) & 0xFFFF),
                        probe_with_response((packed >> 32) & 0xF),
                    ]
                )
            return probes

        transport._transact_unlocked = fake_transact
        actual = transport.capture_statistics_batch()
        self.assertEqual(actual, expected)
        self.assertEqual(call_number, 5)

    def test_live_sample_uses_latched_snapshot_registers(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        calls: list[list[Command]] = []

        def fake_transact(commands: list[Command], _timeout: float = 15.0):
            calls.append(commands)
            return [
                Probe(0),
                probe_with_response(0x3456),
                probe_with_response(0x0002),
                probe_with_response(0x789A),
                probe_with_response(0x0001),
                probe_with_response(0xF00D),
                probe_with_response(0x0000),
                probe_with_response(0xABCD),
                probe_with_response(0x0002),
                probe_with_response(42),
                probe_with_response(1024),
                probe_with_response(17),
            ]

        transport.transact = fake_transact
        snapshot = transport.read_live_sample()

        self.assertEqual(
            [command.opcode for command in calls[0]],
            [OP_LATCH_LIVE_SAMPLE, *([OP_READ_REGISTER] * 11)],
        )
        self.assertEqual(
            [command.index for command in calls[0]],
            [0, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31],
        )
        self.assertEqual(snapshot.sample.mu1_raw, 0x23456)
        self.assertEqual(snapshot.sample.mu2_raw, 0x1789A)
        self.assertEqual(snapshot.onchip_sample.mu1_raw, 0x0F00D)
        self.assertEqual(snapshot.onchip_sample.mu2_raw, 0x2ABCD)
        self.assertEqual(snapshot.sequence, 42)
        self.assertEqual(snapshot.total_filter_samples, 2048)
        self.assertEqual(snapshot.onchip_sequence, 17)

    def test_calibration_snapshot_is_atomic_signed_and_includes_sar(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        calls: list[list[Command]] = []
        raw_values = [
            dsm_signed_to_twos(-16_384),
            dsm_signed_to_twos(32_768),
            dsm_signed_to_twos(-1),
            dsm_signed_to_twos(65_536),
        ]

        def fake_transact(commands: list[Command], _timeout: float = 15.0):
            calls.append(commands)
            register_values: list[int] = []
            for raw in raw_values:
                register_values.extend((raw & 0xFFFF, (raw >> 16) & 0x1FFF))
            register_values.extend((77, 0b11))
            return [
                Probe(0),
                *(probe_with_response(value) for value in register_values),
                *sar_status_probes(
                    1 << 15,
                    3,
                    busy=True,
                    done=False,
                ),
            ]

        transport.transact = fake_transact
        snapshot = transport.read_calibration_snapshot()

        self.assertEqual(
            [command.opcode for command in calls[0]],
            [
                OP_LATCH_CALIBRATION_SAMPLE,
                *([OP_READ_REGISTER] * 13),
            ],
        )
        self.assertEqual(
            [command.index for command in calls[0]],
            [0, *range(32, 42), 6, 7, 8],
        )
        self.assertAlmostEqual(snapshot.poly_freq, -0.25)
        self.assertAlmostEqual(snapshot.poly_system, 0.5)
        self.assertAlmostEqual(snapshot.sar_correction, -1 / 65536)
        self.assertAlmostEqual(snapshot.dsm_input, 1.0)
        self.assertTrue(snapshot.dsm_overflow_high)
        self.assertFalse(snapshot.dsm_overflow_low)
        self.assertEqual(snapshot.sequence, 77)
        self.assertTrue(snapshot.valid)
        self.assertTrue(snapshot.override_enabled)
        self.assertTrue(snapshot.sar_status.busy)
        self.assertEqual(snapshot.sar_status.step, 3)

    def test_calibration_history_and_axis_preserve_overflow_values(self) -> None:
        history = CalibrationTrendHistory(1000)
        status = SarStatus(0, 0, False, False)
        for index in range(1100):
            raw = dsm_signed_to_twos(index - 550)
            history.append(
                CalibrationSnapshot(
                    poly_freq_raw=raw,
                    poly_system_raw=raw,
                    sar_correction_raw=raw,
                    dsm_input_raw=raw,
                    sequence=index & 0xFFFF,
                    valid=True,
                    override_enabled=False,
                    sar_status=status,
                )
            )
        self.assertEqual(history.size, 1000)
        _, _, _, dsm_values = history.values()
        self.assertAlmostEqual(dsm_values[0], -450 / 65536)
        history.clear()
        self.assertEqual(history.size, 0)
        self.assertEqual(history.values(), ([], [], [], []))

        lower, upper = calibration_axis_bounds([-2.0, 3.0])
        self.assertLess(lower, -2.0)
        self.assertGreater(upper, 3.0)
        endpoint_lower, endpoint_upper = calibration_axis_bounds([])
        self.assertLess(endpoint_lower, -0.5)
        self.assertGreater(endpoint_upper, 0.5)

    def test_v4_diagnostic_requires_protocol_version(self) -> None:
        v4 = JtagDiagnostic(
            hardware="DE-SoC [USB-1]",
            device="5CSE",
            source_hex_digits=16,
            probe_hex_digits=16,
            protocol_version=PROTOCOL_VERSION,
            implementation_revision=IMPLEMENTATION_REVISION,
        )
        v1 = JtagDiagnostic(
            hardware="DE-SoC [USB-1]",
            device="5CSE",
            source_hex_digits=16,
            probe_hex_digits=16,
            protocol_version=0,
            implementation_revision=0,
        )
        old_v4 = JtagDiagnostic(
            hardware="DE-SoC [USB-1]",
            device="5CSE",
            source_hex_digits=16,
            probe_hex_digits=16,
            protocol_version=PROTOCOL_VERSION,
            implementation_revision=0,
        )
        self.assertTrue(v4.compatible)
        self.assertFalse(v1.compatible)
        self.assertFalse(old_v4.compatible)
        self.assertIn("不是FPGA V4", v1.summary)
        self.assertIn("实现修订", old_v4.summary)

    def test_target_frequency_is_positive_and_finite(self) -> None:
        self.assertEqual(parse_target_frequency("10.5"), 10.5)
        for invalid in ("", "0", "-1", "nan", "inf"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_target_frequency(invalid)

    def test_sar_start_waits_for_confirmed_2mhz_state(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        call_number = 0
        timeouts: list[float] = []

        def fake_transact(commands: list[Command], timeout: float):
            nonlocal call_number
            call_number += 1
            timeouts.append(timeout)
            if call_number == 1:
                self.assertEqual(
                    [command.opcode for command in commands],
                    [
                        OP_READ_REGISTER,
                        OP_READ_REGISTER,
                        OP_CLEAR_ERROR,
                        OP_SAR_START,
                    ],
                )
                # The clear response may still expose the previous sticky
                # error; START itself must be clean.
                return [
                    probe_with_response(PROTOCOL_VERSION),
                    probe_with_response(IMPLEMENTATION_REVISION),
                    Probe(0x05 << 54),
                    Probe(0),
                ]
            if call_number == 2:
                return sar_status_probes(
                    0,
                    0,
                    busy=False,
                    done=False,
                )
            return sar_status_probes(
                SAR_INITIAL_CODE_Q16,
                0,
                busy=True,
                done=False,
            )

        transport._transact_unlocked = fake_transact
        status = transport.start_sar(ready_timeout=0.2)
        self.assertTrue(status.busy)
        self.assertEqual(status.code_raw, SAR_INITIAL_CODE_Q16)
        self.assertEqual(status.code, 0.0)
        self.assertEqual(call_number, 3)
        self.assertEqual(
            timeouts[1:],
            [MIN_POLL_TRANSACTION_TIMEOUT] * 2,
        )

    def test_poll_transaction_timeout_keeps_quartus_startup_budget(self) -> None:
        self.assertEqual(
            poll_transaction_timeout(0.01),
            MIN_POLL_TRANSACTION_TIMEOUT,
        )
        self.assertEqual(
            poll_transaction_timeout(2.0),
            MIN_POLL_TRANSACTION_TIMEOUT,
        )
        self.assertEqual(poll_transaction_timeout(12.0), 12.0)
        self.assertEqual(poll_transaction_timeout(30.0), 15.0)

    def test_jtag_process_timeout_identifies_host_fpga_link(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport.script = Path(__file__).with_name("issp_batch.tcl")
        transport.quartus_stp = Path("quartus_stp.exe")

        with patch(
            "jtag_transport.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd="quartus_stp.exe",
                timeout=MIN_POLL_TRANSACTION_TIMEOUT,
            ),
        ):
            with self.assertRaisesRegex(
                TimeoutError,
                "与外部芯片或FS是否连接无关",
            ):
                transport._transact_unlocked(
                    [Command(OP_CLEAR_ERROR, 0, 0)],
                    MIN_POLL_TRANSACTION_TIMEOUT,
                )

    def test_sar_start_reports_timeout_if_2mhz_state_does_not_advance(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()

        def fake_transact(commands: list[Command], _timeout: float):
            if len(commands) == 4:
                return [
                    probe_with_response(PROTOCOL_VERSION),
                    probe_with_response(IMPLEMENTATION_REVISION),
                    Probe(0),
                    Probe(0),
                ]
            return sar_status_probes(
                0,
                0,
                busy=False,
                done=False,
            )

        transport._transact_unlocked = fake_transact
        with self.assertRaisesRegex(
            TimeoutError,
            "2 MHz",
        ):
            transport.start_sar(ready_timeout=0.01)

    def test_sar_start_rejects_old_v2_implementation(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        transport._transact_unlocked = lambda _commands, _timeout: [
            probe_with_response(PROTOCOL_VERSION),
            probe_with_response(0),
            Probe(0),
            Probe(0),
        ]

        with self.assertRaisesRegex(RuntimeError, "实现修订0x0000"):
            transport.start_sar()

    def test_sar_decision_is_blocked_before_start(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        calls: list[list[Command]] = []

        def fake_transact(commands: list[Command], _timeout: float):
            calls.append(commands)
            return sar_status_probes(
                0,
                0,
                busy=False,
                done=False,
            )

        transport._transact_unlocked = fake_transact
        with self.assertRaisesRegex(RuntimeError, "请先重新启动SAR"):
            transport.submit_sar_decision(1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(
            all(command.opcode != OP_SAR_COMPARE for command in calls[0])
        )

    def test_sar_decision_waits_for_step_increment(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        call_number = 0

        def fake_transact(commands: list[Command], _timeout: float):
            nonlocal call_number
            call_number += 1
            if call_number == 1:
                return sar_status_probes(
                    SAR_INITIAL_CODE_Q16,
                    0,
                    busy=True,
                    done=False,
                )
            if call_number == 2:
                self.assertEqual(
                    [command.opcode for command in commands],
                    [OP_SAR_COMPARE],
                )
                return [Probe(0)]
            return sar_status_probes(
                16_384,
                1,
                busy=True,
                done=False,
            )

        transport._transact_unlocked = fake_transact
        status = transport.submit_sar_decision(
            1,
            completion_timeout=0.2,
        )
        self.assertEqual(status.step, 1)
        self.assertEqual(status.code_raw, 16_384)
        self.assertAlmostEqual(status.code, 0.25)

    def test_sar_status_decodes_negative_signed_q16(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        negative_half = dsm_signed_to_twos(-32_768)
        status = transport._sar_status_from_probes(
            sar_status_probes(
                negative_half,
                16,
                busy=False,
                done=True,
            )
        )
        self.assertEqual(status.code_raw, negative_half)
        self.assertAlmostEqual(status.code, -0.5)

    def test_sar_background_status_preserves_sticky_error_as_data(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        transport._transact_unlocked = (
            lambda _commands, _timeout: sar_status_probes(
                0,
                0,
                busy=False,
                done=False,
                error=0x05,
            )
        )
        status = transport.read_sar_status()
        self.assertEqual(status.fpga_error, 0x05)
        self.assertFalse(status.busy)


if __name__ == "__main__":
    unittest.main()
