"""Host-side μ history and atomic multi-batch CSV assembly."""

from __future__ import annotations

from collections import deque
import csv
from datetime import datetime
import os
from pathlib import Path
import tempfile
from collections.abc import Iterable

from jtag_transport import MuSample, STATISTICS_SAMPLES


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


def validate_data_count(value: int) -> int:
    if value <= 0:
        raise ValueError("数据量必须为正整数")
    if value % STATISTICS_SAMPLES:
        raise ValueError(
            f"数据量必须为{STATISTICS_SAMPLES}的整数倍"
        )
    return value


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
        self._writer.writerow(["batch", "mu1", "mu2"])
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
        for sample in samples:
            self._writer.writerow(
                [
                    batch_number,
                    f"{sample.mu1:.10f}",
                    f"{sample.mu2:.10f}",
                ]
            )
        self.row_count += len(samples)
        self._file.flush()

    def publish(self, expected_rows: int) -> Path:
        validate_data_count(expected_rows)
        if self.row_count != expected_rows:
            raise RuntimeError(
                f"CSV应包含{expected_rows}行，实际为{self.row_count}行"
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
