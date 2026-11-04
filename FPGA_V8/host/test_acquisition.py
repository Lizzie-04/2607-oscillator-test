import csv
from datetime import datetime
from pathlib import Path
from queue import SimpleQueue
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
    ControlApp,
    MULTI_SAR_MODE_DSM_OVERRIDE,
    MultiTemperatureRunLogger,
    MultiTemperatureSettings,
    MultiTemperatureStepTimings,
    dsm_override_next_bounds,
    multi_temperature_chip_trim_order,
    parse_gate_time,
    parse_minutes,
    parse_startup_attempts,
    parse_startup_settle_seconds,
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
            self.assertEqual(rows[0]["record_status"], "OK")
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
        self.assertEqual(rows[0]["record_status"], "OK")
        self.assertNotIn("sar_mode", rows[0])
        self.assertNotIn("thermistor_source", rows[0])

    def test_multi_temperature_csv_writes_error_row_with_empty_fields(self) -> None:
        row = multi_temperature_row()
        error_row = MultiTemperatureSarRow(
            temp_index=row.temp_index,
            chip=row.chip,
            target_frequency_mhz=row.target_frequency_mhz,
            trim_c_coarse=row.trim_c_coarse,
            sar_mode=row.sar_mode,
            sar_step=0,
            thermistor_ohm=None,
            frequency_mhz=None,
            sar_q16=None,
            dsm_q16=None,
            poly_engine_freq=None,
            poly_engine_system=None,
            samples=[],
            record_status="ERROR",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with AtomicMultiTemperatureDetailCsv(
                Path(temporary),
                chip=2,
                trim_c_coarse="101",
            ) as writer:
                writer.append_row(error_row)
                detail_path = writer.publish()
            with detail_path.open("r", encoding="utf-8", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
        self.assertEqual(rows[0]["sar_step"], "0")
        self.assertEqual(rows[0]["record_status"], "ERROR")
        self.assertEqual(rows[0]["frequency_mhz"], "")
        self.assertEqual(rows[0]["mu1_1"], "")
        self.assertEqual(rows[0]["mu2_100"], "")

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

    def test_live_sample_uses_latched_snapshot_registers(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        actions = []
        transport._write_action = lambda address, data=0: actions.append(address)
        transport._read32 = lambda address, count=1: [
            0x3456, 0x0002, 0x789A, 0x0001, 0xF00D, 0x0000,
            0xABCD, 0x0002, 42, 1024, 17,
        ]
        snapshot = transport.read_live_sample()
        self.assertEqual(actions, [0x0068])
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
        raw_values = [
            dsm_signed_to_twos(-16_384),
            dsm_signed_to_twos(32_768),
            dsm_signed_to_twos(-1),
            dsm_signed_to_twos(65_536),
        ]

        register_values: list[int] = []
        for raw in raw_values:
            register_values.extend((raw & 0xFFFF, (raw >> 16) & 0x1FFF))
        register_values.extend((77, 0b11))
        transport._write_action = lambda address, data=0: None
        transport._read32 = lambda address, count=1: (
            register_values if count == 10 else ([1 << 15, 0, 3] if count == 3 else [1 << 3])
        )
        snapshot = transport.read_calibration_snapshot()
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
        calibration_raw_values = [
            dsm_signed_to_twos(-16_384),
            dsm_signed_to_twos(32_768),
            dsm_signed_to_twos(-1),
            dsm_signed_to_twos(65_536),
        ]

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
            calibration_values.extend((raw & 0xFFFF, (raw >> 16) & 0x1FFF))
        calibration_values.extend((77, 0b11))
        actions = []
        transport._write_action = lambda address, data=0: actions.append(address)

        def fake_read(address, count=1):
            if count == 11:
                return live_values
            if count == 10:
                return calibration_values
            if count == 3:
                return [1 << 15, 0, 3]
            return [1 << 3]

        transport._read32 = fake_read
        snapshot = transport.read_monitor_snapshot()
        self.assertEqual(actions, [0x0068, 0x006C])
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
            avalon_abi_version=1,
            magic=0x4F534356,
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
        self.assertIn("不是兼容的FPGA V8", v1.summary)
        self.assertIn("实现修订", old_v4.summary)

    def test_target_frequency_is_positive_and_finite(self) -> None:
        self.assertEqual(parse_target_frequency("10.5"), 10.5)
        for invalid in ("", "0", "-1", "nan", "inf"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_target_frequency(invalid)

    def test_startup_attempts_requires_positive_integer(self) -> None:
        self.assertEqual(parse_startup_attempts("5"), 5)
        for invalid in ("", "0", "-1", "1.5", "abc"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_startup_attempts(invalid)

    def test_startup_settle_seconds_requires_positive_finite_value(self) -> None:
        self.assertEqual(parse_startup_settle_seconds("5"), 5.0)
        self.assertEqual(parse_startup_settle_seconds("2.5"), 2.5)
        for invalid in ("", "0", "-1", "nan", "inf", "abc"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_startup_settle_seconds(invalid)

    def test_startup_diagnostic_reads_three_times_before_rewriting(self) -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.lines: list[tuple[str, str, str]] = []

            def log(self, level: str, category: str, message: str) -> None:
                self.lines.append((level, category, message))

        app = object.__new__(ControlApp)
        app.multi_temperature_progress = SimpleQueue()
        app.multi_temperature_stop_requested = threading.Event()
        write_calls: list[tuple[int, str, bool, int]] = []

        def fake_write(
            _transport,
            *,
            chip,
            trim_c_coarse,
            en_xo,
            control_sample_count,
        ) -> None:
            write_calls.append(
                (chip, trim_c_coarse, en_xo, control_sample_count)
            )

        frequencies = iter([1.0, 1.1, 1.2, 2.0])
        read_count = 0

        def fake_read(_logger) -> float:
            nonlocal read_count
            read_count += 1
            return next(frequencies)

        app._write_multi_temperature_control_word = fake_write
        app._read_startup_frequency_mhz = fake_read
        logger = FakeLogger()
        settings = MultiTemperatureSettings(
            temperature_points=1,
            wait_seconds=0.0,
            test_seconds=0.0,
            chips=(2,),
            target_frequencies={2: 2.0},
            trim_c_coarse_values=("000", "011", "111"),
            sar_mode=MULTI_SAR_MODE_DSM_OVERRIDE,
            output_directory=Path("."),
            file_stem="test",
            startup_min_frequency_mhz=1.9,
            startup_max_frequency_mhz=2.1,
            startup_settle_seconds=0.001,
            startup_attempts=2,
            control_sample_count=20000,
        )

        ok, started, frequency, message = ControlApp._run_startup_diagnostic(
            app,
            object(),
            logger,
            settings=settings,
            temp_index=1,
            chip=2,
            trim_slot=1,
            trim_c_coarse="000",
        )

        self.assertTrue(ok)
        self.assertTrue(started)
        self.assertEqual(frequency, 2.0)
        self.assertEqual(message, "OK")
        self.assertEqual(
            write_calls,
            [(2, "000", True, 20000), (2, "000", True, 20000)],
        )
        self.assertEqual(read_count, 4)
        text = "\n".join(message for _level, _category, message in logger.lines)
        self.assertIn("仅再次读频率", text)
        self.assertIn("重新写入控制字", text)

    def test_multi_temperature_logger_separates_debug_from_gui_and_main_log(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main_path = root / "multi_temperature.log"
            debug_path = root / "multi_temperature_debug.log"
            progress = SimpleQueue()
            logger = MultiTemperatureRunLogger(
                main_path,
                progress,
                debug_path=debug_path,
            )
            info_line = logger.log("INFO", "RUN", "start")
            debug_line = logger.log(
                "DEBUG", "AVALON", "READ32 addr=0x0010 count=1"
            )
            warning_line = logger.log("WARN", "JTAG", "retry")
            logger.close()

            main_text = main_path.read_text(encoding="utf-8")
            debug_text = debug_path.read_text(encoding="utf-8")
            self.assertIn(info_line, main_text)
            self.assertIn(warning_line, main_text)
            self.assertNotIn(debug_line, main_text)
            self.assertEqual(debug_text.strip(), debug_line)
            self.assertEqual(progress.get_nowait(), ("log", info_line))
            self.assertEqual(progress.get_nowait(), ("log", warning_line))
            self.assertTrue(progress.empty())

    def test_multi_temperature_step_timing_log_is_indented_and_complete(self) -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.lines: list[tuple[str, str, str]] = []

            def log(self, level: str, category: str, message: str) -> None:
                self.lines.append((level, category, message))

        logger = FakeLogger()
        app = object.__new__(ControlApp)
        ControlApp._log_multi_step_timings(
            app,
            logger,
            temp_index=1,
            chip=2,
            trim_c_coarse="000",
            sar_step=3,
            timings=MultiTemperatureStepTimings(
                dsm_write_s=0.1,
                settle_s=5.2,
                frequency_s=1.3,
                dmm_s=0.4,
                snapshot_s=0.5,
                statistics_s=2.6,
                csv_s=0.01,
            ),
        )

        self.assertEqual(logger.lines[0][0], "INFO")
        self.assertEqual(logger.lines[0][1], "SAR")
        message = logger.lines[0][2]
        self.assertTrue(message.startswith("\t温度点1 chip2"))
        for label in (
            "DSM写入耗时",
            "5s稳定耗时",
            "频率读取耗时",
            "DMM耗时",
            "快照耗时",
            "100组μ统计耗时",
            "CSV写入耗时",
        ):
            self.assertIn(label, message)

    def test_multi_temperature_reuses_gui_lifetime_system_console(self) -> None:
        class FakeSleepGuard:
            def __init__(self, _logger) -> None:
                self.enabled = False

            def enable(self) -> None:
                self.enabled = True

            def disable(self) -> None:
                self.enabled = False

        class FakeBaseTransport:
            def __init__(self) -> None:
                self.health_checks = 0
                self.log_callback = None

            def set_log_callback(self, callback) -> None:
                self.log_callback = callback

            def health_check(self):
                self.health_checks += 1
                return JtagDiagnostic(
                    hardware="fake",
                    device="fake",
                    protocol_version=PROTOCOL_VERSION,
                    implementation_revision=IMPLEMENTATION_REVISION,
                    avalon_abi_version=1,
                    magic=0x4F534356,
                )

        app = object.__new__(ControlApp)
        app.multi_temperature_progress = SimpleQueue()
        app.multi_temperature_stop_requested = threading.Event()
        app.multi_temperature_logger = None
        app.gate_time_s = 5.0
        base_transport = FakeBaseTransport()
        used_transports: list[tuple[int, FakeBaseTransport]] = []

        app._require_transport = lambda: base_transport
        app._multi_wait_with_stop = (
            lambda _seconds, *, stage, temp_index: True
        )

        def fake_startup(
            transport,
            _logger,
            *,
            settings,
            temp_index,
            chip,
            trim_slot,
            trim_c_coarse,
        ):
            used_transports.append((temp_index, transport))
            return False, False, None, "forced startup failure"

        app._run_startup_diagnostic = fake_startup

        settings = MultiTemperatureSettings(
            temperature_points=2,
            wait_seconds=0.0,
            test_seconds=0.0,
            chips=(2,),
            target_frequencies={2: 2.0},
            trim_c_coarse_values=("000", "011", "111"),
            sar_mode=MULTI_SAR_MODE_DSM_OVERRIDE,
            output_directory=Path(tempfile.mkdtemp()),
            file_stem="persistent_test",
            startup_min_frequency_mhz=1.9,
            startup_max_frequency_mhz=2.1,
            startup_settle_seconds=0.001,
            startup_attempts=1,
            control_sample_count=20000,
        )

        with patch("gui.WindowsSleepInhibitor", FakeSleepGuard):
            run_directory = ControlApp._execute_multi_temperature_calibration(
                app,
                settings,
                datetime(2026, 8, 7, 12, 0, 0),
            )

        self.assertTrue(run_directory.is_dir())
        self.assertEqual(base_transport.health_checks, 2)
        self.assertIsNone(base_transport.log_callback)
        self.assertEqual(len(used_transports), 6)
        self.assertTrue(
            all(transport is base_transport for _, transport in used_transports)
        )

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
        diagnostic = JtagDiagnostic(
            hardware="fake",
            device="fake",
            protocol_version=PROTOCOL_VERSION,
            implementation_revision=IMPLEMENTATION_REVISION,
            avalon_abi_version=1,
            magic=0x4F534356,
        )
        transport.health_check = lambda: diagnostic
        actions = []
        transport._write_action = lambda address, data=0: actions.append((address, data))
        statuses = [
            SarStatus(0, 0, False, False),
            SarStatus(SAR_INITIAL_CODE_Q16, 0, True, False),
        ]
        transport._sar_status_unlocked = lambda **_kwargs: statuses.pop(0)
        status = transport.start_sar(ready_timeout=0.2)
        self.assertTrue(status.busy)
        self.assertEqual(status.code_raw, SAR_INITIAL_CODE_Q16)
        self.assertEqual(actions, [(0x0060, 0), (0x0058, 1)])

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

    def test_sar_start_reports_timeout_if_2mhz_state_does_not_advance(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        transport.health_check = lambda: JtagDiagnostic(
            hardware="fake", device="fake",
            protocol_version=PROTOCOL_VERSION,
            implementation_revision=IMPLEMENTATION_REVISION,
            avalon_abi_version=1, magic=0x4F534356,
        )
        transport._write_action = lambda address, data=0: None
        transport._sar_status_unlocked = lambda **_kwargs: SarStatus(
            0, 0, False, False
        )
        with self.assertRaisesRegex(TimeoutError, "未进入运行态"):
            transport.start_sar(ready_timeout=0.01)

    def test_sar_start_rejects_old_v2_implementation(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        transport.health_check = lambda: JtagDiagnostic(
            hardware="fake", device="fake",
            protocol_version=PROTOCOL_VERSION,
            implementation_revision=0,
            avalon_abi_version=1, magic=0x4F534356,
        )
        with self.assertRaisesRegex(RuntimeError, "不是兼容"):
            transport.start_sar()

    def test_sar_decision_is_blocked_before_start(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        transport._sar_status_unlocked = lambda **_kwargs: SarStatus(
            0, 0, False, False
        )
        with self.assertRaisesRegex(RuntimeError, "尚未进入运行态"):
            transport.submit_sar_decision(1)

    def test_sar_decision_waits_for_step_increment(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        statuses = [
            SarStatus(SAR_INITIAL_CODE_Q16, 0, True, False),
            SarStatus(16_384, 1, True, False),
        ]
        transport._sar_status_unlocked = lambda **_kwargs: statuses.pop(0)
        actions = []
        transport._write_action = lambda address, data=0: actions.append((address, data))
        status = transport.submit_sar_decision(
            1,
            completion_timeout=0.2,
        )
        self.assertEqual(actions, [(0x005C, 1)])
        self.assertEqual(status.step, 1)
        self.assertEqual(status.code_raw, 16_384)
        self.assertAlmostEqual(status.code, 0.25)

    def test_sar_status_decodes_negative_signed_q16(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        negative_half = dsm_signed_to_twos(-32_768)
        status = transport._sar_from_values(
            [negative_half & 0xFFFF, (negative_half >> 16) & 0x1FFF, 16],
            1 << 4,
        )
        self.assertEqual(status.code_raw, negative_half)
        self.assertAlmostEqual(status.code, -0.5)

    def test_sar_background_status_preserves_sticky_error_as_data(self) -> None:
        transport = object.__new__(QuartusJtagTransport)
        transport._transaction_lock = threading.RLock()
        transport._read32 = lambda address, count=1: (
            [0, 0, 0] if count == 3 else [0x05 << 8]
        )
        status = transport.read_sar_status()
        self.assertEqual(status.fpga_error, 0x05)
        self.assertFalse(status.busy)

    def test_gui_close_is_blocked_during_multi_temperature_run(self) -> None:
        class Var:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value) -> None:
                self.value = value

        class Logger:
            def __init__(self) -> None:
                self.entries = []

            def log(self, *entry) -> None:
                self.entries.append(entry)

        app = object.__new__(ControlApp)
        app.multi_temperature_running = True
        app.status_var = Var()
        app.multi_temperature_status_var = Var()
        app.multi_temperature_logger = Logger()
        app.destroy = lambda: self.fail("running GUI was destroyed")
        ControlApp._close(app)
        self.assertIn("禁止", app.status_var.value)
        self.assertEqual(app.multi_temperature_logger.entries[-1][0], "WARN")

    def test_gui_exit_closes_shared_system_console_transport(self) -> None:
        events = []

        class Closeable:
            def close(self) -> None:
                events.append("close")

        class Executor:
            def shutdown(self, *, wait, cancel_futures) -> None:
                events.append((wait, cancel_futures))

        app = object.__new__(ControlApp)
        app.multi_temperature_running = False
        app.operation = None
        app.frequency_operation = None
        app.frequency_backend = Closeable()
        app.resistance_backend = Closeable()
        app.transport = Closeable()
        app.frequency_executor = Executor()
        app.executor = Executor()
        app.destroy = lambda: events.append("destroy")
        ControlApp._close(app)
        self.assertEqual(events.count("close"), 3)
        self.assertEqual(events[-1], "destroy")


if __name__ == "__main__":
    unittest.main()
