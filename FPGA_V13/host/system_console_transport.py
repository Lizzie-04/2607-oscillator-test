"""FPGA V13 System Console + JTAG-to-Avalon-MM transport."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
import ctypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time


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
OP_WRITE_FPGA_COEFFICIENT = 0xE

MAGIC = 0x4F534356
AVALON_ABI_VERSION = 2
TCP_PROTOCOL_VERSION = 1
PROTOCOL_VERSION = 0x0005
IMPLEMENTATION_REVISION = 0x0001
STATISTICS_SAMPLES = 100
SAR_INITIAL_CODE_Q16 = 0
SAR_STEPS = 16
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
FPGA_COEFFICIENT_WIDTH = 37
FPGA_COEFFICIENT_COUNT = 16
FPGA_COEFFICIENT_WORDS = FPGA_COEFFICIENT_COUNT * 3
FPGA_COEFFICIENT_RAW_MAX = (1 << FPGA_COEFFICIENT_WIDTH) - 1
SYSTEM_CONSOLE_STARTUP_TIMEOUT = 120.0
MIN_POLL_TRANSACTION_TIMEOUT = 5.0
DRIVER_IDLE_TIMEOUT = 30.0
AVALON_SPAN_BYTES = 0x1000

ADDR_MAGIC = 0x0000
ADDR_ABI = 0x0004
ADDR_CORE_VERSION = 0x0008
ADDR_CAPABILITIES = 0x000C
ADDR_CORE_STATUS = 0x0010
ADDR_BRIDGE_STATUS = 0x0014
ADDR_REQUEST_ID = 0x0018
ADDR_LAST_ACCEPTED_ID = 0x001C
ADDR_LAST_COMPLETED_ID = 0x0020
ADDR_LAST_OPERATION = 0x0024
ADDR_LAST_RESULT = 0x0028
ADDR_STATISTICS_READY = 0x0030
ADDR_STATISTICS_CAPTURE_ID = 0x0034
ADDR_STATISTICS_COUNT = 0x0038
ADDR_CONTROL_SAMPLES = 0x0040
ADDR_FILTER_HALF_LENGTH = 0x0044
ADDR_DSM_LOW = 0x0048
ADDR_DSM_HIGH = 0x004C
ADDR_DSM_COMMIT = 0x0050
ADDR_CONTROL_COMMIT = 0x0054
ADDR_SAR_START = 0x0058
ADDR_SAR_COMPARE = 0x005C
ADDR_CLEAR_ERROR = 0x0060
ADDR_STATISTICS_START = 0x0064
ADDR_LATCH_LIVE = 0x0068
ADDR_LATCH_CALIBRATION = 0x006C
ADDR_STATISTICS_RELEASE = 0x0070
ADDR_STATISTICS_ABORT = 0x0074
ADDR_CONTROL_WORDS = 0x0100
ADDR_CORE_REGISTERS = 0x0200
ADDR_STATISTICS_RAM = 0x0400
ADDR_FPGA_COEFFICIENTS = 0x0800

FPGA_ERROR_MESSAGES = {
    0x01: "控制字块索引超出0至29",
    0x02: "30个控制字块尚未全部写入",
    0x03: "芯片启动驱动器正忙",
    0x04: "Filter内部半长超出范围",
    0x05: "SAR尚未启动或已经结束",
    0x06: "未知opcode",
    0x07: "统计采集正在执行",
    0x08: "统计数据尚未准备完成",
    0x09: "统计缓存地址无效",
    0x0A: "控制字驱动样点数N超出范围",
    0x0B: "DSM输入覆盖值或命令索引无效",
    0x0C: "FPGA内部Q20.16系数尚未完整写入",
    0x0D: "FPGA内部Q20.16系数分片无效",
}


def validate_control_sample_count(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("控制字驱动样点数N必须为整数")
    if not MIN_CONTROL_SAMPLE_COUNT <= value <= 0xFFFF:
        raise ValueError(
            f"控制字驱动样点数N必须在{MIN_CONTROL_SAMPLE_COUNT}至65535之间"
        )
    return value


def validate_filter_total_samples(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("Filter总采样点数L必须为整数")
    if value % 2:
        raise ValueError("Filter总采样点数L必须为偶数")
    if not FILTER_TOTAL_SAMPLES_MIN <= value <= FILTER_TOTAL_SAMPLES_MAX:
        raise ValueError(
            f"Filter总采样点数L必须在{FILTER_TOTAL_SAMPLES_MIN}至"
            f"{FILTER_TOTAL_SAMPLES_MAX}之间"
        )
    return value


def validate_filter_half_length(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("Filter内部半长必须为整数")
    if not FILTER_HALF_LENGTH_MIN <= value <= FILTER_HALF_LENGTH_MAX:
        raise ValueError("Filter内部半长超出范围")
    return value


def validate_dsm_override_raw(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("DSM输入覆盖码字必须为整数")
    if not 0 <= value <= DSM_OVERRIDE_RAW_MAX:
        raise ValueError("DSM输入覆盖码字必须在29-bit范围内")
    return value


def dsm_signed_to_twos(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("DSM输入覆盖Q16整数必须为整数")
    if not DSM_OVERRIDE_SIGNED_MIN <= value <= DSM_OVERRIDE_SIGNED_MAX:
        raise ValueError("DSM输入覆盖Q16值超出29-bit有符号范围")
    return value & DSM_OVERRIDE_RAW_MAX


def dsm_twos_to_signed(value: int) -> int:
    raw = validate_dsm_override_raw(value)
    sign = 1 << (DSM_OVERRIDE_WIDTH - 1)
    return raw - (1 << DSM_OVERRIDE_WIDTH) if raw & sign else raw


def describe_fpga_error(code: int) -> str:
    return f"0x{code:02X}（{FPGA_ERROR_MESSAGES.get(code, '未定义错误')}）"


def poll_transaction_timeout(remaining: float, ceiling: float = 15.0) -> float:
    if remaining <= 0:
        raise ValueError("轮询剩余时间必须大于0")
    return max(MIN_POLL_TRANSACTION_TIMEOUT, min(ceiling, remaining))


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
class CalibrationSnapshot:
    poly_freq_raw: int
    poly_system_raw: int
    sar_correction_raw: int
    dsm_input_raw: int
    sequence: int
    valid: bool
    override_enabled: bool
    sar_status: SarStatus

    def __post_init__(self) -> None:
        for value in (
            self.poly_freq_raw,
            self.poly_system_raw,
            self.sar_correction_raw,
            self.dsm_input_raw,
        ):
            if not 0 <= value <= FPGA_COEFFICIENT_RAW_MAX:
                raise ValueError("校准快照值必须在37-bit范围内")

    @staticmethod
    def _q16(raw: int) -> float:
        sign = 1 << (FPGA_COEFFICIENT_WIDTH - 1)
        signed = raw - (1 << FPGA_COEFFICIENT_WIDTH) if raw & sign else raw
        return signed / DSM_OVERRIDE_SCALE

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
class JtagDiagnostic:
    hardware: str
    device: str
    source_hex_digits: int = 8
    probe_hex_digits: int = 8
    protocol_version: int | None = None
    implementation_revision: int | None = None
    avalon_abi_version: int | None = None
    magic: int | None = None

    @property
    def compatible(self) -> bool:
        return (
            self.magic == MAGIC
            and self.avalon_abi_version == AVALON_ABI_VERSION
            and self.protocol_version == PROTOCOL_VERSION
            and self.implementation_revision == IMPLEMENTATION_REVISION
        )

    @property
    def summary(self) -> str:
        if self.compatible:
            return (
                "System Console与FPGA V13连接正常："
                f"Avalon ABI={self.avalon_abi_version}，协议=0x"
                f"{self.protocol_version:04X}，实现修订=0x"
                f"{self.implementation_revision:04X}"
            )
        return (
            "已找到JTAG链路，但当前SOF不是兼容的FPGA V13 Avalon镜像；"
            f"协议={self.protocol_version}，实现修订={self.implementation_revision}，"
            "请加载本工程新生成的SOF"
        )


class SystemConsoleRemoteError(RuntimeError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(f"System Console {error_class}: {message}")
        self.error_class = error_class


class _WindowsJob:
    """Best-effort KILL_ON_JOB_CLOSE ownership for System Console."""

    def __init__(self, process_handle: int) -> None:
        self.handle: int | None = None
        self._kernel32 = None
        if os.name != "nt":
            return

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle_type = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = handle_type
        kernel32.SetInformationJobObject.argtypes = [
            handle_type, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [handle_type, handle_type]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [handle_type]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            kernel32.CloseHandle(handle)
            raise OSError(
                ctypes.get_last_error(), "SetInformationJobObject failed"
            )
        if not kernel32.AssignProcessToJobObject(handle, process_handle):
            kernel32.CloseHandle(handle)
            raise OSError(
                ctypes.get_last_error(), "AssignProcessToJobObject failed"
            )
        self.handle = handle
        self._kernel32 = kernel32

    def close(self) -> None:
        if self.handle is not None:
            assert self._kernel32 is not None
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class SystemConsoleAvalonTransport:
    """One GUI-lifetime System Console process and one serialized TCP link."""

    def __init__(
        self,
        system_console: str | Path | None = None,
        *,
        startup_timeout: float = SYSTEM_CONSOLE_STARTUP_TIMEOUT,
    ) -> None:
        self.server_script = Path(__file__).with_name("system_console_server.tcl")
        self.system_console = (
            Path(system_console).resolve()
            if system_console
            else self._find_system_console()
        )
        self.quartus_root = self._quartus_root(self.system_console)
        self.startup_timeout = startup_timeout
        self._transaction_lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._socket: socket.socket | None = None
        self._receive_buffer = bytearray()
        self._stdout_queue: Queue[str | None] = Queue()
        self._startup_stdout_lines: deque[str] = deque(maxlen=200)
        self._stderr_lines: deque[str] = deque(maxlen=200)
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._job: _WindowsJob | None = None
        self._token = ""
        self._tcp_id = 1
        self._fpga_request_id = 1
        self._log_callback: Callable[[str, str, str], None] | None = None

    def __enter__(self) -> "SystemConsoleAvalonTransport":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def set_log_callback(
        self, callback: Callable[[str, str, str], None] | None
    ) -> None:
        self._log_callback = callback

    def _log(self, level: str, category: str, message: str) -> None:
        callback = self._log_callback
        if callback is not None:
            try:
                callback(level, category, message)
            except Exception:
                pass

    @staticmethod
    def _find_system_console() -> Path:
        candidates: list[Path] = []
        found = shutil.which("system-console.exe") or shutil.which(
            "system-console"
        )
        if found:
            candidates.append(Path(found))
        for variable in ("QUARTUS_ROOTDIR", "QUARTUS_ROOTDIR_OVERRIDE"):
            root = os.environ.get(variable)
            if root:
                candidates.extend(
                    [
                        Path(root) / "sopc_builder" / "bin" / "system-console.exe",
                        Path(root) / "bin64" / "system-console.exe",
                    ]
                )
        candidates.append(
            Path(r"C:\altera_lite\25.1std\quartus\sopc_builder\bin\system-console.exe")
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(
            "未找到system-console.exe；请设置QUARTUS_ROOTDIR或加入PATH"
        )

    @staticmethod
    def _quartus_root(system_console: Path) -> Path:
        for parent in system_console.parents:
            if (parent / "bin64" / "quartus_pgm.exe").is_file():
                return parent
        raise FileNotFoundError("无法从System Console路径定位Quartus根目录")

    def start(self) -> "SystemConsoleAvalonTransport":
        with self._transaction_lock:
            if self._process is not None and self._process.poll() is None:
                return self
            if not self.server_script.is_file():
                raise FileNotFoundError(f"未找到TCP服务脚本：{self.server_script}")
            started = time.monotonic()
            self._token = secrets.token_hex(16)
            environment = os.environ.copy()
            environment["FPGA_V13_TOKEN"] = self._token
            environment["FPGA_V13_PARENT_PID"] = str(os.getpid())
            creationflags = 0x08000000 if os.name == "nt" else 0
            self._stdout_queue = Queue()
            self._startup_stdout_lines = deque(maxlen=200)
            self._stderr_lines = deque(maxlen=200)
            self._process = subprocess.Popen(
                [str(self.system_console), f"--script={self.server_script.name}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                cwd=str(self.server_script.parent),
                creationflags=creationflags,
            )
            try:
                self._job = _WindowsJob(int(self._process._handle))
            except Exception as exc:
                self._job = None
                self._log("WARN", "SYSTEM_CONSOLE", f"Job Object不可用：{exc}")
            self._stdout_thread = threading.Thread(
                target=self._drain_stdout,
                name="system-console-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                name="system-console-stderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            try:
                deadline = time.monotonic() + self.startup_timeout
                port = self._wait_server_port(deadline)
                remaining = max(0.1, deadline - time.monotonic())
                self._socket = socket.create_connection(
                    ("127.0.0.1", port), timeout=remaining
                )
                self._socket.settimeout(min(30.0, remaining))
                self._receive_buffer.clear()
                self._send_line(
                    f"HELLO {self._token} {TCP_PROTOCOL_VERSION}"
                )
                values = self._receive_response(0)
                if values != [TCP_PROTOCOL_VERSION]:
                    raise RuntimeError("System Console TCP握手版本无效")
                self._validate_health_unlocked()
                self._synchronize_request_id_unlocked()
                self._log(
                    "INFO",
                    "SYSTEM_CONSOLE",
                    f"常驻会话已启动，耗时={time.monotonic() - started:.3f}s",
                )
                return self
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        with self._transaction_lock:
            sock = self._socket
            self._socket = None
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3.0)
            if self._job is not None:
                self._job.close()
                self._job = None
            self._receive_buffer.clear()

    def _restart_unlocked(self, delay: float) -> None:
        self._log(
            "WARN", "SYSTEM_CONSOLE", f"通信异常，{delay:g}s后重启常驻会话"
        )
        self.close()
        time.sleep(delay)
        self.start()

    def _drain_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._stdout_queue.put(None)
            return
        try:
            for line in process.stdout:
                self._stdout_queue.put(line.rstrip("\r\n"))
        finally:
            self._stdout_queue.put(None)

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            if not line.startswith("TBBmalloc: skip allocation functions"):
                self._stderr_lines.append(line.rstrip("\r\n"))

    def _wait_server_port(self, deadline: float) -> int:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = "\n".join(
                    [*self._startup_stdout_lines, *self._stderr_lines]
                )
                self.close()
                raise TimeoutError(
                    "System Console服务启动超时" + (f"：{detail}" if detail else "")
                )
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except Empty as exc:
                raise TimeoutError("System Console服务启动超时") from exc
            if line is None:
                detail = "\n".join(
                    [*self._startup_stdout_lines, *self._stderr_lines]
                )
                raise RuntimeError(f"System Console提前退出：{detail}")
            self._startup_stdout_lines.append(line)
            match = re.search(r"SERVER_READY port=(\d+)", line)
            if match:
                return int(match.group(1))

    def _send_line(self, line: str) -> None:
        if self._socket is None:
            raise ConnectionError("System Console TCP尚未连接")
        self._socket.sendall((line + "\n").encode("utf-8"))

    def _read_line(self) -> str:
        if self._socket is None:
            raise ConnectionError("System Console TCP尚未连接")
        while True:
            newline = self._receive_buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._receive_buffer[:newline])
                del self._receive_buffer[: newline + 1]
                return raw.rstrip(b"\r").decode("utf-8")
            chunk = self._socket.recv(65536)
            if not chunk:
                raise ConnectionError("System Console TCP连接已关闭")
            self._receive_buffer.extend(chunk)
            if len(self._receive_buffer) > 1_000_000:
                raise RuntimeError("System Console TCP响应行过长")

    def _receive_response(self, expected_tcp_id: int) -> list[int]:
        fields = self._read_line().split()
        if len(fields) < 4 or fields[0] != "RES":
            raise RuntimeError("System Console TCP响应格式无效")
        response_id = int(fields[1], 0)
        if response_id != expected_tcp_id:
            raise RuntimeError(
                f"TCP事务ID不匹配：期望{expected_tcp_id}，收到{response_id}"
            )
        if fields[2] == "ERR":
            if len(fields) != 5:
                raise RuntimeError("System Console错误响应格式无效")
            message = bytes.fromhex(fields[4]).decode("utf-8", errors="replace")
            raise SystemConsoleRemoteError(fields[3], message)
        if fields[2] != "OK":
            raise RuntimeError("System Console TCP响应状态无效")
        count = int(fields[3], 0)
        if count < 0 or count > 256 or len(fields) != 4 + count:
            raise RuntimeError("System Console TCP响应字数无效")
        return [int(value, 0) & 0xFFFFFFFF for value in fields[4:]]

    def _rpc_once(
        self, operation: str, address: int, values_or_count: Sequence[int] | int
    ) -> list[int]:
        if not 0 <= address <= 0xFFFFFFFF or address % 4:
            raise ValueError("Avalon地址必须为32-bit对齐地址")
        tcp_id = self._tcp_id
        self._tcp_id = 1 if tcp_id == 0xFFFFFFFF else tcp_id + 1
        if operation == "READ32":
            count = int(values_or_count)
            if not 1 <= count <= 256:
                raise ValueError("单次READ32字数必须为1至256")
            line = f"REQ {tcp_id} READ32 {address} {count}"
        elif operation == "WRITE32":
            values = [int(value) & 0xFFFFFFFF for value in values_or_count]
            if not 1 <= len(values) <= 256:
                raise ValueError("单次WRITE32字数必须为1至256")
            count = len(values)
            line = " ".join(
                [f"REQ {tcp_id} WRITE32 {address} {len(values)}"]
                + [str(value) for value in values]
            )
        else:
            raise ValueError("未知TCP Avalon操作")
        if address >= AVALON_SPAN_BYTES or address + count * 4 > AVALON_SPAN_BYTES:
            raise ValueError("Avalon事务超出4 KB地址空间")
        started = time.monotonic()
        self._send_line(line)
        result = self._receive_response(tcp_id)
        self._log(
            "DEBUG",
            "AVALON",
            f"{operation} addr=0x{address:04X} "
            f"count={count if operation == 'READ32' else len(values)} "
            f"耗时={time.monotonic() - started:.3f}s",
        )
        return result

    def _rpc(
        self,
        operation: str,
        address: int,
        values_or_count: Sequence[int] | int,
        *,
        timeout: float | None = None,
    ) -> list[int]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self.start()
                active_socket = self._socket
                previous_timeout = (
                    active_socket.gettimeout() if active_socket is not None else None
                )
                if active_socket is not None and timeout is not None:
                    active_socket.settimeout(timeout)
                try:
                    return self._rpc_once(operation, address, values_or_count)
                finally:
                    if self._socket is active_socket and active_socket is not None:
                        active_socket.settimeout(previous_timeout)
            except SystemConsoleRemoteError as exc:
                if exc.error_class not in {"JTAG", "IO"}:
                    raise
                last_error = exc
            except (ConnectionError, OSError, TimeoutError, RuntimeError) as exc:
                last_error = exc
            if attempt < 2:
                self._restart_unlocked((1.0, 3.0)[attempt])
        raise RuntimeError(
            f"System Console/Avalon通信重试2次后仍失败：{last_error}"
        ) from last_error

    def _read32(
        self, address: int, count: int = 1, *, timeout: float | None = None
    ) -> list[int]:
        return self._rpc("READ32", address, count, timeout=timeout)

    def _write32(self, address: int, values: Sequence[int]) -> None:
        response = self._rpc("WRITE32", address, values)
        if response:
            raise RuntimeError("WRITE32不应返回数据字")

    def _validate_health_unlocked(self) -> None:
        values = self._rpc_once("READ32", ADDR_MAGIC, 4)
        if values[0] != MAGIC:
            raise RuntimeError(
                f"Avalon MAGIC错误：0x{values[0]:08X}，请加载FPGA V13 SOF"
            )
        if values[1] != AVALON_ABI_VERSION:
            raise RuntimeError(
                f"Avalon ABI不兼容：FPGA={values[1]}，host={AVALON_ABI_VERSION}"
            )
        protocol = values[2] & 0xFFFF
        revision = (values[2] >> 16) & 0xFFFF
        if protocol != PROTOCOL_VERSION or revision != IMPLEMENTATION_REVISION:
            raise RuntimeError(
                "FPGA协议或实现修订不兼容："
                f"protocol=0x{protocol:04X}, revision=0x{revision:04X}"
            )

    def health_check(self) -> JtagDiagnostic:
        with self._transaction_lock:
            self.start()
            values = self._read32(ADDR_MAGIC, 3)
            return JtagDiagnostic(
                hardware="System Console JTAG-to-Avalon-MM",
                device="唯一MAGIC匹配服务",
                protocol_version=values[2] & 0xFFFF,
                implementation_revision=(values[2] >> 16) & 0xFFFF,
                avalon_abi_version=values[1],
                magic=values[0],
            )

    def _synchronize_request_id_unlocked(self) -> None:
        deadline = time.monotonic() + 30.0
        while True:
            accepted, completed = self._rpc_once(
                "READ32", ADDR_LAST_ACCEPTED_ID, 2
            )
            if accepted == completed:
                self._fpga_request_id = (
                    1 if completed == 0xFFFFFFFF else completed + 1
                )
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"恢复时FPGA事务{accepted}仍未完成（last_completed={completed}）"
                )
            time.sleep(0.05)

    def _allocate_request_id(self) -> int:
        request_id = self._fpga_request_id
        self._fpga_request_id = (
            1 if request_id == 0xFFFFFFFF else request_id + 1
        )
        return request_id

    def _write_action(
        self, address: int, data: int = 0, *, request_id: int | None = None
    ) -> int:
        action_id = self._allocate_request_id() if request_id is None else request_id
        self._write32(ADDR_REQUEST_ID, [action_id])
        started = time.monotonic()
        self._write32(address, [data])
        accepted, completed = self._read32(ADDR_LAST_ACCEPTED_ID, 2)
        if accepted != action_id or completed != action_id:
            raise RuntimeError(
                f"FPGA事务ID未完成：request={action_id}, "
                f"accepted={accepted}, completed={completed}"
            )
        result = self._read32(ADDR_LAST_RESULT)[0]
        bridge_error = (result >> 24) & 0xFF
        core_error = (result >> 16) & 0xFF
        if bridge_error:
            raise RuntimeError(
                f"Avalon动作0x{address:04X}失败：bridge_error=0x"
                f"{bridge_error:02X}, core_error={describe_fpga_error(core_error)}"
            )
        if core_error:
            raise RuntimeError(
                f"Avalon动作0x{address:04X}失败：FPGA返回"
                f"{describe_fpga_error(core_error)}"
            )
        self._log(
            "DEBUG",
            "AVALON",
            f"FPGA request_id={action_id} action=0x{address:04X} "
            f"完成，耗时={time.monotonic() - started:.3f}s",
        )
        return action_id

    @staticmethod
    def _probe_raw(
        response: int, core_status: int, sample_sequence: int
    ) -> int:
        error = (core_status >> 8) & 0xFF
        driver_busy = bool(core_status & (1 << 1))
        driver_done = bool(core_status & (1 << 2))
        sar_busy = bool(core_status & (1 << 3))
        sar_done = bool(core_status & (1 << 4))
        return (
            (error << 54)
            | (int(driver_busy) << 53)
            | (int(driver_done) << 52)
            | (int(sar_busy) << 51)
            | (int(sar_done) << 50)
            | ((sample_sequence & 0xFFFF) << 33)
            | ((response & 0xFFFF) << 17)
        )

    def transact(
        self, commands: list[Command], timeout: float = 15.0
    ) -> list[Probe]:
        with self._transaction_lock:
            return self._transact_unlocked(commands, timeout)

    def _transact_unlocked(
        self, commands: list[Command], timeout: float
    ) -> list[Probe]:
        del timeout
        responses: list[int] = []
        for command in commands:
            if not 0 <= command.opcode <= 0xF:
                raise ValueError("opcode必须在4-bit范围内")
            if not 0 <= command.index <= 0x3F:
                raise ValueError("index必须在6-bit范围内")
            if not 0 <= command.data <= 0xFFFF:
                raise ValueError("data必须在16-bit范围内")
            responses.append(self._execute_command_unlocked(command))
        if not responses:
            return []
        core_status = self._read32(ADDR_CORE_STATUS)[0]
        sample_sequence = self._read32(ADDR_CORE_REGISTERS + 5 * 4)[0]
        return [
            Probe(self._probe_raw(response, core_status, sample_sequence))
            for response in responses
        ]

    def _execute_command_unlocked(self, command: Command) -> int:
        if command.opcode == OP_WRITE_CONTROL_WORD:
            if command.index >= 30:
                raise ValueError("控制字块索引必须小于30")
            self._write32(
                ADDR_CONTROL_WORDS + command.index * 4, [command.data]
            )
            return 0
        if command.opcode == OP_COMMIT_AND_START:
            self._write_action(ADDR_CONTROL_COMMIT, command.data)
            return 0
        if command.opcode == OP_SET_DECIMATION:
            self._write_action(ADDR_FILTER_HALF_LENGTH, command.data)
            return 0
        if command.opcode == OP_SAR_START:
            self._write_action(ADDR_SAR_START, command.data)
            return 0
        if command.opcode == OP_SAR_COMPARE:
            self._write_action(ADDR_SAR_COMPARE, command.data)
            return 0
        if command.opcode == OP_CLEAR_ERROR:
            self._write_action(ADDR_CLEAR_ERROR)
            return 0
        if command.opcode == OP_STATISTICS_START:
            self._write_action(ADDR_STATISTICS_START)
            return 0
        if command.opcode == OP_READ_REGISTER:
            if command.index >= 46:
                raise ValueError("只读寄存器索引必须小于46")
            return self._read32(ADDR_CORE_REGISTERS + command.index * 4)[0]
        if command.opcode == OP_WRITE_FPGA_COEFFICIENT:
            if command.index >= FPGA_COEFFICIENT_WORDS:
                raise ValueError("FPGA内部系数分片索引必须小于48")
            self._write32(
                ADDR_FPGA_COEFFICIENTS + command.index * 4,
                [command.data],
            )
            return 0
        if command.opcode == OP_STATISTICS_READ:
            if command.data >= STATISTICS_SAMPLES or command.index > 2:
                raise ValueError("统计缓存地址或分片无效")
            mu1, mu2 = self._read32(ADDR_STATISTICS_RAM + command.data * 8, 2)
            packed = (mu1 & 0x3FFFF) | ((mu2 & 0x3FFFF) << 18)
            return (packed >> (16 * command.index)) & 0xFFFF
        if command.opcode == OP_SET_CONTROL_SAMPLES:
            self._write_action(ADDR_CONTROL_SAMPLES, command.data)
            return 0
        if command.opcode == OP_SET_DSM_OVERRIDE:
            if command.index == 0:
                self._write32(ADDR_DSM_LOW, [command.data])
            elif command.index == 1:
                self._write32(ADDR_DSM_HIGH, [command.data])
            elif command.index == 2:
                self._write_action(ADDR_DSM_COMMIT, command.data)
            else:
                raise ValueError("DSM命令索引无效")
            return 0
        if command.opcode == OP_LATCH_LIVE_SAMPLE:
            self._write_action(ADDR_LATCH_LIVE)
            return 0
        if command.opcode == OP_LATCH_CALIBRATION_SAMPLE:
            self._write_action(ADDR_LATCH_CALIBRATION)
            return 0
        raise ValueError(f"不支持的opcode 0x{command.opcode:X}")

    def transact_checked(
        self,
        commands: Sequence[Command],
        *,
        context: str,
        timeout: float = 15.0,
    ) -> list[Probe]:
        probes = self.transact(list(commands), timeout)
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

    def configure_and_start(
        self,
        words16: Sequence[int],
        coefficient_words16: Sequence[int],
        selection: int,
        *,
        transaction_timeout: float = 25.0,
        completion_timeout: float | None = None,
        control_sample_count: int = DEFAULT_CONTROL_SAMPLE_COUNT,
    ) -> Probe:
        del transaction_timeout
        words = list(words16)
        coefficient_words = list(coefficient_words16)
        if len(words) != 30 or any(not 0 <= value <= 0xFFFF for value in words):
            raise ValueError("474位控制字必须恰好包含30个16-bit传输块")
        if (len(coefficient_words) != FPGA_COEFFICIENT_WORDS or
                any(not 0 <= value <= 0xFFFF for value in coefficient_words)):
            raise ValueError("FPGA内部Q20.16系数必须恰好包含48个16-bit分片")
        if any(coefficient_words[index] & 0xFFE0
                for index in range(2, FPGA_COEFFICIENT_WORDS, 3)):
            raise ValueError("FPGA内部Q20.16系数高分片只能使用低5位")
        if not 0 <= selection <= 0xF:
            raise ValueError("芯片选择字段必须在4-bit范围内")
        samples = validate_control_sample_count(control_sample_count)
        wait_timeout = (
            max(8.0, samples * 0.0005 + 2.0)
            if completion_timeout is None
            else completion_timeout
        )
        with self._transaction_lock:
            self._wait_driver_idle_unlocked(DRIVER_IDLE_TIMEOUT)
            self._write_action(ADDR_CLEAR_ERROR)
            self._write_action(ADDR_CONTROL_SAMPLES, samples)
            self._write32(ADDR_CONTROL_WORDS, words)
            self._write32(ADDR_FPGA_COEFFICIENTS, coefficient_words)
            self._write_action(ADDR_CONTROL_COMMIT, selection)
            deadline = time.monotonic() + wait_timeout
            while True:
                status = self._read32(ADDR_CORE_STATUS)[0]
                if status & (1 << 2) and not status & (1 << 1):
                    sequence = self._read32(ADDR_CORE_REGISTERS + 5 * 4)[0]
                    return Probe(self._probe_raw(0, status, sequence))
                if time.monotonic() >= deadline:
                    raise TimeoutError("等待474位驱动事务完成超时")
                time.sleep(0.05)

    def _wait_driver_idle_unlocked(self, timeout: float) -> Probe:
        deadline = time.monotonic() + timeout
        while True:
            status = self._read32(ADDR_CORE_STATUS)[0]
            if not status & (1 << 1):
                sequence = self._read32(ADDR_CORE_REGISTERS + 5 * 4)[0]
                return Probe(self._probe_raw(0, status, sequence))
            if time.monotonic() >= deadline:
                raise TimeoutError("等待474位驱动器空闲超时")
            time.sleep(0.05)

    def read_registers(self, indices: list[int]) -> dict[int, int]:
        if any(not 0 <= index < 46 for index in indices):
            raise ValueError("只读寄存器索引必须在0至45")
        with self._transaction_lock:
            return {
                index: self._read32(ADDR_CORE_REGISTERS + index * 4)[0]
                for index in indices
            }

    def set_filter_total_samples(self, total_samples: int) -> None:
        total = validate_filter_total_samples(total_samples)
        with self._transaction_lock:
            self._write_action(ADDR_FILTER_HALF_LENGTH, total // 2)

    def set_dsm_override(
        self, raw_value: int, *, enabled: bool
    ) -> DsmOverrideStatus:
        raw = validate_dsm_override_raw(raw_value)
        with self._transaction_lock:
            self._write_action(ADDR_CLEAR_ERROR)
            self._write32(
                ADDR_DSM_LOW, [raw & 0xFFFF, (raw >> 16) & 0x1FFF]
            )
            self._write_action(ADDR_DSM_COMMIT, int(enabled))
            return self.read_dsm_override_status()

    def read_dsm_override_status(self) -> DsmOverrideStatus:
        registers = self.read_registers([18, 19, 20])
        raw = registers[19] | ((registers[20] & 0x1FFF) << 16)
        return DsmOverrideStatus(bool(registers[18] & 1), raw)

    @staticmethod
    def _live_sample_from_values(values: Sequence[int]) -> LiveSample:
        return LiveSample(
            sample=MuSample(
                values[0] | ((values[1] & 0x3) << 16),
                values[2] | ((values[3] & 0x3) << 16),
            ),
            onchip_sample=MuSample(
                values[4] | ((values[5] & 0x3) << 16),
                values[6] | ((values[7] & 0x3) << 16),
            ),
            sequence=values[8],
            total_filter_samples=values[9] * 2,
            onchip_sequence=values[10],
        )

    def read_live_sample(self, retries: int = 3) -> LiveSample:
        del retries
        with self._transaction_lock:
            self._write_action(ADDR_LATCH_LIVE)
            return self._live_sample_from_values(
                self._read32(ADDR_CORE_REGISTERS + 21 * 4, 11)
            )

    def read_live_sample_without_snapshot(self, retries: int = 3) -> LiveSample:
        del retries
        registers = self.read_registers([1, 2, 3, 4, 5, 15])
        return LiveSample(
            sample=MuSample(
                registers[1] | ((registers[2] & 3) << 16),
                registers[3] | ((registers[4] & 3) << 16),
            ),
            onchip_sample=MuSample(0, 0),
            sequence=registers[5],
            total_filter_samples=registers[15] * 2,
            onchip_sequence=0,
        )

    @staticmethod
    def _sar_from_values(values: Sequence[int], core_status: int) -> SarStatus:
        return SarStatus(
            values[0] | ((values[1] & 0x1FFF) << 16),
            values[2],
            bool(core_status & (1 << 3)),
            bool(core_status & (1 << 4)),
            (core_status >> 8) & 0xFF,
        )

    def _calibration_from_values(
        self,
        values: Sequence[int],
        high_values: Sequence[int],
        sar_values: Sequence[int],
        core_status: int,
    ) -> CalibrationSnapshot:
        def raw37(offset: int, high_index: int) -> int:
            return (
                values[offset]
                | ((values[offset + 1] & 0x1FFF) << 16)
                | ((high_values[high_index] & 0xFF) << 29)
            )

        return CalibrationSnapshot(
            raw37(0, 0), raw37(2, 1), raw37(4, 2), raw37(6, 3), values[8],
            bool(values[9] & 2), bool(values[9] & 1),
            self._sar_from_values(sar_values, core_status),
        )

    def read_calibration_snapshot(self) -> CalibrationSnapshot:
        with self._transaction_lock:
            self._write_action(ADDR_LATCH_CALIBRATION)
            values = self._read32(ADDR_CORE_REGISTERS + 32 * 4, 10)
            high_values = self._read32(ADDR_CORE_REGISTERS + 42 * 4, 4)
            sar = self._read32(ADDR_CORE_REGISTERS + 6 * 4, 3)
            status = self._read32(ADDR_CORE_STATUS)[0]
            return self._calibration_from_values(values, high_values, sar, status)

    def read_monitor_snapshot(self) -> MonitorSnapshot:
        with self._transaction_lock:
            self._write_action(ADDR_LATCH_LIVE)
            live = self._read32(ADDR_CORE_REGISTERS + 21 * 4, 11)
            self._write_action(ADDR_LATCH_CALIBRATION)
            calibration = self._read32(
                ADDR_CORE_REGISTERS + 32 * 4, 10
            )
            calibration_high = self._read32(
                ADDR_CORE_REGISTERS + 42 * 4, 4
            )
            sar = self._read32(ADDR_CORE_REGISTERS + 6 * 4, 3)
            status = self._read32(ADDR_CORE_STATUS)[0]
            return MonitorSnapshot(
                self._live_sample_from_values(live),
                self._calibration_from_values(
                    calibration, calibration_high, sar, status
                ),
            )

    def _sar_status_unlocked(
        self, timeout: float = 15.0, *, check_error: bool
    ) -> SarStatus:
        del timeout
        values = self._read32(ADDR_CORE_REGISTERS + 6 * 4, 3)
        core_status = self._read32(ADDR_CORE_STATUS)[0]
        status = self._sar_from_values(values, core_status)
        if check_error and status.fpga_error:
            raise RuntimeError(
                f"读取SAR状态时FPGA返回{describe_fpga_error(status.fpga_error)}"
            )
        return status

    def read_sar_status(self) -> SarStatus:
        with self._transaction_lock:
            return self._sar_status_unlocked(check_error=False)

    def read_sar_registers(self) -> tuple[int, int]:
        status = self.read_sar_status()
        return status.code_raw, status.step

    def start_sar(self, *, ready_timeout: float = 3.0) -> SarStatus:
        with self._transaction_lock:
            diagnostic = self.health_check()
            if not diagnostic.compatible:
                raise RuntimeError(diagnostic.summary)
            self._write_action(ADDR_CLEAR_ERROR)
            self._write_action(ADDR_SAR_START, 1)
            deadline = time.monotonic() + ready_timeout
            while True:
                status = self._sar_status_unlocked(check_error=True)
                if (
                    status.busy and not status.done and status.step == 0
                    and status.code_raw == SAR_INITIAL_CODE_Q16
                ):
                    return status
                if time.monotonic() >= deadline:
                    raise TimeoutError("FPGA已接收SAR启动命令，但未进入运行态")
                time.sleep(0.05)

    def submit_sar_decision(
        self, bit: int, *, completion_timeout: float = 3.0
    ) -> SarStatus:
        if bit not in (0, 1):
            raise ValueError("SAR判决必须为0或1")
        with self._transaction_lock:
            before = self._sar_status_unlocked(check_error=True)
            if not before.busy or before.done or before.step >= SAR_STEPS:
                raise RuntimeError("SAR尚未进入运行态或已经结束")
            self._write_action(ADDR_SAR_COMPARE, bit)
            expected = before.step + 1
            deadline = time.monotonic() + completion_timeout
            while True:
                status = self._sar_status_unlocked(check_error=True)
                if status.step == expected:
                    if expected == SAR_STEPS:
                        if status.done and not status.busy:
                            return status
                    elif status.busy and not status.done:
                        return status
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"SAR判决已发送，但步数未从{before.step}更新到{expected}"
                    )
                time.sleep(0.05)

    def capture_statistics_batch(
        self, *, capture_timeout: float = 120.0, download_timeout: float = 60.0
    ) -> list[MuSample]:
        if capture_timeout <= 0 or download_timeout <= 0:
            raise ValueError("统计采集和下载超时必须大于0")
        with self._transaction_lock:
            self._write_action(ADDR_CLEAR_ERROR)
            ready, capture_id = self._read32(ADDR_STATISTICS_READY, 2)
            if ready:
                self._write_action(ADDR_STATISTICS_RELEASE, capture_id)
            capture_started = time.monotonic()
            capture_id = self._write_action(ADDR_STATISTICS_START)
            self._log(
                "INFO", "STATISTICS", f"100组μ采集开始，capture_id={capture_id}"
            )
            deadline = time.monotonic() + capture_timeout
            try:
                while True:
                    ready, observed_id, count = self._read32(
                        ADDR_STATISTICS_READY, 3
                    )
                    if ready and observed_id == capture_id and count == 100:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "等待100组μ统计采集完成超时；请检查FS_DATA_OUT33"
                        )
                    time.sleep(0.05)
            except Exception:
                try:
                    self._write_action(ADDR_STATISTICS_ABORT, capture_id)
                except Exception:
                    pass
                raise
            self._log(
                "INFO",
                "STATISTICS",
                f"capture_id={capture_id}采集完成，耗时="
                f"{time.monotonic() - capture_started:.3f}s",
            )
            before = self._read32(ADDR_STATISTICS_READY, 2)
            download_started = time.monotonic()
            words = self._read32(
                ADDR_STATISTICS_RAM, 200, timeout=download_timeout
            )
            after = self._read32(ADDR_STATISTICS_READY, 2)
            if before != [1, capture_id] or after != [1, capture_id]:
                raise RuntimeError("统计批次在块读取期间发生变化，拒绝接收")
            samples = [
                MuSample(words[index * 2] & 0x3FFFF, words[index * 2 + 1] & 0x3FFFF)
                for index in range(STATISTICS_SAMPLES)
            ]
            self._log(
                "INFO",
                "STATISTICS",
                f"capture_id={capture_id}块下载200字，耗时="
                f"{time.monotonic() - download_started:.3f}s",
            )
            self._write_action(ADDR_STATISTICS_RELEASE, capture_id)
            return samples

    def diagnose(self, timeout: float = 10.0) -> JtagDiagnostic:
        del timeout
        return self.health_check()

    @staticmethod
    def _failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
        return (completed.stderr or completed.stdout).strip()

    def program(self, sof_path: str | Path, timeout: float = 60.0) -> str:
        sof = Path(sof_path)
        if not sof.is_file():
            raise FileNotFoundError(f"未找到SOF：{sof}")
        with self._transaction_lock:
            self.close()
            jtagconfig = self.quartus_root / "bin64" / "jtagconfig.exe"
            quartus_pgm = self.quartus_root / "bin64" / "quartus_pgm.exe"
            chain = subprocess.run(
                [str(jtagconfig)], check=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10.0,
            )
            if chain.returncode:
                raise RuntimeError(self._failure_detail(chain))
            cables = re.findall(
                r"^\d+\)\s+(DE-SoC \[USB-[^\]]+\])$",
                chain.stdout,
                flags=re.MULTILINE,
            )
            if len(cables) != 1 or "02D120DD" not in chain.stdout:
                raise RuntimeError("未唯一检测到DE-SoC Cyclone V JTAG链")
            with tempfile.TemporaryDirectory(prefix="oscv_program_") as temp:
                staged = Path(temp) / "fpga_v8.sof"
                shutil.copy2(sof, staged)
                result = subprocess.run(
                    [str(quartus_pgm), "-c", cables[0], "-m", "JTAG", "-o", f"p;{staged}@2"],
                    check=False, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
            if result.returncode:
                raise RuntimeError(f"FPGA加载失败：{self._failure_detail(result)}")
            self.start()
            return "FPGA_V13 Avalon SOF已成功加载并通过健康检查"


# Transitional import-name compatibility.  All runtime communication uses
# System Console and the Avalon bridge without a secondary transport.
QuartusJtagTransport = SystemConsoleAvalonTransport
