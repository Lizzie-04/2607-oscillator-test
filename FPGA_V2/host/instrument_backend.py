"""Replaceable professional frequency-instrument interface."""

from __future__ import annotations

from typing import Protocol


class FrequencyInstrumentBackend(Protocol):
    def read_frequency_mhz(self) -> float:
        """Return the latest measured frequency in MHz."""


class UnavailableFrequencyInstrument:
    """Safe placeholder until the real instrument model is selected."""

    def read_frequency_mhz(self) -> float:
        raise RuntimeError("专业频率测试仪器尚未配置")

