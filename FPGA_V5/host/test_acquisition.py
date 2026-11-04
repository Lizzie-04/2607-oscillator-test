import csv
from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from acquisition import (
    AtomicDsmScanCsv,
    AtomicMultiTemperatureDetailCsv,
    AtomicSarCalibrationCsv,
    AtomicStatisticsCsv,
    CalibrationTrendHistory,
    MultiTemperatureCsvRun,
    MultiTemperatureSarRow,
    TrendHistory,
    calibration_axis_bounds,
    dsm_scan_values,
    multi_temperature_fieldnames,
    validate_data_count,
    validate_dsm_scan_count,
    validate_file_stem,
)
from gui import (
    dsm_override_next_bounds,
    multi_temperature_chip_trim_order,
    parse_gate_time,
    parse_minutes,
    parse_target_frequency,
    parse_temperature_point_count,
    sar_decision_bit,
)
from instrument_backend import KeithleyDmm7510Backend, Keysight53220ABackend
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


def multi_temperature_row(
    *,
    temp_index: int = 1,
    chip: int = 2,
    trim_c_coarse: str = "101",
    sar_step: int = 1,
) -> MultiTemperatureSarRow:
    return MultiTemperatureSarRow(
        temp_index=temp_index,
        chip=chip,
        target_frequency_mhz=10.0,
        trim_c_coarse=trim_c_coarse,
        sar_mode="regular",
        sar_step=sar_step,
        thermistor_ohm=12_345.678,
        frequency_mhz=10.25,
        sar_q16=-0.125,
        dsm_q16=0.375,
        poly_engine_freq=0.125,
        poly_engine_system=-0.25,
        samples=[MuSample(index, index + 100) for index in range(100)],
    )


