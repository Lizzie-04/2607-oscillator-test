"""Host-side μ history and atomic multi-batch CSV assembly."""

from __future__ import annotations

from collections import deque
import csv
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import tempfile
from collections.abc import Iterable

from jtag_transport import (
    CalibrationSnapshot,
    DSM_OVERRIDE_SCALE,
    MuSample,
    STATISTICS_SAMPLES,
)


TREND_HISTORY_LIMIT = 1000
DEFAULT_DATA_COUNT = 100

_INVALID_FILENAME_CHARACTERS = set('<>:"/\\|?*')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def mu_wide_fieldnames(
    *,
    prefix: list[str] | None = None,
    sample_count: int = STATISTICS_SAMPLES,
) -> list[str]:
    validate_data_count(sample_count)
    fields = list(prefix or [])
    fields.extend(
        f"mu1_{index}" for index in range(1, sample_count + 1)
    )
    fields.extend(
        f"mu2_{index}" for index in range(1, sample_count + 1)
    )
    return fields


def mu_wide_values(
    samples: list[MuSample],
    *,
    expected_count: int = STATISTICS_SAMPLES,
) -> list[str]:
    validate_data_count(expected_count)
    if len(samples) != expected_count:
        raise ValueError(
            f"每行必须恰好包含{expected_count}组μ数据"
        )
    return [
        *(f"{sample.mu1:.10f}" for sample in samples),
        *(f"{sample.mu2:.10f}" for sample in samples),
    ]


def validate_data_count(value: int) -> int:
    if value <= 0:
        raise ValueError("数据量必须为正整数")
    if value % STATISTICS_SAMPLES:
        raise ValueError(
            f"数据量必须为{STATISTICS_SAMPLES}的整数倍"
        )
    return value


def validate_dsm_scan_count(value: int) -> int:
    if value < 2:
        raise ValueError("扫描总数必须是不小于2的整数")
    return value


def dsm_scan_values(count: int) -> list[float]:
    total = validate_dsm_scan_count(count)
    start = -DSM_OVERRIDE_SCALE // 2
    stop = DSM_OVERRIDE_SCALE // 2
    if total == 2:
        raw_values = [start, stop]
    else:
        raw_values = [
            round(start + index * (stop - start) / (total - 1))
            for index in range(total)
        ]
        raw_values[0] = start
        raw_values[-1] = stop
    return [raw / DSM_OVERRIDE_SCALE for raw in raw_values]


def validate_file_stem(text: str) -> str:
    stem = text.strip()
    if not stem:
        raise ValueError("文件名不能为空")
    if any(character in _INVALID_FILENAME_CHARACTERS for character in stem):
        raise ValueError('文件名不能包含 <>:"/\\|?*')
    if stem.endswith((" ", ".")):
        raise ValueError("文件名不能以空格或句点结尾")
    if stem.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError("文件名不能使用Windows保留设备名")
    return stem


def statistics_filename(stem: str, started_at: datetime) -> str:
    valid_stem = validate_file_stem(stem)
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    return f"{valid_stem}_{timestamp}.csv"


def run_directory_name(stem: str, started_at: datetime) -> str:
    valid_stem = validate_file_stem(stem)
    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    return f"{valid_stem}_{timestamp}"


class TrendHistory:
    def __init__(self, limit: int = TREND_HISTORY_LIMIT) -> None:
        if limit <= 0:
            raise ValueError("趋势历史容量必须为正整数")
        self._mu1: deque[float] = deque(maxlen=limit)
        self._mu2: deque[float] = deque(maxlen=limit)
        self._onchip_mu1: deque[float | None] = deque(maxlen=limit)
        self._onchip_mu2: deque[float | None] = deque(maxlen=limit)

    def append(
        self,
        sample: MuSample,
        onchip_sample: MuSample | None = None,
    ) -> None:
        self._mu1.append(sample.mu1)
        self._mu2.append(sample.mu2)
        self._onchip_mu1.append(
            None if onchip_sample is None else onchip_sample.mu1
        )
        self._onchip_mu2.append(
            None if onchip_sample is None else onchip_sample.mu2
        )

    def extend(self, samples: Iterable[MuSample]) -> None:
        for sample in samples:
            self.append(sample)

    def clear(self) -> None:
        self._mu1.clear()
        self._mu2.clear()
        self._onchip_mu1.clear()
        self._onchip_mu2.clear()

    @property
    def size(self) -> int:
        return len(self._mu1)

    def values(
        self,
    ) -> tuple[
        list[float],
        list[float],
        list[float | None],
        list[float | None],
    ]:
        return (
            list(self._mu1),
            list(self._mu2),
            list(self._onchip_mu1),
            list(self._onchip_mu2),
        )


