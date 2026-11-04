"""Verify Keysight/Agilent 53220A frequency readout through PyVISA.

This is a standalone smoke-test script for the SAR calibration instrument
path.  It does not talk to the FPGA; it only verifies that Python can open the
53220A, configure frequency measurement, read repeated samples, and summarize
their stability.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import statistics
import sys
import time


DEFAULT_GATE_TIME_S = 5.0
INVALID_READING_THRESHOLD_HZ = 9.0e37


def _prepend_existing_paths(paths: list[str]) -> None:
    existing = [path for path in paths if Path(path).exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])


def bootstrap_keysight_path() -> None:
    """Make Keysight VISA dependencies visible to a fresh Python process."""
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


bootstrap_keysight_path()

try:
    import pyvisa
    from pyvisa import errors as visa_errors
except ImportError as exc:
    raise SystemExit(
        "PyVISA is not installed. Run: python -m pip install pyvisa"
    ) from exc


@dataclass(frozen=True)
class FrequencySample:
    index: int
    timestamp: datetime
    frequency_hz: float

    @property
    def frequency_mhz(self) -> float:
        return self.frequency_hz / 1_000_000.0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Python/PyVISA frequency sampling from a Keysight 53220A."
        )
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("KEYSIGHT_53220A_RESOURCE"),
        help=(
            "VISA resource string. If omitted, the script scans resources and "
            "uses the first instrument whose *IDN? contains 53220A."
        ),
    )
    parser.add_argument(
        "--visa-library",
        default=os.environ.get("KEYSIGHT_VISA_LIBRARY"),
        help=(
            "Optional VISA DLL path, for example "
            r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin\visa32.dll"
        ),
    )
    parser.add_argument("--channel", type=int, default=1)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--gate-time", type=float, default=DEFAULT_GATE_TIME_S)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument(
        "--expected-mhz",
        type=float,
        default=None,
        help="Optional expected frequency for ppm accuracy check.",
    )
    parser.add_argument(
        "--tolerance-ppm",
        type=float,
        default=100.0,
        help="Allowed mean error when --expected-mhz is provided.",
    )
    parser.add_argument(
        "--max-std-ppm",
        type=float,
        default=None,
        help="Optional stability limit for sample standard deviation.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional output CSV path for raw samples.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only list resources and query IDN candidates; do not sample.",
    )
    return parser.parse_args(argv)


def ensure_positive_finite(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite value")
    return value


def ensure_valid_frequency(value: float, raw_text: str) -> float:
    ensure_positive_finite(value, "frequency")
    if abs(value) >= INVALID_READING_THRESHOLD_HZ:
        raise ValueError(
            "frequency is the 53220A invalid-reading sentinel "
            f"({raw_text!r}); check input signal, trigger level, and gate time"
        )
    return value


def query_error(instrument) -> str:
    try:
        return instrument.query(":SYSTem:ERRor?").strip()
    except Exception:
        return ""


def is_no_error(error_text: str) -> bool:
    text = error_text.strip().lower()
    return (
        not text
        or text.startswith("+0")
        or text.startswith("0")
        or "no error" in text
    )


def checked_write(instrument, command: str, *, tolerate_error: bool = False) -> bool:
    instrument.write(command)
    error_text = query_error(instrument)
    if is_no_error(error_text):
        return True
    if tolerate_error:
        print(f"WARNING: {command!r} returned instrument error: {error_text}")
        return False
    raise RuntimeError(f"{command!r} returned instrument error: {error_text}")


def open_resource_manager(visa_library: str | None):
    if visa_library:
        return pyvisa.ResourceManager(visa_library)
    try:
        return pyvisa.ResourceManager()
    except visa_errors.VisaIOError:
        default_library = (
            r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin\visa32.dll"
        )
        if os.name == "nt" and Path(default_library).exists():
            return pyvisa.ResourceManager(default_library)
        raise


def query_idn(resource_manager, resource: str, timeout_ms: int) -> str:
    instrument = resource_manager.open_resource(resource)
    try:
        instrument.timeout = timeout_ms
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        return instrument.query("*IDN?").strip()
    finally:
        instrument.close()


def find_53220a_resource(
    resource_manager,
    explicit_resource: str | None,
    timeout_ms: int,
) -> tuple[str, str]:
    if explicit_resource:
        idn = query_idn(resource_manager, explicit_resource, timeout_ms)
        return explicit_resource, idn

    resources = tuple(resource_manager.list_resources())
    print(f"VISA resources: {resources}")
    for resource in resources:
        try:
            idn = query_idn(resource_manager, resource, timeout_ms)
        except Exception as exc:
            print(f"  {resource}: IDN query failed: {exc}")
            continue
        print(f"  {resource}: {idn}")
        if "53220A" in idn.upper():
            return resource, idn

    raise RuntimeError(
        "No 53220A resource found. Open Keysight Connection Expert and verify "
        "that the instrument has a VISA address."
    )


def configure_frequency_measurement(
    instrument,
    *,
    channel: int,
    expected_mhz: float | None,
    gate_time_s: float,
) -> str:
    ensure_positive_finite(gate_time_s, "gate time")
    if expected_mhz is not None:
        ensure_positive_finite(expected_mhz, "expected frequency")
        frequency_argument = f"{expected_mhz * 1_000_000.0:.12g}"
        configure_attempts = [
            (
                ":CONFigure:SCALar:VOLTage:FREQuency "
                f"{frequency_argument},MAXimum,(@{channel})"
            ),
            f":CONFigure:FREQuency {frequency_argument},MAXimum,(@{channel})",
            f":CONFigure:FREQuency (@{channel})",
        ]
    else:
        configure_attempts = [
            f":CONFigure:FREQuency (@{channel})",
            f":CONFigure:SCALar:VOLTage:FREQuency (@{channel})",
        ]

    instrument.write("*CLS")
    checked_write(instrument, ":FORMat:DATA ASCii,15", tolerate_error=True)

    last_error = ""
    for command in configure_attempts:
        instrument.write("*CLS")
        instrument.write(command)
        last_error = query_error(instrument)
        if is_no_error(last_error):
            break
    else:
        raise RuntimeError(
            "Unable to configure frequency measurement; last error: "
            f"{last_error}"
        )

    checked_write(instrument, ":SENSe:FREQuency:GATE:SOURce TIME", tolerate_error=True)
    checked_write(
        instrument,
        f":SENSe:FREQuency:GATE:TIME {gate_time_s:.9g}",
        tolerate_error=True,
    )
    return command


def read_samples(
    instrument,
    *,
    sample_count: int,
    interval_s: float,
) -> list[FrequencySample]:
    if sample_count <= 0:
        raise ValueError("sample count must be positive")
    if interval_s < 0:
        raise ValueError("interval must not be negative")

    samples: list[FrequencySample] = []
    for index in range(1, sample_count + 1):
        raw = instrument.query("READ?").strip()
        try:
            frequency_hz = float(raw)
        except ValueError as exc:
            raise RuntimeError(f"READ? returned a non-numeric value: {raw!r}") from exc
        ensure_valid_frequency(frequency_hz, raw)
        sample = FrequencySample(index, datetime.now(), frequency_hz)
        samples.append(sample)
        print(
            f"{index:03d}: {sample.frequency_hz:.6f} Hz "
            f"({sample.frequency_mhz:.9f} MHz)"
        )
        if index != sample_count and interval_s:
            time.sleep(interval_s)
    return samples


def summarize(
    samples: list[FrequencySample],
    *,
    expected_mhz: float | None,
    tolerance_ppm: float,
    max_std_ppm: float | None,
) -> bool:
    values_hz = [sample.frequency_hz for sample in samples]
    mean_hz = statistics.fmean(values_hz)
    minimum_hz = min(values_hz)
    maximum_hz = max(values_hz)
    span_hz = maximum_hz - minimum_hz
    std_hz = statistics.stdev(values_hz) if len(values_hz) > 1 else 0.0
    mean_mhz = mean_hz / 1_000_000.0
    std_ppm = 0.0 if mean_hz == 0 else std_hz / mean_hz * 1_000_000.0
    span_ppm = 0.0 if mean_hz == 0 else span_hz / mean_hz * 1_000_000.0

    print("\nSummary")
    print(f"  samples    : {len(samples)}")
    print(f"  mean       : {mean_hz:.6f} Hz ({mean_mhz:.9f} MHz)")
    print(f"  min/max    : {minimum_hz:.6f} Hz / {maximum_hz:.6f} Hz")
    print(f"  span       : {span_hz:.6f} Hz ({span_ppm:.3f} ppm)")
    print(f"  std dev    : {std_hz:.6f} Hz ({std_ppm:.3f} ppm)")

    passed = True
    if expected_mhz is not None:
        expected_hz = expected_mhz * 1_000_000.0
        error_ppm = (mean_hz - expected_hz) / expected_hz * 1_000_000.0
        print(f"  expected   : {expected_hz:.6f} Hz")
        print(f"  mean error : {error_ppm:+.3f} ppm")
        if abs(error_ppm) > tolerance_ppm:
            print(
                f"  FAIL       : mean error exceeds {tolerance_ppm:.3f} ppm"
            )
            passed = False

    if max_std_ppm is not None and std_ppm > max_std_ppm:
        print(f"  FAIL       : std dev exceeds {max_std_ppm:.3f} ppm")
        passed = False

    if passed:
        print("  PASS       : VISA readout and sample checks completed")
    return passed


def write_csv(path: Path, samples: list[FrequencySample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["index", "timestamp", "frequency_hz", "frequency_mhz"])
        for sample in samples:
            writer.writerow(
                [
                    sample.index,
                    sample.timestamp.isoformat(timespec="milliseconds"),
                    f"{sample.frequency_hz:.9f}",
                    f"{sample.frequency_mhz:.12f}",
                ]
            )
    print(f"CSV written: {path}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    timeout_ms = max(1000, int(args.timeout_s * 1000))

    resource_manager = open_resource_manager(args.visa_library)
    print(f"PyVISA      : {pyvisa.__version__}")
    print(f"VISA library: {resource_manager.visalib}")

    resource, idn = find_53220a_resource(
        resource_manager,
        args.resource,
        timeout_ms,
    )
    print(f"Selected    : {resource}")
    print(f"*IDN?       : {idn}")
    if args.list_only:
        return 0
    if "53220A" not in idn.upper():
        raise RuntimeError(f"Selected instrument is not a 53220A: {idn}")

    instrument = resource_manager.open_resource(resource)
    try:
        instrument.timeout = max(timeout_ms, int((args.gate_time + 3.0) * 1000))
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        command = configure_frequency_measurement(
            instrument,
            channel=args.channel,
            expected_mhz=args.expected_mhz,
            gate_time_s=args.gate_time,
        )
        print(f"Configured  : {command}")
        print(
            f"Sampling    : samples={args.samples}, "
            f"gate={args.gate_time:g}s, interval={args.interval:g}s\n"
        )
        samples = read_samples(
            instrument,
            sample_count=args.samples,
            interval_s=args.interval,
        )
    finally:
        instrument.close()

    passed = summarize(
        samples,
        expected_mhz=args.expected_mhz,
        tolerance_ppm=args.tolerance_ppm,
        max_std_ppm=args.max_std_ppm,
    )
    if args.csv is not None:
        write_csv(args.csv, samples)
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
