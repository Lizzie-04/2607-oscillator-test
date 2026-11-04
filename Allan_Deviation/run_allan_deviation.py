"""Acquire frequency data from an auto-discovered Keysight/Agilent 53220A.

The script keeps all instrument-specific communication in the host layer.  It
uses VISA resource enumeration plus ``*IDN?`` matching, requests either the
minimum or an explicitly selected frequency gate time, performs one contiguous
acquisition, asks the counter for its built-in Allan deviation, and writes raw
and processed data separately.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Iterable


INVALID_READING_THRESHOLD_HZ = 9.0e37
DEFAULT_DURATION_S = 600.0
DEFAULT_CHANNEL = 1


def bootstrap_keysight_path() -> None:
    if os.name != "nt":
        return
    candidates = [
        r"C:\Program Files\Keysight\IO Libraries Suite\bin",
        r"C:\Program Files\Keysight\IO Libraries Suite",
        r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin",
        r"C:\Program Files\IVI Foundation\VISA\Win64\Bin",
        r"C:\Program Files (x86)\Keysight\IO Libraries Suite\bin",
        r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\ktvisa",
        r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\Bin",
    ]
    existing = [path for path in candidates if Path(path).exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(
            existing + [os.environ.get("PATH", "")]
        )


bootstrap_keysight_path()

try:
    import pyvisa
    from pyvisa import errors as visa_errors
except ImportError as exc:  # pragma: no cover - depends on lab workstation
    raise SystemExit("PyVISA is required to communicate with the 53220A") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument(
        "--sample-count",
        type=int,
        default=None,
        help=(
            "Optional explicit reading count. This is useful because the "
            "minimum gate can be shorter than the counter's reading cadence."
        ),
    )
    parser.add_argument(
        "--gate-time-s",
        type=float,
        default=None,
        help=(
            "Optional frequency gate time in seconds. The default is the "
            "instrument minimum. A longer gate can make a long contiguous "
            "run fit inside the instrument reading memory."
        ),
    )
    parser.add_argument(
        "--csv-pair-only",
        action="store_true",
        help=(
            "Write only a raw-frequency CSV and a one-value instrument "
            "ADEV CSV. No JSON or multi-tau CSV is published."
        ),
    )
    parser.add_argument(
        "--measurement-mode",
        choices=("open_loop", "close_loop"),
        default="open_loop",
        help="Label used in the two CSV filenames.",
    )
    parser.add_argument("--channel", type=int, default=DEFAULT_CHANNEL)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    parser.add_argument(
        "--visa-library", default=os.environ.get("KEYSIGHT_VISA_LIBRARY")
    )
    parser.add_argument(
        "--resource", default=os.environ.get("KEYSIGHT_53220A_RESOURCE")
    )
    return parser.parse_args(argv)


def query_error(instrument) -> str:
    try:
        return instrument.query(":SYSTem:ERRor?").strip()
    except Exception:
        return ""


def no_error(text: str) -> bool:
    normalized = text.strip().lower()
    return (
        not normalized
        or normalized.startswith("+0")
        or normalized.startswith("0")
        or "no error" in normalized
    )


def write_checked(instrument, command: str, *, tolerate_error: bool = False) -> bool:
    instrument.write(command)
    error = query_error(instrument)
    if no_error(error):
        return True
    if tolerate_error:
        return False
    raise RuntimeError(f"Command {command!r} failed: {error}")


def open_resource_manager(visa_library: str | None):
    if visa_library:
        return pyvisa.ResourceManager(visa_library)
    try:
        return pyvisa.ResourceManager()
    except visa_errors.VisaIOError:
        fallback = Path(
            r"C:\Program Files\IVI Foundation\VISA\Win64\ktvisa\ktbin\visa32.dll"
        )
        if os.name == "nt" and fallback.exists():
            return pyvisa.ResourceManager(str(fallback))
        raise


def idn_for(resource_manager, resource: str) -> str:
    instrument = resource_manager.open_resource(resource)
    try:
        instrument.timeout = 5000
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        return instrument.query("*IDN?").strip()
    finally:
        instrument.close()


def find_53220a(resource_manager, explicit_resource: str | None) -> tuple[str, str]:
    resources = (
        (explicit_resource,)
        if explicit_resource
        else tuple(resource_manager.list_resources())
    )
    print(f"VISA resources found: {len(resources)}", flush=True)
    failures: list[str] = []
    for resource in resources:
        try:
            idn = idn_for(resource_manager, resource)
        except Exception as exc:
            failures.append(f"{resource}: {exc}")
            continue
        model = idn.split(",")[1].strip() if "," in idn else idn
        print(f"  candidate model: {model}", flush=True)
        if "53220A" in idn.upper():
            return resource, idn
    detail = "; ".join(failures)
    raise RuntimeError(f"No 53220A found by *IDN? enumeration. {detail}")


def redacted_identity(idn: str) -> dict[str, str]:
    fields = [part.strip() for part in idn.split(",")]
    return {
        "manufacturer": fields[0] if fields else "",
        "model": fields[1] if len(fields) > 1 else "",
        "serial_number": "redacted",
        "firmware": fields[3] if len(fields) > 3 else "",
    }


def configure(
    instrument, channel: int, requested_gate_s: float | None
) -> tuple[float, float, int, str]:
    instrument.write("ABORt")
    instrument.write("*CLS")
    configure_commands = [
        f":CONFigure:FREQuency (@{channel})",
        f":CONFigure:SCALar:VOLTage:FREQuency (@{channel})",
    ]
    configured_with = ""
    for command in configure_commands:
        instrument.write("*CLS")
        instrument.write(command)
        if no_error(query_error(instrument)):
            configured_with = command
            break
    if not configured_with:
        raise RuntimeError("Unable to configure a frequency measurement")

    write_checked(instrument, ":FORMat:DATA ASCii,15", tolerate_error=True)
    write_checked(instrument, ":TRIGger:SOURce IMMediate")
    write_checked(instrument, ":TRIGger:SEQuence:COUNt 1")
    write_checked(
        instrument, ":SENSe:FREQuency:GATE:SOURce TIME", tolerate_error=True
    )
    minimum_gate_s = float(instrument.query(":SENSe:FREQuency:GATE:TIME? MINimum"))
    if requested_gate_s is None:
        write_checked(instrument, ":SENSe:FREQuency:GATE:TIME MINimum")
        expected_gate_s = minimum_gate_s
    else:
        if requested_gate_s < minimum_gate_s:
            raise ValueError(
                f"gate-time-s={requested_gate_s} is below the instrument "
                f"minimum of {minimum_gate_s}"
            )
        write_checked(
            instrument, f":SENSe:FREQuency:GATE:TIME {requested_gate_s:.12g}"
        )
        expected_gate_s = requested_gate_s
    active_gate_s = float(instrument.query(":SENSe:FREQuency:GATE:TIME?"))
    if not math.isclose(
        expected_gate_s, active_gate_s, rel_tol=1e-6, abs_tol=1e-12
    ):
        raise RuntimeError(
            f"Gate request did not take effect: requested={expected_gate_s}, "
            f"active={active_gate_s}, minimum={minimum_gate_s}"
        )

    try:
        maximum_samples = int(float(instrument.query(":SAMPle:COUNt? MAXimum")))
    except Exception:
        maximum_samples = 1_000_000
        instrument.write("*CLS")

    write_checked(instrument, ":CALCulate:AVERage:STATe ON")
    write_checked(instrument, ":CALCulate:STATe ON")
    return active_gate_s, minimum_gate_s, maximum_samples, configured_with


def acquire(
    instrument,
    duration_s: float,
    gate_s: float,
    maximum_samples: int,
    sample_count_override: int | None,
) -> tuple[list[float], dict[str, object]]:
    if sample_count_override is not None:
        if sample_count_override < 2:
            raise ValueError("sample-count must be at least 2")
        requested_samples = sample_count_override
        sample_count_strategy = "explicit"
    else:
        samples_from_gate = max(2, math.ceil(duration_s / gate_s))
        if samples_from_gate <= maximum_samples:
            requested_samples = samples_from_gate
            sample_count_strategy = "duration_divided_by_gate"
        else:
            # 53220A reading throughput is slower than its 100 us minimum
            # frequency gate.  A 1 ms cadence estimate keeps typical timed runs
            # inside the one-million-reading memory; actual elapsed time is
            # measured and retained in metadata.
            requested_samples = max(2, math.ceil(duration_s / 0.001))
            sample_count_strategy = "duration_divided_by_1ms_cadence_estimate"
    if requested_samples > maximum_samples:
        raise RuntimeError(
            f"Timed request requires {requested_samples} samples, above "
            f"the instrument maximum of {maximum_samples} at gate={gate_s}s"
        )
    write_checked(instrument, f":SAMPle:COUNt {requested_samples}")
    actual_samples = int(float(instrument.query(":SAMPle:COUNt?")))
    if actual_samples != requested_samples:
        raise RuntimeError(
            f"Sample count mismatch: requested {requested_samples}, got {actual_samples}"
        )

    start = datetime.now().astimezone()
    start_perf = time.perf_counter()
    print(
        f"Acquisition started: {actual_samples} readings, gate={gate_s:.12g} s, "
        f"accumulated gate time={actual_samples * gate_s:.3f} s",
        flush=True,
    )
    instrument.write("INITiate:IMMediate")
    instrument.query("*OPC?")
    end_perf = time.perf_counter()
    end = datetime.now().astimezone()
    acquisition_elapsed_s = end_perf - start_perf
    print(
        f"Acquisition completed in {acquisition_elapsed_s:.3f} s; fetching data",
        flush=True,
    )

    direct: dict[str, object] = {
        "command": ":CALCulate:AVERage:ADEViation?",
        "supported": False,
        "value_hz": None,
        "error": None,
    }
    try:
        direct_value = float(
            instrument.query(":CALCulate:AVERage:ADEViation?").strip()
        )
        direct.update(supported=True, value_hz=direct_value)
    except Exception as exc:
        direct["error"] = str(exc)
        instrument.write("*CLS")

    instrument.chunk_size = 1024 * 1024
    values = instrument.query_ascii_values(
        "FETCh?", converter="f", separator=",", container=list
    )
    print(f"Fetched {len(values)} readings", flush=True)
    if len(values) != actual_samples:
        raise RuntimeError(
            f"FETCh? returned {len(values)} readings; expected {actual_samples}"
        )
    invalid = [
        index
        for index, value in enumerate(values, start=1)
        if not math.isfinite(value)
        or value <= 0
        or abs(value) >= INVALID_READING_THRESHOLD_HZ
    ]
    if invalid:
        raise RuntimeError(f"Invalid frequency readings at indices {invalid[:10]}")

    metadata = {
        "requested_duration_s": duration_s,
        "sample_count": actual_samples,
        "sample_count_strategy": sample_count_strategy,
        "gate_time_s": gate_s,
        "accumulated_gate_time_s": actual_samples * gate_s,
        "host_acquisition_elapsed_s": acquisition_elapsed_s,
        "effective_sample_interval_s": acquisition_elapsed_s / actual_samples,
        "estimated_dead_time_per_reading_s": max(
            0.0, acquisition_elapsed_s / actual_samples - gate_s
        ),
        "host_start_time": start.isoformat(timespec="milliseconds"),
        "host_end_time": end.isoformat(timespec="milliseconds"),
        "instrument_builtin_allan": direct,
    }
    return values, metadata


def averaging_factors(sample_count: int) -> list[int]:
    maximum = max(1, sample_count // 3)
    factors = {1}
    decade = 1
    while decade <= maximum:
        for multiplier in (1, 2, 5):
            value = multiplier * decade
            if value <= maximum:
                factors.add(value)
        decade *= 10
    power = 1
    while power <= maximum:
        factors.add(power)
        power *= 2
    return sorted(factors)


def overlapping_allan(
    frequencies_hz: list[float], gate_s: float, effective_interval_s: float
) -> list[dict[str, float | int]]:
    count = len(frequencies_hz)
    mean_hz = statistics.fmean(frequencies_hz)
    prefix = [0.0]
    running = 0.0
    for value in frequencies_hz:
        running += value
        prefix.append(running)

    rows: list[dict[str, float | int]] = []
    for factor in averaging_factors(count):
        terms = count - 2 * factor + 1
        if terms <= 0:
            continue
        square_sum = 0.0
        for start in range(terms):
            first = (prefix[start + factor] - prefix[start]) / factor
            second = (
                prefix[start + 2 * factor] - prefix[start + factor]
            ) / factor
            difference = second - first
            square_sum += difference * difference
        adev_hz = math.sqrt(square_sum / (2.0 * terms))
        rows.append(
            {
                "averaging_factor_m": factor,
                "tau_effective_s": factor * effective_interval_s,
                "measurement_gate_product_s": factor * gate_s,
                "difference_terms": terms,
                "adev_hz": adev_hz,
                "adev_fractional": adev_hz / mean_hz,
            }
        )
    return rows


def write_raw_csv(
    path: Path, frequencies_hz: Iterable[float], effective_interval_s: float
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["sample_index", "estimated_elapsed_s", "frequency_hz"]
        )
        for index, value in enumerate(frequencies_hz, start=1):
            writer.writerow(
                [
                    index,
                    f"{index * effective_interval_s:.12g}",
                    f"{value:.15g}",
                ]
            )
    temporary.replace(path)


def write_adev_csv(path: Path, rows: list[dict[str, float | int]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    fieldnames = [
        "averaging_factor_m",
        "tau_effective_s",
        "measurement_gate_product_s",
        "difference_terms",
        "adev_hz",
        "adev_fractional",
    ]
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def write_direct_result_csv(path: Path, summary: dict[str, object]) -> None:
    direct = summary["instrument_builtin_allan"]
    if not isinstance(direct, dict):
        raise TypeError("summary instrument ADEV metadata is malformed")
    row = {"instrument_adev_hz": float(direct["value_hz"])}
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["instrument_adev_hz"])
        writer.writeheader()
        writer.writerow(row)
    temporary.replace(path)


def write_json(path: Path, data: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not math.isfinite(args.duration_s) or args.duration_s <= 0:
        raise ValueError("duration-s must be positive and finite")
    if args.gate_time_s is not None and (
        not math.isfinite(args.gate_time_s) or args.gate_time_s <= 0
    ):
        raise ValueError("gate-time-s must be positive and finite")
    if args.channel <= 0:
        raise ValueError("channel must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    resource_manager = open_resource_manager(args.visa_library)
    instrument = None
    try:
        resource, idn = find_53220a(resource_manager, args.resource)
        print(f"Selected instrument: {redacted_identity(idn)}", flush=True)
        instrument = resource_manager.open_resource(resource)
        instrument.timeout = max(30_000, int((args.duration_s + 90.0) * 1000))
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        gate_s, minimum_gate_s, maximum_samples, configured_with = configure(
            instrument, args.channel, args.gate_time_s
        )
        print(
            f"Active gate={gate_s:.12g} s; minimum gate={minimum_gate_s:.12g} "
            f"s; maximum samples={maximum_samples}",
            flush=True,
        )
        frequencies, metadata = acquire(
            instrument,
            args.duration_s,
            gate_s,
            maximum_samples,
            args.sample_count,
        )
    finally:
        if instrument is not None:
            instrument.close()
        resource_manager.close()

    mean_hz = statistics.fmean(frequencies)
    effective_interval_s = float(metadata["effective_sample_interval_s"])
    rows = overlapping_allan(frequencies, gate_s, effective_interval_s)
    direct = metadata["instrument_builtin_allan"]
    if isinstance(direct, dict) and direct.get("value_hz") is not None:
        direct["value_fractional"] = float(direct["value_hz"]) / mean_hz

    summary = {
        "schema_version": 1,
        "method": "53220A contiguous frequency acquisition",
        "instrument": redacted_identity(idn),
        "channel": args.channel,
        "frequency_configuration_command": configured_with,
        **metadata,
        "mean_frequency_hz": mean_hz,
        "minimum_frequency_hz": min(frequencies),
        "maximum_frequency_hz": max(frequencies),
        "sample_standard_deviation_hz": (
            statistics.stdev(frequencies) if len(frequencies) > 1 else 0.0
        ),
        "offline_overlapping_adev_first_tau_hz": rows[0]["adev_hz"],
        "offline_overlapping_adev_first_tau_fractional": rows[0][
            "adev_fractional"
        ],
        "offline_formula": (
            "sqrt(sum((mean(y[i+m:i+2m])-mean(y[i:i+m]))^2) / "
            "(2*(N-2m+1)))"
        ),
    }

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    if args.csv_pair_only:
        prefix = f"{args.measurement_mode}_53220a_{stamp}"
        raw_path = args.output_dir / (
            f"{prefix}_frequency_{len(frequencies)}points.csv"
        )
        direct_path = args.output_dir / f"{prefix}_allan_results.csv"
        write_raw_csv(raw_path, frequencies, effective_interval_s)
        write_direct_result_csv(direct_path, summary)
        print(f"RAW_CSV={raw_path.resolve()}", flush=True)
        print(f"DIRECT_ADEV_CSV={direct_path.resolve()}", flush=True)
        print(
            "Instrument ADEV (Hz): "
            f"{summary['instrument_builtin_allan'].get('value_hz')}",
            flush=True,
        )
        print(
            f"Offline ADEV at effective tau={effective_interval_s:.12g}s: "
            f"{rows[0]['adev_hz']:.12g} Hz "
            f"({rows[0]['adev_fractional']:.12g} fractional)",
            flush=True,
        )
        return 0

    prefix = f"allan_53220a_{stamp}"
    raw_path = args.output_dir / f"{prefix}_raw_frequency.csv"
    adev_path = args.output_dir / f"{prefix}_offline_adev.csv"
    summary_path = args.output_dir / f"{prefix}_summary.json"
    write_raw_csv(raw_path, frequencies, effective_interval_s)
    write_adev_csv(adev_path, rows)
    summary["files"] = {
        "raw_frequency_csv": raw_path.name,
        "offline_adev_csv": adev_path.name,
        "summary_json": summary_path.name,
    }
    write_json(summary_path, summary)
    print(f"RAW_CSV={raw_path.resolve()}", flush=True)
    print(f"ADEV_CSV={adev_path.resolve()}", flush=True)
    print(f"SUMMARY_JSON={summary_path.resolve()}", flush=True)
    print(
        "Instrument ADEV (Hz): "
        f"{summary['instrument_builtin_allan'].get('value_hz')}",
        flush=True,
    )
    print(
        f"Offline ADEV at effective tau={effective_interval_s:.12g}s: "
        f"{rows[0]['adev_hz']:.12g} Hz "
        f"({rows[0]['adev_fractional']:.12g} fractional)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