class CalibrationTrendHistory:
    def __init__(self, limit: int = TREND_HISTORY_LIMIT) -> None:
        if limit <= 0:
            raise ValueError("校准趋势历史容量必须为正整数")
        self._poly_freq: deque[float] = deque(maxlen=limit)
        self._poly_system: deque[float] = deque(maxlen=limit)
        self._sar_correction: deque[float] = deque(maxlen=limit)
        self._dsm_input: deque[float] = deque(maxlen=limit)

    def append(self, snapshot: CalibrationSnapshot) -> None:
        self._poly_freq.append(snapshot.poly_freq)
        self._poly_system.append(snapshot.poly_system)
        self._sar_correction.append(snapshot.sar_correction)
        self._dsm_input.append(snapshot.dsm_input)

    def clear(self) -> None:
        self._poly_freq.clear()
        self._poly_system.clear()
        self._sar_correction.clear()
        self._dsm_input.clear()

    @property
    def size(self) -> int:
        return len(self._poly_freq)

    def values(self) -> tuple[list[float], list[float], list[float], list[float]]:
        return (
            list(self._poly_freq),
            list(self._poly_system),
            list(self._sar_correction),
            list(self._dsm_input),
        )


MULTI_TEMPERATURE_PREFIX_FIELDS = [
    "temp_index",
    "chip",
    "target_frequency_mhz",
    "trim_c_coarse",
    "sar_step",
    "thermistor_ohm",
    "frequency_mhz",
    "sar_q16",
    "dsm_q16",
    "poly_engine_freq",
    "poly_engine_system",
]


@dataclass(frozen=True, slots=True)
class MultiTemperatureSarRow:
    temp_index: int
    chip: int
    target_frequency_mhz: float
    trim_c_coarse: str
    sar_mode: str
    sar_step: int
    thermistor_ohm: float | None
    frequency_mhz: float
    sar_q16: float
    dsm_q16: float
    poly_engine_freq: float
    poly_engine_system: float
    samples: list[MuSample]

    def validate(self) -> None:
        if self.temp_index <= 0:
            raise ValueError("温度点编号必须从1开始")
        if not 0 <= self.chip <= 4:
            raise ValueError("chip编号必须在0至4之间")
        if len(self.trim_c_coarse) != 3 or set(self.trim_c_coarse) - {"0", "1"}:
            raise ValueError("TRIM_C_COARSE必须是3-bit二进制码字")
        if self.sar_mode not in ("regular", "dsm_override"):
            raise ValueError("SAR模式必须为regular或dsm_override")
        if self.sar_step <= 0:
            raise ValueError("SAR步骤编号必须从1开始")
        for name, value in (
            ("目标频率", self.target_frequency_mhz),
            ("实测频率", self.frequency_mhz),
            ("SAR校准输入", self.sar_q16),
            ("DSM输入", self.dsm_q16),
            ("poly_engine_freq", self.poly_engine_freq),
            ("poly_engine_system", self.poly_engine_system),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name}必须为有限数值")
        if self.target_frequency_mhz <= 0 or self.frequency_mhz <= 0:
            raise ValueError("目标频率和实测频率必须大于0")
        if self.thermistor_ohm is not None and not math.isfinite(
            self.thermistor_ohm
        ):
            raise ValueError("温敏电阻值必须为空或有限数值")
        if self.thermistor_ohm is not None and self.thermistor_ohm <= 0:
            raise ValueError("温敏电阻值必须为空或大于0")
        if len(self.samples) != STATISTICS_SAMPLES:
            raise ValueError(
                f"每个SAR步骤必须恰好包含{STATISTICS_SAMPLES}组μ数据"
            )


def multi_temperature_fieldnames() -> list[str]:
    return mu_wide_fieldnames(prefix=MULTI_TEMPERATURE_PREFIX_FIELDS)


