# 2607 晶振测试工程（V1～V13 收尾归档）

本项目将原 DAQ + LabVIEW + MATLAB 的晶振芯片测试流程迁移为 Python 主机控制、
可替换仪器后端与 FPGA 实时驱动/数字处理框架。项目开发阶段已结束，现按
2026-11-04 的本地工程状态整理归档，保留 V1～V13 的源码和演进记录。

对应 GitHub 仓库：[Lizzie-04/2607-oscillator-test](https://github.com/Lizzie-04/2607-oscillator-test)。
最终源码快照为 [FPGA_V13](FPGA_V13/README.md)；V1～V12 用于版本追溯与复现。
目录版本号、RTL 协议版本与 SOF 文件名并不总是一致，请使用对应版本的主机、构建脚本和协议说明。
本次为源码与文档收尾存档，不代表重新完成全部硬件验收或发布新烧录镜像。

## V1～V13 版本更新说明

以下根据各版本 README、工程约束和现存源码差异整理；目录是保留的开发快照，
不是十三次独立硬件验收报告。部分历史 README 沿用了前版文字，差异以本表及实际源码为准。

| 版本 | 主要更新与存档意义 |
|---|---|
| [V1](FPGA_V1/README.md) | 建立 DE1-SoC / Rev G PCB 的首版可综合工程：474-bit 控制字、整体反转位序、0.5 ms 半周期及严格 474 个移位上升沿；集成连续三角窗降采样、On-Chip DigOsc 与外部单 bit SAR 判决；提供 Python GUI、JTAG ISSP 和 GPIO1 握手通路。 |
| [V2](FPGA_V2/README.md) | 增加四标签中文 GUI、协议版本识别、μ1/μ2 趋势、FPGA 固定 100 点双时钟统计缓存及多批 CSV 原子拼接；提供可替换频率仪接口，并将控制事务长度 N_DAQ 与滤波窗口参数分离。 |
| [V3](FPGA_V3/README.md) | 基于 V2 增加片内 μ1/μ2 观测，四路趋势保留最近 1000 点；使用 V3 协议标识；将 RESET 放在 START 已拉高后的事务末尾，保留 474-bit 串行驱动和分批统计能力。 |
| [V4](FPGA_V4/README.md) | 接入 host 层 Keysight/Agilent 53220A PyVISA 自动发现；合并频率测量、SAR 与 DSM 覆盖校准页，增加四路 Q16 校准快照看板；采用有界、端点精确的 DSM，保留原 DSM reference path；SAR 在 FPGA 自生 2 MHz 域运行，范围为 ±0.5。 |
| [V5](FPGA_V5/README.md) | 基于 V4 扩展 host/UI，使用五标签界面；增加可调 gate time、数字 DSM 扫描、五芯片/三组 TRIM 的多温度点校准和 Keithley DMM7510 四线温敏电阻后端；沿用 V4 RTL 与协议。 |
| [V6](FPGA_V6/README.md) | 基于 V5 加强长时间自动测试：写入后起振频率诊断、可配置频率上下限、失败记录与后续工况继续执行；增加运行日志及 CSV record_status，并在测试期间防止系统睡眠和误关 GUI。 |
| [V7](FPGA_V7/README.md) | 基于 V6 细化启动诊断：可配置起振等待及写入尝试次数，每次写入后最多进行三次读频判断；增加 SAR/DSM 写入、等待、仪器读取、统计和 CSV 各阶段耗时日志。历史子 README 仍标为 V6，本条依据 V7 host 源码差异整理。 |
| [V8](FPGA_V8/README.md) | 将运行期通信迁移到常驻 Quartus System Console、JTAG-to-Avalon Master Bridge 和 avalon_host_bridge；Python 通过本机 TCP 请求/响应通信，取消事务临时文件和运行期 ISSP 回退；引入 Avalon ABI 与 MAGIC 识别，必须使用对应的新 RTL 镜像。 |
| [V9](FPGA_V9/README.md) | 从 V7 独立分支，在多温度点校准默认配置中加入 N_DAQ，控制每次码字驱动的总样点数；保留 V4 兼容 ISSP 路线。V9 并非 V8 的 Avalon 通信升级版。 |
| [V10](FPGA_V10/README.md) | 从 V8 分支，保留 System Console/Avalon 通信；增加第六个“多温度点测试”标签，逐芯片载入独立 JSON 配置，遍历 TRIM，执行不启动 SAR 的直接频率测试。 |
| [V11](FPGA_V11/README.md) | 将 FPGA 内部 16 个算法系数扩为 37-bit signed Q20.16，并与 PCB 474-bit 控制字分开配置、原子提交；外部串行算法字段清零，内部 Polynomial 使用 74-bit 乘积；Adder/DSM 限幅至 ±0.5。升级核心协议至 0x0005、Avalon ABI 至 2，采用 schema 2 配置并保留旧配置迁移。 |
| [V12](FPGA_V12/README.md) | 基于 V11 扩展多温度点测试：每芯片最多六份配置，按 chip/config/BYPASS/TRIM 组织工况；独立设置正式重复次数，显示原始频率点和跨温度平均值连线；同芯片切换工况保持晶振开启，换芯片及结束时关断；调整 CSV 字段与文件名。RTL 协议保持兼容。 |
| [V13](FPGA_V13/README.md) | 基于 V12 完善多温度点校准：三组 TRIM 可独立勾选并保留槽位编号；按温度与 SAR 步数显示完整工况曲线，突出 DSM 输入为零和第 16 步结果；错误记录不进入频率历史。保留 V12 的六配置测试流程，兼容 V11/V12 schema 2 配置，作为最终源码存档。 |

主要分支关系：V1 → V2 → V3 → V4 → V5 → V6 → V7；后续保留 V8 的
Avalon 通信路线和 V9 的 V7/ISSP 分支，V10 从 V8 继续，随后为 V11 → V12 → V13。

## 工程入口与目录

- [V13 最终源码、数值格式与测试流程](FPGA_V13/README.md)
- [V4 基准工程、时序与协议说明](FPGA_V4/README.md)
- [MATLAB 驱动逻辑与 FPGA/Python 重构依据](MATLAB驱动芯片逻辑与FPGA-Python重构说明.md)
- [原始 MATLAB 驱动脚本](Matlab_Scripts/drive.m)、[原始数字处理参考](Digital_Scripts)
- [芯片配置快照](Chip_Config)、[Allan 偏差采集脚本](Allan_Deviation/run_allan_deviation.py)
- [Rev G GPIO/FPGA 端口补充表](PCB/GPIO端口_RevG_FPGA补充.xlsx)

```text
2607-oscillator-test/
├─ FPGA_V1/ … FPGA_V13/    各版源码、host、RTL、仿真与构建脚本
├─ Chip_Config/           实验使用的芯片 JSON 配置快照
├─ Allan_Deviation/       53220A 频率采集与 Allan 偏差脚本
├─ Matlab_Scripts/        原 DAQ/LabVIEW 调用的 MATLAB 波形源码
├─ Digital_Scripts/       原数字后端与 SAR 参考代码
├─ PCB/                   PCB 原理图及 GPIO 表
├─ docs/                  驱动示意图与历史说明
├─ references/terasic/    DE1-SoC 厂商手册及参考约束
├─ archive/               已停用原型与一次性生成过程
└─ AGENTS.md              原有工程协作与修改约束
```

原有 AGENTS.md 的 V4 开发范围约束保留；本次将 V5～V13 作为已有源码快照入库，
不改动各版 RTL、主机业务逻辑或历史基线。archive/ 与 docs/history/ 仅用于追溯，
不属于当前构建路径。

## 复现与环境

需要 Windows / PowerShell、包含 Tcl/Tk 的 Python 3.10+；仪器读取需要 PyVISA
与相应 VISA 驱动。FPGA 构建需要 Quartus、Cyclone V 支持，仿真需要 Questa。
V8/V10～V13 的通信还需要 Quartus System Console 与对应 Avalon 镜像。
工具通过 PATH 或工程文档列出的环境变量发现，不依赖特定用户名或工作目录。

```powershell
git clone https://github.com/Lizzie-04/2607-oscillator-test.git
cd .\2607-oscillator-test\FPGA_V13

# 离线主机测试，不连接 FPGA 或仪器
python -m unittest discover -s host -p "test_*.py"

# 需要另外安装对应工具；构建不会自动加载 SOF
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\simulate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Quartus/Questa 中间数据库由脚本置于系统临时目录，正式输出回写各版
quartus/output_files。V13 的构建目标为 fpga_v13.sof；本次归档没有生成该镜像。
历史已有 SOF 按实际文件保留，不以同名文件推断跨版本兼容性，也不以文件存在
作为已验证其与当前源码一致的依据。运行或加载前请遵守相应目录的工程约束。

## 归档范围与验证边界

本次保留源码、说明文档、测试、构建脚本、芯片配置与已有编程文件。
原始实验采样数据、各版 data/、运行日志、本地依赖缓存、工具数据库和重复 ZIP
继续留在本地，不随 Git 提交；本仓库是工程归档，不是实验原始数据的完整备份。

V12/V13 子 README 分别记录过 84/88 项 host 测试通过；这些是历史记录。
本次没有修改 RTL，不重新开展全版本 Quartus/Questa 验收，不连接 VISA/JTAG、
不启动 GUI、不烧录 SOF，也不部署 SignalTap。真实硬件性能与测量结论应以
对应实验记录为依据，不能由“项目完成归档”推定。

频率仪和温敏电阻仪器耦合保持在 host 层，通过 VISA 资源枚举和 *IDN?* 发现设备；
可替换后端不应改变 RTL/SAR。芯片配置文件属于具体实验快照，复用前应确认
目标芯片、协议版本及参数含义。

2026-11-04 归档检查：在 Python 3.14 下运行 V13 离线 host 单元测试，
89 项全部通过（正常临时目录环境）；未开展硬件连接或新的 RTL 构建。
