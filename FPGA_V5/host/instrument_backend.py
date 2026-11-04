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


def validate_gate_time_s(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("gate time必须是大于0的有限秒数")
    return value


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

    def set_gate_time_s(self, gate_time_s: float) -> None:
        """Update the gate time used by subsequent frequency reads."""


class ResistanceInstrumentBackend(Protocol):
    def read_resistance_ohm(self) -> float:
        """Return the latest measured resistance in ohms."""


class UnavailableFrequencyInstrument:
    """Safe placeholder until the real instrument model is selected."""

    def read_frequency_mhz(self) -> float:
        raise RuntimeError("专业频率测试仪器尚未配置")

    def set_gate_time_s(self, gate_time_s: float) -> None:
        validate_gate_time_s(gate_time_s)


class UnavailableResistanceInstrument:
    """Safe placeholder for optional thermistor readout."""

    def read_resistance_ohm(self) -> float:
        raise RuntimeError("DMM7510温敏电阻仪器尚未配置")

    def describe(self) -> str:
        return "DMM7510尚未配置"


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
        gate_time_s = validate_gate_time_s(gate_time_s)
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
        self._timeout_overridden = timeout_s is not None
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

    def set_gate_time_s(self, gate_time_s: float) -> None:
        value = validate_gate_time_s(gate_time_s)
        timeout_s = self.timeout_s
        if not self._timeout_overridden:
            timeout_s = max(8.0, value + 3.0)
        if self._instrument is not None:
            self._instrument.timeout = max(1000, int(timeout_s * 1000))
            self._write_checked(
                self._instrument,
                f":SENSe:FREQuency:GATE:TIME {value:.9g}",
            )
            self._instrument.timeout = max(1000, int(timeout_s * 1000))
        self.gate_time_s = value
        self.timeout_s = timeout_s

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
            try:
                idn = self._query_idn(
                    resource_manager,
                    self.requested_resource,
                )
            except Exception as exc:
                raise RuntimeError(
                    "已指定53220A VISA资源，但*IDN?查询失败："
                    f"{self.requested_resource}: {exc}。"
                    "请关闭Keysight Interactive IO/其他占用会话后重试，"
                    "或在Connection Expert中对该资源执行*IDN?确认。"
                ) from exc
            self._validate_53220a_idn(idn, self.requested_resource)
            return self.requested_resource, idn

        failures: list[str] = []
        mismatches: list[str] = []
        resources = tuple(resource_manager.list_resources())
        for resource in resources:
            try:
                idn = self._query_idn(resource_manager, resource)
            except Exception as exc:
                failures.append(f"{resource}: {exc}")
                continue
            if "53220A" in idn.upper():
                return resource, idn
            mismatches.append(f"{resource}: {idn!r}")

        if not resources:
            message = "VISA未列出任何资源，未自动发现Keysight/Agilent 53220A"
        elif failures and not mismatches:
            message = (
                "VISA已枚举到资源，但未能通过*IDN?确认Keysight/Agilent "
                "53220A"
            )
        else:
            message = "未自动发现Keysight/Agilent 53220A"
        if mismatches:
            message += "；非53220A响应：" + "；".join(mismatches)
        if failures:
            message += "；*IDN?失败资源：" + "；".join(failures)
        raise RuntimeError(
            message
            + "。请关闭Keysight Interactive IO/其他占用会话，"
            + "并在Connection Expert中对资源执行*IDN?确认。"
        )

    def _query_idn(self, resource_manager, resource: str) -> str:
        instrument = resource_manager.open_resource(resource)
        try:
            instrument.timeout = max(1000, int(self.timeout_s * 1000))
            instrument.write_termination = "\n"
            instrument.read_termination = "\n"
            try:
                instrument.query_delay = 0.05
            except Exception:
                pass
            clear = getattr(instrument, "clear", None)
            if clear is not None:
                try:
                    clear()
                except Exception:
                    pass
            try:
                instrument.write("*CLS")
            except Exception:
                pass
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


class KeithleyDmm7510Backend:
    """PyVISA backend for a Keithley DMM7510 4-wire resistance readout."""

    def __init__(
        self,
        *,
        resource: str | None = None,
        visa_library: str | None = None,
        nplc: float = 1.0,
        timeout_s: float = 8.0,
    ) -> None:
        if not math.isfinite(nplc) or nplc <= 0:
            raise ValueError("DMM7510 NPLC必须是大于0的有限数值")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("DMM7510超时时间必须是大于0的有限秒数")

        self.requested_resource = (
            resource
            or os.environ.get("KEITHLEY_DMM7510_RESOURCE")
            or None
        )
        self.visa_library = (
            visa_library
            or os.environ.get("KEITHLEY_VISA_LIBRARY")
            or None
        )
        self.nplc = nplc
        self.timeout_s = timeout_s

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
        return "Keithley DMM7510自动检索"

    def read_resistance_ohm(self) -> float:
        instrument = self._ensure_instrument()
        raw_text = instrument.query("READ?").strip()
        try:
            resistance = float(raw_text)
        except ValueError as exc:
            raise RuntimeError(
                f"DMM7510返回的电阻不是数字：{raw_text!r}"
            ) from exc
        if not math.isfinite(resistance):
            raise RuntimeError(
                f"DMM7510返回无效电阻：{raw_text!r}；请检查四线接线和量程"
            )
        return resistance

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
        if self.visa_library:
            self._resource_manager = pyvisa.ResourceManager(
                self.visa_library
            )
        else:
            self._resource_manager = pyvisa.ResourceManager()
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
        self._configure_resistance_measurement(instrument)
        self._configured = True
        return instrument

    def _find_resource(self, resource_manager) -> tuple[str, str]:
        if self.requested_resource:
            try:
                idn = self._query_idn(
                    resource_manager,
                    self.requested_resource,
                )
            except Exception as exc:
                raise RuntimeError(
                    "已指定DMM7510 VISA资源，但*IDN?查询失败："
                    f"{self.requested_resource}: {exc}。"
                    "请关闭其他占用会话后重试，或在Connection Expert中确认。"
                ) from exc
            self._validate_dmm7510_idn(idn, self.requested_resource)
            return self.requested_resource, idn

        failures: list[str] = []
        mismatches: list[str] = []
        resources = tuple(resource_manager.list_resources())
        for resource in resources:
            try:
                idn = self._query_idn(resource_manager, resource)
            except Exception as exc:
                failures.append(f"{resource}: {exc}")
                continue
            if "DMM7510" in idn.upper():
                return resource, idn
            mismatches.append(f"{resource}: {idn!r}")

        if not resources:
            message = "VISA未列出任何资源，未自动发现Keithley DMM7510"
        elif failures and not mismatches:
            message = "VISA已枚举到资源，但未能通过*IDN?确认DMM7510"
        else:
            message = "未自动发现Keithley DMM7510"
        if mismatches:
            message += "；非DMM7510响应：" + "；".join(mismatches)
        if failures:
            message += "；*IDN?失败资源：" + "；".join(failures)
        raise RuntimeError(message)

    def _query_idn(self, resource_manager, resource: str) -> str:
        instrument = resource_manager.open_resource(resource)
        try:
            instrument.timeout = max(1000, int(self.timeout_s * 1000))
            instrument.write_termination = "\n"
            instrument.read_termination = "\n"
            try:
                instrument.query_delay = 0.05
            except Exception:
                pass
            clear = getattr(instrument, "clear", None)
            if clear is not None:
                try:
                    clear()
                except Exception:
                    pass
            try:
                instrument.write("*CLS")
            except Exception:
                pass
            return instrument.query("*IDN?").strip()
        finally:
            instrument.close()

    @staticmethod
    def _validate_dmm7510_idn(idn: str, resource: str) -> None:
        if "DMM7510" not in idn.upper():
            raise RuntimeError(
                f"VISA资源不是DMM7510：{resource} 返回 {idn!r}"
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
            try:
                instrument.write("*CLS")
            except Exception:
                pass
            return False
        raise RuntimeError(f"DMM7510命令失败：{command}；{error_text}")

    def _configure_resistance_measurement(self, instrument) -> None:
        instrument.write("*CLS")
        self._write_checked(
            instrument,
            ":FORMat:DATA ASCii,15",
            tolerate_error=True,
        )
        self._write_checked(instrument, ':SENSe:FUNCtion "FRES"')
        self._write_checked(
            instrument,
            ":SENSe:FRESistance:OCOMpensated ON",
            tolerate_error=True,
        )
        self._write_checked(
            instrument,
            ":SENSe:FRESistance:OCOM ON",
            tolerate_error=True,
        )
        self._write_checked(
            instrument,
            ":SENSe:FRESistance:AZERo ON",
            tolerate_error=True,
        )
        self._write_checked(
            instrument,
            ":SENSe:FRESistance:NPLC " + f"{self.nplc:.9g}",
        )
