"""Read-only smoke test for the FPGA V12 System Console transport."""

from __future__ import annotations

import argparse
import time

from system_console_transport import SystemConsoleAvalonTransport


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify FPGA V12 MAGIC/ABI and repeated Avalon reads."
    )
    parser.add_argument(
        "--reads",
        type=int,
        default=1000,
        help="number of read-only health checks (default: 1000)",
    )
    parser.add_argument(
        "--statistics",
        action="store_true",
        help="also capture and download one frozen 100-pair mu batch",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="also latch and print one live/calibration monitor snapshot",
    )
    args = parser.parse_args()
    if args.reads < 1:
        parser.error("--reads must be at least 1")

    transport = SystemConsoleAvalonTransport()

    def report(level: str, category: str, message: str) -> None:
        if level != "DEBUG":
            print(f"[{level}] [{category}] {message}", flush=True)

    transport.set_log_callback(report)
    started = time.monotonic()
    try:
        diagnostic = transport.health_check()
        print(
            "Connected: "
            f"magic=0x{diagnostic.magic:08X}, "
            f"abi={diagnostic.avalon_abi_version}, "
            f"protocol=0x{diagnostic.protocol_version:04X}, "
            f"revision=0x{diagnostic.implementation_revision:04X}"
        )
        for index in range(args.reads):
            transport.health_check()
            if (index + 1) % 100 == 0:
                print(f"Read checks: {index + 1}/{args.reads}", flush=True)
        if args.snapshot:
            snapshot = transport.read_monitor_snapshot()
            live = snapshot.live
            calibration = snapshot.calibration
            print(
                "Live snapshot: "
                f"sequence={live.sequence}, filter_samples={live.total_filter_samples}, "
                f"mu1={live.sample.mu1:.6f}, mu2={live.sample.mu2:.6f}, "
                f"onchip_sequence={live.onchip_sequence}, "
                f"onchip_mu1={live.onchip_sample.mu1:.6f}, "
                f"onchip_mu2={live.onchip_sample.mu2:.6f}"
            )
            print(
                "Calibration snapshot: "
                f"sequence={calibration.sequence}, valid={int(calibration.valid)}, "
                f"poly_freq={calibration.poly_freq:.6f}, "
                f"poly_system={calibration.poly_system:.6f}, "
                f"sar={calibration.sar_correction:.6f}, "
                f"dsm={calibration.dsm_input:.6f}, "
                f"override={int(calibration.override_enabled)}, "
                f"sar_step={calibration.sar_status.step}, "
                f"sar_busy={int(calibration.sar_status.busy)}, "
                f"sar_done={int(calibration.sar_status.done)}"
            )
        if args.statistics:
            samples = transport.capture_statistics_batch()
            print(
                "Statistics: "
                f"count={len(samples)}, first=({samples[0].mu1:.6f}, "
                f"{samples[0].mu2:.6f}), last=({samples[-1].mu1:.6f}, "
                f"{samples[-1].mu2:.6f})"
            )
        print(f"PASS in {time.monotonic() - started:.3f}s")
        return 0
    finally:
        transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
