"""Verify Keithley DMM7510 thermistor resistance readout through PyVISA."""

from __future__ import annotations

import argparse
from datetime import datetime
import math
import os
from pathlib import Path
import statistics
import sys
import time

from instrument_backend import (
    KeithleyDmm7510Backend,
    bootstrap_keysight_visa_path,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Python/PyVISA resistance sampling from a DMM7510."
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("KEITHLEY_DMM7510_RESOURCE"),
        help=(
            "VISA resource string. If omitted, scan resources and use the "
            "first instrument whose *IDN? contains DMM7510."
        ),
    )
    parser.add_argument(
        "--visa-library",
        default=os.environ.get("KEITHLEY_VISA_LIBRARY"),
        help="Optional VISA DLL path.",
    )
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--nplc", type=float, default=1.0)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only list VISA resources and query IDN candidates; do not sample.",
    )
    return parser.parse_args(argv)


def open_resource_manager(visa_library: str | None):
    bootstrap_keysight_visa_path()
    try:
        import pyvisa
    except ImportError as exc:
        raise RuntimeError(
            "PyVISA is not installed. Run: python -m pip install pyvisa"
        ) from exc
    if visa_library:
        return pyvisa.ResourceManager(visa_library)
    return pyvisa.ResourceManager()


def query_idn(resource_manager, resource: str, timeout_ms: int) -> str:
    instrument = resource_manager.open_resource(resource)
    try:
        instrument.timeout = timeout_ms
        instrument.write_termination = "\n"
        instrument.read_termination = "\n"
        return instrument.query("*IDN?").strip()
    finally:
        instrument.close()


def list_resources(
    *,
    explicit_resource: str | None,
    visa_library: str | None,
    timeout_ms: int,
) -> None:
    resource_manager = open_resource_manager(visa_library)
    try:
        resources = (
            (explicit_resource,)
            if explicit_resource
            else tuple(resource_manager.list_resources())
        )
        print(f"VISA resources: {resources}")
        for resource in resources:
            try:
                idn = query_idn(resource_manager, resource, timeout_ms)
            except Exception as exc:
                print(f"  {resource}: IDN query failed: {exc}")
                continue
            marker = "DMM7510" if "DMM7510" in idn.upper() else "other"
            print(f"  {resource}: [{marker}] {idn}")
    finally:
        close = getattr(resource_manager, "close", None)
        if close is not None:
            close()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.samples <= 0:
        raise SystemExit("--samples must be positive")
    if not math.isfinite(args.interval) or args.interval < 0:
        raise SystemExit("--interval must be a finite non-negative value")

    timeout_ms = max(1000, int(args.timeout_s * 1000))
    list_resources(
        explicit_resource=args.resource,
        visa_library=args.visa_library,
        timeout_ms=timeout_ms,
    )
    if args.list_only:
        return 0

    backend = KeithleyDmm7510Backend(
        resource=args.resource,
        visa_library=args.visa_library,
        nplc=args.nplc,
        timeout_s=args.timeout_s,
    )
    samples: list[float] = []
    try:
        for index in range(1, args.samples + 1):
            value = backend.read_resistance_ohm()
            samples.append(value)
            print(
                f"{index:03d}: {value:.10g} ohm @ "
                f"{datetime.now().isoformat(timespec='seconds')}"
            )
            if index != args.samples and args.interval:
                time.sleep(args.interval)
    finally:
        backend.close()

    mean = statistics.fmean(samples)
    std = statistics.stdev(samples) if len(samples) > 1 else 0.0
    print(f"Mean ohm   : {mean:.10g}")
    print(f"Stddev ohm : {std:.10g}")
    print("PASS       : DMM7510 VISA readout completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
