"""Active end-to-end hardware verification for FPGA V10.

This script changes chip, DSM, and SAR state. It restores the original DSM
override and shuts down the selected chip before exiting, including on errors.
"""

from __future__ import annotations

import argparse
import math
import time

from control_word import ControlWord
from instrument_backend import KeithleyDmm7510Backend, Keysight53220ABackend
from system_console_transport import (
    DSM_OVERRIDE_SCALE,
    SAR_STEPS,
    SystemConsoleAvalonTransport,
    dsm_signed_to_twos,
)


def default_control_word(trim_c_coarse: int, *, en_xo: bool) -> ControlWord:
    return ControlWord(
        bypass=1,
        cs=0b10,
        en_bias=1,
        en_dllbuf=0,
        en_intbuf=0,
        en_sensor=1,
        en_xo=int(en_xo),
        en_xobufa=0,
        su=1,
        trim_c_coarse=trim_c_coarse,
        trim_c_fine=0b111,
        trim_r_xo=0b1000,
        alpha=0,
        batch=[0] * 7,
        polarity=0,
        poly=[0] * 8,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Actively verify FPGA V10 control, DSM, SAR, and instruments."
    )
    parser.add_argument("--chip", type=int, default=2)
    parser.add_argument("--trim", default="011")
    parser.add_argument("--target-mhz", type=float, default=2.0)
    parser.add_argument("--control-samples", type=int, default=20000)
    parser.add_argument("--gate-time", type=float, default=1.0)
    parser.add_argument("--settle", type=float, default=5.0)
    args = parser.parse_args()

    if not 0 <= args.chip <= 4:
        parser.error("--chip must be in the range 0..4")
    if len(args.trim) != 3 or set(args.trim) - {"0", "1"}:
        parser.error("--trim must be exactly three binary digits")
    if not math.isfinite(args.target_mhz) or args.target_mhz <= 0:
        parser.error("--target-mhz must be a positive finite value")
    if not math.isfinite(args.settle) or args.settle < 0:
        parser.error("--settle must be a non-negative finite value")

    trim = int(args.trim, 2)
    run_word = default_control_word(trim, en_xo=True)
    stop_word = default_control_word(trim, en_xo=False)
    transport = SystemConsoleAvalonTransport()
    counter = Keysight53220ABackend(gate_time_s=args.gate_time)
    dmm = KeithleyDmm7510Backend(nplc=1.0)
    original_dsm = None
    chip_started = False
    cleanup_errors: list[str] = []

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
            f"revision=0x{diagnostic.implementation_revision:04X}",
            flush=True,
        )
        original_dsm = transport.read_dsm_override_status()
        print(
            "Original DSM: "
            f"enabled={int(original_dsm.enabled)}, "
            f"raw=0x{original_dsm.raw_value:08X}, "
            f"q16={original_dsm.q16_value:.6f}",
            flush=True,
        )

        control_started = time.monotonic()
        transport.configure_and_start(
            run_word.words16(),
            args.chip,
            control_sample_count=args.control_samples,
        )
        chip_started = True
        print(
            f"Control start: chip={args.chip}, trim={args.trim}, "
            f"N={args.control_samples}, elapsed={time.monotonic() - control_started:.3f}s",
            flush=True,
        )
        time.sleep(args.settle)
        startup_frequency = counter.read_frequency_mhz()
        resistance = dmm.read_resistance_ohm()
        startup_snapshot = transport.read_monitor_snapshot()
        print(
            f"Startup readout: frequency={startup_frequency:.9f} MHz, "
            f"resistance={resistance:.6f} ohm, "
            f"mu1={startup_snapshot.live.sample.mu1:.6f}, "
            f"mu2={startup_snapshot.live.sample.mu2:.6f}",
            flush=True,
        )
        if not 1.9 <= startup_frequency <= 2.1:
            raise RuntimeError(
                f"startup frequency {startup_frequency:.9f} MHz is outside 1.9..2.1 MHz"
            )

        transport.set_dsm_override(dsm_signed_to_twos(0), enabled=True)
        time.sleep(args.settle)
        frequency_zero = counter.read_frequency_mhz()
        dsm_zero = transport.read_calibration_snapshot()
        print(
            f"DSM 0.000000: frequency={frequency_zero:.9f} MHz, "
            f"snapshot_dsm={dsm_zero.dsm_input:.6f}",
            flush=True,
        )

        dsm_positive_q16 = int(round(0.125 * DSM_OVERRIDE_SCALE))
        transport.set_dsm_override(
            dsm_signed_to_twos(dsm_positive_q16), enabled=True
        )
        time.sleep(args.settle)
        frequency_positive = counter.read_frequency_mhz()
        dsm_positive = transport.read_calibration_snapshot()
        print(
            f"DSM +0.125000: frequency={frequency_positive:.9f} MHz, "
            f"snapshot_dsm={dsm_positive.dsm_input:.6f}",
            flush=True,
        )
        if not frequency_positive < frequency_zero:
            raise RuntimeError(
                "DSM direction check failed: positive DSM did not lower frequency"
            )

        transport.set_dsm_override(0, enabled=False)
        sar_status = transport.start_sar()
        print(
            f"SAR start: step={sar_status.step}, code={sar_status.code:.6f}",
            flush=True,
        )
        sar_frequencies: list[float] = []
        for expected_step in range(1, SAR_STEPS + 1):
            time.sleep(args.settle)
            measured = counter.read_frequency_mhz()
            decision = 1 if measured < args.target_mhz else 0
            sar_status = transport.submit_sar_decision(decision)
            sar_frequencies.append(measured)
            print(
                f"SAR step {expected_step:02d}: frequency={measured:.9f} MHz, "
                f"decision={decision}, code={sar_status.code:.6f}, "
                f"busy={int(sar_status.busy)}, done={int(sar_status.done)}",
                flush=True,
            )
        if not sar_status.done or sar_status.busy or sar_status.step != SAR_STEPS:
            raise RuntimeError("SAR did not reach the expected completed state")

        final_snapshot = transport.read_monitor_snapshot()
        samples = transport.capture_statistics_batch()
        final_resistance = dmm.read_resistance_ohm()
        print(
            "Final snapshot: "
            f"sar={final_snapshot.calibration.sar_correction:.6f}, "
            f"dsm={final_snapshot.calibration.dsm_input:.6f}, "
            f"mu1={final_snapshot.live.sample.mu1:.6f}, "
            f"mu2={final_snapshot.live.sample.mu2:.6f}, "
            f"resistance={final_resistance:.6f} ohm",
            flush=True,
        )
        print(
            "Statistics: "
            f"count={len(samples)}, "
            f"first=({samples[0].mu1:.6f}, {samples[0].mu2:.6f}), "
            f"last=({samples[-1].mu1:.6f}, {samples[-1].mu2:.6f})",
            flush=True,
        )
    finally:
        if original_dsm is not None:
            try:
                transport.set_dsm_override(
                    original_dsm.raw_value, enabled=original_dsm.enabled
                )
                print(
                    "Cleanup: original DSM override restored "
                    f"(enabled={int(original_dsm.enabled)}, "
                    f"q16={original_dsm.q16_value:.6f})",
                    flush=True,
                )
            except Exception as exc:
                cleanup_errors.append(f"restore DSM failed: {exc}")
        if chip_started:
            try:
                stop_started = time.monotonic()
                transport.configure_and_start(
                    stop_word.words16(),
                    args.chip,
                    control_sample_count=args.control_samples,
                )
                print(
                    f"Cleanup: chip{args.chip} EN_XO=0 written, "
                    f"elapsed={time.monotonic() - stop_started:.3f}s",
                    flush=True,
                )
            except Exception as exc:
                cleanup_errors.append(f"chip shutdown failed: {exc}")
        dmm.close()
        counter.close()
        transport.close()
        for message in cleanup_errors:
            print(f"[ERROR] [CLEANUP] {message}", flush=True)
    if cleanup_errors:
        raise RuntimeError("; ".join(cleanup_errors))
    print(
        f"PASS: active hardware verification and cleanup completed in "
        f"{time.monotonic() - started:.3f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
