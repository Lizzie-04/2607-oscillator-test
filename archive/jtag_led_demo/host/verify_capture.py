from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load_samples(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    header_index = next(
        (index for index, row in enumerate(rows) if row and row[0].strip() == "time unit: ns"),
        None,
    )
    if header_index is None:
        raise ValueError("CSV does not contain the SignalTap data header")

    header = [cell.strip() for cell in rows[header_index]]
    samples: list[dict[str, str]] = []
    for row in rows[header_index + 1 :]:
        cells = [cell.strip() for cell in row]
        if not cells or not cells[0].isdigit():
            continue
        cells += [""] * (len(header) - len(cells))
        sample = dict(zip(header, cells))
        if all(sample.get(name, "") not in {"", "X", "XX", "XXX"} for name in (
            "request_word",
            "pending_value",
            "acq_data_in[22]",
            "acq_data_in[21]",
            "latched_value",
            "LEDR",
            "acq_data_in[0]",
        )):
            samples.append(sample)
    if len(samples) < 3:
        raise ValueError("CSV contains fewer than three valid samples")
    return header, samples


def verify(path: Path, expected: int) -> None:
    _, samples = load_samples(path)
    pulse_samples = [
        (index, sample)
        for index, sample in enumerate(samples)
        if sample["acq_data_in[0]"] == "1"
    ]
    if len(pulse_samples) != 1:
        raise AssertionError(
            f"update_pulse must be high for exactly one sample, observed {len(pulse_samples)}"
        )

    index, current = pulse_samples[0]
    if index == 0 or index + 1 >= len(samples):
        raise AssertionError("trigger sample is not surrounded by pre/post samples")
    previous = samples[index - 1]
    following = samples[index + 1]

    request_word = int(current["request_word"], 16)
    pending = int(current["pending_value"], 16)
    latched = int(current["latched_value"], 16)
    leds = int(current["LEDR"], 16)
    busy_before = int(previous["acq_data_in[22]"])
    busy_now = int(current["acq_data_in[22]"])
    ack_now = int(current["acq_data_in[21]"])
    request_toggle = (request_word >> 11) & 1

    if request_word & 0x3FF != expected:
        raise AssertionError("captured request value does not match the requested value")
    if ((request_word >> 10) & 1) != 1:
        raise AssertionError("captured command is not marked as deferred")
    if pending != expected:
        raise AssertionError(f"pending value mismatch: expected {expected}, got {pending}")
    if latched != expected or leds != expected:
        raise AssertionError(
            f"output mismatch at trigger: latched={latched}, LEDR={leds}, expected={expected}"
        )
    if busy_before != 1 or busy_now != 0:
        raise AssertionError(
            f"busy must clear on update: before={busy_before}, trigger={busy_now}"
        )
    if ack_now != request_toggle:
        raise AssertionError(
            f"ack/request mismatch: ack={ack_now}, request={request_toggle}"
        )
    if following["acq_data_in[0]"] != "0":
        raise AssertionError("update_pulse did not return low after one clock")

    old_leds = int(previous["LEDR"], 16)
    print(
        "CAPTURE PASS "
        f"sample={current['time unit: ns']} old_led={old_leds} new_led={leds} "
        f"request={request_toggle} ack={ack_now} busy={busy_before}->{busy_now}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify DE1-SoC SignalTap CSV")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("expected", type=lambda text: int(text, 0))
    args = parser.parse_args()
    if not 0 <= args.expected <= 1023:
        parser.error("expected must be in the range 0..1023")
    verify(args.csv_file, args.expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
