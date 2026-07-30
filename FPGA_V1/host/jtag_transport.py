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
OP_READ_REGISTER = 0x8

FPGA_ERROR_MESSAGES = {
    0x01: "控制字块索引超出0至29",
    0x02: "30个控制字块尚未全部写入",
    0x03: "芯片启动驱动器正忙",
    0x04: "Filter内部半长N_FILTER超出32至4096",
    0x05: "SAR尚未启动或已经结束",
    0x06: "未知opcode",
}


def describe_fpga_error(code: int) -> str:
    description = FPGA_ERROR_MESSAGES.get(code, "未定义错误")
    return f"0x{code:02X}（{description}）"


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
class JtagDiagnostic:
    hardware: str
    device: str
    source_hex_digits: int
    probe_hex_digits: int

    @property
    def compatible(self) -> bool:
        return self.source_hex_digits == 16 and self.probe_hex_digits == 16

    @property
    def summary(self) -> str:
        source_bits = self.source_hex_digits * 4
        probe_bits = self.probe_hex_digits * 4
        if self.compatible:
            return (
                f"连接正常：{self.hardware}；FPGA V1 ISSP 为 "
                f"{source_bits}-bit/{probe_bits}-bit"
            )
        return (
            f"已检测到 JTAG 和 FPGA，但当前镜像的 ISSP 为 "
            f"{source_bits}-bit/{probe_bits}-bit；FPGA V1 需要 "
            "64-bit/64-bit。请加载 fpga_v1.sof。"
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
            if completed.returncode:
                detail = self._failure_detail(completed)
                raise RuntimeError(f"Quartus JTAG事务失败：{detail}")

            results: list[Probe] = []
            for line in completed.stdout.splitlines():
                if line.startswith("RESULT "):
                    _, _, probe_hex = line.split()
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
        completion_timeout: float = 8.0,
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

        commands = [Command(OP_CLEAR_ERROR, 0, 0)]
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

            deadline = time.monotonic() + completion_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "等待474位驱动事务完成超时；FPGA可能仍处于busy状态"
                    )
                status_probe = self._transact_unlocked(
                    [Command(OP_READ_REGISTER, 0, 0)],
                    max(1.0, min(5.0, remaining)),
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
        return {
            index: probe.response for index, probe in zip(indices, probes, strict=True)
        }

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
            return JtagDiagnostic(
                hardware=fields["hardware"],
                device=fields["device"],
                source_hex_digits=int(fields["source_hex_digits"]),
                probe_hex_digits=int(fields["probe_hex_digits"]),
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
            staged_sof = Path(temporary) / "fpga_v1.sof"
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
        return "FPGA V1 已成功加载"
