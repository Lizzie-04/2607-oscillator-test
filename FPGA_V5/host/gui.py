"""晶振测试 FPGA V5：V4协议兼容的控制、监控与校准界面。"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
from queue import Empty, SimpleQueue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from acquisition import (
    AtomicDsmScanCsv,
    MultiTemperatureCsvRun,
    MultiTemperatureSarRow,
    AtomicSarCalibrationCsv,
    AtomicStatisticsCsv,
    CalibrationTrendHistory,
    DEFAULT_DATA_COUNT,
    TREND_HISTORY_LIMIT,
    TrendHistory,
    calibration_axis_bounds,
    dsm_scan_values,
    run_directory_name,
    statistics_filename,
    validate_data_count,
    validate_dsm_scan_count,
)
from control_word import (
    ControlWord,
    bits_as_text,
    parse_alpha_decimal,
    parse_coefficient_decimal,
    signed_q16_to_decimal_text,
    unsigned_q16_to_decimal_text,
)
from instrument_backend import (
    FrequencyInstrumentBackend,
    KEYSIGHT_53220A_DEFAULT_GATE_TIME_S,
    KeithleyDmm7510Backend,
    Keysight53220ABackend,
    ResistanceInstrumentBackend,
    UnavailableFrequencyInstrument,
    validate_gate_time_s,
)
from jtag_transport import (
    CalibrationSnapshot,
    DSM_OVERRIDE_SCALE,
    DEFAULT_CONTROL_SAMPLE_COUNT,
    DsmOverrideStatus,
    FILTER_TOTAL_SAMPLES_DEFAULT,
    LiveSample,
    MonitorSnapshot,
    MuSample,
    QuartusJtagTransport,
    SAR_STEPS,
    SarStatus,
    dsm_signed_to_twos,
    validate_dsm_override_raw,
    validate_control_sample_count,
    validate_filter_total_samples,
)


STATISTICS_BATCH_SIZE = 100
DSM_SCAN_SETTLE_TIME_S = 5.0
SAR_SETTLE_TIME_S = 5.0
SETTLE_DASHBOARD_UPDATE_TIME_S = 2.0
DEFAULT_CHIP_NUMBER = 2
MULTI_TEMPERATURE_DEFAULT_STEM = "multi_temperature_calibration"
MULTI_SAR_MODE_REGULAR = "regular"
MULTI_SAR_MODE_DSM_OVERRIDE = "dsm_override"
DEFAULT_CONTROL_FLAGS = {
    "bypass": 1,
    "en_bias": 1,
    "en_dllbuf": 0,
    "en_intbuf": 0,
    "en_sensor": 1,
    "en_xo": 1,
    "en_xobufa": 0,
    "su": 1,
    "polarity": 0,
}

MULTI_DEFAULT_CONTROL_FIELDS = {
    "cs": "10",
    "trim_c_fine": "111",
    "trim_r_xo": "1000",
    "alpha": "0.0",
}


@dataclass(frozen=True, slots=True)
class MultiTemperatureSettings:
    temperature_points: int
    wait_seconds: float
    test_seconds: float
    chips: tuple[int, ...]
    target_frequencies: dict[int, float]
    trim_c_coarse_values: tuple[str, str, str]
    sar_mode: str
    output_directory: Path
    file_stem: str


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


def sar_decision_bit(measured_frequency_mhz: float, target_frequency_mhz: float) -> int:
    if not math.isfinite(measured_frequency_mhz) or measured_frequency_mhz <= 0:
        raise ValueError("实测频率必须是大于0的有限数值")
    if not math.isfinite(target_frequency_mhz) or target_frequency_mhz <= 0:
        raise ValueError("目标频率必须是大于0的有限数值")
    return 1 if measured_frequency_mhz < target_frequency_mhz else 0


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


def parse_gate_time(text: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise ValueError("gate time必须是有效数字") from exc
    return validate_gate_time_s(value)


def parse_temperature_point_count(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError as exc:
        raise ValueError("温度点数必须为十进制整数") from exc
    if value <= 0:
        raise ValueError("温度点数必须为正整数")
    return value


def parse_minutes(text: str, name: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise ValueError(f"{name}必须是有效数字") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name}必须是不小于0的有限分钟数")
    return value


def dsm_override_next_bounds(
    low: float,
    high: float,
    trial: float,
    measured_frequency_mhz: float,
    target_frequency_mhz: float,
) -> tuple[float, float]:
    if not low <= trial <= high:
        raise ValueError("DSM二分试探值必须位于当前搜索区间内")
    if not math.isfinite(measured_frequency_mhz) or measured_frequency_mhz <= 0:
        raise ValueError("实测频率必须是大于0的有限数值")
    if not math.isfinite(target_frequency_mhz) or target_frequency_mhz <= 0:
        raise ValueError("目标频率必须是大于0的有限数值")
    if measured_frequency_mhz < target_frequency_mhz:
        return low, trial
    return trial, high


def multi_temperature_chip_trim_order(
    chips: tuple[int, ...],
    trim_c_coarse_values: tuple[str, str, str],
) -> tuple[tuple[int, int, str], ...]:
    result: list[tuple[int, int, str]] = []
    for chip in sorted(chips):
        if not 0 <= chip <= 4:
            raise ValueError("chip编号必须在0至4之间")
        for trim_slot, trim_c_coarse in enumerate(
            trim_c_coarse_values,
            start=1,
        ):
            parse_bit_string(trim_c_coarse, 3, f"TRIM_C_COARSE_{trim_slot}")
            result.append((chip, trim_slot, trim_c_coarse))
    return tuple(result)


def parse_dsm_scan_count(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError as exc:
        raise ValueError("扫描总数必须为十进制整数") from exc
    return validate_dsm_scan_count(value)


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
        self.title("晶振测试 FPGA V5")
        self.geometry("1180x850")
        self.minsize(1000, 720)

        self.transport: QuartusJtagTransport | None = None
        self.transport_startup_error: Exception | None = None
        try:
            self.transport = QuartusJtagTransport()
        except Exception as exc:
            self.transport_startup_error = exc

        self.frequency_backend: FrequencyInstrumentBackend = (
            Keysight53220ABackend(
                gate_time_s=KEYSIGHT_53220A_DEFAULT_GATE_TIME_S
            )
        )
        self.resistance_backend: ResistanceInstrumentBackend = (
            KeithleyDmm7510Backend()
        )
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.frequency_executor = ThreadPoolExecutor(max_workers=1)
        self.operation: Future | None = None
        self.frequency_operation: Future | None = None
        self.submission_waiting = False
        self.statistics_running = False
        self.dsm_scan_running = False
        self.sar_running = False
        self.multi_temperature_running = False
        self.multi_temperature_stop_requested = threading.Event()
        self.statistics_progress: SimpleQueue = SimpleQueue()
        self.dsm_scan_progress: SimpleQueue = SimpleQueue()
        self.sar_progress: SimpleQueue = SimpleQueue()
        self.multi_temperature_progress: SimpleQueue = SimpleQueue()
        self.live_before_statistics = False
        self.live_before_scan = False
        self.calibration_live_before_scan = False
        self.frequency_live_before_scan = False
        self.live_before_sar = False
        self.calibration_live_before_sar = False
        self.frequency_live_before_sar = False
        self.live_before_multi_temperature = False
        self.calibration_live_before_multi_temperature = False
        self.frequency_live_before_multi_temperature = False
        self.monitor_next_read = "live"
        self.last_live_sequence: int | None = None
        self.last_calibration_sequence: int | None = None
        self.history = TrendHistory(TREND_HISTORY_LIMIT)
        self.calibration_history = CalibrationTrendHistory(
            TREND_HISTORY_LIMIT
        )
        self.multi_frequency_history: deque[float] = deque(
            maxlen=TREND_HISTORY_LIMIT
        )
        self.multi_mu_history = TrendHistory(TREND_HISTORY_LIMIT)
        self.multi_calibration_history = CalibrationTrendHistory(
            TREND_HISTORY_LIMIT
        )

        self.field_vars: dict[str, tk.StringVar] = {}
        self.flag_vars: dict[str, tk.IntVar] = {}
        self.status_var = tk.StringVar(value="就绪")
        self.connection_var = tk.StringVar(value="尚未检查连接")
        self.mu1_var = tk.StringVar(value="—")
        self.mu2_var = tk.StringVar(value="—")
        self.onchip_mu1_var = tk.StringVar(value="—")
        self.onchip_mu2_var = tk.StringVar(value="—")
        self.active_filter_samples_var = tk.StringVar(value="—")
        self.sar_var = tk.StringVar(value="—")
        self.sar_status_var = tk.StringVar(value="尚未开始SAR校准")
        self.measured_frequency_var = tk.StringVar(
            value="— MHz（53220A自动检索）"
        )
        self.frequency_status_var = tk.StringVar(
            value="53220A自动检索；gate time=5 s"
        )
        self.gate_time_s = KEYSIGHT_53220A_DEFAULT_GATE_TIME_S
        self.gate_time_var = tk.StringVar(value="5 s")
        self.target_frequency_var = tk.StringVar(value="")
        self.frequency_live_var = tk.BooleanVar(value=True)
        self.live_var = tk.BooleanVar(value=True)
        self.chip_var = tk.IntVar(value=DEFAULT_CHIP_NUMBER)
        self.broadcast_var = tk.BooleanVar(value=False)
        self.drive_sample_count_var = tk.StringVar(
            value=str(DEFAULT_CONTROL_SAMPLE_COUNT)
        )
        self.filter_total_samples_var = tk.StringVar(
            value=str(FILTER_TOTAL_SAMPLES_DEFAULT)
        )
        self.dsm_override_value_var = tk.StringVar(value="0.0")
        self.dsm_override_status_var = tk.StringVar(value="尚未读取")
        self.calibration_poly_freq_var = tk.StringVar(value="—")
        self.calibration_poly_system_var = tk.StringVar(value="—")
        self.calibration_sar_correction_var = tk.StringVar(value="—")
        self.calibration_dsm_input_var = tk.StringVar(value="—")
        self.calibration_dsm_state_var = tk.StringVar(value="等待校准链路数据")
        self.calibration_live_var = tk.BooleanVar(value=True)
        self.data_count_var = tk.StringVar(value=str(DEFAULT_DATA_COUNT))
        self.file_stem_var = tk.StringVar(value="mu")
        self.dsm_scan_count_var = tk.StringVar(value="11")
        self.dsm_scan_file_stem_var = tk.StringVar(value="dsm_scan")
        self.dsm_scan_progress_var = tk.StringVar(value="尚未开始扫描")
        self.sar_data_count_var = tk.StringVar(value=str(DEFAULT_DATA_COUNT))
        self.sar_file_stem_var = tk.StringVar(value="sar_calibration")
        default_output_directory = (
            Path(__file__).resolve().parents[1] / "data" / "statistics"
        )
        self.output_directory_var = tk.StringVar(
            value=str(default_output_directory)
        )
        self.statistics_progress_var = tk.StringVar(value="尚未开始统计")
        self.temperature_point_count_var = tk.StringVar(value="1")
        self.temperature_wait_minutes_var = tk.StringVar(value="0")
        self.temperature_test_minutes_var = tk.StringVar(value="0")
        self.multi_chip_vars = [
            tk.BooleanVar(value=(chip == DEFAULT_CHIP_NUMBER))
            for chip in range(5)
        ]
        self.multi_target_frequency_vars = [
            tk.StringVar(value="") for _chip in range(5)
        ]
        self.multi_trim_coarse_vars = [
            tk.StringVar(value="000") for _index in range(3)
        ]
        self.multi_sar_mode_var = tk.StringVar(
            value=MULTI_SAR_MODE_REGULAR
        )
        self.multi_file_stem_var = tk.StringVar(
            value=MULTI_TEMPERATURE_DEFAULT_STEM
        )
        self.multi_output_directory_var = tk.StringVar(
            value=str(default_output_directory)
        )
        self.multi_temperature_status_var = tk.StringVar(
            value="尚未开始多温度点校准"
        )
        self.multi_temperature_output_var = tk.StringVar(value="—")
        self.multi_temperature_thermistor_var = tk.StringVar(value="—")
        self.multi_temperature_frequency_var = tk.StringVar(value="— MHz")
        self.multi_mu1_var = tk.StringVar(value="—")
        self.multi_mu2_var = tk.StringVar(value="—")
        self.multi_onchip_mu1_var = tk.StringVar(value="—")
        self.multi_onchip_mu2_var = tk.StringVar(value="—")
        self.multi_poly_freq_var = tk.StringVar(value="—")
        self.multi_poly_system_var = tk.StringVar(value="—")
        self.multi_sar_correction_var = tk.StringVar(value="—")
        self.multi_dsm_input_var = tk.StringVar(value="—")
        self.multi_default_flag_vars: dict[str, tk.IntVar] = {
            key: tk.IntVar(value=value)
            for key, value in DEFAULT_CONTROL_FLAGS.items()
        }
        self.multi_default_field_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value=value)
            for key, value in MULTI_DEFAULT_CONTROL_FIELDS.items()
        }
        self.multi_default_batch_vars = [
            tk.StringVar(value="0.0") for _index in range(7)
        ]
        self.multi_default_poly_vars = [
            tk.StringVar(value="0.0") for _index in range(8)
        ]

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(250, self._check_connection)
        self.after(200, self._schedule_background_read)
        self.after(500, self._schedule_frequency_read)
        self.after(100, self._drain_statistics_progress)
        self.after(100, self._drain_dsm_scan_progress)
        self.after(100, self._drain_sar_progress)
        self.after(100, self._drain_multi_temperature_progress)

    def _build_ui(self) -> None:
        self.status_bar = ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
        )
        self.status_bar.pack(fill="x", side="bottom")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.connection_tab = ttk.Frame(self.notebook, padding=14)
        self.chip_control_tab = ttk.Frame(self.notebook, padding=10)
        self.monitor_tab = ttk.Frame(self.notebook, padding=10)
        self.calibration_tab = ttk.Frame(self.notebook, padding=10)
        self.test_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.connection_tab, text="FPGA连接")
        self.notebook.add(self.chip_control_tab, text="芯片控制")
        self.notebook.add(self.monitor_tab, text="监控")
        self.notebook.add(self.calibration_tab, text="校准")
        self.notebook.add(self.test_tab, text="多温度点校准")
        self.notebook.bind("<<NotebookTabChanged>>", self._redraw_visible_dashboard)

        self._build_connection_tab(self.connection_tab)
        self._build_control_tab(self.chip_control_tab)
        self._build_monitor_tab(self.monitor_tab)
        self._build_calibration_tab(self.calibration_tab)
        self._build_test_tab(self.test_tab)

    def _redraw_visible_dashboard(self, _event=None) -> None:
        if not hasattr(self, "chart_canvas"):
            return
        selected_tab = self.notebook.select()
        if selected_tab == str(self.monitor_tab):
            self._redraw_chart()
            self._redraw_calibration_chart()
        if selected_tab == str(self.test_tab) and hasattr(
            self,
            "multi_frequency_canvas",
        ):
            self._redraw_multi_frequency_chart()
            self._redraw_multi_mu_chart()
            self._redraw_multi_calibration_chart()

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
            text="加载 FPGA V5 兼容SOF",
            command=self._program_fpga,
        ).grid(row=0, column=2, padx=5)
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text=(
                "连接检查同时验证USB-Blaster、Cyclone V、64-bit ISSP和"
                "V4协议版本0x0004。FPGA_V5沿用V4 RTL/bitstream；"
                "加载SOF前请确认Rev G PCB能够安全接收FPGA输出。"
            ),
            wraplength=1050,
            justify="left",
        ).pack(fill="x", pady=18)

    def _build_control_tab(self, parent: ttk.Frame) -> None:
        switches = ttk.LabelFrame(parent, text="芯片开关", padding=8)
        switches.pack(fill="x")
        flags = [
            ("bypass", "BYPASS", DEFAULT_CONTROL_FLAGS["bypass"]),
            ("en_bias", "EN_BIAS", DEFAULT_CONTROL_FLAGS["en_bias"]),
            ("en_dllbuf", "EN_DLLBUF", DEFAULT_CONTROL_FLAGS["en_dllbuf"]),
            ("en_intbuf", "EN_INTBUF", DEFAULT_CONTROL_FLAGS["en_intbuf"]),
            ("en_sensor", "EN_SENSOR", DEFAULT_CONTROL_FLAGS["en_sensor"]),
            ("en_xo", "EN_XO", DEFAULT_CONTROL_FLAGS["en_xo"]),
            ("en_xobufa", "EN_XOBUFA", DEFAULT_CONTROL_FLAGS["en_xobufa"]),
            ("su", "SU", DEFAULT_CONTROL_FLAGS["su"]),
            ("polarity", "polarity", DEFAULT_CONTROL_FLAGS["polarity"]),
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
        ttk.Label(alpha_frame, text="ALPHA [unsigned Q2.16 十进制]").pack(
            side="left"
        )
        alpha_variable = tk.StringVar(value="1.0")
        self.field_vars["alpha"] = alpha_variable
        ttk.Entry(
            alpha_frame,
            textvariable=alpha_variable,
            width=18,
            font=("Consolas", 10),
        ).pack(side="left", padx=8)

        coefficient_columns = ttk.Frame(coefficients)
        coefficient_columns.pack(fill="both", expand=True)
        batch_frame = ttk.LabelFrame(
            coefficient_columns, text="Batch系数 [signed Q12.16 十进制]", padding=8
        )
        poly_frame = ttk.LabelFrame(
            coefficient_columns,
            text="Polynomial系数 [signed Q12.16 十进制]",
            padding=8,
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

    def _build_monitor_tab(self, parent: ttk.Frame) -> None:
        content = self._build_scrollable_content(parent)
        self._build_frequency_panel(content)
        self._build_mu_dashboard(content)
        self._build_calibration_dashboard(content)

    def _build_scrollable_content(self, parent: ttk.Frame) -> ttk.Frame:
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            parent,
            orient="vertical",
            command=canvas.yview,
        )
        content = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def update_content_width(event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def on_mousewheel(event) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def bind_mousewheel(_event) -> None:
            canvas.bind_all("<MouseWheel>", on_mousewheel)

        def unbind_mousewheel(_event) -> None:
            canvas.unbind_all("<MouseWheel>")

        content.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", update_content_width)
        canvas.bind("<Enter>", bind_mousewheel)
        canvas.bind("<Leave>", unbind_mousewheel)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return content

    def _build_frequency_panel(self, parent: ttk.Frame) -> None:
        measurement = ttk.LabelFrame(
            parent, text="专业仪器实测频率", padding=10
        )
        measurement.pack(fill="x")
        ttk.Label(measurement, text="实测频率：").grid(
            row=0, column=0, sticky="e"
        )
        ttk.Label(
            measurement,
            textvariable=self.measured_frequency_var,
            font=("Consolas", 13, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(
            measurement,
            text="读取仪器",
            command=self._read_instrument_frequency,
        ).grid(row=0, column=2, padx=(16, 0))
        ttk.Checkbutton(
            measurement,
            text="持续读取",
            variable=self.frequency_live_var,
        ).grid(row=0, column=3, padx=(12, 0))
        ttk.Label(measurement, text="gate time：").grid(
            row=0, column=4, sticky="e", padx=(24, 0)
        )
        ttk.Label(
            measurement,
            textvariable=self.gate_time_var,
            font=("Consolas", 11, "bold"),
        ).grid(row=0, column=5, sticky="w", padx=(4, 0))
        ttk.Button(
            measurement,
            text="设置gate time",
            command=self._configure_gate_time,
        ).grid(row=0, column=6, padx=(12, 0))
        ttk.Label(
            measurement,
            textvariable=self.frequency_status_var,
            wraplength=1000,
            justify="left",
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))
        measurement.columnconfigure(1, weight=1)

    def _build_mu_dashboard(self, parent: ttk.Frame) -> None:
        settings = ttk.LabelFrame(
            parent, text="μ看板", padding=10
        )
        settings.pack(fill="x", pady=(8, 0))
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

        ttk.Label(settings, text="当前L：").grid(
            row=0, column=4, padx=(12, 2)
        )
        ttk.Label(
            settings,
            textvariable=self.active_filter_samples_var,
            font=("Consolas", 12, "bold"),
        ).grid(row=0, column=5, sticky="w")

        metrics = [
            ("μ1", self.mu1_var),
            ("μ2", self.mu2_var),
            ("片内μ1", self.onchip_mu1_var),
            ("片内μ2", self.onchip_mu2_var),
        ]
        for index, (label, variable) in enumerate(metrics):
            column = index * 2
            ttk.Label(settings, text=f"{label}：").grid(
                row=1, column=column, padx=(0 if index == 0 else 12, 2),
                pady=(8, 0),
            )
            ttk.Label(
                settings,
                textvariable=variable,
                font=("Consolas", 12, "bold"),
            ).grid(row=1, column=column + 1, sticky="w", pady=(8, 0))

        chart_frame = ttk.LabelFrame(
            parent,
            text="μ1 / μ2 / 片内μ1 / 片内μ2 最近1000点趋势",
            padding=6,
        )
        chart_frame.pack(fill="both", expand=True, pady=8)
        self.chart_canvas = tk.Canvas(
            chart_frame,
            background="white",
            highlightthickness=0,
            height=250,
        )
        self.chart_canvas.pack(fill="both", expand=True)
        self.chart_canvas.bind(
            "<Configure>", lambda _event: self._redraw_chart()
        )

    def _build_calibration_dashboard(self, parent: ttk.Frame) -> None:
        metrics_frame = ttk.LabelFrame(
            parent,
            text="校准链路看板",
            padding=10,
        )
        metrics_frame.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(
            metrics_frame,
            text="实时监控",
            variable=self.calibration_live_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 16))
        ttk.Button(
            metrics_frame,
            text="清空看板",
            command=self._clear_calibration_dashboard,
        ).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(
            metrics_frame,
            textvariable=self.calibration_dsm_state_var,
            wraplength=850,
            justify="left",
        ).grid(row=0, column=2, columnspan=6, sticky="w")

        metrics = [
            ("poly_engine_freq", self.calibration_poly_freq_var),
            ("poly_engine_system", self.calibration_poly_system_var),
            ("SAR校准输入", self.calibration_sar_correction_var),
            ("DSM实际输入", self.calibration_dsm_input_var),
        ]
        for index, (label, variable) in enumerate(metrics):
            column = index * 2
            ttk.Label(metrics_frame, text=f"{label}：").grid(
                row=1,
                column=column,
                sticky="e",
                padx=(0 if index == 0 else 14, 4),
                pady=(8, 0),
            )
            ttk.Label(
                metrics_frame,
                textvariable=variable,
                font=("Consolas", 11, "bold"),
            ).grid(row=1, column=column + 1, sticky="w", pady=(8, 0))
            metrics_frame.columnconfigure(column + 1, weight=1)

        chart_frame = ttk.LabelFrame(
            parent,
            text="校准链路最近1000点趋势（虚线为DSM ±0.5端点）",
            padding=6,
        )
        chart_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.calibration_chart_canvas = tk.Canvas(
            chart_frame,
            background="white",
            highlightthickness=0,
            height=220,
        )
        self.calibration_chart_canvas.pack(fill="both", expand=True)
        self.calibration_chart_canvas.bind(
            "<Configure>",
            lambda _event: self._redraw_calibration_chart(),
        )

    def _build_statistics_panel(self, parent: ttk.Frame) -> None:
        statistics = ttk.LabelFrame(parent, text="统计μ值", padding=10)
        statistics.pack(fill="x", pady=(10, 0))
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

    def _build_calibration_tab(self, parent: ttk.Frame) -> None:
        self._build_dsm_override_panel(parent)
        self._build_statistics_panel(parent)
        self._build_dsm_scan_panel(parent)
        self._build_sar_panel(parent)

    def _build_test_tab(self, parent: ttk.Frame) -> None:
        content = self._build_scrollable_content(parent)

        flow = ttk.LabelFrame(content, text="流程设置", padding=10)
        flow.pack(fill="x")
        ttk.Label(flow, text="温度点数").grid(row=0, column=0, sticky="e")
        self.temperature_point_count_entry = ttk.Entry(
            flow,
            textvariable=self.temperature_point_count_var,
            width=8,
        )
        self.temperature_point_count_entry.grid(row=0, column=1, padx=(5, 18))
        ttk.Label(flow, text="等待时间").grid(row=0, column=2, sticky="e")
        self.temperature_wait_entry = ttk.Entry(
            flow,
            textvariable=self.temperature_wait_minutes_var,
            width=8,
        )
        self.temperature_wait_entry.grid(row=0, column=3, padx=(5, 3))
        ttk.Label(flow, text="分钟").grid(row=0, column=4, sticky="w")
        ttk.Label(flow, text="测试时间").grid(
            row=0,
            column=5,
            sticky="e",
            padx=(18, 0),
        )
        self.temperature_test_entry = ttk.Entry(
            flow,
            textvariable=self.temperature_test_minutes_var,
            width=8,
        )
        self.temperature_test_entry.grid(row=0, column=6, padx=(5, 3))
        ttk.Label(flow, text="分钟").grid(row=0, column=7, sticky="w")
        ttk.Label(flow, text="文件名").grid(
            row=1,
            column=0,
            sticky="e",
            pady=(10, 0),
        )
        self.multi_file_stem_entry = ttk.Entry(
            flow,
            textvariable=self.multi_file_stem_var,
            width=24,
        )
        self.multi_file_stem_entry.grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(5, 18),
            pady=(10, 0),
        )
        ttk.Label(flow, text="保存目录").grid(
            row=1,
            column=3,
            sticky="e",
            pady=(10, 0),
        )
        self.multi_output_directory_entry = ttk.Entry(
            flow,
            textvariable=self.multi_output_directory_var,
            width=48,
        )
        self.multi_output_directory_entry.grid(
            row=1,
            column=4,
            columnspan=3,
            sticky="ew",
            padx=(5, 8),
            pady=(10, 0),
        )
        self.multi_output_directory_button = ttk.Button(
            flow,
            text="选择目录",
            command=self._choose_multi_temperature_directory,
        )
        self.multi_output_directory_button.grid(
            row=1,
            column=7,
            sticky="w",
            pady=(10, 0),
        )
        flow.columnconfigure(2, weight=1)
        flow.columnconfigure(6, weight=1)

        matrix = ttk.LabelFrame(
            content,
            text="待校准芯片与目标频率",
            padding=10,
        )
        matrix.pack(fill="x", pady=(8, 0))
        for chip in range(5):
            checkbox = ttk.Checkbutton(
                matrix,
                text=f"chip{chip}",
                variable=self.multi_chip_vars[chip],
            )
            setattr(self, f"multi_chip_checkbutton_{chip}", checkbox)
            checkbox.grid(row=0, column=chip * 3, sticky="w", padx=(0, 4))
            ttk.Label(matrix, text=f"目标频率{chip}").grid(
                row=0,
                column=chip * 3 + 1,
                sticky="e",
                padx=(4, 2),
            )
            entry = ttk.Entry(
                matrix,
                textvariable=self.multi_target_frequency_vars[chip],
                width=12,
            )
            setattr(self, f"multi_target_frequency_entry_{chip}", entry)
            entry.grid(row=0, column=chip * 3 + 2, sticky="w", padx=(0, 12))
        ttk.Label(matrix, text="MHz").grid(row=0, column=15, sticky="w")

        trims = ttk.LabelFrame(content, text="TRIM_C_COARSE遍历", padding=10)
        trims.pack(fill="x", pady=(8, 0))
        for index, variable in enumerate(self.multi_trim_coarse_vars, start=1):
            ttk.Label(trims, text=f"TRIM_C_COARSE_{index}").grid(
                row=0,
                column=(index - 1) * 2,
                sticky="e",
                padx=(0 if index == 1 else 16, 4),
            )
            entry = ttk.Entry(
                trims,
                textvariable=variable,
                width=8,
                font=("Consolas", 10),
            )
            setattr(self, f"multi_trim_coarse_entry_{index}", entry)
            entry.grid(row=0, column=(index - 1) * 2 + 1, sticky="w")

        mode = ttk.LabelFrame(content, text="SAR模式", padding=10)
        mode.pack(fill="x", pady=(8, 0))
        self.multi_regular_radio = ttk.Radiobutton(
            mode,
            text="常规SAR校准",
            variable=self.multi_sar_mode_var,
            value=MULTI_SAR_MODE_REGULAR,
        )
        self.multi_regular_radio.pack(side="left", padx=(0, 20))
        self.multi_dsm_override_radio = ttk.Radiobutton(
            mode,
            text="DSM覆盖SAR校准",
            variable=self.multi_sar_mode_var,
            value=MULTI_SAR_MODE_DSM_OVERRIDE,
        )
        self.multi_dsm_override_radio.pack(side="left")

        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(10, 0))
        self.multi_default_config_button = ttk.Button(
            actions,
            text="默认配置",
            command=self._open_multi_default_config,
        )
        self.multi_default_config_button.pack(side="left")
        self.multi_temperature_start_button = ttk.Button(
            actions,
            text="开始",
            command=self._start_multi_temperature_calibration,
        )
        self.multi_temperature_start_button.pack(side="left", padx=(8, 0))
        self.multi_temperature_stop_button = ttk.Button(
            actions,
            text="停止",
            command=self._stop_multi_temperature_calibration,
            state="disabled",
        )
        self.multi_temperature_stop_button.pack(side="left", padx=(8, 0))
        ttk.Label(
            actions,
            textvariable=self.multi_temperature_output_var,
        ).pack(side="left", padx=(16, 0))

        ttk.Label(
            content,
            textvariable=self.multi_temperature_status_var,
            wraplength=1050,
            justify="left",
        ).pack(fill="x", pady=(8, 0))

        self._build_multi_temperature_dashboards(content)

    def _build_multi_temperature_dashboards(self, parent: ttk.Frame) -> None:
        frequency = ttk.LabelFrame(parent, text="实测频率看板", padding=10)
        frequency.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(frequency, text="最近实测频率：").pack(side="top", anchor="w")
        ttk.Label(
            frequency,
            textvariable=self.multi_temperature_frequency_var,
            font=("Consolas", 12, "bold"),
        ).pack(side="top", anchor="w", pady=(2, 6))
        self.multi_frequency_canvas = tk.Canvas(
            frequency,
            background="white",
            highlightthickness=0,
            height=170,
        )
        self.multi_frequency_canvas.pack(fill="both", expand=True)
        self.multi_frequency_canvas.bind(
            "<Configure>",
            lambda _event: self._redraw_multi_frequency_chart(),
        )

        mu = ttk.LabelFrame(parent, text="μ1 / μ2看板", padding=10)
        mu.pack(fill="both", expand=True, pady=(10, 0))
        metrics = [
            ("μ1", self.multi_mu1_var),
            ("μ2", self.multi_mu2_var),
            ("片内μ1", self.multi_onchip_mu1_var),
            ("片内μ2", self.multi_onchip_mu2_var),
        ]
        for index, (label, variable) in enumerate(metrics):
            ttk.Label(mu, text=f"{label}：").grid(
                row=0,
                column=index * 2,
                sticky="e",
                padx=(0 if index == 0 else 12, 2),
            )
            ttk.Label(
                mu,
                textvariable=variable,
                font=("Consolas", 11, "bold"),
            ).grid(row=0, column=index * 2 + 1, sticky="w")
        self.multi_mu_canvas = tk.Canvas(
            mu,
            background="white",
            highlightthickness=0,
            height=210,
        )
        self.multi_mu_canvas.grid(
            row=1,
            column=0,
            columnspan=8,
            sticky="nsew",
            pady=(8, 0),
        )
        self.multi_mu_canvas.bind(
            "<Configure>",
            lambda _event: self._redraw_multi_mu_chart(),
        )
        mu.columnconfigure(7, weight=1)
        mu.rowconfigure(1, weight=1)

        calibration = ttk.LabelFrame(
            parent,
            text="校准链路看板",
            padding=10,
        )
        calibration.pack(fill="both", expand=True, pady=(10, 0))
        metrics = [
            ("poly_engine_freq", self.multi_poly_freq_var),
            ("poly_engine_system", self.multi_poly_system_var),
            ("SAR校准输入", self.multi_sar_correction_var),
            ("DSM实际输入", self.multi_dsm_input_var),
            ("温敏电阻", self.multi_temperature_thermistor_var),
        ]
        for index, (label, variable) in enumerate(metrics):
            ttk.Label(calibration, text=f"{label}：").grid(
                row=index // 3,
                column=(index % 3) * 2,
                sticky="e",
                padx=(0 if index % 3 == 0 else 12, 2),
                pady=(0 if index < 3 else 6, 0),
            )
            ttk.Label(
                calibration,
                textvariable=variable,
                font=("Consolas", 11, "bold"),
            ).grid(
                row=index // 3,
                column=(index % 3) * 2 + 1,
                sticky="w",
                pady=(0 if index < 3 else 6, 0),
            )
        self.multi_calibration_canvas = tk.Canvas(
            calibration,
            background="white",
            highlightthickness=0,
            height=210,
        )
        self.multi_calibration_canvas.grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="nsew",
            pady=(8, 0),
        )
        self.multi_calibration_canvas.bind(
            "<Configure>",
            lambda _event: self._redraw_multi_calibration_chart(),
        )
        calibration.columnconfigure(5, weight=1)
        calibration.rowconfigure(2, weight=1)

    def _open_multi_default_config(self) -> None:
        window = tk.Toplevel(self)
        window.title("多温度点校准默认配置")
        window.transient(self)
        window.geometry("760x620")

        content = self._build_scrollable_content(window)
        switches = ttk.LabelFrame(content, text="芯片开关", padding=10)
        switches.pack(fill="x")
        for column, key in enumerate(DEFAULT_CONTROL_FLAGS):
            ttk.Checkbutton(
                switches,
                text=key.upper(),
                variable=self.multi_default_flag_vars[key],
            ).grid(row=0, column=column, sticky="w", padx=5)

        fields = ttk.LabelFrame(content, text="选通与TRIM", padding=10)
        fields.pack(fill="x", pady=(8, 0))
        field_specs = [
            ("cs", "CS [2-bit]", 2),
            ("trim_c_fine", "TRIM_C_FINE [3-bit]", 3),
            ("trim_r_xo", "TRIM_R_XO [4-bit]", 4),
            ("alpha", "ALPHA [unsigned Q2.16]", 18),
        ]
        for column, (key, label, _width) in enumerate(field_specs):
            ttk.Label(fields, text=label).grid(
                row=0,
                column=column,
                padx=8,
                sticky="w",
            )
            ttk.Entry(
                fields,
                textvariable=self.multi_default_field_vars[key],
                width=18,
                font=("Consolas", 10),
            ).grid(row=1, column=column, padx=8, sticky="ew")

        coefficients = ttk.LabelFrame(content, text="算法系数", padding=10)
        coefficients.pack(fill="both", expand=True, pady=(8, 0))
        batch_frame = ttk.LabelFrame(
            coefficients,
            text="Batch系数 [signed Q12.16 十进制]",
            padding=8,
        )
        poly_frame = ttk.LabelFrame(
            coefficients,
            text="Polynomial系数 [signed Q12.16 十进制]",
            padding=8,
        )
        batch_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))
        poly_frame.pack(side="left", fill="both", expand=True, padx=(4, 0))
        for row, variable in enumerate(self.multi_default_batch_vars):
            ttk.Label(batch_frame, text=f"batch_a{row}").grid(
                row=row,
                column=0,
                sticky="e",
                padx=5,
                pady=3,
            )
            ttk.Entry(
                batch_frame,
                textvariable=variable,
                width=18,
                font=("Consolas", 10),
            ).grid(row=row, column=1, sticky="ew", padx=5, pady=3)
        for row, variable in enumerate(self.multi_default_poly_vars):
            ttk.Label(poly_frame, text=f"poly_a{row}").grid(
                row=row,
                column=0,
                sticky="e",
                padx=5,
                pady=3,
            )
            ttk.Entry(
                poly_frame,
                textvariable=variable,
                width=18,
                font=("Consolas", 10),
            ).grid(row=row, column=1, sticky="ew", padx=5, pady=3)
        batch_frame.columnconfigure(1, weight=1)
        poly_frame.columnconfigure(1, weight=1)

        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(
            buttons,
            text="恢复默认值",
            command=self._reset_multi_default_config,
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="关闭",
            command=window.destroy,
        ).pack(side="right")

    def _reset_multi_default_config(self) -> None:
        for key, value in DEFAULT_CONTROL_FLAGS.items():
            self.multi_default_flag_vars[key].set(value)
        for key, value in MULTI_DEFAULT_CONTROL_FIELDS.items():
            self.multi_default_field_vars[key].set(value)
        for variable in self.multi_default_batch_vars:
            variable.set("0.0")
        for variable in self.multi_default_poly_vars:
            variable.set("0.0")
        self.status_var.set("多温度默认配置已恢复")

    def _choose_multi_temperature_directory(self) -> None:
        selected = filedialog.askdirectory(
            title="选择多温度校准保存目录",
            initialdir=self.multi_output_directory_var.get(),
        )
        if selected:
            self.multi_output_directory_var.set(selected)

    def _parse_multi_temperature_settings(self) -> MultiTemperatureSettings:
        temperature_points = parse_temperature_point_count(
            self.temperature_point_count_var.get()
        )
        wait_seconds = parse_minutes(
            self.temperature_wait_minutes_var.get(),
            "等待时间",
        ) * 60.0
        test_seconds = parse_minutes(
            self.temperature_test_minutes_var.get(),
            "测试时间",
        ) * 60.0
        chips = tuple(
            chip for chip, variable in enumerate(self.multi_chip_vars)
            if variable.get()
        )
        if not chips:
            raise ValueError("请至少勾选一个待校准芯片")
        target_frequencies: dict[int, float] = {}
        for chip in chips:
            target_frequencies[chip] = parse_target_frequency(
                self.multi_target_frequency_vars[chip].get()
            )
        trim_values = tuple(
            f"{parse_bit_string(variable.get(), 3, f'TRIM_C_COARSE_{index}'):03b}"
            for index, variable in enumerate(
                self.multi_trim_coarse_vars,
                start=1,
            )
        )
        if len(trim_values) != 3:
            raise ValueError("必须提供3个TRIM_C_COARSE码字")
        sar_mode = self.multi_sar_mode_var.get()
        if sar_mode not in (
            MULTI_SAR_MODE_REGULAR,
            MULTI_SAR_MODE_DSM_OVERRIDE,
        ):
            raise ValueError("SAR模式无效")
        output_text = self.multi_output_directory_var.get().strip()
        if not output_text:
            raise ValueError("保存目录不能为空")
        output_directory = Path(output_text).expanduser().resolve()
        file_stem = self.multi_file_stem_var.get()
        run_name = run_directory_name(file_stem, datetime.now())
        if (output_directory / run_name).exists():
            raise FileExistsError(
                f"多温度运行目录已存在：{output_directory / run_name}"
            )
        return MultiTemperatureSettings(
            temperature_points=temperature_points,
            wait_seconds=wait_seconds,
            test_seconds=test_seconds,
            chips=chips,
            target_frequencies=target_frequencies,
            trim_c_coarse_values=trim_values,
            sar_mode=sar_mode,
            output_directory=output_directory,
            file_stem=file_stem,
        )

    def _multi_default_control_word(
        self,
        trim_c_coarse: str,
        *,
        en_xo: bool,
    ) -> ControlWord:
        flags = {
            key: variable.get()
            for key, variable in self.multi_default_flag_vars.items()
        }
        flags["en_xo"] = int(en_xo)
        batch = [
            parse_coefficient_decimal(variable.get(), f"batch_a{index}")
            for index, variable in enumerate(self.multi_default_batch_vars)
        ]
        poly = [
            parse_coefficient_decimal(variable.get(), f"poly_a{index}")
            for index, variable in enumerate(self.multi_default_poly_vars)
        ]
        return ControlWord(
            **flags,
            cs=parse_bit_string(
                self.multi_default_field_vars["cs"].get(),
                2,
                "CS",
            ),
            trim_c_coarse=parse_bit_string(
                trim_c_coarse,
                3,
                "TRIM_C_COARSE",
            ),
            trim_c_fine=parse_bit_string(
                self.multi_default_field_vars["trim_c_fine"].get(),
                3,
                "TRIM_C_FINE",
            ),
            trim_r_xo=parse_bit_string(
                self.multi_default_field_vars["trim_r_xo"].get(),
                4,
                "TRIM_R_XO",
            ),
            alpha=parse_alpha_decimal(
                self.multi_default_field_vars["alpha"].get(),
                "ALPHA",
            ),
            batch=batch,
            poly=poly,
        )

    def _set_multi_temperature_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        widget_names = [
            "temperature_point_count_entry",
            "temperature_wait_entry",
            "temperature_test_entry",
            "multi_file_stem_entry",
            "multi_output_directory_entry",
            "multi_output_directory_button",
            "multi_default_config_button",
            "multi_temperature_start_button",
        ]
        for name in widget_names:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state=state)
        for chip in range(5):
            checkbox = getattr(self, f"multi_chip_checkbutton_{chip}", None)
            if checkbox is not None:
                checkbox.configure(state=state)
            widget = getattr(self, f"multi_target_frequency_entry_{chip}", None)
            if widget is not None:
                widget.configure(state=state)
        for index in range(1, 4):
            widget = getattr(self, f"multi_trim_coarse_entry_{index}", None)
            if widget is not None:
                widget.configure(state=state)
        for widget in (
            getattr(self, "multi_regular_radio", None),
            getattr(self, "multi_dsm_override_radio", None),
        ):
            if widget is not None:
                widget.configure(state=state)
        self.multi_temperature_stop_button.configure(
            state="normal" if self.multi_temperature_running else "disabled"
        )

    def _restore_after_multi_temperature(self) -> None:
        self.multi_temperature_running = False
        self._set_multi_temperature_controls(True)
        self._set_statistics_controls(True)
        self._set_dsm_scan_controls(True)
        self._set_sar_controls(True)
        self.live_var.set(self.live_before_multi_temperature)
        self.calibration_live_var.set(
            self.calibration_live_before_multi_temperature
        )
        self.frequency_live_var.set(
            self.frequency_live_before_multi_temperature
        )

    def _stop_multi_temperature_calibration(self) -> None:
        if not self.multi_temperature_running:
            return
        self.multi_temperature_stop_requested.set()
        self.multi_temperature_status_var.set(
            "已请求停止；当前SAR步骤写入CSV后将安全收尾"
        )

    def _read_thermistor_for_multi(self) -> float | None:
        try:
            return self.resistance_backend.read_resistance_ohm()
        except Exception as exc:
            self.multi_temperature_progress.put(
                ("thermistor_error", str(exc))
            )
            return None

    def _multi_wait_with_stop(
        self,
        seconds: float,
        *,
        stage: str,
        temp_index: int,
    ) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if self.multi_temperature_stop_requested.is_set():
                return False
            self.multi_temperature_progress.put(
                ("wait", stage, temp_index, max(0.0, remaining), seconds)
            )
            time.sleep(min(1.0, remaining))

    def _write_multi_temperature_control_word(
        self,
        transport: QuartusJtagTransport,
        *,
        chip: int,
        trim_c_coarse: str,
        en_xo: bool,
    ) -> None:
        word = self._multi_default_control_word(
            trim_c_coarse,
            en_xo=en_xo,
        )
        transport.configure_and_start(
            word.words16(),
            chip,
            control_sample_count=DEFAULT_CONTROL_SAMPLE_COUNT,
        )

    def _record_multi_temperature_step(
        self,
        transport: QuartusJtagTransport,
        writer,
        *,
        settings: MultiTemperatureSettings,
        temp_index: int,
        chip: int,
        trim_c_coarse: str,
        sar_step: int,
        frequency_mhz: float,
        dsm_q16: float | None = None,
    ) -> MultiTemperatureSarRow:
        thermistor_ohm = self._read_thermistor_for_multi()
        snapshot = transport.read_calibration_snapshot()
        samples = transport.capture_statistics_batch()
        row = MultiTemperatureSarRow(
            temp_index=temp_index,
            chip=chip,
            target_frequency_mhz=settings.target_frequencies[chip],
            trim_c_coarse=trim_c_coarse,
            sar_mode=settings.sar_mode,
            sar_step=sar_step,
            thermistor_ohm=thermistor_ohm,
            frequency_mhz=frequency_mhz,
            sar_q16=snapshot.sar_correction,
            dsm_q16=snapshot.dsm_input if dsm_q16 is None else dsm_q16,
            poly_engine_freq=snapshot.poly_freq,
            poly_engine_system=snapshot.poly_system,
            samples=samples,
        )
        writer.append_row(row)
        self.multi_temperature_progress.put(
            ("record", row, snapshot, samples)
        )
        return row

    def _run_regular_multi_temperature_sar(
        self,
        transport: QuartusJtagTransport,
        writer,
        *,
        settings: MultiTemperatureSettings,
        temp_index: int,
        chip: int,
        trim_c_coarse: str,
    ) -> None:
        transport.set_dsm_override(0, enabled=False)
        status = transport.start_sar()
        self.multi_temperature_progress.put(
            ("sar_started", temp_index, chip, trim_c_coarse, status)
        )
        decision_frequency = self.frequency_backend.read_frequency_mhz()
        while status.busy and not status.done and status.step < SAR_STEPS:
            step = status.step + 1
            bit = sar_decision_bit(
                decision_frequency,
                settings.target_frequencies[chip],
            )
            self.multi_temperature_progress.put(
                (
                    "decision",
                    temp_index,
                    chip,
                    trim_c_coarse,
                    step,
                    decision_frequency,
                    bit,
                )
            )
            status = transport.submit_sar_decision(bit)
            self.multi_temperature_progress.put(
                ("settle", temp_index, chip, trim_c_coarse, status.step)
            )
            self._settle_with_dashboard_update(
                transport,
                self.multi_temperature_progress,
                (
                    "multi_settle_dashboard",
                    temp_index,
                    chip,
                    trim_c_coarse,
                    status.step,
                    settings.sar_mode,
                ),
                total_wait_s=SAR_SETTLE_TIME_S,
            )
            measured = self.frequency_backend.read_frequency_mhz()
            self._record_multi_temperature_step(
                transport,
                writer,
                settings=settings,
                temp_index=temp_index,
                chip=chip,
                trim_c_coarse=trim_c_coarse,
                sar_step=status.step,
                frequency_mhz=measured,
            )
            decision_frequency = measured
            if self.multi_temperature_stop_requested.is_set():
                break

    def _run_dsm_override_multi_temperature_sar(
        self,
        transport: QuartusJtagTransport,
        writer,
        *,
        settings: MultiTemperatureSettings,
        temp_index: int,
        chip: int,
        trim_c_coarse: str,
    ) -> None:
        low = -0.5
        high = 0.5
        try:
            for step in range(1, SAR_STEPS + 1):
                trial = (low + high) / 2.0
                raw_value = dsm_signed_to_twos(
                    round(trial * DSM_OVERRIDE_SCALE)
                )
                transport.set_dsm_override(raw_value, enabled=True)
                self.multi_temperature_progress.put(
                    (
                        "dsm_trial",
                        temp_index,
                        chip,
                        trim_c_coarse,
                        step,
                        trial,
                    )
                )
                self._settle_with_dashboard_update(
                    transport,
                    self.multi_temperature_progress,
                    (
                        "multi_settle_dashboard",
                        temp_index,
                        chip,
                        trim_c_coarse,
                        step,
                        settings.sar_mode,
                    ),
                    total_wait_s=SAR_SETTLE_TIME_S,
                )
                measured = self.frequency_backend.read_frequency_mhz()
                self._record_multi_temperature_step(
                    transport,
                    writer,
                    settings=settings,
                    temp_index=temp_index,
                    chip=chip,
                    trim_c_coarse=trim_c_coarse,
                    sar_step=step,
                    frequency_mhz=measured,
                    dsm_q16=trial,
                )
                low, high = dsm_override_next_bounds(
                    low,
                    high,
                    trial,
                    measured,
                    settings.target_frequencies[chip],
                )
                if self.multi_temperature_stop_requested.is_set():
                    break
        finally:
            transport.set_dsm_override(0, enabled=False)

    def _execute_multi_temperature_calibration(
        self,
        settings: MultiTemperatureSettings,
        started_at: datetime,
    ) -> Path:
        transport = self._require_transport()
        run = MultiTemperatureCsvRun(
            settings.output_directory,
            settings.file_stem,
            started_at,
        )
        try:
            for temp_index in range(1, settings.temperature_points + 1):
                if self.multi_temperature_stop_requested.is_set():
                    break
                self.multi_temperature_progress.put(
                    ("temperature", temp_index, settings.temperature_points)
                )
                if not self._multi_wait_with_stop(
                    settings.wait_seconds,
                    stage="等待",
                    temp_index=temp_index,
                ):
                    break
                test_started = time.monotonic()
                for chip in settings.chips:
                    last_trim = settings.trim_c_coarse_values[-1]
                    chip_was_started = False
                    try:
                        for trim_slot, trim_c_coarse in enumerate(
                            settings.trim_c_coarse_values,
                            start=1,
                        ):
                            if self.multi_temperature_stop_requested.is_set():
                                break
                            self.multi_temperature_progress.put(
                                (
                                    "trim_start",
                                    temp_index,
                                    chip,
                                    trim_slot,
                                    trim_c_coarse,
                                )
                            )
                            self._write_multi_temperature_control_word(
                                transport,
                                chip=chip,
                                trim_c_coarse=trim_c_coarse,
                                en_xo=True,
                            )
                            chip_was_started = True
                            last_trim = trim_c_coarse
                            with run.detail_writer(
                                temp_index,
                                chip,
                                trim_c_coarse,
                                trim_slot=trim_slot,
                            ) as writer:
                                if settings.sar_mode == MULTI_SAR_MODE_REGULAR:
                                    self._run_regular_multi_temperature_sar(
                                        transport,
                                        writer,
                                        settings=settings,
                                        temp_index=temp_index,
                                        chip=chip,
                                        trim_c_coarse=trim_c_coarse,
                                    )
                                else:
                                    self._run_dsm_override_multi_temperature_sar(
                                        transport,
                                        writer,
                                        settings=settings,
                                        temp_index=temp_index,
                                        chip=chip,
                                        trim_c_coarse=trim_c_coarse,
                                    )
                                if writer.row_count:
                                    path = writer.publish()
                                    run.add_summary_rows(writer.rows)
                                    self.multi_temperature_progress.put(
                                        ("detail", path, writer.row_count)
                                    )
                            if self.multi_temperature_stop_requested.is_set():
                                break
                    finally:
                        if chip_was_started:
                            self.multi_temperature_progress.put(
                                ("shutdown", temp_index, chip)
                            )
                            self._write_multi_temperature_control_word(
                                transport,
                                chip=chip,
                                trim_c_coarse=last_trim,
                                en_xo=False,
                            )
                    if self.multi_temperature_stop_requested.is_set():
                        break
                elapsed = time.monotonic() - test_started
                remaining = settings.test_seconds - elapsed
                if (
                    remaining > 0
                    and not self.multi_temperature_stop_requested.is_set()
                ):
                    self._multi_wait_with_stop(
                        remaining,
                        stage="测试补足",
                        temp_index=temp_index,
                    )
        except Exception:
            summaries = run.publish_summaries()
            self.multi_temperature_progress.put(("summaries", summaries))
            raise

        summaries = run.publish_summaries()
        self.multi_temperature_progress.put(("summaries", summaries))
        return run.run_directory

    def _start_multi_temperature_calibration(
        self,
        waiting: bool = False,
    ) -> None:
        if self.statistics_running or self.dsm_scan_running or self.sar_running:
            messagebox.showinfo(
                "采集中",
                "请等待统计、DSM扫描或SAR校准完成后再开始多温度点校准",
            )
            return
        if self.multi_temperature_running:
            self.status_var.set("多温度点校准已经在运行")
            return
        if not waiting:
            if self.submission_waiting:
                self.status_var.set(
                    "请等待上一项操作开始或完成后再启动多温度点校准"
                )
                return
            self.live_before_multi_temperature = self.live_var.get()
            self.calibration_live_before_multi_temperature = (
                self.calibration_live_var.get()
            )
            self.frequency_live_before_multi_temperature = (
                self.frequency_live_var.get()
            )
            self.live_var.set(False)
            self.calibration_live_var.set(False)
            self.frequency_live_var.set(False)

        if self.operation is not None and not self.operation.done():
            self.status_var.set(
                "正在结束当前JTAG读取，随后自动开始多温度点校准"
            )
            self.after(
                100,
                lambda: self._start_multi_temperature_calibration(True),
            )
            return
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            self.status_var.set(
                "正在等待当前仪器gate读数完成，随后开始多温度点校准"
            )
            self.after(
                250,
                lambda: self._start_multi_temperature_calibration(True),
            )
            return

        try:
            settings = self._parse_multi_temperature_settings()
            # Validate the configured control word before launching the long run.
            for trim_c_coarse in settings.trim_c_coarse_values:
                self._multi_default_control_word(
                    trim_c_coarse,
                    en_xo=True,
                ).validate()
            started_at = datetime.now()
        except Exception as exc:
            self.live_var.set(self.live_before_multi_temperature)
            self.calibration_live_var.set(
                self.calibration_live_before_multi_temperature
            )
            self.frequency_live_var.set(
                self.frequency_live_before_multi_temperature
            )
            messagebox.showerror("多温度校准设置无效", str(exc))
            return

        try:
            while True:
                self.multi_temperature_progress.get_nowait()
        except Empty:
            pass

        self.multi_frequency_history.clear()
        self.multi_mu_history.clear()
        self.multi_calibration_history.clear()
        self.multi_temperature_stop_requested.clear()
        self.multi_temperature_running = True
        self._set_multi_temperature_controls(False)
        self._set_statistics_controls(False)
        self._set_dsm_scan_controls(False)
        self._set_sar_controls(False)
        self.multi_temperature_status_var.set(
            f"准备执行{settings.temperature_points}个温度点；"
            f"{len(settings.chips)}个chip；SAR模式={settings.sar_mode}"
        )
        self.multi_temperature_output_var.set("正在创建运行目录…")
        self._redraw_multi_frequency_chart()
        self._redraw_multi_mu_chart()
        self._redraw_multi_calibration_chart()

        def work() -> Path:
            return self._execute_multi_temperature_calibration(
                settings,
                started_at,
            )

        def completed(path: Path) -> None:
            stopped = self.multi_temperature_stop_requested.is_set()
            self._restore_after_multi_temperature()
            text = "已停止并保留已完成数据" if stopped else "已完成"
            self.multi_temperature_status_var.set(
                f"多温度点校准{text}；运行目录：{path}"
            )
            self.multi_temperature_output_var.set(str(path))
            messagebox.showinfo("多温度点校准", f"{text}：\n{path}")

        def failed(_exc: Exception) -> None:
            self._restore_after_multi_temperature()
            self.multi_temperature_status_var.set(
                "多温度点校准失败；已发布完成的明细CSV和chip汇总CSV"
            )

        self._submit(
            "正在执行多温度点校准…",
            work,
            completed,
            failed,
        )

    def _build_dsm_override_panel(self, parent: ttk.Frame) -> None:
        override = ttk.LabelFrame(parent, text="数字DSM输入覆盖", padding=14)
        override.pack(fill="x", pady=(10, 0))
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

    def _build_dsm_scan_panel(self, parent: ttk.Frame) -> None:
        scan = ttk.LabelFrame(parent, text="数字DSM扫描功能", padding=14)
        scan.pack(fill="x", pady=(10, 0))
        ttk.Label(scan, text="扫描总数").grid(
            row=0, column=0, sticky="e"
        )
        self.dsm_scan_count_entry = ttk.Entry(
            scan,
            textvariable=self.dsm_scan_count_var,
            width=10,
        )
        self.dsm_scan_count_entry.grid(row=0, column=1, padx=(5, 15))
        ttk.Label(scan, text="文件名").grid(row=0, column=2, sticky="e")
        self.dsm_scan_file_stem_entry = ttk.Entry(
            scan,
            textvariable=self.dsm_scan_file_stem_var,
            width=18,
        )
        self.dsm_scan_file_stem_entry.grid(row=0, column=3, padx=(5, 15))
        ttk.Label(scan, text="保存目录").grid(
            row=0,
            column=4,
            sticky="e",
        )
        self.dsm_scan_output_directory_entry = ttk.Entry(
            scan,
            textvariable=self.output_directory_var,
            width=42,
        )
        self.dsm_scan_output_directory_entry.grid(
            row=0,
            column=5,
            sticky="ew",
            padx=5,
        )
        self.dsm_scan_output_directory_button = ttk.Button(
            scan,
            text="选择目录",
            command=self._choose_statistics_directory,
        )
        self.dsm_scan_output_directory_button.grid(
            row=0,
            column=6,
            padx=5,
        )
        self.dsm_scan_button = ttk.Button(
            scan,
            text="开始扫描",
            command=self._start_dsm_scan,
        )
        self.dsm_scan_button.grid(row=0, column=7, padx=8)
        ttk.Label(
            scan,
            textvariable=self.dsm_scan_progress_var,
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, columnspan=8, sticky="w", pady=(10, 0))
        scan.columnconfigure(5, weight=1)

    def _build_sar_panel(self, parent: ttk.Frame) -> None:
        controls = ttk.LabelFrame(
            parent, text="16步SAR校准", padding=14
        )
        controls.pack(fill="x", pady=(10, 0))
        ttk.Label(controls, text="实测频率").grid(
            row=0, column=0, sticky="e"
        )
        ttk.Label(
            controls,
            textvariable=self.measured_frequency_var,
            font=("Consolas", 12, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(5, 15))
        ttk.Label(controls, text="目标频率").grid(
            row=0, column=2, sticky="e"
        )
        self.sar_target_frequency_entry = ttk.Entry(
            controls,
            textvariable=self.target_frequency_var,
            width=16,
        )
        self.sar_target_frequency_entry.grid(row=0, column=3, padx=(5, 3))
        ttk.Label(controls, text="MHz").grid(row=0, column=4, sticky="w")
        ttk.Label(controls, text="gate time：").grid(
            row=0, column=5, sticky="e", padx=(18, 0)
        )
        ttk.Label(
            controls,
            textvariable=self.gate_time_var,
            font=("Consolas", 11, "bold"),
        ).grid(row=0, column=6, sticky="w", padx=(4, 12))
        self.sar_start_button = ttk.Button(
            controls,
            text="启动SAR校准",
            command=self._start_auto_sar_calibration,
        )
        self.sar_start_button.grid(row=0, column=7, padx=8)

        ttk.Label(controls, text="数据量").grid(
            row=1,
            column=0,
            sticky="e",
            pady=(10, 0),
        )
        self.sar_data_count_entry = ttk.Entry(
            controls,
            textvariable=self.sar_data_count_var,
            width=10,
        )
        self.sar_data_count_entry.grid(
            row=1,
            column=1,
            sticky="w",
            padx=(5, 15),
            pady=(10, 0),
        )
        ttk.Label(controls, text="文件名").grid(
            row=1,
            column=2,
            sticky="e",
            pady=(10, 0),
        )
        self.sar_file_stem_entry = ttk.Entry(
            controls,
            textvariable=self.sar_file_stem_var,
            width=18,
        )
        self.sar_file_stem_entry.grid(
            row=1,
            column=3,
            sticky="w",
            padx=(5, 15),
            pady=(10, 0),
        )
        ttk.Label(controls, text="保存目录").grid(
            row=1,
            column=4,
            sticky="e",
            pady=(10, 0),
        )
        self.sar_output_directory_entry = ttk.Entry(
            controls,
            textvariable=self.output_directory_var,
            width=42,
        )
        self.sar_output_directory_entry.grid(
            row=1,
            column=5,
            columnspan=2,
            sticky="ew",
            padx=5,
            pady=(10, 0),
        )
        self.sar_output_directory_button = ttk.Button(
            controls,
            text="选择目录",
            command=self._choose_statistics_directory,
        )
        self.sar_output_directory_button.grid(
            row=1,
            column=7,
            padx=8,
            pady=(10, 0),
        )
        ttk.Label(controls, textvariable=self.sar_status_var).grid(
            row=2,
            column=0,
            columnspan=8,
            sticky="w",
            pady=(12, 0),
        )
        controls.columnconfigure(5, weight=1)

        ttk.Label(
            parent,
            text=(
                "当前假设校准码增大使频率增大；自动校准按实测频率与目标频率"
                "的大小关系提交16步SAR判决。"
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
        variable = tk.StringVar(value="0.0")
        self.field_vars[key] = variable
        ttk.Entry(
            parent,
            textvariable=variable,
            width=20,
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
            parse_coefficient_decimal(
                self.field_vars[f"batch_a{i}"].get(),
                f"batch_a{i}",
            )
            for i in range(7)
        ]
        poly = [
            parse_coefficient_decimal(
                self.field_vars[f"poly_a{i}"].get(),
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
            alpha=parse_alpha_decimal(
                self.field_vars["alpha"].get(),
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
                self.status_var.set("FPGA_V5界面已连接到V4协议镜像")
            else:
                self.status_var.set("FPGA已连接，但当前镜像不是V4协议0x0004")

        self._submit(
            "正在检查USB-Blaster、FPGA TAP、ISSP与V4协议…",
            self.transport.diagnose,
            show_result,
        )

    def _program_fpga(self) -> None:
        sof_path = (
            Path(__file__).resolve().parents[1]
            / "quartus"
            / "output_files"
            / "fpga_v4.sof"
        )
        confirmed = messagebox.askyesno(
            "加载FPGA_V5兼容SOF",
            "该操作会把V5工程继承的fpga_v4.sof加载到易失性FPGA，并启动"
            "2 MHz CLK_3P3输出。请先确认Rev G PCB可以安全接收"
            "FPGA输出。\n\n是否继续？",
        )
        if not confirmed:
            return
        self.live_var.set(False)

        def work() -> str:
            return self._require_transport().program(sof_path)

        def programmed(_result) -> None:
            self.connection_var.set("FPGA已加载，正在复查V4协议…")
            self.after(500, self._check_connection)

        self._submit("正在加载FPGA_V5兼容SOF…", work, programmed)

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
        if (
            self.statistics_running
            or self.dsm_scan_running
            or self.sar_running
            or self.multi_temperature_running
        ):
            messagebox.showinfo(
                "采集中",
                "采集或校准流程运行期间不能修改Filter长度",
            )
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

    @staticmethod
    def _format_dsm_status(status: DsmOverrideStatus) -> str:
        mode = "覆盖已启用" if status.enabled else "环路已恢复"
        return f"{mode}；Q16={status.q16_value:.6f}"

    def _show_dsm_override_status(
        self,
        status: DsmOverrideStatus,
    ) -> None:
        self.dsm_override_status_var.set(
            self._format_dsm_status(status)
        )

    @staticmethod
    def _format_calibration_value(value: float) -> str:
        return f"{value:.6f}"

    def _show_calibration_snapshot(
        self,
        snapshot: CalibrationSnapshot,
    ) -> None:
        self.calibration_poly_freq_var.set(
            self._format_calibration_value(snapshot.poly_freq)
        )
        self.calibration_poly_system_var.set(
            self._format_calibration_value(snapshot.poly_system)
        )
        self.calibration_sar_correction_var.set(
            self._format_calibration_value(snapshot.sar_correction)
        )
        self.calibration_dsm_input_var.set(
            self._format_calibration_value(snapshot.dsm_input)
        )

        mode = "覆盖模式" if snapshot.override_enabled else "正常环路"
        value = snapshot.dsm_input
        if not snapshot.valid:
            protection = "尚未收到2 MHz域有效数据"
        elif value < -0.5:
            protection = "下溢：看板保留原值，DSM输出强制全0"
        elif value == -0.5:
            protection = "位于-0.5端点，DSM输出全0"
        elif value > 0.5:
            protection = "上溢：看板保留原值，DSM输出强制全1"
        elif value == 0.5:
            protection = "位于+0.5端点，DSM输出全1"
        else:
            protection = "位于有效范围内，DSM按输入值调制占空比"
        self.calibration_dsm_state_var.set(
            f"{mode}；{protection}"
        )

    def _clear_calibration_dashboard(self) -> None:
        self.calibration_history.clear()
        self.last_calibration_sequence = None
        self.calibration_poly_freq_var.set("—")
        self.calibration_poly_system_var.set("—")
        self.calibration_sar_correction_var.set("—")
        self.calibration_dsm_input_var.set("—")
        self.calibration_dsm_state_var.set("等待校准链路数据")
        self._redraw_calibration_chart()
        self.status_var.set("校准看板已清空")

    def _redraw_calibration_chart(self) -> None:
        canvas = self.calibration_chart_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 200)
        left, right, top, bottom = 76, 20, 34, 34
        plot_width = width - left - right
        plot_height = height - top - bottom

        series = self.calibration_history.values()
        labels = (
            ("poly_freq", "#0072B2"),
            ("poly_system", "#D55E00"),
            ("SAR", "#009E73"),
            ("DSM输入", "#7B3294"),
        )
        all_values = [value for values in series for value in values]
        y_min, y_max = calibration_axis_bounds(all_values)

        canvas.create_rectangle(
            left,
            top,
            left + plot_width,
            top + plot_height,
            outline="#666666",
        )

        def y_for(value: float) -> float:
            return top + (y_max - value) * plot_height / (y_max - y_min)

        for tick in range(5):
            value = y_max - tick * (y_max - y_min) / 4
            y = y_for(value)
            canvas.create_line(left - 4, y, left, y, fill="#666666")
            canvas.create_text(
                left - 8,
                y,
                text=f"{value:.3g}",
                anchor="e",
                fill="#444444",
            )

        for reference in (-0.5, 0.5):
            y = y_for(reference)
            canvas.create_line(
                left,
                y,
                left + plot_width,
                y,
                fill="#888888",
                dash=(5, 4),
            )
            canvas.create_text(
                left + 4,
                y - 2,
                text=f"{reference:+.1f}",
                anchor="sw",
                fill="#666666",
            )

        legend_x = left
        for label, color in labels:
            canvas.create_line(
                legend_x,
                15,
                legend_x + 20,
                15,
                fill=color,
                width=2,
            )
            canvas.create_text(
                legend_x + 24,
                15,
                text=label,
                anchor="w",
                fill=color,
            )
            legend_x += 125

        size = self.calibration_history.size
        if not size:
            canvas.create_text(
                left + plot_width / 2,
                top + plot_height / 2,
                text="等待校准数据",
                fill="#777777",
            )
            return

        denominator = max(size - 1, 1)
        for values, (_label, color) in zip(series, labels):
            points: list[float] = []
            for index, value in enumerate(values):
                x = left + index * plot_width / denominator
                points.extend((x, y_for(value)))
            if len(points) == 2:
                x, y = points
                canvas.create_oval(
                    x - 2,
                    y - 2,
                    x + 2,
                    y + 2,
                    fill=color,
                    outline=color,
                )
            else:
                canvas.create_line(*points, fill=color, width=2)

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

    def _settle_with_dashboard_update(
        self,
        transport: QuartusJtagTransport,
        progress_queue: SimpleQueue,
        event_prefix: tuple,
        *,
        total_wait_s: float,
    ) -> None:
        deadline = time.monotonic() + total_wait_s
        first_wait = min(SETTLE_DASHBOARD_UPDATE_TIME_S, total_wait_s)
        if first_wait > 0:
            time.sleep(first_wait)
        try:
            snapshot = transport.read_monitor_snapshot()
        except Exception as exc:
            progress_queue.put((*event_prefix, "dashboard_error", str(exc)))
        else:
            progress_queue.put((*event_prefix, "dashboard", snapshot))

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def _set_dsm_scan_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (
            self.dsm_scan_count_entry,
            self.dsm_scan_file_stem_entry,
            self.dsm_scan_output_directory_entry,
            self.dsm_scan_output_directory_button,
            self.dsm_scan_button,
        ):
            widget.configure(state=state)

    def _start_dsm_scan(self, waiting: bool = False) -> None:
        if self.multi_temperature_running:
            messagebox.showinfo(
                "多温度校准进行中",
                "请等待多温度点校准完成后再开始DSM扫描",
            )
            return
        if self.statistics_running:
            messagebox.showinfo("统计进行中", "请等待统计完成后再开始DSM扫描")
            return
        if self.sar_running:
            messagebox.showinfo(
                "SAR校准进行中",
                "请等待SAR校准完成后再开始DSM扫描",
            )
            return
        if self.dsm_scan_running:
            self.status_var.set("DSM扫描已经在运行")
            return
        if not waiting:
            if self.submission_waiting:
                self.status_var.set(
                    "请等待上一项操作开始或完成后再启动DSM扫描"
                )
                return
            self.live_before_scan = self.live_var.get()
            self.calibration_live_before_scan = self.calibration_live_var.get()
            self.frequency_live_before_scan = self.frequency_live_var.get()
            self.live_var.set(False)
            self.calibration_live_var.set(False)
            self.frequency_live_var.set(False)

        if self.operation is not None and not self.operation.done():
            self.status_var.set("正在结束当前JTAG读取，随后自动开始DSM扫描")
            self.after(100, lambda: self._start_dsm_scan(True))
            return
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            self.status_var.set("正在等待当前仪器gate读数完成，随后自动扫描")
            self.after(250, lambda: self._start_dsm_scan(True))
            return

        try:
            scan_count = parse_dsm_scan_count(
                self.dsm_scan_count_var.get()
            )
            scan_values = dsm_scan_values(scan_count)
            output_text = self.output_directory_var.get().strip()
            if not output_text:
                raise ValueError("保存目录不能为空")
            output_directory = Path(output_text).expanduser().resolve()
            started_at = datetime.now()
            file_stem = self.dsm_scan_file_stem_var.get()
            final_name = statistics_filename(file_stem, started_at)
            if (output_directory / final_name).exists():
                raise FileExistsError(
                    f"扫描文件已存在：{output_directory / final_name}"
                )
        except Exception as exc:
            self.live_var.set(self.live_before_scan)
            self.calibration_live_var.set(self.calibration_live_before_scan)
            self.frequency_live_var.set(self.frequency_live_before_scan)
            messagebox.showerror("DSM扫描设置无效", str(exc))
            return

        self.dsm_scan_running = True
        self._set_dsm_scan_controls(False)
        self._set_statistics_controls(False)
        self._set_sar_controls(False)
        self._set_multi_temperature_controls(False)
        self.dsm_scan_progress_var.set(
            f"准备扫描0 / {scan_count}点；每点稳定5 s后读取频率和100个μ"
        )

        def work() -> Path:
            transport = self._require_transport()
            with AtomicDsmScanCsv(
                output_directory,
                file_stem,
                started_at,
            ) as writer:
                try:
                    for scan_index, value in enumerate(scan_values, start=1):
                        raw_value = dsm_signed_to_twos(
                            round(value * DSM_OVERRIDE_SCALE)
                        )
                        transport.set_dsm_override(
                            raw_value,
                            enabled=True,
                        )
                        self.dsm_scan_progress.put(
                            ("settle", scan_index, scan_count, value)
                        )
                        self._settle_with_dashboard_update(
                            transport,
                            self.dsm_scan_progress,
                            (
                                "settle_dashboard",
                                scan_index,
                                scan_count,
                                value,
                            ),
                            total_wait_s=DSM_SCAN_SETTLE_TIME_S,
                        )
                        measured = self.frequency_backend.read_frequency_mhz()
                        samples = transport.capture_statistics_batch()
                        writer.append_scan_point(
                            scan_index,
                            value,
                            measured,
                            samples,
                        )
                        self.dsm_scan_progress.put(
                            (
                                "scan",
                                scan_index,
                                scan_count,
                                value,
                                measured,
                                samples,
                            )
                        )
                finally:
                    transport.set_dsm_override(0, enabled=False)
                return writer.publish(scan_count)

        def completed(path: Path) -> None:
            self.dsm_scan_running = False
            self._set_dsm_scan_controls(True)
            self._set_statistics_controls(True)
            self._set_sar_controls(True)
            self._set_multi_temperature_controls(True)
            self.live_var.set(self.live_before_scan)
            self.calibration_live_var.set(self.calibration_live_before_scan)
            self.frequency_live_var.set(self.frequency_live_before_scan)
            self.dsm_override_status_var.set("环路已恢复；Q16=0.000000")
            self.dsm_scan_progress_var.set(
                f"DSM扫描完成：{scan_count}点已保存到 {path}"
            )
            messagebox.showinfo("DSM扫描完成", f"CSV已保存：\n{path}")

        def failed(_exc: Exception) -> None:
            self.dsm_scan_running = False
            self._set_dsm_scan_controls(True)
            self._set_statistics_controls(True)
            self._set_sar_controls(True)
            self._set_multi_temperature_controls(True)
            self.live_var.set(self.live_before_scan)
            self.calibration_live_var.set(self.calibration_live_before_scan)
            self.frequency_live_var.set(self.frequency_live_before_scan)
            self.dsm_scan_progress_var.set(
                "DSM扫描失败，临时文件已清理，并已请求恢复数字环路"
            )

        self._submit(
            f"正在执行DSM扫描，共{scan_count}点…",
            work,
            completed,
            failed,
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
        if self.multi_temperature_running:
            messagebox.showinfo(
                "多温度校准进行中",
                "请等待多温度点校准完成后再启动统计",
            )
            return
        if self.dsm_scan_running:
            messagebox.showinfo("DSM扫描进行中", "请等待扫描完成后再启动统计")
            return
        if self.sar_running:
            messagebox.showinfo(
                "SAR校准进行中",
                "请等待SAR校准完成后再启动统计",
            )
            return
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
        self._set_dsm_scan_controls(False)
        self._set_sar_controls(False)
        self._set_multi_temperature_controls(False)
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
            self._set_dsm_scan_controls(True)
            self._set_sar_controls(True)
            self._set_multi_temperature_controls(True)
            self.live_var.set(self.live_before_statistics)
            self.statistics_progress_var.set(
                f"统计完成：{data_count}点已保存到 {path}"
            )
            messagebox.showinfo("统计完成", f"CSV已保存：\n{path}")

        def failed(_exc: Exception) -> None:
            self.statistics_running = False
            self._set_statistics_controls(True)
            self._set_dsm_scan_controls(True)
            self._set_sar_controls(True)
            self._set_multi_temperature_controls(True)
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

    def _drain_dsm_scan_progress(self) -> None:
        try:
            while True:
                event = self.dsm_scan_progress.get_nowait()
                if event[0] == "settle":
                    _kind, scan_index, scan_count, value = event
                    self.dsm_scan_progress_var.set(
                        f"第{scan_index} / {scan_count}点："
                        f"DSM={value:.6f}，正在稳定5 s"
                    )
                    continue
                if event[0] == "settle_dashboard":
                    (
                        _kind,
                        scan_index,
                        scan_count,
                        value,
                        result_kind,
                        result,
                    ) = event
                    if result_kind == "dashboard":
                        self._apply_monitor_snapshot(result)
                        self.dsm_scan_progress_var.set(
                            f"第{scan_index} / {scan_count}点："
                            f"DSM={value:.6f}，稳定2 s看板已更新"
                        )
                    else:
                        self.dsm_scan_progress_var.set(
                            f"第{scan_index} / {scan_count}点："
                            f"DSM={value:.6f}，稳定2 s看板更新失败：{result}"
                        )
                    continue
                if event[0] != "scan":
                    continue
                (
                    _kind,
                    scan_index,
                    scan_count,
                    value,
                    measured,
                    _samples,
                ) = event
                self.dsm_scan_progress_var.set(
                    f"第{scan_index} / {scan_count}点完成；"
                    f"DSM={value:.6f}；频率={measured:.9g} MHz；"
                    "已保存100个μ样本"
                )
        except Empty:
            pass
        self.after(100, self._drain_dsm_scan_progress)

    def _drain_multi_temperature_progress(self) -> None:
        try:
            while True:
                event = self.multi_temperature_progress.get_nowait()
                kind = event[0]
                if kind == "temperature":
                    _kind, temp_index, total = event
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index} / {total}：等待温箱稳定"
                    )
                    continue
                if kind == "wait":
                    _kind, stage, temp_index, remaining, total = event
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：{stage}倒计时"
                        f"{remaining / 60.0:.2f} / {total / 60.0:.2f}分钟"
                    )
                    continue
                if kind == "trim_start":
                    _kind, temp_index, chip, trim_slot, trim = event
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：chip{chip}，"
                        f"TRIM_C_COARSE_{trim_slot}={trim}，正在写入控制字"
                    )
                    continue
                if kind == "sar_started":
                    _kind, temp_index, chip, trim, status = event
                    self._show_sar_status(status)
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：chip{chip}，TRIM={trim}，"
                        "常规SAR已启动"
                    )
                    continue
                if kind == "decision":
                    (
                        _kind,
                        temp_index,
                        chip,
                        trim,
                        step,
                        measured,
                        bit,
                    ) = event
                    direction = "增大校准码" if bit else "减小校准码"
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：chip{chip}，TRIM={trim}，"
                        f"第{step}步判决，实测{measured:.9g} MHz，{direction}"
                    )
                    continue
                if kind == "settle":
                    _kind, temp_index, chip, trim, step = event
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：chip{chip}，TRIM={trim}，"
                        f"第{step}步正在稳定{SAR_SETTLE_TIME_S:g} s"
                    )
                    continue
                if kind == "dsm_trial":
                    _kind, temp_index, chip, trim, step, trial = event
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：chip{chip}，TRIM={trim}，"
                        f"DSM覆盖第{step}步试探{trial:.6f}"
                    )
                    continue
                if kind == "multi_settle_dashboard":
                    (
                        _kind,
                        temp_index,
                        chip,
                        trim,
                        step,
                        mode,
                        result_kind,
                        result,
                    ) = event
                    if result_kind == "dashboard":
                        self._apply_multi_monitor_snapshot(result)
                        self.multi_temperature_status_var.set(
                            f"温度点{temp_index}：chip{chip}，TRIM={trim}，"
                            f"{mode}第{step}步稳定2 s看板已更新"
                        )
                    else:
                        self.multi_temperature_status_var.set(
                            f"温度点{temp_index}：chip{chip}，TRIM={trim}，"
                            f"第{step}步稳定2 s看板更新失败：{result}"
                        )
                    continue
                if kind == "record":
                    _kind, row, snapshot, samples = event
                    self._apply_multi_record(row, snapshot, samples)
                    self.multi_temperature_status_var.set(
                        f"温度点{row.temp_index}：chip{row.chip}，"
                        f"TRIM={row.trim_c_coarse}，第{row.sar_step}步已保存；"
                        f"频率{row.frequency_mhz:.9g} MHz"
                    )
                    continue
                if kind == "detail":
                    _kind, path, row_count = event
                    self.multi_temperature_output_var.set(str(path))
                    self.multi_temperature_status_var.set(
                        f"明细CSV已发布：{path}（{row_count}行）"
                    )
                    continue
                if kind == "shutdown":
                    _kind, temp_index, chip = event
                    self.multi_temperature_status_var.set(
                        f"温度点{temp_index}：正在关闭chip{chip}晶振"
                    )
                    continue
                if kind == "thermistor_error":
                    _kind, detail = event
                    self.multi_temperature_thermistor_var.set("—")
                    self.status_var.set(
                        f"DMM7510温敏电阻读取失败，已按空值继续：{detail}"
                    )
                    continue
                if kind == "summaries":
                    _kind, paths = event
                    if paths:
                        self.multi_temperature_status_var.set(
                            f"已生成{len(paths)}个chip汇总CSV"
                        )
                    continue
        except Empty:
            pass
        self.after(100, self._drain_multi_temperature_progress)

    def _apply_multi_monitor_snapshot(
        self,
        snapshot: MonitorSnapshot,
    ) -> None:
        self.multi_mu_history.append(
            snapshot.live.sample,
            snapshot.live.onchip_sample,
        )
        self.multi_mu1_var.set(f"{snapshot.live.sample.mu1:.6f}")
        self.multi_mu2_var.set(f"{snapshot.live.sample.mu2:.6f}")
        self.multi_onchip_mu1_var.set(
            f"{snapshot.live.onchip_sample.mu1:.6f}"
        )
        self.multi_onchip_mu2_var.set(
            f"{snapshot.live.onchip_sample.mu2:.6f}"
        )
        self.multi_calibration_history.append(snapshot.calibration)
        self.multi_poly_freq_var.set(f"{snapshot.calibration.poly_freq:.6f}")
        self.multi_poly_system_var.set(
            f"{snapshot.calibration.poly_system:.6f}"
        )
        self.multi_sar_correction_var.set(
            f"{snapshot.calibration.sar_correction:.6f}"
        )
        self.multi_dsm_input_var.set(f"{snapshot.calibration.dsm_input:.6f}")
        self._redraw_multi_mu_chart()
        self._redraw_multi_calibration_chart()

    def _apply_multi_record(
        self,
        row: MultiTemperatureSarRow,
        snapshot: CalibrationSnapshot,
        samples: list[MuSample],
    ) -> None:
        self.multi_frequency_history.append(row.frequency_mhz)
        self.multi_temperature_frequency_var.set(
            f"{row.frequency_mhz:.9g} MHz"
        )
        if row.thermistor_ohm is None:
            self.multi_temperature_thermistor_var.set("—")
        else:
            self.multi_temperature_thermistor_var.set(
                f"{row.thermistor_ohm:.10g} Ω"
            )
        self.multi_mu_history.extend(samples)
        if samples:
            latest = samples[-1]
            self.multi_mu1_var.set(f"{latest.mu1:.6f}")
            self.multi_mu2_var.set(f"{latest.mu2:.6f}")
        self.multi_calibration_history.append(snapshot)
        self.multi_poly_freq_var.set(f"{snapshot.poly_freq:.6f}")
        self.multi_poly_system_var.set(f"{snapshot.poly_system:.6f}")
        self.multi_sar_correction_var.set(f"{snapshot.sar_correction:.6f}")
        self.multi_dsm_input_var.set(f"{snapshot.dsm_input:.6f}")
        self._redraw_multi_frequency_chart()
        self._redraw_multi_mu_chart()
        self._redraw_multi_calibration_chart()

    def _redraw_multi_frequency_chart(self) -> None:
        canvas = self.multi_frequency_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 150)
        left, right, top, bottom = 72, 20, 22, 30
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)
        values = list(self.multi_frequency_history)

        canvas.create_rectangle(
            left,
            top,
            left + plot_width,
            top + plot_height,
            outline="#666666",
        )
        if not values:
            canvas.create_text(
                left + plot_width / 2,
                top + plot_height / 2,
                text="等待频率数据",
                fill="#777777",
            )
            return

        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        padding = max(span * 0.08, 1e-9)
        y_min = minimum - padding
        y_max = maximum + padding

        def y_for(value: float) -> float:
            return top + (y_max - value) * plot_height / (y_max - y_min)

        for tick in range(4):
            value = y_max - tick * (y_max - y_min) / 3
            y = y_for(value)
            canvas.create_line(left - 4, y, left, y, fill="#666666")
            canvas.create_text(
                left - 8,
                y,
                text=f"{value:.9g}",
                anchor="e",
                fill="#444444",
            )

        denominator = max(len(values) - 1, 1)
        points: list[float] = []
        for index, value in enumerate(values):
            points.extend((left + index * plot_width / denominator, y_for(value)))
        if len(points) == 2:
            x, y = points
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#0072B2")
        else:
            canvas.create_line(*points, fill="#0072B2", width=2)

    def _redraw_multi_mu_chart(self) -> None:
        canvas = self.multi_mu_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 180)
        left, right, top, bottom = 55, 20, 22, 30
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)

        for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = top + (1.0 - tick) * plot_height
            canvas.create_line(left, y, left + plot_width, y, fill="#eeeeee")
            canvas.create_text(
                left - 8,
                y,
                text=f"{tick:.2g}",
                anchor="e",
                fill="#555555",
            )
        canvas.create_rectangle(
            left,
            top,
            left + plot_width,
            top + plot_height,
            outline="#666666",
        )
        series = self.multi_mu_history.values()
        labels = (
            ("μ1", "#0072B2"),
            ("μ2", "#D55E00"),
            ("片内μ1", "#009E73"),
            ("片内μ2", "#7B3294"),
        )
        if not series[0]:
            canvas.create_text(
                left + plot_width / 2,
                top + plot_height / 2,
                text="等待μ数据",
                fill="#777777",
            )
            return
        size = len(series[0])
        denominator = max(size - 1, 1)

        def draw(values, color: str) -> None:
            points: list[float] = []
            for index, value in enumerate(values):
                if value is None:
                    continue
                x = left + index * plot_width / denominator
                y = top + (1.0 - value) * plot_height
                points.extend((x, y))
            if len(points) == 2:
                x, y = points
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color)
            elif points:
                canvas.create_line(*points, fill=color, width=2)

        legend_x = left
        for values, (label, color) in zip(series, labels):
            draw(values, color)
            canvas.create_line(
                legend_x,
                12,
                legend_x + 20,
                12,
                fill=color,
                width=2,
            )
            canvas.create_text(
                legend_x + 24,
                12,
                text=label,
                anchor="w",
                fill=color,
            )
            legend_x += 105

    def _redraw_multi_calibration_chart(self) -> None:
        canvas = self.multi_calibration_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 180)
        left, right, top, bottom = 76, 20, 34, 30
        plot_width = max(width - left - right, 1)
        plot_height = max(height - top - bottom, 1)
        series = self.multi_calibration_history.values()
        labels = (
            ("poly_freq", "#0072B2"),
            ("poly_system", "#D55E00"),
            ("SAR", "#009E73"),
            ("DSM输入", "#7B3294"),
        )
        all_values = [value for values in series for value in values]
        y_min, y_max = calibration_axis_bounds(all_values)

        canvas.create_rectangle(
            left,
            top,
            left + plot_width,
            top + plot_height,
            outline="#666666",
        )

        def y_for(value: float) -> float:
            return top + (y_max - value) * plot_height / (y_max - y_min)

        for reference in (-0.5, 0.5):
            y = y_for(reference)
            canvas.create_line(
                left,
                y,
                left + plot_width,
                y,
                fill="#888888",
                dash=(5, 4),
            )
            canvas.create_text(
                left + 4,
                y - 2,
                text=f"{reference:+.1f}",
                anchor="sw",
                fill="#666666",
            )
        if not series[0]:
            canvas.create_text(
                left + plot_width / 2,
                top + plot_height / 2,
                text="等待校准链路数据",
                fill="#777777",
            )
            return
        size = len(series[0])
        denominator = max(size - 1, 1)
        legend_x = left
        for values, (label, color) in zip(series, labels):
            points: list[float] = []
            for index, value in enumerate(values):
                x = left + index * plot_width / denominator
                points.extend((x, y_for(value)))
            if len(points) == 2:
                x, y = points
                canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color)
            else:
                canvas.create_line(*points, fill=color, width=2)
            canvas.create_line(
                legend_x,
                15,
                legend_x + 20,
                15,
                fill=color,
                width=2,
            )
            canvas.create_text(
                legend_x + 24,
                15,
                text=label,
                anchor="w",
                fill=color,
            )
            legend_x += 125

    def _show_mu_sample(
        self,
        sample: MuSample,
        onchip_sample: MuSample | None = None,
    ) -> None:
        self.mu1_var.set(f"{sample.mu1:.6f}")
        self.mu2_var.set(f"{sample.mu2:.6f}")
        if onchip_sample is not None:
            self.onchip_mu1_var.set(f"{onchip_sample.mu1:.6f}")
            self.onchip_mu2_var.set(f"{onchip_sample.mu2:.6f}")

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
        legend = [
            ("μ1", "#0072B2", ()),
            ("μ2", "#D55E00", ()),
            ("片内μ1", "#009E73", (4, 3)),
            ("片内μ2", "#7B3294", (4, 3)),
        ]
        legend_x = max(left + 20, width - 380)
        for index, (label, color, dash) in enumerate(legend):
            x = legend_x + index * 92
            line_options = {"fill": color, "width": 2}
            if dash:
                line_options["dash"] = dash
            canvas.create_line(
                x,
                12,
                x + 28,
                12,
                **line_options,
            )
            canvas.create_text(
                x + 34,
                12,
                text=label,
                anchor="w",
                fill=color,
            )

        (
            mu1_values,
            mu2_values,
            onchip_mu1_values,
            onchip_mu2_values,
        ) = self.history.values()
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

        def draw_optional_series(
            values: list[float | None],
            color: str,
        ) -> None:
            denominator = max(len(values) - 1, 1)
            segment: list[float] = []
            for index, value in enumerate(values):
                if value is None:
                    if len(segment) >= 4:
                        canvas.create_line(
                            *segment,
                            fill=color,
                            width=2,
                            dash=(4, 3),
                        )
                    segment = []
                    continue
                x = left + index * plot_width / denominator
                clamped = min(max(value, 0.0), 1.0)
                y = top + (1.0 - clamped) * plot_height
                segment.extend((x, y))
            if len(segment) >= 4:
                canvas.create_line(
                    *segment,
                    fill=color,
                    width=2,
                    dash=(4, 3),
                )

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
            draw_optional_series(onchip_mu1_values, "#009E73")
            draw_optional_series(onchip_mu2_values, "#7B3294")

    def _schedule_background_read(self) -> None:
        if (
            self.statistics_running
            or self.dsm_scan_running
            or self.sar_running
            or self.multi_temperature_running
        ):
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
        if selected_tab == str(self.monitor_tab):
            if self.live_var.get() and self.calibration_live_var.get():
                future = self.executor.submit(
                    self.transport.read_monitor_snapshot
                )
                self.operation = future
                self._finish_monitor_read(future)
                return
            if self.live_var.get():
                future = self.executor.submit(self.transport.read_live_sample)
                self.operation = future
                self._finish_live_read(future)
                return
            if self.calibration_live_var.get():
                future = self.executor.submit(
                    self.transport.read_calibration_snapshot
                )
                self.operation = future
                self._finish_calibration_read(future)
                return
        self.after(200, self._schedule_background_read)

    def _finish_monitor_read(self, future: Future) -> None:
        if not future.done():
            self.after(50, self._finish_monitor_read, future)
            return
        try:
            snapshot: MonitorSnapshot = future.result()
            self._apply_monitor_snapshot(snapshot)
        except Exception as exc:
            self.status_var.set(f"监控读取失败：{exc}")
        self.after(120, self._schedule_background_read)

    def _apply_monitor_snapshot(self, snapshot: MonitorSnapshot) -> None:
        self._apply_live_snapshot(snapshot.live)
        self._apply_calibration_snapshot(snapshot.calibration)

    def _finish_live_read(self, future: Future) -> None:
        if not future.done():
            self.after(50, self._finish_live_read, future)
            return
        try:
            snapshot: LiveSample = future.result()
            self._apply_live_snapshot(snapshot)
        except Exception as exc:
            self.status_var.set(f"实时读取已暂停：{exc}")
            self.live_var.set(False)
        self.after(80, self._schedule_background_read)

    def _apply_live_snapshot(self, snapshot: LiveSample) -> None:
        self._show_mu_sample(snapshot.sample, snapshot.onchip_sample)
        self.active_filter_samples_var.set(
            str(snapshot.total_filter_samples)
        )
        if snapshot.sequence != self.last_live_sequence:
            self.history.append(
                snapshot.sample,
                snapshot.onchip_sample,
            )
            self.last_live_sequence = snapshot.sequence
            self._redraw_chart()

    def _finish_calibration_read(self, future: Future) -> None:
        if not future.done():
            self.after(50, self._finish_calibration_read, future)
            return
        try:
            snapshot: CalibrationSnapshot = future.result()
            self._apply_calibration_snapshot(snapshot)
        except Exception as exc:
            self.status_var.set(f"校准链路读取失败：{exc}")
        self.after(300, self._schedule_background_read)

    def _apply_calibration_snapshot(
        self,
        snapshot: CalibrationSnapshot,
    ) -> None:
        self._show_calibration_snapshot(snapshot)
        self._show_sar_status(snapshot.sar_status)
        if (
            snapshot.valid
            and snapshot.sequence != self.last_calibration_sequence
        ):
            self.calibration_history.append(snapshot)
            self.last_calibration_sequence = snapshot.sequence
            self._redraw_calibration_chart()
        if snapshot.sar_status.fpga_error:
            self.status_var.set(
                "FPGA存在粘滞错误"
                f"0x{snapshot.sar_status.fpga_error:02X}；"
                "重新启动SAR将自动清除"
            )

    def _schedule_frequency_read(self) -> None:
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            self.after(250, self._schedule_frequency_read)
            return
        if self.sar_running or self.multi_temperature_running:
            self.after(500, self._schedule_frequency_read)
            return
        if not self.frequency_live_var.get():
            self.after(500, self._schedule_frequency_read)
            return
        if self.notebook.select() != str(self.monitor_tab):
            self.after(500, self._schedule_frequency_read)
            return
        self._start_frequency_read()

    def _gate_time_text(self) -> str:
        return f"{self.gate_time_s:g} s"

    def _set_gate_time_value(self, gate_time_s: float) -> None:
        value = validate_gate_time_s(gate_time_s)
        setter = getattr(self.frequency_backend, "set_gate_time_s", None)
        if setter is not None:
            setter(value)
        self.gate_time_s = value
        self.gate_time_var.set(self._gate_time_text())
        self.frequency_status_var.set(
            f"gate time={self._gate_time_text()}；53220A自动检索"
        )

    def _configure_gate_time(self) -> None:
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            messagebox.showinfo(
                "仪器读取中",
                "当前gate读数尚未完成，请等待完成后再修改gate time。",
            )
            return
        value = simpledialog.askfloat(
            "设置 gate time",
            "请输入gate time（秒）：",
            initialvalue=self.gate_time_s,
            parent=self,
        )
        if value is None:
            return
        try:
            self._set_gate_time_value(value)
        except Exception as exc:
            messagebox.showerror("gate time无效", str(exc))
            return
        self.status_var.set(f"gate time已设置为{self._gate_time_text()}")

    def _start_frequency_read(self) -> None:
        if self.sar_running or self.multi_temperature_running:
            self.frequency_status_var.set(
                "自动校准流程正在使用53220A，手动/后台读数已暂停"
            )
            return
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            self.frequency_status_var.set(
                f"53220A正在完成本次{self._gate_time_text()} gate读数"
            )
            return
        self.frequency_status_var.set(
            f"正在读取53220A（gate time={self._gate_time_text()}）…"
        )
        future = self.frequency_executor.submit(
            self.frequency_backend.read_frequency_mhz
        )
        self.frequency_operation = future
        self._finish_frequency_read(future)

    def _finish_frequency_read(self, future: Future) -> None:
        if not future.done():
            self.after(100, self._finish_frequency_read, future)
            return
        retry_delay_ms = 200
        try:
            measured = float(future.result())
            if not math.isfinite(measured) or measured <= 0:
                raise RuntimeError(
                    "仪器返回的频率必须是大于0的有限数值"
                )
            self.measured_frequency_var.set(f"{measured:.9g} MHz")
            backend_description = getattr(
                self.frequency_backend,
                "describe",
                lambda: "专业频率测试仪器",
            )()
            self.frequency_status_var.set(
                "53220A读取正常；"
                f"gate time={self._gate_time_text()}；{backend_description}"
            )
        except Exception as exc:
            self.measured_frequency_var.set("— MHz（53220A读取失败）")
            self.frequency_status_var.set(f"53220A读取失败：{exc}")
            retry_delay_ms = 5000
        self.after(retry_delay_ms, self._schedule_frequency_read)

    def _set_sar_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for name in (
            "sar_target_frequency_entry",
            "sar_data_count_entry",
            "sar_file_stem_entry",
            "sar_output_directory_entry",
            "sar_output_directory_button",
            "sar_start_button",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state=state)

    def _restore_after_sar(self) -> None:
        self.sar_running = False
        self._set_sar_controls(True)
        self._set_statistics_controls(True)
        self._set_dsm_scan_controls(True)
        self._set_multi_temperature_controls(True)
        self.live_var.set(self.live_before_sar)
        self.calibration_live_var.set(self.calibration_live_before_sar)
        self.frequency_live_var.set(self.frequency_live_before_sar)

    def _start_auto_sar_calibration(self, waiting: bool = False) -> None:
        if self.multi_temperature_running:
            messagebox.showinfo(
                "多温度校准进行中",
                "请等待多温度点校准完成后再启动SAR校准",
            )
            return
        if self.statistics_running:
            messagebox.showinfo(
                "统计进行中",
                "请等待统计完成后再启动SAR校准",
            )
            return
        if self.dsm_scan_running:
            messagebox.showinfo(
                "DSM扫描进行中",
                "请等待DSM扫描完成后再启动SAR校准",
            )
            return
        if self.sar_running:
            self.status_var.set("SAR自动校准已经在运行")
            return
        if not waiting:
            if self.submission_waiting:
                self.status_var.set(
                    "请等待上一项操作开始或完成后再启动SAR校准"
                )
                return
            self.live_before_sar = self.live_var.get()
            self.calibration_live_before_sar = self.calibration_live_var.get()
            self.frequency_live_before_sar = self.frequency_live_var.get()
            self.live_var.set(False)
            self.calibration_live_var.set(False)
            self.frequency_live_var.set(False)
            self._set_sar_controls(False)

        if self.operation is not None and not self.operation.done():
            self.status_var.set(
                "正在结束当前JTAG读取，随后自动开始SAR校准"
            )
            self.after(100, lambda: self._start_auto_sar_calibration(True))
            return
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            self.status_var.set(
                "正在等待当前仪器gate读数完成，随后自动开始SAR校准"
            )
            self.after(250, lambda: self._start_auto_sar_calibration(True))
            return

        try:
            target_frequency = parse_target_frequency(
                self.target_frequency_var.get()
            )
            try:
                data_count_value = int(
                    self.sar_data_count_var.get().strip()
                )
            except ValueError as exc:
                raise ValueError("数据量必须为十进制整数") from exc
            data_count = validate_data_count(data_count_value)
            output_text = self.output_directory_var.get().strip()
            if not output_text:
                raise ValueError("保存目录不能为空")
            output_directory = Path(output_text).expanduser().resolve()
            started_at = datetime.now()
            file_stem = self.sar_file_stem_var.get()
            final_name = statistics_filename(file_stem, started_at)
            if (output_directory / final_name).exists():
                raise FileExistsError(
                    f"SAR校准文件已存在：{output_directory / final_name}"
                )
        except Exception as exc:
            self._set_sar_controls(True)
            self.live_var.set(self.live_before_sar)
            self.calibration_live_var.set(self.calibration_live_before_sar)
            self.frequency_live_var.set(self.frequency_live_before_sar)
            messagebox.showerror("SAR校准设置无效", str(exc))
            return

        try:
            while True:
                self.sar_progress.get_nowait()
        except Empty:
            pass

        total_batches = data_count // STATISTICS_BATCH_SIZE
        self.sar_running = True
        self._set_sar_controls(False)
        self._set_statistics_controls(False)
        self._set_dsm_scan_controls(False)
        self._set_multi_temperature_controls(False)
        self.sar_status_var.set(
            f"准备启动SAR自动校准；目标{target_frequency:g} MHz，"
            f"每步记录{data_count}个μ值"
        )

        def work() -> Path:
            transport = self._require_transport()
            steps_completed = 0
            with AtomicSarCalibrationCsv(
                output_directory,
                file_stem,
                started_at,
                sample_count=data_count,
            ) as writer:
                status = transport.start_sar()
                self.sar_progress.put(("started", status))
                decision_frequency = (
                    self.frequency_backend.read_frequency_mhz()
                )
                while status.busy and not status.done and status.step < SAR_STEPS:
                    next_step = status.step + 1
                    bit = sar_decision_bit(
                        decision_frequency,
                        target_frequency,
                    )
                    self.sar_progress.put(
                        (
                            "decision",
                            next_step,
                            SAR_STEPS,
                            decision_frequency,
                            target_frequency,
                            bit,
                        )
                    )
                    status = transport.submit_sar_decision(bit)
                    self.sar_progress.put(("settle", status, bit))
                    self._settle_with_dashboard_update(
                        transport,
                        self.sar_progress,
                        (
                            "settle_dashboard",
                            status.step,
                            SAR_STEPS,
                            bit,
                        ),
                        total_wait_s=SAR_SETTLE_TIME_S,
                    )
                    measured = self.frequency_backend.read_frequency_mhz()
                    snapshot = transport.read_calibration_snapshot()
                    samples: list[MuSample] = []
                    for batch_number in range(1, total_batches + 1):
                        batch = transport.capture_statistics_batch()
                        samples.extend(batch)
                        self.sar_progress.put(
                            (
                                "batch",
                                status.step,
                                batch_number,
                                total_batches,
                                len(samples),
                                data_count,
                            )
                        )
                    writer.append_step(
                        status.step,
                        measured,
                        snapshot.sar_correction,
                        snapshot.dsm_input,
                        samples,
                    )
                    steps_completed = status.step
                    self.sar_progress.put(
                        (
                            "record",
                            status.step,
                            SAR_STEPS,
                            measured,
                            snapshot,
                            samples,
                            data_count,
                        )
                    )
                    decision_frequency = measured
                return writer.publish(steps_completed)

        def completed(path: Path) -> None:
            self._restore_after_sar()
            self.sar_status_var.set(f"SAR自动校准完成；CSV已保存到 {path}")
            messagebox.showinfo("SAR校准完成", f"CSV已保存：\n{path}")

        def failed(_exc: Exception) -> None:
            self._restore_after_sar()
            self.sar_status_var.set(
                "SAR自动校准失败，临时文件已清理，监控状态已恢复"
            )

        self._submit(
            f"正在执行SAR自动校准：目标{target_frequency:g} MHz",
            work,
            completed,
            failed,
        )

    def _drain_sar_progress(self) -> None:
        try:
            while True:
                event = self.sar_progress.get_nowait()
                kind = event[0]
                if kind == "started":
                    _kind, status = event
                    self._show_sar_status(status)
                    self.sar_status_var.set(
                        "SAR已启动；正在读取首次实测频率用于第1步判决"
                    )
                    continue
                if kind == "decision":
                    (
                        _kind,
                        step,
                        total_steps,
                        measured,
                        target,
                        bit,
                    ) = event
                    direction = "增大校准码" if bit else "减小校准码"
                    self.sar_status_var.set(
                        f"第{step} / {total_steps}步判决："
                        f"实测{measured:.9g} MHz，目标{target:.9g} MHz，"
                        f"{direction}"
                    )
                    continue
                if kind == "settle":
                    _kind, status, bit = event
                    self._show_sar_status(status)
                    direction = "增大校准码" if bit else "减小校准码"
                    self.sar_status_var.set(
                        f"第{status.step} / {SAR_STEPS}步码字已更新"
                        f"（{direction}），正在稳定{SAR_SETTLE_TIME_S:g} s"
                    )
                    continue
                if kind == "settle_dashboard":
                    (
                        _kind,
                        step,
                        total_steps,
                        bit,
                        result_kind,
                        result,
                    ) = event
                    direction = "增大校准码" if bit else "减小校准码"
                    if result_kind == "dashboard":
                        self._apply_monitor_snapshot(result)
                        self.sar_status_var.set(
                            f"第{step} / {total_steps}步稳定2 s看板已更新"
                            f"（{direction}）"
                        )
                    else:
                        self.sar_status_var.set(
                            f"第{step} / {total_steps}步稳定2 s看板更新失败"
                            f"（{direction}）：{result}"
                        )
                    continue
                if kind == "batch":
                    (
                        _kind,
                        step,
                        batch_number,
                        total_batches,
                        completed,
                        data_count,
                    ) = event
                    self.sar_status_var.set(
                        f"第{step} / {SAR_STEPS}步μ值采集："
                        f"{batch_number} / {total_batches}批，"
                        f"{completed} / {data_count}点"
                    )
                    continue
                if kind != "record":
                    continue
                (
                    _kind,
                    step,
                    total_steps,
                    measured,
                    _snapshot,
                    _samples,
                    data_count,
                ) = event
                self.sar_status_var.set(
                    f"第{step} / {total_steps}步已记录："
                    f"频率{measured:.9g} MHz，"
                    f"μ值{data_count}点"
                )
        except Empty:
            pass
        self.after(100, self._drain_sar_progress)

    def _set_sar_decisions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for name in ("sar_low_button", "sar_high_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state=state)

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
        self._start_frequency_read()

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
                "搜索范围为-0.5～+0.5，首个试探码为0"
            )

        def failed(_exc: Exception) -> None:
            self._set_sar_decisions_enabled(False)
            self.sar_status_var.set(
                "启动未确认；外部芯片和FS不是启动条件，"
                "请检查JTAG连接及FPGA_V5兼容的V4协议镜像"
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
            }
            for key, width in widths.items():
                value = getattr(word, key) & ((1 << width) - 1)
                self.field_vars[key].set(f"{value:0{width}b}")
            self.field_vars["alpha"].set(
                unsigned_q16_to_decimal_text(word.alpha)
            )
            for index, value in enumerate(word.batch):
                self.field_vars[f"batch_a{index}"].set(
                    signed_q16_to_decimal_text(value)
                )
            for index, value in enumerate(word.poly):
                self.field_vars[f"poly_a{index}"].set(
                    signed_q16_to_decimal_text(value)
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
        if (
            self.frequency_operation is not None
            and not self.frequency_operation.done()
        ):
            messagebox.showinfo(
                "操作进行中",
                f"当前53220A的{self._gate_time_text()} gate读数尚未完成，"
                "请等待完成后再关闭窗口。",
            )
            return
        close_instrument = getattr(self.frequency_backend, "close", None)
        if close_instrument is not None:
            close_instrument()
        close_resistance = getattr(self.resistance_backend, "close", None)
        if close_resistance is not None:
            close_resistance()
        self.frequency_executor.shutdown(wait=False, cancel_futures=True)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


if __name__ == "__main__":
    ControlApp().mainloop()
