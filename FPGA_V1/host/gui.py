"""晶振测试 FPGA V1：控制字、连续 μ 读出和单 bit SAR 图形界面。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from control_word import ControlWord, bits_as_text
from jtag_transport import (
    Command,
    OP_SAR_COMPARE,
    OP_SAR_START,
    OP_SET_DECIMATION,
    QuartusJtagTransport,
)


FILTER_TOTAL_SAMPLES_DEFAULT = 2048
FILTER_TOTAL_SAMPLES_MIN = 64
FILTER_TOTAL_SAMPLES_MAX = 8192


def parse_bit_string(text: str, width: int, name: str) -> int:
    bits = text.strip()
    if any(character not in "01" for character in bits):
        raise ValueError(f"{name}：只能包含字符0和1")
    if len(bits) != width:
        raise ValueError(
            f"{name}：必须恰好输入{width}位二进制码字，当前为{len(bits)}位"
        )
    return int(bits, 2)


def parse_integer(text: str, width: int, name: str) -> int:
    try:
        value = int(text.strip(), 0)
    except ValueError as exc:
        raise ValueError(
            f"{name}：请输入十进制数，或以 0x 开头的十六进制数"
        ) from exc
    if value < 0:
        value += 1 << width
    if not 0 <= value < (1 << width):
        raise ValueError(f"{name}：数值超出 {width}-bit 范围")
    return value


class ControlApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("晶振测试 FPGA V1")
        self.geometry("1120x790")
        self.minsize(920, 650)

        self.transport: QuartusJtagTransport | None = None
        self.transport_startup_error: Exception | None = None
        try:
            self.transport = QuartusJtagTransport()
        except Exception as exc:
            self.transport_startup_error = exc

        self.executor = ThreadPoolExecutor(max_workers=1)
        self.operation: Future | None = None
        self.field_vars: dict[str, tk.StringVar] = {}
        self.flag_vars: dict[str, tk.IntVar] = {}
        self.status_var = tk.StringVar(value="就绪")
        self.mu1_var = tk.StringVar(value="—")
        self.mu2_var = tk.StringVar(value="—")
        self.sequence_var = tk.StringVar(value="—")
        self.active_filter_samples_var = tk.StringVar(value="—")
        self.sar_var = tk.StringVar(value="—")
        self.live_var = tk.BooleanVar(value=False)
        self.connection_var = tk.StringVar(value="尚未检查连接")
        self.chip_var = tk.IntVar(value=0)
        self.broadcast_var = tk.BooleanVar(value=False)
        self.filter_total_samples_var = tk.StringVar(
            value=str(FILTER_TOTAL_SAMPLES_DEFAULT)
        )

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(250, self._check_connection)
        self.after(400, self._schedule_live_read)

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        control_tab = ttk.Frame(notebook, padding=10)
        calibration_tab = ttk.Frame(notebook, padding=10)
        notebook.add(control_tab, text="474位控制字")
        notebook.add(calibration_tab, text="连续采样与 SAR")

        static = ttk.LabelFrame(control_tab, text="芯片配置字段", padding=8)
        static.pack(fill="x")
        flags = [
            ("bypass", "BYPASS", 0),
            ("en_bias", "EN_BIAS", 1),
            ("en_dllbuf", "EN_DLLBUF", 1),
            ("en_intbuf", "EN_INTBUF", 1),
            ("en_sensor", "EN_SENSOR", 1),
            ("en_xo", "EN_XO", 1),
            ("en_xobufa", "EN_XOBUFA", 1),
            ("su", "SU", 0),
            ("polarity", "polarity", 0),
        ]
        for column, (key, label, default) in enumerate(flags):
            variable = tk.IntVar(value=default)
            self.flag_vars[key] = variable
            ttk.Checkbutton(static, text=label, variable=variable).grid(
                row=0, column=column, sticky="w", padx=5, pady=3
            )

        numeric = ttk.Frame(static)
        numeric.grid(row=1, column=0, columnspan=len(flags), sticky="ew")
        static.columnconfigure(0, weight=1)
        defaults = [
            ("cs", "CS [2-bit]", "00", 2),
            ("trim_c_coarse", "TRIM_C_COARSE [3-bit]", "000", 3),
            ("trim_c_fine", "TRIM_C_FINE [3-bit]", "000", 3),
            ("trim_r_xo", "TRIM_R_XO [4-bit]", "0000", 4),
            (
                "alpha",
                "ALPHA [18-bit]",
                "010000000000000000",
                18,
            ),
        ]
        for column, (key, label, default, width) in enumerate(defaults):
            ttk.Label(numeric, text=label).grid(row=0, column=column, padx=5)
            variable = tk.StringVar(value=default)
            self.field_vars[key] = variable
            ttk.Entry(
                numeric,
                textvariable=variable,
                width=max(15, width + 2),
                font=("Consolas", 10),
            ).grid(
                row=1, column=column, padx=5, pady=(0, 4)
            )

        ttk.Label(
            static,
            text=(
                "输入格式：所有多 bit 字段均直接输入定长二进制码字，"
                "只能包含0和1。CS/三个TRIM必须符合标签位宽；"
                "ALPHA必须为18位，例如010000000000000000表示Q16的1.0。"
                "全0仅为占位码字，写入前请载入并核对已验证配置。"
            ),
            wraplength=1020,
            justify="left",
        ).grid(
            row=2,
            column=0,
            columnspan=len(flags),
            sticky="w",
            padx=5,
            pady=(5, 2),
        )

        coefficient_frame = ttk.Frame(control_tab)
        coefficient_frame.pack(fill="both", expand=True, pady=8)
        batch_frame = ttk.LabelFrame(
            coefficient_frame,
            text="Batch 系数（29-bit 二进制码字/Q16原始位型）",
            padding=8,
        )
        poly_frame = ttk.LabelFrame(
            coefficient_frame,
            text="Polynomial 系数（29-bit 二进制码字/Q16原始位型）",
            padding=8,
        )
        batch_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))
        poly_frame.pack(side="left", fill="both", expand=True, padx=(4, 0))
        for row in range(7):
            self._coefficient_entry(batch_frame, f"batch_a{row}", row)
        for row in range(8):
            self._coefficient_entry(poly_frame, f"poly_a{row}", row)

        action = ttk.Frame(control_tab)
        action.pack(fill="x")
        ttk.Label(action, text="目标芯片编号").pack(side="left")
        ttk.Spinbox(
            action, from_=0, to=4, textvariable=self.chip_var, width=4
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            action, text="广播到全部芯片", variable=self.broadcast_var
        ).pack(side="left", padx=5)
        ttk.Button(
            action,
            text="写入474位并启动芯片",
            command=self._write_and_start,
        ).pack(side="left", padx=8)
        ttk.Button(action, text="保存 JSON", command=self._save_json).pack(
            side="left"
        )
        ttk.Button(action, text="载入 JSON", command=self._load_json).pack(
            side="left", padx=5
        )

        connection_frame = ttk.LabelFrame(
            calibration_tab, text="FPGA 连接", padding=12
        )
        connection_frame.pack(fill="x")
        ttk.Label(
            connection_frame,
            textvariable=self.connection_var,
            wraplength=700,
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            connection_frame,
            text="检查连接",
            command=self._check_connection,
        ).pack(side="left", padx=5)
        ttk.Button(
            connection_frame,
            text="加载 FPGA V1 SOF",
            command=self._program_fpga,
        ).pack(side="left", padx=5)

        sample_frame = ttk.LabelFrame(
            calibration_tab, text="连续三角窗降采样输出", padding=12
        )
        sample_frame.pack(fill="x", pady=(12, 0))
        ttk.Label(
            sample_frame,
            text="Filter 总采样点数 L=2N（64…8192）",
        ).grid(row=0, column=0, sticky="w")
        ttk.Entry(
            sample_frame,
            textvariable=self.filter_total_samples_var,
            width=10,
        ).grid(row=0, column=1, padx=6)
        ttk.Button(
            sample_frame,
            text="在下一窗口边界应用",
            command=self._apply_filter_length,
        ).grid(row=0, column=2, padx=5)
        ttk.Checkbutton(
            sample_frame,
            text="连续读取",
            variable=self.live_var,
        ).grid(row=0, column=3, padx=12)
        ttk.Label(
            sample_frame,
            text=(
                "片内 filter_down 一轮共2048个样点："
                "权重 0…1023、1024…1；因此 L=2048、内部半长 N=1024。"
            ),
            wraplength=950,
            justify="left",
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))

        metrics = [
            ("μ1", self.mu1_var),
            ("μ2", self.mu2_var),
            ("样本序号", self.sequence_var),
            ("当前 L", self.active_filter_samples_var),
        ]
        for column, (label, variable) in enumerate(metrics):
            ttk.Label(sample_frame, text=label).grid(
                row=2,
                column=column * 2,
                pady=(14, 0),
                sticky="e",
            )
            ttk.Label(
                sample_frame,
                textvariable=variable,
                font=("Consolas", 13, "bold"),
            ).grid(
                row=2,
                column=column * 2 + 1,
                pady=(14, 0),
                padx=8,
                sticky="w",
            )

        sar_frame = ttk.LabelFrame(
            calibration_tab, text="单 bit SAR 校准输入", padding=12
        )
        sar_frame.pack(fill="x", pady=12)
        ttk.Button(
            sar_frame,
            text="启动 SAR（首个试探码=0.5）",
            command=self._sar_start,
        ).pack(side="left", padx=5)
        ttk.Button(
            sar_frame,
            text="1：实测频率偏低",
            command=lambda: self._sar_compare(1),
        ).pack(side="left", padx=5)
        ttk.Button(
            sar_frame,
            text="0：实测频率偏高",
            command=lambda: self._sar_compare(0),
        ).pack(side="left", padx=5)
        ttk.Label(sar_frame, text="SAR Q16：").pack(side="left", padx=(20, 3))
        ttk.Label(
            sar_frame,
            textvariable=self.sar_var,
            font=("Consolas", 13, "bold"),
        ).pack(side="left")

        ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
        ).pack(fill="x", side="bottom")

    def _coefficient_entry(
        self, parent: ttk.Frame, key: str, row: int
    ) -> None:
        ttk.Label(parent, text=key).grid(
            row=row, column=0, sticky="e", padx=5, pady=4
        )
        variable = tk.StringVar(value="0" * 29)
        self.field_vars[key] = variable
        ttk.Entry(
            parent,
            textvariable=variable,
            width=34,
            font=("Consolas", 10),
        ).grid(
            row=row, column=1, sticky="ew", padx=5, pady=4
        )
        parent.columnconfigure(1, weight=1)

    def _require_transport(self) -> QuartusJtagTransport:
        if self.transport is None:
            if self.transport_startup_error is not None:
                raise RuntimeError(str(self.transport_startup_error))
            raise RuntimeError("Quartus JTAG 通信后端尚未初始化")
        return self.transport

    def _control_word(self) -> ControlWord:
        batch = [
            parse_bit_string(
                self.field_vars[f"batch_a{i}"].get(),
                29,
                f"batch_a{i}",
            )
            for i in range(7)
        ]
        poly = [
            parse_bit_string(
                self.field_vars[f"poly_a{i}"].get(),
                29,
                f"poly_a{i}",
            )
            for i in range(8)
        ]
        return ControlWord(
            **{key: variable.get() for key, variable in self.flag_vars.items()},
            cs=parse_bit_string(self.field_vars["cs"].get(), 2, "CS"),
            trim_c_coarse=parse_bit_string(
                self.field_vars["trim_c_coarse"].get(),
                3,
                "TRIM_C_COARSE",
            ),
            trim_c_fine=parse_bit_string(
                self.field_vars["trim_c_fine"].get(),
                3,
                "TRIM_C_FINE",
            ),
            trim_r_xo=parse_bit_string(
                self.field_vars["trim_r_xo"].get(),
                4,
                "TRIM_R_XO",
            ),
            alpha=parse_bit_string(
                self.field_vars["alpha"].get(),
                18,
                "ALPHA",
            ),
            batch=batch,
            poly=poly,
        )

    def _submit(self, label: str, function, on_success=None) -> None:
        if self.operation is not None and not self.operation.done():
            self.status_var.set("已有一项 JTAG 操作正在执行")
            return
        self.status_var.set(label)
        self.operation = self.executor.submit(function)

        def finish() -> None:
            assert self.operation is not None
            if not self.operation.done():
                self.after(50, finish)
                return
            try:
                result = self.operation.result()
                if on_success is not None:
                    on_success(result)
                self.status_var.set(str(result or "操作完成"))
            except Exception as exc:  # GUI 边界统一显示
                self.status_var.set(f"错误：{exc}")
                messagebox.showerror("FPGA 操作失败", str(exc))

        self.after(50, finish)

    def _check_connection(self) -> None:
        if self.transport is None:
            detail = str(
                self.transport_startup_error
                or "Quartus JTAG 通信后端尚未初始化"
            )
            self.connection_var.set(detail)
            self.status_var.set("未找到 Quartus JTAG 工具")
            return

        def show_result(diagnostic) -> None:
            self.connection_var.set(diagnostic.summary)
            if diagnostic.compatible:
                self.status_var.set("FPGA V1 连接已就绪")
            else:
                self.status_var.set("FPGA 已连接，但当前 SOF 不是 FPGA V1")

        self._submit(
            "正在检查 USB-Blaster、FPGA TAP 和 ISSP 位宽…",
            self.transport.diagnose,
            show_result,
        )

    def _program_fpga(self) -> None:
        sof_path = (
            Path(__file__).resolve().parents[1]
            / "quartus"
            / "output_files"
            / "fpga_v1.sof"
        )
        confirmed = messagebox.askyesno(
            "加载 FPGA V1",
            "该操作会把 fpga_v1.sof 加载到易失性 FPGA，并启动 "
            "2 MHz CLK_3P3 输出。请先确认 Rev G PCB 可以安全接收 "
            "FPGA 输出。\n\n是否继续？",
        )
        if not confirmed:
            return
        self.live_var.set(False)

        def work() -> str:
            return self._require_transport().program(sof_path)

        def programmed(_result) -> None:
            self.connection_var.set("FPGA 已加载，正在复查 V1 ISSP…")
            self.after(500, self._check_connection)

        self._submit("正在加载 FPGA V1…", work, programmed)

    def _write_and_start(self) -> None:
        try:
            word = self._control_word()
            words = word.words16()
            chip = self.chip_var.get()
            if not 0 <= chip <= 4:
                raise ValueError("目标芯片编号必须在0至4之间")
            selection = chip | (int(self.broadcast_var.get()) << 3)
        except Exception as exc:
            messagebox.showerror("控制字无效", str(exc))
            return

        def work() -> str:
            self._require_transport().configure_and_start(
                words,
                selection,
            )
            return (
                "474位控制字驱动流程已完整执行；"
                f"d1…d16={bits_as_text(word.serial_bits()[:16])}"
            )

        self._submit("正在写入30个传输块并等待750 ms驱动完成…", work)

    def _apply_filter_length(self) -> None:
        try:
            total_samples = parse_integer(
                self.filter_total_samples_var.get(),
                16,
                "Filter 总采样点数 L",
            )
            if not (
                FILTER_TOTAL_SAMPLES_MIN
                <= total_samples
                <= FILTER_TOTAL_SAMPLES_MAX
            ):
                raise ValueError(
                    "Filter 总采样点数 L 必须在64至8192之间"
                )
            if total_samples % 2:
                raise ValueError(
                    "Filter 总采样点数 L 必须为偶数，因为 L=2N"
                )
            half_length = total_samples // 2
        except Exception as exc:
            messagebox.showerror("Filter 长度无效", str(exc))
            return

        def work() -> str:
            self._require_transport().transact(
                [Command(OP_SET_DECIMATION, 0, half_length)]
            )
            return (
                f"Filter 总采样点数 L={total_samples}、内部半长 "
                f"N={half_length}；将在下一窗口边界生效"
            )

        self._submit("正在设置 Filter 窗口长度…", work)

    def _sar_start(self) -> None:
        def work() -> str:
            self._require_transport().transact(
                [Command(OP_SAR_START, 0, 1)]
            )
            return "SAR 已启动，首个试探码为0.5"

        self._submit("正在启动 SAR…", work)

    def _sar_compare(self, bit: int) -> None:
        def work() -> str:
            self._require_transport().transact(
                [Command(OP_SAR_COMPARE, 0, bit)]
            )
            return f"SAR 比较位 {bit} 已接收"

        self._submit(f"正在提交 SAR 比较位 {bit}…", work)

    def _schedule_live_read(self) -> None:
        if (
            self.transport is not None
            and self.live_var.get()
            and (self.operation is None or self.operation.done())
        ):
            self.operation = self.executor.submit(
                self.transport.read_registers,
                [1, 2, 3, 4, 5, 6, 7, 8, 9],
            )

            def finish_read() -> None:
                assert self.operation is not None
                if not self.operation.done():
                    self.after(50, finish_read)
                    return
                try:
                    registers = self.operation.result()
                    mu1 = registers[1] | ((registers[2] & 0x3) << 16)
                    mu2 = registers[3] | ((registers[4] & 0x3) << 16)
                    sar = registers[6] | ((registers[7] & 0x1) << 16)
                    self.mu1_var.set(
                        f"{mu1 / 65536:.6f} (0x{mu1:05X})"
                    )
                    self.mu2_var.set(
                        f"{mu2 / 65536:.6f} (0x{mu2:05X})"
                    )
                    self.sequence_var.set(str(registers[5]))
                    self.active_filter_samples_var.set(
                        str(registers[9] * 2)
                    )
                    self.sar_var.set(
                        f"{sar / 65536:.6f}，步骤={registers[8]}"
                    )
                except Exception as exc:
                    self.status_var.set(f"连续读取已暂停：{exc}")
                    self.live_var.set(False)
                self.after(1200, self._schedule_live_read)

            self.after(50, finish_read)
        else:
            self.after(1200, self._schedule_live_read)

    def _save_json(self) -> None:
        try:
            word = self._control_word()
        except Exception as exc:
            messagebox.showerror("控制字无效", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="保存控制字配置",
            defaultextension=".json",
            filetypes=[("JSON 配置", "*.json")],
        )
        if path:
            Path(path).write_text(
                json.dumps(
                    word.to_json_dict(),
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.status_var.set("控制字配置已保存")

    def _load_json(self) -> None:
        path = filedialog.askopenfilename(
            title="载入控制字配置",
            filetypes=[("JSON 配置", "*.json")],
        )
        if not path:
            return
        try:
            word = ControlWord.from_json_dict(
                json.loads(Path(path).read_text(encoding="utf-8"))
            )
            word.validate()
            for key, variable in self.flag_vars.items():
                variable.set(getattr(word, key))
            for key in (
                "cs",
                "trim_c_coarse",
                "trim_c_fine",
                "trim_r_xo",
                "alpha",
            ):
                width = {
                    "cs": 2,
                    "trim_c_coarse": 3,
                    "trim_c_fine": 3,
                    "trim_r_xo": 4,
                    "alpha": 18,
                }[key]
                value = getattr(word, key) & ((1 << width) - 1)
                self.field_vars[key].set(f"{value:0{width}b}")
            for index, value in enumerate(word.batch):
                self.field_vars[f"batch_a{index}"].set(
                    f"{value & ((1 << 29) - 1):029b}"
                )
            for index, value in enumerate(word.poly):
                self.field_vars[f"poly_a{index}"].set(
                    f"{value & ((1 << 29) - 1):029b}"
                )
            self.status_var.set("控制字配置已载入")
        except Exception as exc:
            messagebox.showerror("无法载入配置", str(exc))

    def _close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    ControlApp().mainloop()
