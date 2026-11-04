"""Minimal DMM7510 4-wire resistance demo.

Example:
    python demo_keithley_dmm7510_4wire.py --resource USB0::0x05E6::0x7510::04542196::0::INSTR
"""

from __future__ import annotations

import argparse
from datetime import datetime
import math
import os
import sys
import time

from instrument_backend import KeithleyDmm7510Backend


DEFAULT_RESOURCE = "USB0::0x05E6::0x7510::04542196::0::INSTR"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read 4-wire resistance from a Keithley DMM7510."
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("KEITHLEY_DMM7510_RESOURCE", DEFAULT_RESOURCE),
        help="DMM7510 VISA resource string.",
    )
    parser.add_argument(
        "--visa-library",
        default=os.environ.get("KEITHLEY_VISA_LIBRARY"),
        help="Optional VISA library DLL path.",
    )
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--nplc", type=float, default=1.0)
    parser.add_argument("--timeout-s", type=float, default=10.0)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.samples <= 0:
        raise SystemExit("--samples must be positive")
    if not math.isfinite(args.interval) or args.interval < 0:
        raise SystemExit("--interval must be a finite non-negative value")

    backend = KeithleyDmm7510Backend(
        resource=args.resource,
        visa_library=args.visa_library,
        nplc=args.nplc,
        timeout_s=args.timeout_s,
    )
    try:
        for index in range(1, args.samples + 1):
            resistance = backend.read_resistance_ohm()
            print(
                f"{index:03d}, "
                f"{datetime.now().isoformat(timespec='seconds')}, "
                f"{resistance:.10g}, ohm"
            )
            if index != args.samples and args.interval:
                time.sleep(args.interval)
        print(f"IDN      : {backend.idn}")
        print(f"Resource : {backend.selected_resource}")
        print("Mode     : 4-wire resistance, NPLC=" + f"{args.nplc:.9g}")
    finally:
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
