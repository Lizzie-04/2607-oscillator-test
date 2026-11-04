"""Replaceable professional frequency-instrument interface."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Protocol


KEYSIGHT_53220A_DEFAULT_GATE_TIME_S = 5.0
KEYSIGHT_53220A_DEFAULT_VISA_LIBRARY = (
    r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin\visa32.dll"
)
KEYSIGHT_53220A_INVALID_READING_THRESHOLD_HZ = 9.0e37


def _prepend_existing_paths(paths: list[str]) -> None:
    existing = [path for path in paths if Path(path).exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(
            existing + [os.environ.get("PATH", "")]
        )


def bootstrap_keysight_visa_path() -> None:
    """Expose Keysight VISA DLL dependencies to newly started Python."""
    if os.name != "nt":
        return
    _prepend_existing_paths(
        [
            r"C:\Program Files\Keysight\IO Libraries Suite\bin",
            r"C:\Program Files\Keysight\IO Libraries Suite",
            r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin",
            r"C:\Program Files\IVI Foundation\VISA\Win64\Bin",
            r"C:\Program Files (x86)\Keysight\IO Libraries Suite\bin",
            r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\ktvisa",
            r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\Bin",
        ]
    )


class FrequencyInstrumentBackend(Protocol):
    def read_frequency_mhz(self) -> float:
        """Return the latest measured frequency in MHz."""


class UnavailableFrequencyInstrument:
    """Safe placeholder until the real instrument model is selected."""

    def read_frequency_mhz(self) -> float:
        raise RuntimeError("专业频率测试仪器尚未配置")


class Keysight53220ABackend:
    """PyVISA backend for a Keysight/Agilent 53220A frequency counter.

    The backend is lazy: GUI startup does not block on instrument discovery.
    The first read scans all VISA resources, opens the first device whose
    ``*IDN?`` contains ``53220A``, configures frequency measurement, and then
    performs a 5 s gate-time read by default.
    """

    def __init__(
        self,
        *,
        resource: str | None = None,
        visa_library: str | None = None,
        channel: int = 1,
        gate_time_s: float = KEYSIGHT_53220A_DEFAULT_GATE_TIME_S,
        expected_frequency_hz: float | None = None,
        timeout_s: float | None = None,
    ) -> None:
        if channel <= 0:
            raise ValueError("53220A通道号必须为正整数")
        if not math.isfinite(gate_time_s) or gate_time_s <= 0:
            raise ValueError("53220A gate time必须为正有限数值")
        if (
            expected_frequency_hz is not None
            and (
                not math.isfinite(expected_frequency_hz)
                or expected_frequency_hz <= 0
            )
        ):
            raise ValueError("53220A期望频率必须为正有限数值")

        self.requested_resource = (
            resource
            or os.environ.get("KEYSIGHT_53220A_RESOURCE")
            or None
        )
        self.visa_library = (
            visa_library
            or os.environ.get("KEYSIGHT_VISA_LIBRARY")
            or None
        )
        self.channel = channel
        self.gate_time_s = gate_time_s
        self.expected_frequency_hz = expected_frequency_hz
        self.timeout_s = timeout_s or max(8.0, gate_time_s + 3.0)

        self._pyvisa = None
        self._visa_errors = None
        self._resource_manager = None
        self._instrument = None
        self._selected_resource: str | None = None
        self._idn: str | None = None
        self._configured = False

    @property
    def selected_resource(self) -> str | None:
        return self._selected_resource

    @property
    def idn(self) -> str | None:
        return self._idn

    def describe(self) -> str:
        if self._selected_resource and self._idn:
            return f"{self._idn} @ {self._selected_resource}"
        return "Keysight 53220A自动检索"

    def read_frequency_mhz(self) -> float:
        instrument = self._ensure_instrument()
        raw_text = instrument.query("READ?").strip()
        try:
            frequency_hz = float(raw_text)
        except ValueError as exc:
            raise RuntimeError(
                f"53220A返回的频率不是数字：{raw_text!r}"
            ) from exc
        if (
            not math.isfinite(frequency_hz)
            or frequency_hz <= 0
            or abs(frequency_hz) >= KEYSIGHT_53220A_INVALID_READING_THRESHOLD_HZ
        ):
            raise RuntimeError(
                "53220A返回无效频率："
                f"{raw_text!r}；请检查输入信号、触发电平和gate time"
            )
        return frequency_hz / 1_000_000.0

    def close(self) -> None:
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None
        if self._resource_manager is not None:
            close = getattr(self._resource_manager, "close", None)
            if close is not None:
                close()
            self._resource_manager = None
        self._configured = False

    def _ensure_pyvisa(self):
        if self._pyvisa is not None:
            return self._pyvisa
        bootstrap_keysight_visa_path()
        try:
            import pyvisa
            from pyvisa import errors as visa_errors
        except ImportError as exc:
            raise RuntimeError(
                "未安装PyVISA，请先运行：python -m pip install pyvisa"
            ) from exc
        self._pyvisa = pyvisa
        self._visa_errors = visa_errors
        return pyvisa

    def _ensure_resource_manager(self):
        if self._resource_manager is not None:
            return self._resource_manager

        pyvisa = self._ensure_pyvisa()
        visa_errors = self._visa_errors
        try:
            if self.visa_library:
                self._resource_manager = pyvisa.ResourceManager(
                    self.visa_library
                )
            else:
                self._resource_manager = pyvisa.ResourceManager()
        except visa_errors.VisaIOError:
            if (
                os.name == "nt"
                and not self.visa_library
                and Path(KEYSIGHT_53220A_DEFAULT_VISA_LIBRARY).exists()
            ):
                self._resource_manager = pyvisa.ResourceManager(
                    KEYSIGHT_53220A_DEFAULT_VISA_LIBRARY
                )
            else:
                raise
        return self._resource_manager

    def _ensure_instrument(self):
        if self._instrument is not None and self._configured:
            return self._instrument

        resource_manager = self._ensure_resource_manager()
        resource, idn = self._find_resource(resource_manager)
        instrument = resource_manager.open_resource(resource)
        instrument.timeout = max(1000, int(self.timeout_s * 1000))
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        self._instrument = instrument
        self._selected_resource = resource
        self._idn = idn
        self._configure_frequency_measurement(instrument)
        self._configured = True
        return instrument

    def _find_resource(self, resource_manager) -> tuple[str, str]:
        if self.requested_resource:
            idn = self._query_idn(resource_manager, self.requested_resource)
            self._validate_53220a_idn(idn, self.requested_resource)
            return self.requested_resource, idn

        failures: list[str] = []
        for resource in tuple(resource_manager.list_resources()):
            try:
                idn = self._query_idn(resource_manager, resource)
            except Exception as exc:
                failures.append(f"{resource}: {exc}")
                continue
            if "53220A" in idn.upper():
                return resource, idn

        message = "未自动发现Keysight/Agilent 53220A"
        if failures:
            message += "；已尝试资源：" + "；".join(failures)
        raise RuntimeError(
            message + "。请在Keysight Connection Expert中确认仪器在线。"
        )

    def _query_idn(self, resource_manager, resource: str) -> str:
        instrument = resource_manager.open_resource(resource)
        try:
            instrument.timeout = max(1000, int(self.timeout_s * 1000))
            instrument.write_termination = "\n"
            instrument.read_termination = "\n"
            return instrument.query("*IDN?").strip()
        finally:
            instrument.close()

    @staticmethod
    def _validate_53220a_idn(idn: str, resource: str) -> None:
        if "53220A" not in idn.upper():
            raise RuntimeError(
                f"VISA资源不是53220A：{resource} 返回 {idn!r}"
            )

    @staticmethod
    def _is_no_error(error_text: str) -> bool:
        text = error_text.strip().lower()
        return (
            not text
            or text.startswith("+0")
            or text.startswith("0")
            or "no error" in text
        )

    @staticmethod
    def _query_error(instrument) -> str:
        try:
            return instrument.query(":SYSTem:ERRor?").strip()
        except Exception:
            return ""

    def _write_checked(
        self,
        instrument,
        command: str,
        *,
        tolerate_error: bool = False,
    ) -> bool:
        instrument.write(command)
        error_text = self._query_error(instrument)
        if self._is_no_error(error_text):
            return True
        if tolerate_error:
            return False
        raise RuntimeError(f"53220A命令失败：{command}；{error_text}")

    def _configure_frequency_measurement(self, instrument) -> None:
        if self.expected_frequency_hz is None:
            configure_commands = [
                f":CONFigure:FREQuency (@{self.channel})",
                f":CONFigure:SCALar:VOLTage:FREQuency (@{self.channel})",
            ]
        else:
            frequency_argument = f"{self.expected_frequency_hz:.12g}"
            configure_commands = [
                (
                    ":CONFigure:SCALar:VOLTage:FREQuency "
                    f"{frequency_argument},MAXimum,(@{self.channel})"
                ),
                (
                    f":CONFigure:FREQuency "
                    f"{frequency_argument},MAXimum,(@{self.channel})"
                ),
                f":CONFigure:FREQuency (@{self.channel})",
            ]

        instrument.write("*CLS")
        self._write_checked(
            instrument,
            ":FORMat:DATA ASCii,15",
            tolerate_error=True,
        )

        last_error = ""
        for command in configure_commands:
            instrument.write("*CLS")
            instrument.write(command)
            last_error = self._query_error(instrument)
            if self._is_no_error(last_error):
                break
        else:
            raise RuntimeError(
                "53220A频率测量配置失败，最后错误：" + last_error
            )

        self._write_checked(
            instrument,
            ":SENSe:FREQuency:GATE:SOURce TIME",
            tolerate_error=True,
        )
        self._write_checked(
            instrument,
            f":SENSe:FREQuency:GATE:TIME {self.gate_time_s:.9g}",
            tolerate_error=True,
        )