def multi_temperature_values(row: MultiTemperatureSarRow) -> list[str]:
    row.validate()
    thermistor_text = (
        "" if row.thermistor_ohm is None else f"{row.thermistor_ohm:.10f}"
    )
    return [
        row.temp_index,
        row.chip,
        f"{row.target_frequency_mhz:.10f}",
        row.trim_c_coarse,
        row.sar_step,
        thermistor_text,
        f"{row.frequency_mhz:.10f}",
        f"{row.sar_q16:.10f}",
        f"{row.dsm_q16:.10f}",
        f"{row.poly_engine_freq:.10f}",
        f"{row.poly_engine_system:.10f}",
        *mu_wide_values(row.samples),
    ]


class AtomicMultiTemperatureDetailCsv:
    """Publish one chip/TRIM detail CSV after the detail is complete."""

    def __init__(
        self,
        temp_directory: Path,
        *,
        chip: int,
        trim_c_coarse: str,
        trim_slot: int | None = None,
    ) -> None:
        if not 0 <= chip <= 4:
            raise ValueError("chip编号必须在0至4之间")
        if len(trim_c_coarse) != 3 or set(trim_c_coarse) - {"0", "1"}:
            raise ValueError("TRIM_C_COARSE必须是3-bit二进制码字")
        temp_directory.mkdir(parents=True, exist_ok=True)
        if trim_slot is not None and trim_slot <= 0:
            raise ValueError("TRIM序号必须从1开始")
        trim_prefix = "" if trim_slot is None else f"trim{trim_slot}_"
        self.final_path = temp_directory / (
            f"chip{chip}_{trim_prefix}{trim_c_coarse}.csv"
        )
        if self.final_path.exists():
            raise FileExistsError(f"多温度明细文件已存在：{self.final_path}")

        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{self.final_path.stem}_",
            suffix=".tmp",
            dir=temp_directory,
            delete=False,
        )
        self._file = temporary
        self.temporary_path = Path(temporary.name)
        self._writer = csv.writer(temporary)
        self._writer.writerow(multi_temperature_fieldnames())
        self.rows: list[MultiTemperatureSarRow] = []
        self._published = False

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def append_row(self, row: MultiTemperatureSarRow) -> None:
        if self._file.closed:
            raise RuntimeError("多温度明细CSV已经关闭")
        self._writer.writerow(multi_temperature_values(row))
        self.rows.append(row)
        self._file.flush()

    def publish(self) -> Path:
        if not self.rows:
            raise RuntimeError("多温度明细CSV没有可发布的数据行")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        if self.final_path.exists():
            raise FileExistsError(f"多温度明细文件已存在：{self.final_path}")
        self.temporary_path.replace(self.final_path)
        self._published = True
        return self.final_path

    def abort(self) -> None:
        if not self._file.closed:
            self._file.close()
        if not self._published:
            self.temporary_path.unlink(missing_ok=True)

    def __enter__(self) -> "AtomicMultiTemperatureDetailCsv":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._published:
            self.abort()


class MultiTemperatureCsvRun:
    """Manage per-temperature detail CSVs and per-chip summary CSVs."""

    def __init__(
        self,
        output_directory: Path,
        stem: str,
        started_at: datetime,
    ) -> None:
        output_directory.mkdir(parents=True, exist_ok=True)
        self.run_directory = output_directory / run_directory_name(
            stem,
            started_at,
        )
        if self.run_directory.exists():
            raise FileExistsError(
                f"多温度运行目录已存在：{self.run_directory}"
            )
        self.run_directory.mkdir()
        self._summary_rows: dict[int, list[MultiTemperatureSarRow]] = {}

    def temp_directory(self, temp_index: int) -> Path:
        if temp_index <= 0:
            raise ValueError("温度点编号必须从1开始")
        directory = self.run_directory / f"temp_{temp_index:03d}"
        directory.mkdir(exist_ok=True)
        return directory

    def detail_writer(
        self,
        temp_index: int,
        chip: int,
        trim_c_coarse: str,
        *,
        trim_slot: int | None = None,
    ) -> AtomicMultiTemperatureDetailCsv:
        return AtomicMultiTemperatureDetailCsv(
            self.temp_directory(temp_index),
            chip=chip,
            trim_c_coarse=trim_c_coarse,
            trim_slot=trim_slot,
        )

    def add_summary_rows(self, rows: Iterable[MultiTemperatureSarRow]) -> None:
        for row in rows:
            row.validate()
            self._summary_rows.setdefault(row.chip, []).append(row)

    def publish_summaries(self) -> list[Path]:
        paths: list[Path] = []
        for chip in sorted(self._summary_rows):
            rows = self._summary_rows[chip]
            if not rows:
                continue
            final_path = self.run_directory / f"chip{chip}_summary.csv"
            if final_path.exists():
                raise FileExistsError(f"芯片汇总文件已存在：{final_path}")
            temporary = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=f".{final_path.stem}_",
                suffix=".tmp",
                dir=self.run_directory,
                delete=False,
            )
            temporary_path = Path(temporary.name)
            try:
                writer = csv.writer(temporary)
                writer.writerow(multi_temperature_fieldnames())
                for row in rows:
                    writer.writerow(multi_temperature_values(row))
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary.close()
                temporary_path.replace(final_path)
                paths.append(final_path)
            finally:
                if not temporary.closed:
                    temporary.close()
                temporary_path.unlink(missing_ok=True)
        return paths


