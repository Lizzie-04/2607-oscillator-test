"""晶振测试 FPGA V2：控制字、连续 μ 采样、统计与 SAR 界面。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
from queue import Empty, SimpleQueue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from acquisition import (
    AtomicStatisticsCsv,
    DEFAULT_DATA_COUNT,
    TREND_HISTORY_LIMIT,
    TrendHistory,
    statistics_filename,
    validate_data_count,
)
from control_word import ControlWord, bits_as_text
from instrument_backend import (
    FrequencyInstrumentBackend,
    UnavailableFrequencyInstrument,
)
from jtag_transport import (
    DSM_OVERRIDE_SCALE,
    DEFAULT_CONTROL_SAMPLE_COUNT,
    DsmOverrideStatus,
    FILTER_TOTAL_SAMPLES_DEFAULT,
    LiveSample,
    MuSample,
    QuartusJtagTransport,
    SarStatus,
    dsm_signed_to_twos,
    validate_dsm_override_raw,
    validate_control_sample_count,
    validate_filter_total_samples,
)


STATISTICS_BATCH_SIZE = 100


def parse_bit_string(text: str, width: int, name: str) -> int:
    bits = text.strip()
    if any(character not in "01" for character in bits):
        raise ValueError(f"{name}：只能包含字符0和1")
    if len(bits) != width:
        raise ValueError(
            f"{name}：必须恰好输入{width}位二进制码字，当前为{len(bits)}位"
        )
    return int(bits, 2)


def parse_target_frequency(text: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise ValueError("目标频率必须是有效数字") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("目标频率必须是大于0的有限数值")
    return value


def parse_control_sample_count(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError as exc:
        raise ValueError("控制字驱动样点数N必须为十进制整数") from exc
    return validate_control_sample_count(value)


def parse_filter_total_sample_count(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError as exc:
        raise ValueError("Filter总采样点数L必须为十进制整数") from exc
    return validate_filter_total_samples(value)


def parse_dsm_override_value(text: str) -> int:
    value_text = text.strip().replace("_", "")
    if not value_text:
        raise ValueError("DSM输入不能为空")
    if len(value_text) == 29 and set(value_text) <= {"0", "1"}:
        return validate_dsm_override_raw(int(value_text, 2))
    if value_text.lower().startswith(("0x", "0b")):
        try:
            return validate_dsm_override_raw(int(value_text, 0))
        except ValueError as exc:
            raise ValueError("DSM输入原始码字必须在29-bit范围内") from exc
    try:
        q16_value = float(value_text)
    except ValueError as exc:
        raise ValueError(
            "DSM输入请填写十进制Q16数值，或0x/0b开头的29-bit原始码字"
        ) from exc
    if not math.isfinite(q16_value):
        raise ValueError("DSM输入必须为有限数值")
    scaled = round(q16_value * DSM_OVERRIDE_SCALE)
    return dsm_signed_to_twos(scaled)


class ControlApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("晶振测试 FPGA V2")
        self.geometry("1180x850")
        self.minsize(1000, 720)

        self.transport: QuartusJtagTransport | None = None
        self.transport_startup_error: Exception | None = None
        try:
            self.transport = QuartusJtagTransport()
        except Exception as exc:
            self.transport_startup_error = exc

        self.frequency_backend: FrequencyInstrumentBackend = (
            UnavailableFrequencyInstrument()
        )
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.operation: Future | None = None
        self.submission_waiting = False
        self.statistics_running = False
        self.statistics_progress: SimpleQueue = SimpleQueue()
        self.live_before_statistics = False
        self.last_live_sequence: int | None = None
        self.history = TrendHistory(TREND_HISTORY_LIMIT)

        self.field_vars: dict[str, tk.StringVar] = {}
        self.flag_vars: dict[str, tk.IntVar] = {}
        self.status_var = tk.StringVar(value="就绪")
        self.connection_var = tk.StringVar(value="尚未检查连接")
        self.mu1_var = tk.StringVar(value="—")
        self.mu2_var = tk.StringVar(value="—")
        self.active_filter_samples_var = tk.StringVar(value="—")
        self.sar_var = tk.StringVar(value="—")
        self.sar_status_var = tk.StringVar(value="—")
        self.measured_frequency_var = tk.StringVar(
            value="— MHz（仪器未配置）"
        )
        self.target_frequency_var = tk.StringVar(value="")
        self.live_var = tk.BooleanVar(value=False)
        self.chip_var = tk.IntVar(value=0)
        self.broadcast_var = tk.BooleanVar(value=False)
        self.drive_sample_count_var = tk.StringVar(
            value=str(DEFAULT_CONTROL_SAMPLE_COUNT)
        )
        self.filter_total_samples_var = tk.StringVar(
            value=str(FILTER_TOTAL_SAMPLES_DEFAULT)
        )
        self.dsm_override_value_var = tk.StringVar(value="0.0")
        self.dsm_override_status_var = tk.StringVar(value="尚未读取")
        self.data_count_var = tk.StringVar(value=str(DEFAULT_DATA_COUNT))
        self.file_stem_var = tk.StringVar(value="mu")
        self.output_directory_var = tk.StringVar(
            value=str(
                Path(__file__).resolve().parents[1]
                / "data"
                / "statistics"
            )
        )
        self.statistics_progress_var = tk.StringVar(value="尚未开始统计")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(250, self._check_connection)
        self.after(200, self._schedule_background_read)
        self.after(100, self._drain_statistics_progress)

    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.connection_tab = ttk.Frame(self.notebook, padding=14)
        self.control_tab = ttk.Frame(self.notebook, padding=10)
        self.sampling_tab = ttk.Frame(self.notebook, padding=10)
        self.traverse_tab = ttk.Frame(self.notebook, padding=14)
        self.sar_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.connection_tab, text="检查FPGA连接")
        self.notebook.add(self.control_tab, text="控制字输入")
        self.notebook.add(self.sampling_tab, text="连续采样")
        self.notebook.add(self.traverse_tab, text="遍历")
        self.notebook.add(self.sar_tab, text="SAR校准")

        self._build_connection_tab(self.connection_tab)
        self._build_control_tab(self.control_tab)
        self._build_sampling_tab(self.sampling_tab)
        self._build_traverse_tab(self.traverse_tab)
        self._build_sar_tab(self.sar_tab)

        ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
        ).pack(fill="x", side="bottom")

    def _build_connection_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="FPGA连接状态", padding=16)
        frame.pack(fill="x")
        ttk.Label(
            frame,
            textvariable=self.connection_var,
            wraplength=780,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 15))
        ttk.Button(
            frame,
            text="检查连接",
            command=self._check_connection,
        ).grid(row=0, column=1, padx=5)
        ttk.Button(
            frame,
            text="加载 FPGA V2 SOF",
            command=self._program_fpga,
        ).grid(row=0, column=2, padx=5)
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text=(
                "连接检查同时验证USB-Blaster、Cyclone V、64-bit ISSP和"
                "V2协议版本。加载SOF前请确认Rev G PCB能够安全接收FPGA输出。"
            ),
            wraplength=1050,
            justify="left",
        ).pack(fill="x", pady=18)

    def _build_control_tab(self, parent: ttk.Frame) -> None:
        switches = ttk.LabelFrame(parent, text="芯片开关", padding=8)
        switches.pack(fill="x")
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
            ttk.Checkbutton(
                switches, text=label, variable=variable
            ).grid(row=0, column=column, sticky="w", padx=7, pady=3)

        trims = ttk.LabelFrame(parent, text="选通与TRIM", padding=8)
        trims.pack(fill="x", pady=(8, 0))
        trim_defaults = [
            ("cs", "CS [2-bit]", "00", 2),
            ("trim_c_coarse", "TRIM_C_COARSE [3-bit]", "000", 3),
            ("trim_c_fine", "TRIM_C_FINE [3-bit]", "000", 3),
            ("trim_r_xo", "TRIM_R_XO [4-bit]", "0000", 4),
        ]
        for column, (key, label, default, width) in enumerate(trim_defaults):
            ttk.Label(trims, text=label).grid(
                row=0, column=column, padx=8
            )
            variable = tk.StringVar(value=default)
            self.field_vars[key] = variable
            ttk.Entry(
                trims,
                textvariable=variable,
                width=max(18, width + 2),
                font=("Consolas", 10),
            ).grid(row=1, column=column, padx=8, pady=(0, 4))

        coefficients = ttk.LabelFrame(parent, text="算法系数", padding=8)
        coefficients.pack(fill="both", expand=True, pady=8)

        alpha_frame = ttk.Frame(coefficients)
        alpha_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(alpha_frame, text="ALPHA [18-bit]").pack(side="left")
        alpha_variable = tk.StringVar(value="010000000000000000")
        self.field_vars["alpha"] = alpha_variable
        ttk.Entry(
            alpha_frame,
            textvariable=alpha_variable,
            width=24,
            font=("Consolas", 10),
        ).pack(side="left", padx=8)

        coefficient_columns = ttk.Frame(coefficients)
        coefficient_columns.pack(fill="both", expand=True)
        batch_frame = ttk.LabelFrame(
            coefficient_columns, text="Batch系数 [29-bit]", padding=8
        )
        poly_frame = ttk.LabelFrame(
            coefficient_columns, text="Polynomial系数 [29-bit]", padding=8
        )
        batch_frame.pack(
            side="left", fill="both", expand=True, padx=(0, 4)
        )
        poly_frame.pack(
            side="left", fill="both", expand=True, padx=(4, 0)
        )
        for row in range(7):
            self._coefficient_entry(batch_frame, f"batch_a{row}", row)
        for row in range(8):
            self._coefficient_entry(poly_frame, f"poly_a{row}", row)

        action = ttk.Frame(parent)
        action.pack(fill="x")
        ttk.Label(action, text="目标芯片编号").pack(side="left")
        ttk.Spinbox(
            action, from_=0, to=4, textvariable=self.chip_var, width=4
        ).pack(side="left", padx=5)
        ttk.Checkbutton(
            action, text="广播到全部芯片", variable=self.broadcast_var
        ).pack(side="left", padx=5)
        ttk.Label(action, text="驱动样点数N").pack(side="left", padx=(12, 0))
        ttk.Entry(
            action,
            textvariable=self.drive_sample_count_var,
            width=7,
        ).pack(side="left", padx=5)
        ttk.Button(
            action,
            text="写入474位并启动芯片",
            command=self._write_and_start,
        ).pack(side="left", padx=8)
        ttk.Button(
            action, text="保存JSON", command=self._save_json
        ).pack(side="left")
        ttk.Button(
            action, text="载入JSON", command=self._load_json
        ).pack(side="left", padx=5)

    def _build_sampling_tab(self, parent: ttk.Frame) -> None:
        settings = ttk.LabelFrame(
            parent, text="连续采样设置", padding=10
        )
        settings.pack(fill="x")
        ttk.Label(settings, text="Filter总采样点数 L").grid(
            row=0, column=0, sticky="w"
        )
        self.filter_entry = ttk.Entry(
            settings,
            textvariable=self.filter_total_samples_var,
            width=10,
        )
        self.filter_entry.grid(row=0, column=1, padx=6)
        self.filter_apply_button = ttk.Button(
            settings,
            text="在下一窗口边界应用",
            command=self._apply_filter_length,
        )
        self.filter_apply_button.grid(row=0, column=2, padx=5)
        ttk.Checkbutton(
            settings,
            text="实时读取",
            variable=self.live_var,
        ).grid(row=0, column=3, padx=14)

        metrics = [
            ("μ1", self.mu1_var),
            ("μ2", self.mu2_var),
            ("当前L", self.active_filter_samples_var),
        ]
        for index, (label, variable) in enumerate(metrics):
            column = 4 + index * 2
            ttk.Label(settings, text=f"{label}：").grid(
                row=0, column=column, padx=(12, 2)
            )
            ttk.Label(
                settings,
                textvariable=variable,
                font=("Consolas", 12, "bold"),
            ).grid(row=0, column=column + 1, sticky="w")

        chart_frame = ttk.LabelFrame(
            parent,
            text="μ1 / μ2 最近2000点趋势",
            padding=6,
        )
        chart_frame.pack(fill="both", expand=True, pady=8)
        self.chart_canvas = tk.Canvas(
            chart_frame,
            background="white",
            highlightthickness=0,
            height=390,
        )
        self.chart_canvas.pack(fill="both", expand=True)
        self.chart_canvas.bind(
            "<Configure>", lambda _event: self._redraw_chart()
        )

        statistics = ttk.LabelFrame(parent, text="统计并保存CSV", padding=10)
        statistics.pack(fill="x")
        ttk.Label(statistics, text="数据量").grid(
            row=0, column=0, sticky="e"
        )
        self.data_count_entry = ttk.Entry(
            statistics, textvariable=self.data_count_var, width=10
        )
        self.data_count_entry.grid(row=0, column=1, padx=(5, 15))
        ttk.Label(statistics, text="文件名").grid(
            row=0, column=2, sticky="e"
        )
        self.file_stem_entry = ttk.Entry(
            statistics, textvariable=self.file_stem_var, width=18
        )
        self.file_stem_entry.grid(row=0, column=3, padx=(5, 15))
        ttk.Label(statistics, text="保存目录").grid(
            row=0, column=4, sticky="e"
        )
        self.output_directory_entry = ttk.Entry(
            statistics,
            textvariable=self.output_directory_var,
            width=42,
        )
        self.output_directory_entry.grid(
            row=0, column=5, padx=5, sticky="ew"
        )
        self.output_directory_button = ttk.Button(
            statistics,
            text="选择目录",
            command=self._choose_statistics_directory,
        )
        self.output_directory_button.grid(row=0, column=6, padx=5)
        self.statistics_button = ttk.Button(
            statistics,
            text="统计",
            command=self._start_statistics,
        )
        self.statistics_button.grid(row=0, column=7, padx=8)
        ttk.Label(
            statistics,
            textvariable=self.statistics_progress_var,
        ).grid(
            row=1,
            column=0,
            columnspan=8,
            sticky="w",
            pady=(8, 0),
        )
        statistics.columnconfigure(5, weight=1)

    def _build_traverse_tab(self, parent: ttk.Frame) -> None:
        override = ttk.LabelFrame(parent, text="数字DSM输入覆盖", padding=14)
        override.pack(fill="x")
        ttk.Label(override, text="DSM输入Q16").grid(
            row=0, column=0, sticky="e"
        )
        ttk.Entry(
            override,
            textvariable=self.dsm_override_value_var,
            width=22,
            font=("Consolas", 11),
        ).grid(row=0, column=1, padx=8, sticky="w")
        ttk.Button(
            override,
            text="启用覆盖",
            command=self._enable_dsm_override,
        ).grid(row=0, column=2, padx=5)
        ttk.Button(
            override,
            text="恢复环路",
            command=self._disable_dsm_override,
        ).grid(row=0, column=3, padx=5)
        ttk.Button(
            override,
            text="读取状态",
            command=self._read_dsm_override,
        ).grid(row=0, column=4, padx=5)
        ttk.Label(
            override,
            textvariable=self.dsm_override_status_var,
            font=("Consolas", 11),
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(12, 0))
        override.columnconfigure(1, weight=1)

    def _build_sar_tab(self, parent: ttk.Frame) -> None:
        measurement = ttk.LabelFrame(
            parent, text="频率测量", padding=14
        )
        measurement.pack(fill="x")
        ttk.Label(measurement, text="专业仪器实测频率：").grid(
            row=0, column=0, sticky="e"
        )
        ttk.Label(
            measurement,
            textvariable=self.measured_frequency_var,
            font=("Consolas", 13, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(measurement, text="目标频率：").grid(
            row=0, column=2, sticky="e", padx=(30, 0)
        )
        ttk.Entry(
            measurement,
            textvariable=self.target_frequency_var,
            width=16,
        ).grid(row=0, column=3, padx=(5, 3))
        ttk.Label(measurement, text="MHz").grid(
            row=0, column=4, sticky="w"
        )
        ttk.Button(
            measurement,
            text="读取仪器",
            command=self._read_instrument_frequency,
        ).grid(row=0, column=5, padx=(16, 0))

        controls = ttk.LabelFrame(
            parent, text="16步SAR校准", padding=14
        )
        controls.pack(fill="x", pady=12)
        self.sar_start_button = ttk.Button(
            controls,
            text="启动SAR（首个试探码0.5）",
            command=self._sar_start,
        )
        self.sar_start_button.grid(row=0, column=0, padx=5)
        self.sar_low_button = ttk.Button(
            controls,
            text="实测低于目标 → 增大校准码",
            command=lambda: self._sar_compare(1),
            state="disabled",
        )
        self.sar_low_button.grid(row=0, column=1, padx=5)
        self.sar_high_button = ttk.Button(
            controls,
            text="实测高于目标 → 减小校准码",
            command=lambda: self._sar_compare(0),
            state="disabled",
        )
        self.sar_high_button.grid(row=0, column=2, padx=5)
        ttk.Label(controls, text="SAR Q16：").grid(
            row=0, column=3, padx=(20, 3)
        )
        ttk.Label(
            controls,
            textvariable=self.sar_var,
            font=("Consolas", 13, "bold"),
        ).grid(row=0, column=4, sticky="w")
        ttk.Label(controls, textvariable=self.sar_status_var).grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="w",
            pady=(12, 0),
        )

        ttk.Label(
            parent,
            text=(
                "每个试探码只提交一次频率高低判决，共完成16步。当前假设"
                "校准码增大使频率增大；真实仪器联调若方向相反，由Python"
                "仪器后端反转判决。"
            ),
            wraplength=1050,
            justify="left",
        ).pack(fill="x")

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
        ).grid(row=row, column=1, sticky="ew", padx=5, pady=4)
        parent.columnconfigure(1, weight=1)

    def _require_transport(self) -> QuartusJtagTransport:
        if self.transport is None:
            if self.transport_startup_error is not None:
                raise RuntimeError(str(self.transport_startup_error))
            raise RuntimeError("Quartus JTAG通信后端尚未初始化")
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

    def _submit(
        self,
        label: str,
        function,
        on_success=None,
        on_error=None,
        _waiting: bool = False,
    ) -> None:
        if self.submission_waiting and not _waiting:
            self.status_var.set("已有一项操作正在等待后台读取结束")
            return
        if self.operation is not None and not self.operation.done():
            if not _waiting:
                self.submission_waiting = True
            self.status_var.set("正在结束后台读取，随后自动执行操作")
            self.after(
                100,
                lambda: self._submit(
                    label,
                    function,
                    on_success,
                    on_error,
                    True,
                ),
            )
            return
        self.submission_waiting = False
        self.status_var.set(label)
        future = self.executor.submit(function)
        self.operation = future

        def finish() -> None:
            if not future.done():
                self.after(50, finish)
                return
            try:
                result = future.result()
                self.status_var.set(str(result or "操作完成"))
                if on_success is not None:
                    on_success(result)
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)
                self.status_var.set(f"错误：{exc}")
                messagebox.showerror("FPGA操作失败", str(exc))

        self.after(50, finish)

    def _check_connection(self) -> None:
        if self.transport is None:
            detail = str(
                self.transport_startup_error
                or "Quartus JTAG通信后端尚未初始化"
            )
            self.connection_var.set(detail)
            self.status_var.set("未找到Quartus JTAG工具")
            return

        def show_result(diagnostic) -> None:
            self.connection_var.set(diagnostic.summary)
            if diagnostic.compatible:
                self.status_var.set("FPGA V2连接已就绪")
            else:
                self.status_var.set("FPGA已连接，但当前镜像不是FPGA V2")

        self._submit(
            "正在检查USB-Blaster、FPGA TAP、ISSP与V2协议…",
            self.transport.diagnose,
            show_result,
        )

    def _program_fpga(self) -> None:
        sof_path = (
            Path(__file__).resolve().parents[1]
            / "quartus"
            / "output_files"
            / "fpga_v2.sof"
        )
        confirmed = messagebox.askyesno(
            "加载FPGA V2",
            "该操作会把fpga_v2.sof加载到易失性FPGA，并启动"
            "2 MHz CLK_3P3输出。请先确认Rev G PCB可以安全接收"
            "FPGA输出。\n\n是否继续？",
        )
        if not confirmed:
            return
        self.live_var.set(False)

        def work() -> str:
            return self._require_transport().program(sof_path)

        def programmed(_result) -> None:
            self.connection_var.set("FPGA已加载，正在复查V2协议…")
            self.after(500, self._check_connection)

        self._submit("正在加载FPGA V2…", work, programmed)

    def _write_and_start(self) -> None:
        try:
            word = self._control_word()
            words = word.words16()
            chip = self.chip_var.get()
            if not 0 <= chip <= 4:
                raise ValueError("目标芯片编号必须在0至4之间")
            selection = chip | (int(self.broadcast_var.get()) << 3)
            drive_sample_count = parse_control_sample_count(
                self.drive_sample_count_var.get()
            )
        except Exception as exc:
            messagebox.showerror("控制字无效", str(exc))
            return

        def work() -> str:
            self._require_transport().configure_and_start(
                words,
                selection,
                control_sample_count=drive_sample_count,
            )
            return (
                "474位控制字驱动流程已完整执行；"
                f"N={drive_sample_count}；"
                f"d1…d16={bits_as_text(word.serial_bits()[:16])}"
            )

        self._submit(
            f"正在写入30个块并等待N={drive_sample_count}的驱动完成…",
            work,
        )

    def _apply_filter_length(self) -> None:
        if self.statistics_running:
            messagebox.showinfo("统计进行中", "统计期间不能修改Filter长度")
            return
        try:
            total_samples = parse_filter_total_sample_count(
                self.filter_total_samples_var.get()
            )
        except Exception as exc:
            messagebox.showerror("Filter长度无效", str(exc))
            return

        def work() -> str:
            self._require_transport().set_filter_total_samples(total_samples)
            return (
                f"Filter总采样点数L={total_samples}；"
                "将在下一窗口边界生效"
            )

        self._submit("正在设置Filter窗口长度…", work)

    def _format_dsm_status(self, status: DsmOverrideStatus) -> str:
        mode = "覆盖已启用" if status.enabled else "环路已恢复"
        return (
            f"{mode}；raw=0x{status.raw_value:08X}；"
            f"Q16={status.q16_value:.6f}"
        )

    def _show_dsm_override_status(
        self,
        status: DsmOverrideStatus,
    ) -> None:
        self.dsm_override_status_var.set(
            self._format_dsm_status(status)
        )

    def _enable_dsm_override(self) -> None:
        try:
            raw_value = parse_dsm_override_value(
                self.dsm_override_value_var.get()
            )
        except Exception as exc:
            messagebox.showerror("DSM输入无效", str(exc))
            return

        def work() -> DsmOverrideStatus:
            return self._require_transport().set_dsm_override(
                raw_value,
                enabled=True,
            )

        self._submit(
            "正在启用DSM输入覆盖…",
            work,
            self._show_dsm_override_status,
        )

    def _disable_dsm_override(self) -> None:
        def work() -> DsmOverrideStatus:
            return self._require_transport().set_dsm_override(
                0,
                enabled=False,
            )

        self._submit(
            "正在恢复数字环路DSM输入…",
            work,
            self._show_dsm_override_status,
        )

    def _read_dsm_override(self) -> None:
        def work() -> DsmOverrideStatus:
            return self._require_transport().read_dsm_override_status()

        self._submit(
            "正在读取DSM输入覆盖状态…",
            work,
            self._show_dsm_override_status,
        )

    def _choose_statistics_directory(self) -> None:
        selected = filedialog.askdirectory(
            title="选择统计CSV保存目录",
            initialdir=self.output_directory_var.get(),
        )
        if selected:
            self.output_directory_var.set(selected)

    def _set_statistics_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (
            self.filter_entry,
            self.filter_apply_button,
            self.data_count_entry,
            self.file_stem_entry,
            self.output_directory_entry,
            self.output_directory_button,
            self.statistics_button,
        ):
            widget.configure(state=state)

    def _start_statistics(self, waiting_for_live_read: bool = False) -> None:
        if not waiting_for_live_read:
            if self.submission_waiting:
                self.status_var.set(
                    "请等待上一项操作开始或完成后再启动统计"
                )
                return
            self.live_before_statistics = self.live_var.get()
            self.live_var.set(False)

        if self.operation is not None and not self.operation.done():
            self.status_var.set("正在结束当前JTAG读取，随后自动开始统计")
            self.after(100, lambda: self._start_statistics(True))
            return

        try:
            try:
                data_count_value = int(self.data_count_var.get().strip())
            except ValueError as exc:
                raise ValueError("数据量必须为十进制整数") from exc
            data_count = validate_data_count(data_count_value)
            output_text = self.output_directory_var.get().strip()
            if not output_text:
                raise ValueError("保存目录不能为空")
            output_directory = Path(output_text).expanduser().resolve()
            started_at = datetime.now()
            file_stem = self.file_stem_var.get()
            # Validate the stem and collision before starting FPGA capture.
            final_name = statistics_filename(
                file_stem, started_at
            )
            if (output_directory / final_name).exists():
                raise FileExistsError(
                    f"统计文件已存在：{output_directory / final_name}"
                )
        except Exception as exc:
            self.live_var.set(self.live_before_statistics)
            messagebox.showerror("统计设置无效", str(exc))
            return

        total_batches = data_count // STATISTICS_BATCH_SIZE
        self.statistics_running = True
        self._set_statistics_controls(False)
        self.statistics_progress_var.set(
            f"准备采集0 / {data_count}点，共{total_batches}批"
        )

        def work() -> Path:
            with AtomicStatisticsCsv(
                output_directory,
                file_stem,
                started_at,
            ) as writer:
                for batch_number in range(1, total_batches + 1):
                    samples = (
                        self._require_transport()
                        .capture_statistics_batch()
                    )
                    writer.append_batch(batch_number, samples)
                    completed = batch_number * STATISTICS_BATCH_SIZE
                    self.statistics_progress.put(
                        (
                            "batch",
                            batch_number,
                            total_batches,
                            completed,
                            data_count,
                            samples,
                        )
                    )
                return writer.publish(data_count)

        def completed(path: Path) -> None:
            self.statistics_running = False
            self._set_statistics_controls(True)
            self.live_var.set(self.live_before_statistics)
            self.statistics_progress_var.set(
                f"统计完成：{data_count}点已保存到 {path}"
            )
            messagebox.showinfo("统计完成", f"CSV已保存：\n{path}")

        def failed(_exc: Exception) -> None:
            self.statistics_running = False
            self._set_statistics_controls(True)
            self.live_var.set(self.live_before_statistics)
            self.statistics_progress_var.set(
                "统计失败，临时文件已清理"
            )

        self._submit(
            f"正在统计{data_count}点…",
            work,
            completed,
            failed,
        )

    def _drain_statistics_progress(self) -> None:
        try:
            while True:
                event = self.statistics_progress.get_nowait()
                if event[0] != "batch":
                    continue
                (
                    _kind,
                    batch_number,
                    total_batches,
                    completed,
                    data_count,
                    samples,
                ) = event
                self.history.extend(samples)
                if samples:
                    self._show_mu_sample(samples[-1])
                self._redraw_chart()
                self.statistics_progress_var.set(
                    f"第{batch_number} / {total_batches}批完成；"
                    f"已下载{completed} / {data_count}点"
                )
        except Empty:
            pass
        self.after(100, self._drain_statistics_progress)

    def _show_mu_sample(self, sample: MuSample) -> None:
        self.mu1_var.set(f"{sample.mu1:.6f}")
        self.mu2_var.set(f"{sample.mu2:.6f}")

    def _redraw_chart(self) -> None:
        canvas = self.chart_canvas
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 200)
        canvas.delete("all")

        left, right, top, bottom = 55, 20, 22, 34
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)

        for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = top + (1.0 - tick) * plot_height
            canvas.create_line(
                left,
                y,
                width - right,
                y,
                fill="#D8D8D8",
            )
            canvas.create_text(
                left - 8,
                y,
                text=f"{tick:.2f}".rstrip("0").rstrip("."),
                anchor="e",
                fill="#444444",
            )
        canvas.create_line(
            left, top, left, height - bottom, fill="#444444", width=1
        )
        canvas.create_line(
            left,
            height - bottom,
            width - right,
            height - bottom,
            fill="#444444",
            width=1,
        )
        canvas.create_text(
            left,
            height - 12,
            text="较早",
            anchor="w",
            fill="#555555",
        )
        canvas.create_text(
            width - right,
            height - 12,
            text="当前",
            anchor="e",
            fill="#555555",
        )
        canvas.create_line(
            width - 185, 12, width - 155, 12, fill="#0072B2", width=2
        )
        canvas.create_text(
            width - 150, 12, text="μ1", anchor="w", fill="#0072B2"
        )
        canvas.create_line(
            width - 105, 12, width - 75, 12, fill="#D55E00", width=2
        )
        canvas.create_text(
            width - 70, 12, text="μ2", anchor="w", fill="#D55E00"
        )

        mu1_values, mu2_values = self.history.values()
        if not mu1_values:
            canvas.create_text(
                left + plot_width / 2,
                top + plot_height / 2,
                text="尚无采样数据",
                fill="#777777",
            )
            return

        def points(values: list[float]) -> list[float]:
            result: list[float] = []
            denominator = max(len(values) - 1, 1)
            for index, value in enumerate(values):
                x = left + index * plot_width / denominator
                clamped = min(max(value, 0.0), 1.0)
                y = top + (1.0 - clamped) * plot_height
                result.extend((x, y))
            return result

        if len(mu1_values) == 1:
            x = left + plot_width
            for value, color in (
                (mu1_values[0], "#0072B2"),
                (mu2_values[0], "#D55E00"),
            ):
                y = top + (1.0 - value) * plot_height
                canvas.create_oval(
                    x - 2, y - 2, x + 2, y + 2, fill=color, outline=color
                )
        else:
            canvas.create_line(
                *points(mu1_values),
                fill="#0072B2",
                width=2,
            )
            canvas.create_line(
                *points(mu2_values),
                fill="#D55E00",
                width=2,
            )

    def _schedule_background_read(self) -> None:
        if self.statistics_running:
            self.after(200, self._schedule_background_read)
            return
        if self.submission_waiting:
            self.after(100, self._schedule_background_read)
            return
        if self.operation is not None and not self.operation.done():
            self.after(100, self._schedule_background_read)
            return
        if self.transport is None:
            self.after(1000, self._schedule_background_read)
            return

        selected_tab = self.notebook.select()
        if selected_tab == str(self.sampling_tab) and self.live_var.get():
            future = self.executor.submit(
                self.transport.read_live_sample
            )
            self.operation = future
            self._finish_live_read(future)
            return
        if selected_tab == str(self.sar_tab):
            future = self.executor.submit(
                self.transport.read_sar_status
            )
            self.operation = future
            self._finish_sar_read(future)
            return
        self.after(200, self._schedule_background_read)

    def _finish_live_read(self, future: Future) -> None:
        if not future.done():
            self.after(50, self._finish_live_read, future)
            return
        try:
            snapshot: LiveSample = future.result()
            self._show_mu_sample(snapshot.sample)
            self.active_filter_samples_var.set(
                str(snapshot.total_filter_samples)
            )
            if snapshot.sequence != self.last_live_sequence:
                self.history.append(snapshot.sample)
                self.last_live_sequence = snapshot.sequence
                self._redraw_chart()
        except Exception as exc:
            self.status_var.set(f"实时读取已暂停：{exc}")
            self.live_var.set(False)
        self.after(80, self._schedule_background_read)

    def _finish_sar_read(self, future: Future) -> None:
        if not future.done():
            self.after(50, self._finish_sar_read, future)
            return
        try:
            status: SarStatus = future.result()
            self._show_sar_status(status)
            if status.fpga_error:
                self.status_var.set(
                    "FPGA存在粘滞错误"
                    f"0x{status.fpga_error:02X}；重新启动SAR将自动清除"
                )
        except Exception as exc:
            self.status_var.set(f"SAR状态读取失败：{exc}")
        self.after(800, self._schedule_background_read)

    def _set_sar_decisions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.sar_low_button.configure(state=state)
        self.sar_high_button.configure(state=state)

    def _show_sar_status(self, status: SarStatus) -> None:
        self.sar_var.set(f"{status.code:.6f}")
        if status.busy:
            state_text = "运行中"
        elif status.done:
            state_text = "已完成"
        else:
            state_text = "未启动"
        self.sar_status_var.set(
            f"{state_text}；已完成判决：{status.step} / 16"
        )
        self._set_sar_decisions_enabled(
            status.busy and not status.done and status.step < 16
        )

    def _read_instrument_frequency(self) -> None:
        def work() -> float:
            measured = float(
                self.frequency_backend.read_frequency_mhz()
            )
            if not math.isfinite(measured) or measured <= 0:
                raise RuntimeError(
                    "仪器返回的频率必须是大于0的有限数值"
                )
            return measured

        def show_result(measured: float) -> None:
            self.measured_frequency_var.set(f"{measured:.9g} MHz")

        self._submit("正在读取专业频率测试仪器…", work, show_result)

    def _sar_start(self) -> None:
        try:
            target = parse_target_frequency(
                self.target_frequency_var.get()
            )
        except Exception as exc:
            messagebox.showerror("目标频率无效", str(exc))
            return

        self._set_sar_decisions_enabled(False)
        self.sar_status_var.set(
            "启动命令已发送，正在等待2 MHz数字核心确认SAR运行态…"
        )

        def work() -> SarStatus:
            return self._require_transport().start_sar()

        def started(status: SarStatus) -> None:
            self._show_sar_status(status)
            self.status_var.set(
                f"SAR已确认启动，目标频率{target:g} MHz，"
                "首个试探码为0.5"
            )

        def failed(_exc: Exception) -> None:
            self._set_sar_decisions_enabled(False)
            self.sar_status_var.set(
                "启动未确认；外部芯片和FS不是启动条件，"
                "请检查JTAG连接及最新FPGA V2镜像"
            )

        self._submit("正在启动并确认SAR运行态…", work, started, failed)

    def _sar_compare(self, bit: int) -> None:
        try:
            parse_target_frequency(self.target_frequency_var.get())
        except Exception as exc:
            messagebox.showerror("目标频率无效", str(exc))
            return

        meaning = "实测低于目标，增大校准码" if bit else (
            "实测高于目标，减小校准码"
        )

        self._set_sar_decisions_enabled(False)

        def work() -> SarStatus:
            return self._require_transport().submit_sar_decision(bit)

        def completed(status: SarStatus) -> None:
            self._show_sar_status(status)
            self.status_var.set(
                f"SAR判决已确认：{meaning}；"
                f"已完成{status.step} / 16"
            )

        def failed(_exc: Exception) -> None:
            self._set_sar_decisions_enabled(False)

        self._submit("正在提交并确认SAR判决…", work, completed, failed)

    def _save_json(self) -> None:
        try:
            word = self._control_word()
        except Exception as exc:
            messagebox.showerror("控制字无效", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="保存控制字配置",
            defaultextension=".json",
            filetypes=[("JSON配置", "*.json")],
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
            filetypes=[("JSON配置", "*.json")],
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
            widths = {
                "cs": 2,
                "trim_c_coarse": 3,
                "trim_c_fine": 3,
                "trim_r_xo": 4,
                "alpha": 18,
            }
            for key, width in widths.items():
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
        if self.operation is not None and not self.operation.done():
            messagebox.showinfo(
                "操作进行中",
                "当前JTAG操作尚未完成，请等待完成后再关闭窗口。",
            )
            return
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    ControlApp().mainloop()