class AcquisitionTest(unittest.TestCase):
    def test_data_count_requires_positive_hundreds(self) -> None:
        self.assertEqual(validate_data_count(100), 100)
        self.assertEqual(validate_data_count(1200), 1200)
        for invalid in (0, -100, 1, 150):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_data_count(invalid)

    def test_dsm_scan_count_and_values_include_endpoints(self) -> None:
        self.assertEqual(validate_dsm_scan_count(2), 2)
        self.assertEqual(dsm_scan_values(2), [-0.5, 0.5])
        self.assertEqual(dsm_scan_values(3), [-0.5, 0.0, 0.5])
        values = dsm_scan_values(5)
        self.assertEqual(len(values), 5)
        self.assertAlmostEqual(values[0], -0.5)
        self.assertAlmostEqual(values[-1], 0.5)
        with self.assertRaises(ValueError):
            validate_dsm_scan_count(1)

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
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                list(rows[0].keys()),
                [
                    "batch",
                    *(f"mu1_{index}" for index in range(1, 101)),
                    *(f"mu2_{index}" for index in range(1, 101)),
                ],
            )
            self.assertEqual(rows[0]["batch"], "1")
            self.assertEqual(rows[0]["mu1_1"], "0.0000000000")
            self.assertEqual(rows[0]["mu2_100"], "0.0015258789")
            self.assertEqual(rows[-1]["batch"], "2")
            self.assertEqual(rows[-1]["mu1_1"], "0.0015258789")

    def test_dsm_scan_csv_is_wide_form_atomic_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            started = datetime(2026, 7, 30, 16, 1, 2)
            samples = [MuSample(index, index + 1) for index in range(100)]
            with AtomicDsmScanCsv(
                output, "scan", started
            ) as writer:
                temporary_path = writer.temporary_path
                writer.append_scan_point(1, -0.5, 10.0, samples)
                writer.append_scan_point(2, 0.5, 11.0, samples)
                final_path = writer.publish(2)

            self.assertFalse(temporary_path.exists())
            self.assertEqual(
                final_path.name,
                "scan_20260730_160102.csv",
            )
            with final_path.open(
                "r", encoding="utf-8", newline=""
            ) as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                list(rows[0].keys()),
                [
                    "scan_index",
                    "dsm_q16",
                    "frequency_mhz",
                    *(f"mu1_{index}" for index in range(1, 101)),
                    *(f"mu2_{index}" for index in range(1, 101)),
                ],
            )
            self.assertEqual(rows[0]["scan_index"], "1")
            self.assertEqual(rows[0]["mu1_1"], "0.0000000000")
            self.assertEqual(rows[0]["mu2_100"], "0.0015258789")
            self.assertEqual(rows[1]["scan_index"], "2")
            self.assertEqual(rows[-1]["frequency_mhz"], "11.0000000000")

    def test_sar_calibration_csv_is_wide_form_and_variable_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            started = datetime(2026, 8, 3, 10, 20, 30)
            samples = [MuSample(index, index + 200) for index in range(200)]
            with AtomicSarCalibrationCsv(
                output,
                "sar",
                started,
                sample_count=200,
            ) as writer:
                temporary_path = writer.temporary_path
                writer.append_step(
                    1,
                    10.25,
                    -0.125,
                    0.375,
                    samples,
                )
                final_path = writer.publish(1)

            self.assertFalse(temporary_path.exists())
            self.assertEqual(
                final_path.name,
                "sar_20260803_102030.csv",
            )
            with final_path.open(
                "r", encoding="utf-8", newline=""
            ) as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                list(rows[0].keys()),
                [
                    "sar_step",
                    "frequency_mhz",
                    "sar_q16",
                    "dsm_q16",
                    *(f"mu1_{index}" for index in range(1, 201)),
                    *(f"mu2_{index}" for index in range(1, 201)),
                ],
            )
            self.assertEqual(rows[0]["sar_step"], "1")
            self.assertEqual(rows[0]["frequency_mhz"], "10.2500000000")
            self.assertEqual(rows[0]["sar_q16"], "-0.1250000000")
            self.assertEqual(rows[0]["dsm_q16"], "0.3750000000")
            self.assertEqual(rows[0]["mu1_200"], f"{199 / 65536:.10f}")
            self.assertEqual(rows[0]["mu2_200"], f"{399 / 65536:.10f}")

    def test_multi_temperature_detail_and_summary_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            started = datetime(2026, 8, 5, 9, 10, 11)
            run = MultiTemperatureCsvRun(output, "multi", started)
            with run.detail_writer(
                1,
                2,
                "101",
                trim_slot=1,
            ) as writer:
                row = multi_temperature_row(
                    temp_index=1,
                    chip=2,
                    trim_c_coarse="101",
                    sar_step=1,
                )
                writer.append_row(row)
                detail_path = writer.publish()
                run.add_summary_rows(writer.rows)
            summary_paths = run.publish_summaries()

            self.assertEqual(
                run.run_directory.name,
                "multi_20260805_091011",
            )
            self.assertEqual(
                detail_path.relative_to(run.run_directory).as_posix(),
                "temp_001/chip2_trim1_101.csv",
            )
            self.assertEqual(
                [path.name for path in summary_paths],
                ["chip2_summary.csv"],
            )
            with detail_path.open("r", encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                list(rows[0].keys()),
                multi_temperature_fieldnames(),
            )
            self.assertEqual(rows[0]["temp_index"], "1")
            self.assertEqual(rows[0]["chip"], "2")
            self.assertEqual(rows[0]["trim_c_coarse"], "101")
            self.assertEqual(rows[0]["thermistor_ohm"], "12345.6780000000")
            self.assertNotIn("sar_mode", rows[0])
            self.assertNotIn("thermistor_source", rows[0])

            with summary_paths[0].open(
                "r",
                encoding="utf-8",
                newline="",
            ) as csv_file:
                summary_rows = list(csv.DictReader(csv_file))
            self.assertEqual(summary_rows, rows)

    def test_multi_temperature_csv_allows_empty_thermistor(self) -> None:
        row = multi_temperature_row()
        empty_row = MultiTemperatureSarRow(
            temp_index=row.temp_index,
            chip=row.chip,
            target_frequency_mhz=row.target_frequency_mhz,
            trim_c_coarse=row.trim_c_coarse,
            sar_mode="dsm_override",
            sar_step=row.sar_step,
            thermistor_ohm=None,
            frequency_mhz=row.frequency_mhz,
            sar_q16=row.sar_q16,
            dsm_q16=row.dsm_q16,
            poly_engine_freq=row.poly_engine_freq,
            poly_engine_system=row.poly_engine_system,
            samples=row.samples,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with AtomicMultiTemperatureDetailCsv(
                Path(temporary),
                chip=2,
                trim_c_coarse="101",
            ) as writer:
                writer.append_row(empty_row)
                detail_path = writer.publish()
            with detail_path.open("r", encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
        self.assertEqual(rows[0]["thermistor_ohm"], "")
        self.assertNotIn("sar_mode", rows[0])
        self.assertNotIn("thermistor_source", rows[0])

    def test_failed_dsm_scan_csv_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            writer = AtomicDsmScanCsv(
                output,
                "failed_scan",
                datetime(2026, 7, 30, 16, 1, 3),
            )
            temporary_path = writer.temporary_path
            writer.append_scan_point(
                1,
                0.0,
                10.0,
                [MuSample(0, 0) for _ in range(100)],
            )
            writer.abort()
            self.assertFalse(temporary_path.exists())
            self.assertFalse(writer.final_path.exists())

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

    def test_monitor_snapshot_reads_live_and_calibration_in_one_session(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        calls: list[list[Command]] = []
        calibration_raw_values = [
            dsm_signed_to_twos(-16_384),
            dsm_signed_to_twos(32_768),
            dsm_signed_to_twos(-1),
            dsm_signed_to_twos(65_536),
        ]

        def fake_transact(commands: list[Command], _timeout: float = 15.0):
            calls.append(commands)
            live_values = [
                0x3456,
                0x0002,
                0x789A,
                0x0001,
                0xF00D,
                0x0000,
                0xABCD,
                0x0002,
                42,
                1024,
                17,
            ]
            calibration_values: list[int] = []
            for raw in calibration_raw_values:
                calibration_values.extend(
                    (raw & 0xFFFF, (raw >> 16) & 0x1FFF)
                )
            calibration_values.extend((77, 0b11))
            return [
                Probe(0),
                *(probe_with_response(value) for value in live_values),
                Probe(0),
                *(
                    probe_with_response(value)
                    for value in calibration_values
                ),
                *sar_status_probes(
                    1 << 15,
                    3,
                    busy=True,
                    done=False,
                ),
            ]

        transport.transact = fake_transact
        snapshot = transport.read_monitor_snapshot()

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [command.opcode for command in calls[0]],
            [
                OP_LATCH_LIVE_SAMPLE,
                *([OP_READ_REGISTER] * 11),
                OP_LATCH_CALIBRATION_SAMPLE,
                *([OP_READ_REGISTER] * 13),
            ],
        )
        self.assertEqual(
            [command.index for command in calls[0]],
            [0, *range(21, 32), 0, *range(32, 42), 6, 7, 8],
        )
        self.assertEqual(snapshot.live.sample.mu1_raw, 0x23456)
        self.assertEqual(snapshot.live.sample.mu2_raw, 0x1789A)
        self.assertEqual(snapshot.live.sequence, 42)
        self.assertAlmostEqual(snapshot.calibration.poly_freq, -0.25)
        self.assertAlmostEqual(snapshot.calibration.dsm_input, 1.0)
        self.assertTrue(snapshot.calibration.sar_status.busy)

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

    def test_sar_decision_bit_uses_measured_vs_target_frequency(self) -> None:
        self.assertEqual(sar_decision_bit(9.99, 10.0), 1)
        self.assertEqual(sar_decision_bit(10.01, 10.0), 0)
        self.assertEqual(sar_decision_bit(10.0, 10.0), 0)
        for invalid in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    sar_decision_bit(invalid, 10.0)

    def test_multi_temperature_input_parsers(self) -> None:
        self.assertEqual(parse_temperature_point_count("3"), 3)
        self.assertEqual(parse_minutes("1.5", "等待时间"), 1.5)
        for invalid in ("", "0", "-1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_temperature_point_count(invalid)
        for invalid in ("", "-0.1", "nan", "inf"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_minutes(invalid, "测试时间")

    def test_multi_temperature_chip_trim_order_is_chip_major(self) -> None:
        self.assertEqual(
            multi_temperature_chip_trim_order(
                (3, 1),
                ("000", "101", "101"),
            ),
            (
                (1, 1, "000"),
                (1, 2, "101"),
                (1, 3, "101"),
                (3, 1, "000"),
                (3, 2, "101"),
                (3, 3, "101"),
            ),
        )
        with self.assertRaises(ValueError):
            multi_temperature_chip_trim_order((5,), ("000", "001", "010"))

    def test_dsm_override_sar_direction_uses_inverse_frequency_slope(self) -> None:
        self.assertEqual(
            dsm_override_next_bounds(-0.5, 0.5, 0.0, 9.9, 10.0),
            (-0.5, 0.0),
        )
        self.assertEqual(
            dsm_override_next_bounds(-0.5, 0.5, 0.0, 10.1, 10.0),
            (0.0, 0.5),
        )
        self.assertEqual(
            dsm_override_next_bounds(-0.5, 0.5, 0.0, 10.0, 10.0),
            (0.0, 0.5),
        )

    def test_gate_time_is_positive_and_updates_53220a_backend(self) -> None:
        self.assertEqual(parse_gate_time("5"), 5.0)
        for invalid in ("", "0", "-1", "nan", "inf"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_gate_time(invalid)

        backend = Keysight53220ABackend(gate_time_s=5.0)
        backend.set_gate_time_s(12.5)
        self.assertEqual(backend.gate_time_s, 12.5)
        self.assertEqual(backend.timeout_s, 15.5)

    def test_dmm7510_backend_is_lazy_and_validates_settings(self) -> None:
        backend = KeithleyDmm7510Backend(nplc=1.0, timeout_s=3.0)
        self.assertEqual(backend.describe(), "Keithley DMM7510自动检索")
        self.assertIsNone(backend.selected_resource)
        with self.assertRaises(ValueError):
            KeithleyDmm7510Backend(nplc=0.0)
        with self.assertRaises(ValueError):
            KeithleyDmm7510Backend(timeout_s=0.0)

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