def calibration_axis_bounds(values: Iterable[float]) -> tuple[float, float]:
    """Return truthful signed plot bounds that always include DSM endpoints."""
    finite_values = [value for value in values if math.isfinite(value)]
    minimum = min([-0.5, *finite_values])
    maximum = max([0.5, *finite_values])
    span = maximum - minimum
    padding = max(span * 0.08, 0.05)
    return minimum - padding, maximum + padding


class AtomicStatisticsCsv:
    """Stream complete 100-point batches, then atomically publish one CSV."""

    def __init__(
        self,
        output_directory: Path,
        stem: str,
        started_at: datetime,
    ) -> None:
        output_directory.mkdir(parents=True, exist_ok=True)
        self.final_path = (
            output_directory
            / statistics_filename(stem, started_at)
        )
        if self.final_path.exists():
            raise FileExistsError(f"统计文件已存在：{self.final_path}")

        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{self.final_path.stem}_",
            suffix=".tmp",
            dir=output_directory,
            delete=False,
        )
        self._file = temporary
        self.temporary_path = Path(temporary.name)
        self._writer = csv.writer(temporary)
        self._writer.writerow(mu_wide_fieldnames(prefix=["batch"]))
        self.row_count = 0
        self._published = False

    def append_batch(
        self, batch_number: int, samples: list[MuSample]
    ) -> None:
        if self._file.closed:
            raise RuntimeError("统计CSV已经关闭")
        if batch_number <= 0:
            raise ValueError("批次编号必须从1开始")
        if len(samples) != STATISTICS_SAMPLES:
            raise ValueError(
                f"每批必须恰好包含{STATISTICS_SAMPLES}组数据"
            )
        self._writer.writerow([batch_number, *mu_wide_values(samples)])
        self.row_count += 1
        self._file.flush()

    def publish(self, expected_rows: int) -> Path:
        validate_data_count(expected_rows)
        expected_batches = expected_rows // STATISTICS_SAMPLES
        if self.row_count != expected_batches:
            raise RuntimeError(
                f"CSV应包含{expected_batches}行，实际为{self.row_count}行"
            )
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        if self.final_path.exists():
            raise FileExistsError(f"统计文件已存在：{self.final_path}")
        self.temporary_path.replace(self.final_path)
        self._published = True
        return self.final_path

    def abort(self) -> None:
        if not self._file.closed:
            self._file.close()
        if not self._published:
            self.temporary_path.unlink(missing_ok=True)

    def __enter__(self) -> "AtomicStatisticsCsv":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._published:
            self.abort()


