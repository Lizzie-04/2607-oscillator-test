"""Quartus In-System Sources and Probes transport used by the GUI and tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence


OP_WRITE_CONTROL_WORD = 0x1
OP_COMMIT_AND_START = 0x2
OP_SET_DECIMATION = 0x3
OP_SAR_START = 0x4
OP_SAR_COMPARE = 0x5
OP_CLEAR_ERROR = 0x6
OP_STATISTICS_START = 0x7
OP_READ_REGISTER = 0x8
OP_STATISTICS_READ = 0x9
OP_SET_CONTROL_SAMPLES = 0xA
OP_SET_DSM_OVERRIDE = 0xB
OP_LATCH_LIVE_SAMPLE = 0xC
OP_LATCH_CALIBRATION_SAMPLE = 0xD

PROTOCOL_VERSION = 0x0004
IMPLEMENTATION_REVISION = 0x0003
STATISTICS_SAMPLES = 100
SAR_INITIAL_CODE_Q16 = 0
SAR_STEPS = 16
MIN_POLL_TRANSACTION_TIMEOUT = 5.0
DEFAULT_CONTROL_SAMPLE_COUNT = 1500
MIN_CONTROL_SAMPLE_COUNT = 968
FILTER_TOTAL_SAMPLES_DEFAULT = 2048
FILTER_TOTAL_SAMPLES_MIN = 2048
FILTER_TOTAL_SAMPLES_MAX = 100000
FILTER_HALF_LENGTH_MIN = FILTER_TOTAL_SAMPLES_MIN // 2
FILTER_HALF_LENGTH_MAX = FILTER_TOTAL_SAMPLES_MAX // 2
DSM_OVERRIDE_WIDTH = 29
DSM_OVERRIDE_FRACTION_BITS = 16
DSM_OVERRIDE_SCALE = 1 << DSM_OVERRIDE_FRACTION_BITS
DSM_OVERRIDE_RAW_MAX = (1 << DSM_OVERRIDE_WIDTH) - 1
DSM_OVERRIDE_SIGNED_MIN = -(1 << (DSM_OVERRIDE_WIDTH - 1))
DSM_OVERRIDE_SIGNED_MAX = (1 << (DSM_OVERRIDE_WIDTH - 1)) - 1

FPGA_ERROR_MESSAGES = {
    0x01: "控制字块索引超出0至29",
    0x02: "30个控制字块尚未全部写入",
    0x03: "芯片启动驱动器正忙",
    0x04: "Filter内部半长N_FILTER超出1024至50000",
    0x05: "SAR尚未启动或已经结束",
    0x06: "未知opcode",
    0x07: "统计采集正在执行",
    0x08: "统计数据尚未准备完成",
    0x09: "统计缓存地址或16-bit分片无效",
    0x0A: "控制字驱动样点数N_DAQ超出范围",
    0x0B: "DSM输入覆盖值或命令索引无效",
}


def validate_control_sample_count(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("控制字驱动样点数N必须为整数")
    if value < MIN_CONTROL_SAMPLE_COUNT:
        raise ValueError(
            f"控制字驱动样点数N必须不小于{MIN_CONTROL_SAMPLE_COUNT}"
        )
    if value > 0xFFFF:
        raise ValueError("控制字驱动样点数N不能超过65535")
    return value


def validate_filter_total_samples(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("Filter总采样点数L必须为整数")
    if value % 2:
        raise ValueError("Filter总采样点数L必须为偶数")
    if not FILTER_TOTAL_SAMPLES_MIN <= value <= FILTER_TOTAL_SAMPLES_MAX:
        raise ValueError(
            "Filter总采样点数L必须在"
            f"{FILTER_TOTAL_SAMPLES_MIN}至{FILTER_TOTAL_SAMPLES_MAX}之间"
        )
    return value


def validate_filter_half_length(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("Filter内部半长N_FILTER必须为整数")
    if not FILTER_HALF_LENGTH_MIN <= value <= FILTER_HALF_LENGTH_MAX:
        raise ValueError(
            "Filter内部半长N_FILTER必须在"
            f"{FILTER_HALF_LENGTH_MIN}至{FILTER_HALF_LENGTH_MAX}之间"
        )
    return value


def validate_dsm_override_raw(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("DSM输入覆盖码字必须为整数")
    if not 0 <= value <= DSM_OVERRIDE_RAW_MAX:
        raise ValueError(
            f"DSM输入覆盖码字必须在29-bit范围0至0x{DSM_OVERRIDE_RAW_MAX:X}"
        )
    return value


def dsm_signed_to_twos(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("DSM输入覆盖Q16整数必须为整数")
    if not DSM_OVERRIDE_SIGNED_MIN <= value <= DSM_OVERRIDE_SIGNED_MAX:
        raise ValueError(
            "DSM输入覆盖Q16值必须在"
            f"{DSM_OVERRIDE_SIGNED_MIN}至{DSM_OVERRIDE_SIGNED_MAX}之间"
        )
    return value & DSM_OVERRIDE_RAW_MAX


def dsm_twos_to_signed(value: int) -> int:
    raw = validate_dsm_override_raw(value)
    sign_bit = 1 << (DSM_OVERRIDE_WIDTH - 1)
    if raw & sign_bit:
        return raw - (1 << DSM_OVERRIDE_WIDTH)
    return raw


def describe_fpga_error(code: int) -> str:
    description = FPGA_ERROR_MESSAGES.get(code, "未定义错误")
    return f"0x{code:02X}（{description}）"


def poll_transaction_timeout(
    remaining: float,
    ceiling: float = 15.0,
) -> float:
    """Give a new Quartus process enough time to start during state polling.

    ``remaining`` is the logical wait budget of the FPGA operation.  It must
    not be used as the subprocess startup budget: every poll launches a new
    quartus_stp process, which commonly needs more than one second before the
    first JTAG response is available.
    """
    if remaining <= 0:
        raise ValueError("轮询剩余时间必须大于0")
    if ceiling < MIN_POLL_TRANSACTION_TIMEOUT:
        raise ValueError(
            "JTAG事务超时上限不能小于"
            f"{MIN_POLL_TRANSACTION_TIMEOUT:g}秒"
        )
    return max(
        MIN_POLL_TRANSACTION_TIMEOUT,
        min(ceiling, remaining),
    )


@dataclass(frozen=True, slots=True)
class Command:
    opcode: int
    index: int = 0
    data: int = 0


@dataclass(frozen=True, slots=True)
class Probe:
    raw: int

    @property
    def ack(self) -> int:
        return (self.raw >> 63) & 1

    @property
    def pending(self) -> bool:
        return bool((self.raw >> 62) & 1)

    @property
    def error(self) -> int:
        return (self.raw >> 54) & 0xFF

    @property
    def driver_busy(self) -> bool:
        return bool((self.raw >> 53) & 1)

    @property
    def driver_done(self) -> bool:
        return bool((self.raw >> 52) & 1)

    @property
    def sar_busy(self) -> bool:
        return bool((self.raw >> 51) & 1)

    @property
    def sar_done(self) -> bool:
        return bool((self.raw >> 50) & 1)

    @property
    def sample_toggle(self) -> int:
        return (self.raw >> 49) & 1

    @property
    def sample_sequence(self) -> int:
        return (self.raw >> 33) & 0xFFFF

    @property
    def response(self) -> int:
        return (self.raw >> 17) & 0xFFFF

    @property
    def sar_code_low17(self) -> int:
        return self.raw & 0x1FFFF


@dataclass(frozen=True, slots=True)
class MuSample:
    mu1_raw: int
    mu2_raw: int

    def __post_init__(self) -> None:
        if not 0 <= self.mu1_raw <= 0x3FFFF:
            raise ValueError("μ1原始值必须在18-bit范围内")
        if not 0 <= self.mu2_raw <= 0x3FFFF:
            raise ValueError("μ2原始值必须在18-bit范围内")

    @property
    def mu1(self) -> float:
        return self.mu1_raw / 65536

    @property
    def mu2(self) -> float:
        return self.mu2_raw / 65536


@dataclass(frozen=True, slots=True)
class LiveSample:
    sample: MuSample
    onchip_sample: MuSample
    sequence: int
    total_filter_samples: int
    onchip_sequence: int


@dataclass(frozen=True, slots=True)
class DsmOverrideStatus:
    enabled: bool
    raw_value: int

    def __post_init__(self) -> None:
        validate_dsm_override_raw(self.raw_value)

    @property
    def signed_value(self) -> int:
        return dsm_twos_to_signed(self.raw_value)

    @property
    def q16_value(self) -> float:
        return self.signed_value / DSM_OVERRIDE_SCALE


@dataclass(frozen=True, slots=True)
class CalibrationSnapshot:
    poly_freq_raw: int
    poly_system_raw: int
    sar_correction_raw: int
    dsm_input_raw: int
    sequence: int
    valid: bool
    override_enabled: bool
    sar_status: "SarStatus"

    def __post_init__(self) -> None:
        for value in (
            self.poly_freq_raw,
            self.poly_system_raw,
            self.sar_correction_raw,
            self.dsm_input_raw,
        ):
            validate_dsm_override_raw(value)
        if not 0 <= self.sequence <= 0xFFFF:
            raise ValueError("校准快照序号必须在16-bit范围内")

    @staticmethod
    def _q16(raw: int) -> float:
        return dsm_twos_to_signed(raw) / DSM_OVERRIDE_SCALE

    @property
    def poly_freq(self) -> float:
        return self._q16(self.poly_freq_raw)

    @property
    def poly_system(self) -> float:
        return self._q16(self.poly_system_raw)

    @property
    def sar_correction(self) -> float:
        return self._q16(self.sar_correction_raw)

    @property
    def dsm_input(self) -> float:
        return self._q16(self.dsm_input_raw)

    @property
    def dsm_overflow_low(self) -> bool:
        return self.dsm_input < -0.5

    @property
    def dsm_overflow_high(self) -> bool:
        return self.dsm_input > 0.5


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    live: LiveSample
    calibration: CalibrationSnapshot


@dataclass(frozen=True, slots=True)
class StatisticsStatus:
    completion_toggle: int
    done: bool
    busy: bool
    request_active: bool
    captured_count: int

    @classmethod
    def from_registers(
        cls, status_register: int, captured_count: int
    ) -> "StatisticsStatus":
        return cls(
            completion_toggle=status_register & 1,
            done=bool((status_register >> 1) & 1),
            busy=bool((status_register >> 2) & 1),
            request_active=bool((status_register >> 3) & 1),
            captured_count=captured_count,
        )


@dataclass(frozen=True, slots=True)
class SarStatus:
    code_raw: int
    step: int
    busy: bool
    done: bool
    fpga_error: int = 0

    def __post_init__(self) -> None:
        validate_dsm_override_raw(self.code_raw)

    @property
    def code(self) -> float:
        return dsm_twos_to_signed(self.code_raw) / DSM_OVERRIDE_SCALE


@dataclass(frozen=True, slots=True)
class JtagDiagnostic:
    hardware: str
    device: str
    source_hex_digits: int
    probe_hex_digits: int
    protocol_version: int | None = None
    implementation_revision: int | None = None

    @property
    def compatible(self) -> bool:
        return (
            self.source_hex_digits == 16
            and self.probe_hex_digits == 16
            and self.protocol_version == PROTOCOL_VERSION
            and self.implementation_revision == IMPLEMENTATION_REVISION
        )

    @property
    def summary(self) -> str:
        source_bits = self.source_hex_digits * 4
        probe_bits = self.probe_hex_digits * 4
        if source_bits != 64 or probe_bits != 64:
            return (
                f"已检测到 JTAG 和 FPGA，但当前镜像的 ISSP 为 "
                f"{source_bits}-bit/{probe_bits}-bit；FPGA V4 需要 "
                "64-bit/64-bit。请加载 fpga_v4.sof。"
            )
        if self.compatible:
            return (
                f"连接正常：{self.hardware}；FPGA V4 ISSP 为 "
                f"{source_bits}-bit/{probe_bits}-bit，协议版本0x"
                f"{self.protocol_version:04X}，实现修订0x"
                f"{self.implementation_revision:04X}"
            )
        protocol_version = (
            "无法读取"
            if self.protocol_version is None
            else f"0x{self.protocol_version:04X}"
        )
        implementation_revision = (
            "无法读取"
            if self.implementation_revision is None
            else f"0x{self.implementation_revision:04X}"
        )
        if self.protocol_version == PROTOCOL_VERSION:
            return (
                "已检测到FPGA V4协议，但实现修订为"
                f"{implementation_revision}，当前GUI要求0x"
                f"{IMPLEMENTATION_REVISION:04X}（支持有界DSM、±0.5 SAR"
                "和控制字RESET数字复位）。"
                "请加载本工程最新的fpga_v4.sof。"
            )
        return (
            "已检测到64-bit/64-bit ISSP，但协议版本为"
            f"{protocol_version}，不是FPGA V4的0x{PROTOCOL_VERSION:04X}。"
            "请加载 fpga_v4.sof。"
        )


class QuartusJtagTransport:
    def __init__(self, quartus_stp: str | Path | None = None) -> None:
        self.script = Path(__file__).with_name("issp_batch.tcl")
        self.diagnostic_script = Path(__file__).with_name("issp_diagnose.tcl")
        self.quartus_stp = Path(quartus_stp) if quartus_stp else self._find_stp()
        self.quartus_bin = self.quartus_stp.parent
        # One ISSP source instance must never be driven by overlapping
        # subprocesses.  RLock also keeps configure-and-wait atomic while
        # allowing it to reuse transact() internally.
        self._transaction_lock = threading.RLock()

    @staticmethod
    def _find_stp() -> Path:
        candidates: list[Path] = []
        from_path = shutil.which("quartus_stp.exe") or shutil.which("quartus_stp")
        if from_path:
            candidates.append(Path(from_path))
        for variable in ("QUARTUS_ROOTDIR", "QUARTUS_ROOTDIR_OVERRIDE"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root) / "bin64" / "quartus_stp.exe")
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise FileNotFoundError(
            "未找到 quartus_stp.exe。请从 Quartus Prime Command Prompt "
            "启动、把 quartus/bin64 加入 PATH，或设置 QUARTUS_ROOTDIR。"
        )

    @staticmethod
    def _failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
        raw = completed.stderr or completed.stdout
        lines = [
            line
            for line in raw.splitlines()
            if not line.startswith("TBBmalloc: skip allocation functions")
        ]
        return "\n".join(lines).strip()

    def transact(
        self, commands: list[Command], timeout: float = 15.0
    ) -> list[Probe]:
        with self._transaction_lock:
            return self._transact_unlocked(commands, timeout)

    def _transact_unlocked(
        self, commands: list[Command], timeout: float
    ) -> list[Probe]:
        if not commands:
            return []
        for command in commands:
            if not 0 <= command.opcode <= 0xF:
                raise ValueError("opcode 必须在4-bit范围内")
            if not 0 <= command.index <= 0x3F:
                raise ValueError("index 必须在6-bit范围内")
            if not 0 <= command.data <= 0xFFFF:
                raise ValueError("data 必须在16-bit范围内")

        transaction_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", prefix="oscv_", delete=False, encoding="ascii"
            ) as transaction_file:
                transaction_path = Path(transaction_file.name)
                for command in commands:
                    transaction_file.write(
                        f"{command.opcode} {command.index} {command.data}\n"
                    )

            try:
                completed = subprocess.run(
                    [
                        str(self.quartus_stp),
                        "-t",
                        self.script.name,
                        str(transaction_path),
                    ],
                    cwd=self.script.parent,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f"Quartus JTAG会话在{timeout:g}秒内未返回。"
                    "该超时发生在电脑与FPGA之间，与外部芯片或FS是否连接无关；"
                    "请在“FPGA连接”页确认USB-Blaster和FPGA，"
                    "并关闭可能占用JTAG的其他Quartus工具。"
                ) from exc
            if completed.returncode:
                detail = self._failure_detail(completed)
                raise RuntimeError(f"Quartus JTAG事务失败：{detail}")

            results: list[Probe] = []
            for line in completed.stdout.splitlines():
                if line.startswith("RESULT "):
                    _, ordinal_text, probe_hex = line.split()
                    ordinal = int(ordinal_text)
                    if ordinal != len(results):
                        raise RuntimeError(
                            "JTAG应答顺序无效：期望事务序号"
                            f"{len(results)}，实际为{ordinal}"
                        )
                    results.append(Probe(int(probe_hex, 16)))
            if len(results) != len(commands):
                raise RuntimeError(
                    f"应收到{len(commands)}个JTAG应答，实际收到{len(results)}个"
                )
            return results
        finally:
            if transaction_path is not None:
                transaction_path.unlink(missing_ok=True)

    def configure_and_start(
        self,
        words16: Sequence[int],
        selection: int,
        *,
        transaction_timeout: float = 25.0,
        completion_timeout: float | None = None,
        control_sample_count: int = DEFAULT_CONTROL_SAMPLE_COUNT,
    ) -> Probe:
        """Atomically write one 474-bit word, start it, and wait for DONE.

        GUI and future batch loops share this method so they cannot diverge in
        the 30-block order, error handling, or driver-completion semantics.
        """
        words = list(words16)
        if len(words) != 30:
            raise ValueError(
                f"474位控制字必须恰好包含30个16-bit传输块，当前为{len(words)}个"
            )
        if any(not 0 <= value <= 0xFFFF for value in words):
            raise ValueError("控制字传输块必须在16-bit范围内")
        if not 0 <= selection <= 0xF:
            raise ValueError("芯片选择字段必须在4-bit范围内")
        control_samples = validate_control_sample_count(
            control_sample_count
        )
        wait_timeout = (
            max(8.0, control_samples * 0.0005 + 2.0)
            if completion_timeout is None
            else completion_timeout
        )

        commands = [Command(OP_CLEAR_ERROR, 0, 0)]
        commands.append(
            Command(OP_SET_CONTROL_SAMPLES, 0, control_samples)
        )
        commands.extend(
            Command(OP_WRITE_CONTROL_WORD, index, value)
            for index, value in enumerate(words)
        )
        commands.append(Command(OP_COMMIT_AND_START, 0, selection))

        with self._transaction_lock:
            probes = self._transact_unlocked(commands, transaction_timeout)
            for position, (command, probe) in enumerate(
                zip(commands, probes, strict=True),
                start=1,
            ):
                if probe.error:
                    if command.opcode == OP_WRITE_CONTROL_WORD:
                        location = f"控制字块{command.index}"
                    else:
                        location = (
                            f"opcode=0x{command.opcode:X}，"
                            f"index={command.index}"
                        )
                    raise RuntimeError(
                        f"第{position}条协议命令（{location}）失败：FPGA返回"
                        f"{describe_fpga_error(probe.error)}；"
                        "本轮COMMIT已被硬件安全门禁止"
                    )

            deadline = time.monotonic() + wait_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "等待474位驱动事务完成超时；FPGA可能仍处于busy状态"
                    )
                status_probe = self._transact_unlocked(
                    [Command(OP_READ_REGISTER, 0, 0)],
                    poll_transaction_timeout(remaining, 5.0),
                )[0]
                if status_probe.error:
                    raise RuntimeError(
                        "等待驱动完成时FPGA返回"
                        f"{describe_fpga_error(status_probe.error)}"
                    )
                if status_probe.driver_done and not status_probe.driver_busy:
                    return status_probe
                time.sleep(min(0.05, remaining))

    def read_registers(self, indices: list[int]) -> dict[int, int]:
        probes = self.transact(
            [Command(OP_READ_REGISTER, index, 0) for index in indices]
        )
        self._check_probe_errors(probes, "读取FPGA寄存器时")
        return {
            index: probe.response for index, probe in zip(indices, probes, strict=True)
        }

    def transact_checked(
        self,
        commands: Sequence[Command],
        *,
        context: str,
        timeout: float = 15.0,
    ) -> list[Probe]:
        probes = self.transact(commands, timeout)
        self._check_probe_errors(probes, context)
        return probes

    @staticmethod
    def _check_probe_errors(probes: Sequence[Probe], context: str) -> None:
        for position, probe in enumerate(probes, start=1):
            if probe.error:
                raise RuntimeError(
                    f"{context}第{position}条命令失败：FPGA返回"
                    f"{describe_fpga_error(probe.error)}"
                )

    def set_filter_total_samples(self, total_samples: int) -> None:
        total = validate_filter_total_samples(total_samples)
        self.transact_checked(
            [Command(OP_SET_DECIMATION, 0, total // 2)],
            context="设置Filter窗口长度时",
        )

    def set_dsm_override(
        self,
        raw_value: int,
        *,
        enabled: bool,
    ) -> DsmOverrideStatus:
        raw = validate_dsm_override_raw(raw_value)
        commands = [Command(OP_CLEAR_ERROR, 0, 0)]
        commands.extend(
            [
                Command(OP_SET_DSM_OVERRIDE, 0, raw & 0xFFFF),
                Command(
                    OP_SET_DSM_OVERRIDE,
                    1,
                    (raw >> 16) & 0x1FFF,
                ),
            ]
        )
        commands.append(Command(OP_SET_DSM_OVERRIDE, 2, int(enabled)))
        self.transact_checked(
            commands,
            context="设置DSM输入覆盖时",
        )
        return self.read_dsm_override_status()

    def read_dsm_override_status(self) -> DsmOverrideStatus:
        registers = self.read_registers([18, 19, 20])
        raw = registers[19] | ((registers[20] & 0x1FFF) << 16)
        return DsmOverrideStatus(
            enabled=bool(registers[18] & 1),
            raw_value=raw,
        )

    def read_live_sample(self, retries: int = 3) -> LiveSample:
        commands = [
            Command(OP_LATCH_LIVE_SAMPLE, 0, 0),
            *(
                Command(OP_READ_REGISTER, index, 0)
                for index in range(21, 32)
            ),
        ]
        probes = self.transact(commands)
        if len(probes) != len(commands):
            raise RuntimeError(
                "读取实时样本快照时返回的寄存器数量不完整："
                f"期望{len(commands)}个，实际{len(probes)}个"
            )
        self._check_probe_errors(probes, "读取实时样本快照时")
        return self._live_sample_from_probes(probes)

    @staticmethod
    def _live_sample_from_probes(probes: Sequence[Probe]) -> LiveSample:
        values = [probe.response for probe in probes[1:]]
        mu1_raw = values[0] | ((values[1] & 0x3) << 16)
        mu2_raw = values[2] | ((values[3] & 0x3) << 16)
        onchip_mu1_raw = values[4] | ((values[5] & 0x3) << 16)
        onchip_mu2_raw = values[6] | ((values[7] & 0x3) << 16)
        return LiveSample(
            sample=MuSample(mu1_raw, mu2_raw),
            onchip_sample=MuSample(onchip_mu1_raw, onchip_mu2_raw),
            sequence=values[8],
            total_filter_samples=values[9] * 2,
            onchip_sequence=values[10],
        )

    def read_live_sample_without_snapshot(
        self,
        retries: int = 3,
    ) -> LiveSample:
        commands = [
            Command(OP_READ_REGISTER, index, 0)
            for index in (1, 2, 3, 4, 5, 15)
        ]
        for _ in range(retries):
            probes = self.transact(commands)
            self._check_probe_errors(probes, "读取实时样本时")
            sequences = {probe.sample_sequence for probe in probes}
            if len(sequences) != 1:
                continue
            values = [probe.response for probe in probes]
            mu1_raw = values[0] | ((values[1] & 0x3) << 16)
            mu2_raw = values[2] | ((values[3] & 0x3) << 16)
            return LiveSample(
                sample=MuSample(mu1_raw, mu2_raw),
                onchip_sample=MuSample(0, 0),
                sequence=values[4],
                total_filter_samples=values[5] * 2,
                onchip_sequence=0,
            )
        raise RuntimeError("读取μ1/μ2期间FPGA样本持续变化，请重试")

    def read_calibration_snapshot(self) -> CalibrationSnapshot:
        """Latch and read one atomic four-channel calibration snapshot.

        SAR status registers are read in the same JTAG process so the merged
        calibration page does not launch a second overlapping ISSP session.
        A sticky FPGA error is returned through ``sar_status`` instead of
        aborting the background dashboard read.
        """
        commands = [
            Command(OP_LATCH_CALIBRATION_SAMPLE, 0, 0),
            *(
                Command(OP_READ_REGISTER, index, 0)
                for index in range(32, 42)
            ),
            Command(OP_READ_REGISTER, 6, 0),
            Command(OP_READ_REGISTER, 7, 0),
            Command(OP_READ_REGISTER, 8, 0),
        ]
        probes = self.transact(commands)
        if len(probes) != len(commands):
            raise RuntimeError(
                "读取校准快照时返回的寄存器数量不完整："
                f"期望{len(commands)}个，实际{len(probes)}个"
            )
        return self._calibration_snapshot_from_probes(probes)

    def _calibration_snapshot_from_probes(
        self,
        probes: Sequence[Probe],
    ) -> CalibrationSnapshot:
        values = [probe.response for probe in probes[1:11]]

        def raw29(low_index: int) -> int:
            return (
                values[low_index]
                | ((values[low_index + 1] & 0x1FFF) << 16)
            )

        flags = values[9]
        return CalibrationSnapshot(
            poly_freq_raw=raw29(0),
            poly_system_raw=raw29(2),
            sar_correction_raw=raw29(4),
            dsm_input_raw=raw29(6),
            sequence=values[8],
            valid=bool((flags >> 1) & 1),
            override_enabled=bool(flags & 1),
            sar_status=self._sar_status_from_probes(probes[11:14]),
        )

    def read_monitor_snapshot(self) -> MonitorSnapshot:
        """Latch live μ and calibration dashboards in one JTAG session.

        The two hardware snapshots are still latched by separate opcodes, but
        they are captured back-to-back inside one host/JTAG transaction.
        """
        commands = [
            Command(OP_LATCH_LIVE_SAMPLE, 0, 0),
            *(
                Command(OP_READ_REGISTER, index, 0)
                for index in range(21, 32)
            ),
            Command(OP_LATCH_CALIBRATION_SAMPLE, 0, 0),
            *(
                Command(OP_READ_REGISTER, index, 0)
                for index in range(32, 42)
            ),
            Command(OP_READ_REGISTER, 6, 0),
            Command(OP_READ_REGISTER, 7, 0),
            Command(OP_READ_REGISTER, 8, 0),
        ]
        probes = self.transact(commands)
        if len(probes) != len(commands):
            raise RuntimeError(
                "读取监控快照时返回的寄存器数量不完整："
                f"期望{len(commands)}个，实际{len(probes)}个"
            )
        live_probes = probes[:12]
        calibration_probes = probes[12:]
        return MonitorSnapshot(
            live=self._live_sample_from_probes(live_probes),
            calibration=self._calibration_snapshot_from_probes(
                calibration_probes
            ),
        )

    @staticmethod
    def _sar_status_from_probes(probes: Sequence[Probe]) -> SarStatus:
        if len(probes) != 3:
            raise RuntimeError(
                f"SAR状态应包含3个寄存器应答，实际为{len(probes)}个"
            )
        sar_raw = (
            probes[0].response
            | ((probes[1].response & 0x1FFF) << 16)
        )
        return SarStatus(
            code_raw=sar_raw,
            step=probes[2].response,
            busy=probes[2].sar_busy,
            done=probes[2].sar_done,
            fpga_error=probes[2].error,
        )

    def _sar_status_unlocked(
        self,
        timeout: float = 15.0,
        *,
        check_error: bool,
    ) -> SarStatus:
        probes = self._transact_unlocked(
            [
                Command(OP_READ_REGISTER, 6, 0),
                Command(OP_READ_REGISTER, 7, 0),
                Command(OP_READ_REGISTER, 8, 0),
            ],
            timeout,
        )
        if check_error:
            self._check_probe_errors(probes, "读取SAR状态时")
        return self._sar_status_from_probes(probes)

    def read_sar_status(self) -> SarStatus:
        # Register responses remain valid when an earlier sticky FPGA error
        # is present.  Returning that error as data prevents the GUI's
        # background status display from entering a repeated exception loop.
        with self._transaction_lock:
            return self._sar_status_unlocked(check_error=False)

    def read_sar_registers(self) -> tuple[int, int]:
        status = self.read_sar_status()
        return status.code_raw, status.step

    def start_sar(
        self,
        *,
        ready_timeout: float = 3.0,
    ) -> SarStatus:
        """Start SAR and wait for confirmation from the 2 MHz data domain."""
        with self._transaction_lock:
            probes = self._transact_unlocked(
                [
                    Command(OP_READ_REGISTER, 14, 0),
                    Command(OP_READ_REGISTER, 16, 0),
                    Command(OP_CLEAR_ERROR, 0, 0),
                    Command(OP_SAR_START, 0, 1),
                ],
                15.0,
            )
            protocol_version = probes[0].response
            implementation_revision = probes[1].response
            if (
                protocol_version != PROTOCOL_VERSION
                or implementation_revision != IMPLEMENTATION_REVISION
            ):
                raise RuntimeError(
                    "当前FPGA镜像与GUI要求的V4实现修订不一致："
                    f"协议版本0x{protocol_version:04X}，"
                    f"实现修订0x{implementation_revision:04X}；"
                    "请加载本工程最新的fpga_v4.sof"
                )
            # The clear command may report the old sticky error in its
            # response.  The following START response must be clean.
            self._check_probe_errors(probes[3:], "启动SAR时")

            deadline = time.monotonic() + ready_timeout
            last_status: SarStatus | None = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = ""
                    if last_status is not None:
                        detail = (
                            f"；最后状态busy={int(last_status.busy)}、"
                            f"step={last_status.step}、"
                            f"code={last_status.code:.6f}"
                        )
                    raise TimeoutError(
                        "FPGA已接收SAR启动命令，但SAR状态机未进入运行态。"
                        "请检查FPGA V4镜像及内部2 MHz数字核心时钟"
                        f"{detail}"
                    )

                last_status = self._sar_status_unlocked(
                    poll_transaction_timeout(remaining),
                    check_error=True,
                )
                if (
                    last_status.busy
                    and not last_status.done
                    and last_status.step == 0
                    and last_status.code_raw == SAR_INITIAL_CODE_Q16
                ):
                    return last_status
                time.sleep(min(0.05, remaining))

    def submit_sar_decision(
        self,
        bit: int,
        *,
        completion_timeout: float = 3.0,
    ) -> SarStatus:
        """Submit one decision only while SAR is ready, then confirm progress."""
        if bit not in (0, 1):
            raise ValueError("SAR判决必须为0或1")

        with self._transaction_lock:
            before = self._sar_status_unlocked(check_error=True)
            if not before.busy or before.done or before.step >= SAR_STEPS:
                raise RuntimeError(
                    "SAR尚未进入运行态或已经结束；请先重新启动SAR"
                )

            submitted = self._transact_unlocked(
                [Command(OP_SAR_COMPARE, 0, bit)],
                15.0,
            )
            self._check_probe_errors(submitted, "提交SAR判决时")

            expected_step = before.step + 1
            deadline = time.monotonic() + completion_timeout
            last_status = before
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"SAR判决已发送，但步数未从{before.step}更新到"
                        f"{expected_step}；请检查内部2 MHz数字核心时钟"
                    )
                last_status = self._sar_status_unlocked(
                    poll_transaction_timeout(remaining),
                    check_error=True,
                )
                if last_status.step == expected_step:
                    if expected_step == SAR_STEPS:
                        if last_status.done and not last_status.busy:
                            return last_status
                    elif last_status.busy and not last_status.done:
                        return last_status
                time.sleep(min(0.05, remaining))

    def _statistics_status_unlocked(
        self, timeout: float = 15.0
    ) -> StatisticsStatus:
        probes = self._transact_unlocked(
            [
                Command(OP_READ_REGISTER, 12, 0),
                Command(OP_READ_REGISTER, 13, 0),
            ],
            timeout,
        )
        self._check_probe_errors(probes, "读取统计状态时")
        return StatisticsStatus.from_registers(
            probes[0].response,
            probes[1].response,
        )

    def capture_statistics_batch(
        self,
        *,
        capture_timeout: float = 120.0,
        download_timeout: float = 60.0,
    ) -> list[MuSample]:
        """Capture and download one exact 100-window statistics batch."""
        with self._transaction_lock:
            cleared = self._transact_unlocked(
                [Command(OP_CLEAR_ERROR, 0, 0)],
                15.0,
            )
            self._check_probe_errors(cleared, "清除统计错误时")
            baseline = self._statistics_status_unlocked()

            started = self._transact_unlocked(
                [Command(OP_STATISTICS_START, 0, 0)],
                15.0,
            )
            self._check_probe_errors(started, "启动统计采集时")

            deadline = time.monotonic() + capture_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "等待100组μ统计采集完成超时；请检查FS_DATA_OUT33"
                    )
                status = self._statistics_status_unlocked(
                    poll_transaction_timeout(remaining)
                )
                if (
                    status.completion_toggle
                    != baseline.completion_toggle
                ):
                    if status.captured_count > STATISTICS_SAMPLES:
                        raise RuntimeError(
                            "统计捕获数量超出硬件固定的100组："
                            f"{status.captured_count}"
                        )
                    # The completion toggle, DONE level and multi-bit count
                    # cross the clock boundary independently.  Allow their
                    # two-flop synchronizers to settle before accepting the
                    # completed batch.
                    if (
                        status.done
                        and not status.busy
                        and not status.request_active
                        and status.captured_count == STATISTICS_SAMPLES
                    ):
                        break
                time.sleep(min(0.05, remaining))

            commands = [
                Command(OP_STATISTICS_READ, part, sample_index)
                for sample_index in range(STATISTICS_SAMPLES)
                for part in range(3)
            ]
            for ordinal, command in enumerate(commands):
                expected_address = ordinal // 3
                expected_part = ordinal % 3
                if (
                    command.opcode != OP_STATISTICS_READ
                    or command.data != expected_address
                    or command.index != expected_part
                ):
                    raise RuntimeError(
                        "统计读命令地址顺序无效；必须按地址0至99、"
                        "每地址低/中/高三个字排列"
                    )
            probes = self._transact_unlocked(
                commands,
                download_timeout,
            )
            self._check_probe_errors(probes, "下载统计缓存时")
            if len(probes) != STATISTICS_SAMPLES * 3:
                raise RuntimeError(
                    "统计缓存应返回300个16-bit字，实际为"
                    f"{len(probes)}个"
                )

            samples: list[MuSample] = []
            for sample_index in range(STATISTICS_SAMPLES):
                low, middle, high = (
                    probes[sample_index * 3 + offset].response
                    for offset in range(3)
                )
                packed = low | (middle << 16) | ((high & 0xF) << 32)
                samples.append(
                    MuSample(
                        mu1_raw=packed & 0x3FFFF,
                        mu2_raw=(packed >> 18) & 0x3FFFF,
                    )
                )
            return samples

    def diagnose(self, timeout: float = 10.0) -> JtagDiagnostic:
        """Check the cable, FPGA TAP and currently loaded ISSP widths."""
        completed = subprocess.run(
            [
                str(self.quartus_stp),
                "-t",
                self.diagnostic_script.name,
            ],
            cwd=self.diagnostic_script.parent,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if completed.returncode:
            detail = self._failure_detail(completed)
            raise RuntimeError(f"Quartus JTAG诊断失败：{detail}")

        fields: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if not line.startswith("DIAG "):
                continue
            for key, value in re.findall(r"(\w+)=([^ ]+(?: \[[^]]+\])?)", line):
                fields[key] = value

        try:
            source_hex_digits = int(fields["source_hex_digits"])
            probe_hex_digits = int(fields["probe_hex_digits"])
            protocol_version: int | None = None
            implementation_revision: int | None = None
            if source_hex_digits == 16 and probe_hex_digits == 16:
                try:
                    # The version response is valid even when an unrelated,
                    # older sticky FPGA error is present.  Connection
                    # diagnosis must not clear that error as a side effect.
                    probes = self.transact(
                        [
                            Command(OP_READ_REGISTER, 14, 0),
                            Command(OP_READ_REGISTER, 16, 0),
                        ]
                    )
                    protocol_version = probes[0].response
                    implementation_revision = probes[1].response
                except Exception:
                    protocol_version = None
                    implementation_revision = None
            return JtagDiagnostic(
                hardware=fields["hardware"],
                device=fields["device"],
                source_hex_digits=source_hex_digits,
                probe_hex_digits=probe_hex_digits,
                protocol_version=protocol_version,
                implementation_revision=implementation_revision,
            )
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                "无法解析Quartus诊断输出：\n"
                + completed.stdout.strip()
            ) from exc

    def program(self, sof_path: str | Path, timeout: float = 60.0) -> str:
        """Program the volatile FPGA image after validating the JTAG chain."""
        sof_path = Path(sof_path)
        if not sof_path.is_file():
            raise FileNotFoundError(f"未找到 SOF：{sof_path}")

        jtagconfig = self.quartus_bin / "jtagconfig.exe"
        quartus_pgm = self.quartus_bin / "quartus_pgm.exe"
        chain = subprocess.run(
            [str(jtagconfig)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
        )
        if chain.returncode:
            raise RuntimeError((chain.stderr or chain.stdout).strip())
        cable_matches = re.findall(
            r"^\d+\)\s+(DE-SoC \[USB-[^\]]+\])$",
            chain.stdout,
            flags=re.MULTILINE,
        )
        if len(cable_matches) != 1:
            raise RuntimeError(
                f"应检测到一个 DE-SoC 下载器，实际为 {len(cable_matches)} 个"
            )
        if "02D120DD" not in chain.stdout:
            raise RuntimeError("未检测到 Cyclone V FPGA TAP 02D120DD")

        with tempfile.TemporaryDirectory(prefix="oscv_program_") as temporary:
            staged_sof = Path(temporary) / "fpga_v4.sof"
            shutil.copy2(sof_path, staged_sof)
            programmed = subprocess.run(
                [
                    str(quartus_pgm),
                    "-c",
                    cable_matches[0],
                    "-m",
                    "JTAG",
                    "-o",
                    f"p;{staged_sof}@2",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        if programmed.returncode:
            detail = self._failure_detail(programmed)
            raise RuntimeError(f"FPGA加载失败：{detail}")
        return "FPGA_V5兼容SOF已成功加载"
