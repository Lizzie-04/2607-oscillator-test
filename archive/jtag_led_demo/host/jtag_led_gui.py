from __future__ import annotations

import queue
import re
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parents[1]
QUARTUS_BIN = Path(r"C:\altera_lite\25.1std\quartus\bin64")
QUARTUS_STP = QUARTUS_BIN / "quartus_stp.exe"
ISSP_SCRIPT = ROOT / "host" / "issp_write.tcl"
JTAG_CHECK = ROOT / "scripts" / "jtag_check.ps1"
FULL_FLOW = ROOT / "scripts" / "verify_full_flow.ps1"


def parse_value(text: str) -> int:
    cleaned = text.strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", cleaned):
        value = int(cleaned, 16)
    elif re.fullmatch(r"[0-9]+", cleaned):
        value = int(cleaned, 10)
    else:
        raise ValueError("请输入十进制 0–1023，或十六进制 0x000–0x3FF。")
    if not 0 <= value <= 1023:
        raise ValueError("数值必须在 0–1023 范围内。")
    return value


class LedGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DE1-SoC JTAG LED 控制器")
        self.geometry("760x520")
        self.minsize(700, 460)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.value_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="尚未连接")
        self.led_widgets: list[tk.Label] = []

        self._build_ui()
        self.after(100, self._process_events)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="输入控制字（十进制或 0x 前缀十六进制，10 位）").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 12))
        entry = ttk.Entry(row, textvariable=self.value_var, font=("Consolas", 14))
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.send_value())

        ttk.Button(row, text="发送", command=self.send_value).pack(side="left", padx=6)
        ttk.Button(row, text="连接检查", command=self.check_connection).pack(side="left")
        ttk.Button(row, text="全流程验证", command=self.full_flow).pack(side="left", padx=(6, 0))

        ttk.Label(frame, textvariable=self.status_var).pack(anchor="w", pady=(0, 12))

        led_frame = ttk.LabelFrame(frame, text="LEDR[9:0]", padding=12)
        led_frame.pack(fill="x")
        for bit in range(9, -1, -1):
            cell = ttk.Frame(led_frame)
            cell.pack(side="left", expand=True)
            label = tk.Label(
                cell,
                width=3,
                height=1,
                bg="#3b1d1d",
                fg="white",
                relief="sunken",
                text="0",
                font=("Consolas", 12, "bold"),
            )
            label.pack()
            ttk.Label(cell, text=str(bit)).pack()
            self.led_widgets.append(label)

        ttk.Label(frame, text="运行日志").pack(anchor="w", pady=(16, 4))
        self.log = tk.Text(frame, height=14, state="disabled", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_leds(self, value: int) -> None:
        for widget, bit in zip(self.led_widgets, range(9, -1, -1)):
            on = bool(value & (1 << bit))
            widget.configure(text="1" if on else "0", bg="#e02020" if on else "#3b1d1d")

    def _run_background(
        self, label: str, command: list[str], value: int | None = None
    ) -> None:
        self.status_var.set(f"{label}…")

        def worker() -> None:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            self.events.put(("completed", (label, completed, value)))

        threading.Thread(target=worker, daemon=True).start()

    def check_connection(self) -> None:
        self._run_background(
            "检查 JTAG",
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(JTAG_CHECK),
            ],
        )

    def send_value(self) -> None:
        try:
            value = parse_value(self.value_var.get())
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))
            return
        self._run_background(
            f"发送 {value}",
            [str(QUARTUS_STP), "-t", str(ISSP_SCRIPT), str(value), "0"],
            value,
        )

    def full_flow(self) -> None:
        try:
            value = parse_value(self.value_var.get())
        except ValueError as exc:
            messagebox.showerror("输入错误", str(exc))
            return
        self._run_background(
            f"全流程验证 {value}",
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(FULL_FLOW),
                "-Value",
                str(value),
            ],
            value,
        )

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event != "completed":
                    continue
                label, completed, value = payload
                output = (completed.stdout or "") + (completed.stderr or "")
                self._append_log(f"> {label}\n{output}")
                if completed.returncode == 0:
                    self.status_var.set(f"{label}：成功")
                    if value is not None:
                        self._set_leds(value)
                else:
                    self.status_var.set(f"{label}：失败（{completed.returncode}）")
                    messagebox.showerror("操作失败", output or f"退出代码 {completed.returncode}")
        except queue.Empty:
            pass
        self.after(100, self._process_events)


if __name__ == "__main__":
    LedGui().mainloop()