class AtomicDsmScanCsv:
    """Stream DSM scan rows, then atomically publish one long-form CSV."""

    def __init__(
        self,
        output_directory: Path,
        stem: str,
        started_at: datetime,
    ) -> None:
        output_directory.mkdir(parents=True, exist_ok=True)
        self.final_path = (
            output_directory
            / statistics_filename(stem, started_at)
        )
        if self.final_path.exists():
            raise FileExistsError(f"扫描文件已存在：{self.final_path}")

        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{self.final_path.stem}_",
            suffix=".tmp",
            dir=output_directory,
            delete=False,
        )
        self._file = temporary
        self.temporary_path = Path(temporary.name)
        self._writer = csv.writer(temporary)
        self._writer.writerow(
            mu_wide_fieldnames(
                prefix=["scan_index", "dsm_q16", "frequency_mhz"]
            )
        )
        self.row_count = 0
        self._published = False

    def append_scan_point(
        self,
        scan_index: int,
        dsm_q16: float,
        frequency_mhz: float,
        samples: list[MuSample],
    ) -> None:
        if self._file.closed:
            raise RuntimeError("DSM扫描CSV已经关闭")
        if scan_index <= 0:
            raise ValueError("扫描序号必须从1开始")
        if not math.isfinite(dsm_q16):
            raise ValueError("DSM扫描值必须为有限数值")
        if not math.isfinite(frequency_mhz) or frequency_mhz <= 0:
            raise ValueError("实测频率必须是大于0的有限数值")
        if len(samples) != STATISTICS_SAMPLES:
            raise ValueError(
                f"每个DSM扫描点必须恰好包含{STATISTICS_SAMPLES}组μ数据"
            )
        self._writer.writerow(
            [
                scan_index,
                f"{dsm_q16:.10f}",
                f"{frequency_mhz:.10f}",
                *mu_wide_values(samples),
            ]
        )
        self.row_count += 1
        self._file.flush()

    def publish(self, expected_scan_count: int) -> Path:
        scans = validate_dsm_scan_count(expected_scan_count)
        if self.row_count != scans:
            raise RuntimeError(
                f"CSV应包含{scans}行，实际为{self.row_count}行"
            )
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        if self.final_path.exists():
            raise FileExistsError(f"扫描文件已存在：{self.final_path}")
        self.temporary_path.replace(self.final_path)
        self._published = True
        return self.final_path

    def abort(self) -> None:
        if not self._file.closed:
            self._file.close()
        if not self._published:
            self.temporary_path.unlink(missing_ok=True)

    def __enter__(self) -> "AtomicDsmScanCsv":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._published:
            self.abort()


class AtomicSarCalibrationCsv:
    """Stream one wide row per SAR decision, then atomically publish."""

    def __init__(
        self,
        output_directory: Path,
        stem: str,
        started_at: datetime,
        *,
        sample_count: int,
    ) -> None:
        self.sample_count = validate_data_count(sample_count)
        output_directory.mkdir(parents=True, exist_ok=True)
        self.final_path = (
            output_directory
            / statistics_filename(stem, started_at)
        )
        if self.final_path.exists():
            raise FileExistsError(f"SAR校准文件已存在：{self.final_path}")

        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{self.final_path.stem}_",
            suffix=".tmp",
            dir=output_directory,
            delete=False,
        )
        self._file = temporary
        self.temporary_path = Path(temporary.name)
        self._writer = csv.writer(temporary)
        self._writer.writerow(
            mu_wide_fieldnames(
                prefix=[
                    "sar_step",
                    "frequency_mhz",
                    "sar_q16",
                    "dsm_q16",
                ],
                sample_count=self.sample_count,
            )
        )
        self.row_count = 0
        self._published = False

    def append_step(
        self,
        sar_step: int,
        frequency_mhz: float,
        sar_q16: float,
        dsm_q16: float,
        samples: list[MuSample],
    ) -> None:
        if self._file.closed:
            raise RuntimeError("SAR校准CSV已经关闭")
        if sar_step <= 0:
            raise ValueError("SAR步骤编号必须从1开始")
        for name, value in (
            ("实测频率", frequency_mhz),
            ("SAR校准输入", sar_q16),
            ("DSM实际输入", dsm_q16),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name}必须为有限数值")
        if frequency_mhz <= 0:
            raise ValueError("实测频率必须是大于0的有限数值")

        self._writer.writerow(
            [
                sar_step,
                f"{frequency_mhz:.10f}",
                f"{sar_q16:.10f}",
                f"{dsm_q16:.10f}",
                *mu_wide_values(
                    samples,
                    expected_count=self.sample_count,
                ),
            ]
        )
        self.row_count += 1
        self._file.flush()

    def publish(self, expected_steps: int) -> Path:
        if expected_steps <= 0:
            raise ValueError("SAR校准步骤数必须为正整数")
        if self.row_count != expected_steps:
            raise RuntimeError(
                f"CSV应包含{expected_steps}行，实际为{self.row_count}行"
            )
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        if self.final_path.exists():
            raise FileExistsError(f"SAR校准文件已存在：{self.final_path}")
        self.temporary_path.replace(self.final_path)
        self._published = True
        return self.final_path

    def abort(self) -> None:
        if not self._file.closed:
            self._file.close()
        if not self._published:
            self.temporary_path.unlink(missing_ok=True)

    def __enter__(self) -> "AtomicSarCalibrationCsv":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._published:
            self.abort()
