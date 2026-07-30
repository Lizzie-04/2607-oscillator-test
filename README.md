# 2607 晶振测试工程

本仓库把原 DAQ + LabVIEW + MATLAB 的模拟芯片自动化测试流程逐步迁移为
“Python 控制/仪器后端 + FPGA 实时驱动与数字处理”的可复用框架。

当前可运行版本是 `FPGA_V1`：电脑端以中文 GUI 或 Python API 发送 474 位
芯片控制字，FPGA 完成启动时序、连续三角窗采样和单 bit SAR 校准。工程
暂不部署 SignalTap，也尚未绑定具体频率仪型号。

## 工程入口

- [FPGA V1 使用、协议与验证说明](FPGA_V1/README.md)
- [MATLAB 驱动逻辑与 FPGA/Python 重构依据](MATLAB驱动芯片逻辑与FPGA-Python重构说明.md)
- [Rev G GPIO/FPGA 端口补充表](PCB/GPIO端口_RevG_FPGA补充.xlsx)
- [原始 MATLAB 驱动脚本](Matlab_Scripts/drive.m)
- [原始数字处理代码与 SAR 参考实现](Digital_Scripts)

## 新电脑快速开始

克隆仓库后，从仓库根目录进入 `FPGA_V1`：

```powershell
gh repo clone Lizzie-04/2607-oscillator-test
cd .\2607-oscillator-test\FPGA_V1

# 主机端单元测试；只需要 Python 3.10+
python -m unittest discover -s .\host -p "test_*.py" -v

# RTL 仿真；需要 Questa FPGA Starter Edition
powershell -ExecutionPolicy Bypass -File .\scripts\simulate.ps1

# FPGA 全编译；需要 Quartus Prime Lite 25.1std 和 Cyclone V 支持
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1

# 启动中文 GUI
powershell -ExecutionPolicy Bypass -File .\scripts\run_gui.ps1
```

GUI 本身只使用 Python 标准库，不需要 `pip install`。Quartus/Questa 应放入
`PATH`，或通过 `QUARTUS_ROOTDIR`、`QUARTUS_ROOTDIR_OVERRIDE`、
`QUESTA_FSE_ROOT`、`QUESTA_ROOT` 指定；仓库不绑定安装盘符或版本目录。

## 路径原则

全部工程源文件都可以、也应该保存在本仓库目录中。当前脚本不需要
`C:\Users\Lizzie\OneDrive\FPGA`，也不会在那里创建工程副本：

- PowerShell 从 `$PSScriptRoot` 推导项目位置；
- Python 从 `Path(__file__)` 推导 Tcl 与 `.sof` 位置；
- QSF/SDC 使用相对于 `FPGA_V1/quartus` 的路径；
- Quartus 和 Questa 的中间数据库只在 Windows `%TEMP%` 中临时生成；
- Quartus 构建期间可能把该临时目录映射为临时盘符，结束后立即解除；
- 成功构建/仿真后删除临时目录，失败时保留目录并打印路径供调试；
- 最终 `.sof` 与时序/布局摘要回写到
  `FPGA_V1/quartus/output_files`。

因此，工程可以整体复制或克隆到不同盘符、用户名、中文或带空格的目录。
系统 `%TEMP%` 是工具运行缓存，不是第二份工程，也不应提交到 Git。

## 目录结构

```text
2607-oscillator-test/
├─ FPGA_V1/               当前可综合、可仿真的 FPGA/Python 工程
├─ Matlab_Scripts/         原 DAQ/LabVIEW 调用的 MATLAB 波形源码
├─ Digital_Scripts/        原数字后端与 SAR 参考代码
├─ PCB/                    PCB 原理图、原始及补充 GPIO 表
├─ docs/                   MATLAB 驱动示意图与历史说明
├─ references/terasic/     DE1-SoC 厂商手册及参考约束
├─ archive/                已停用原型与一次性生成过程
├─ AGENTS.md               工程协作约束
└─ README.md               本说明
```

`archive` 和 `docs/history` 只用于追溯，不属于当前构建路径；其中可能保留旧
工具版本和旧目录示例，不应作为新电脑配置依据。

## 当前边界

- 未部署 SignalTap；
- 未完成真实 PCB 的电气与频率仪联调；
- 频率仪自动化后端尚未绑定，可选择 Python VISA/SCPI、厂商 SDK，或由
  LabVIEW 通过 TCP/ZeroMQ/文件队列把测量结果送入 Python；
- 正式上板前应载入并复核已验证的芯片控制参数，GUI 默认全零只代表占位。
