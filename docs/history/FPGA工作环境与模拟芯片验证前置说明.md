# FPGA 工作环境与模拟芯片验证前置说明（历史归档）

> 本文记录 V1 建立前的环境探索，包含已经停用的固定安装路径、
> `C:\Users\Lizzie\OneDrive\FPGA` 工作目录和 SignalTap LED demo。
> 当前工程不再依赖这些路径；请以仓库根目录 `README.md` 和
> `FPGA_V1/README.md` 为准。

本文用于后续使用 FPGA 辅助模拟芯片验证时快速恢复工作环境，避免重复进行软件安装、下载线配置和基础 JTAG 联调。内容只记录已经确认可用的环境、接口能力和后续工程约束。

## 1. 当前硬件平台

- 开发板：Terasic DE1-SoC，按 Rev.F/G 处理。
- FPGA：Cyclone V SoC `5CSEMA5F31C6`。
- JTAG 下载接口：开发板板载 USB-Blaster，通过 USB 直接透传到 Windows 虚拟机。
- 已确认的 JTAG 链：
  - HPS TAP：`4BA00477 SOCVHPS`；
  - FPGA TAP：`02D120DD 5CSE(BA5|MA5)...`。
- 当前采用 JTAG 下载 `.sof`，配置断电后失效；尚未使用非易失配置存储器。

虚拟机 USB 透传规则应同时覆盖 Terasic 下载线的两个状态：

```text
VID:PID 09FB:6810    初始化状态
VID:PID 09FB:6010    固件加载后的运行状态
```

若只透传其中一个 PID，下载线可能在初始化后从虚拟机中消失。

## 2. 已安装并验证的工具链

### Quartus

- Quartus Prime Lite Edition `25.1std.0 Build 1129`。
- 安装根目录：

```text
C:\altera_lite\25.1std
```

- 常用命令行工具目录：

```text
C:\altera_lite\25.1std\quartus\bin64
```

- 已安装 Cyclone V 器件支持。
- 已验证可以完成综合、布局布线、时序分析、生成 `.sof` 和通过 JTAG 下载。

### Questa

- Questa – Altera FPGA Starter Edition `2025.2`。
- 可执行文件目录：

```text
C:\altera_lite\25.1std\questa_fse\win64
```

- Starter Edition 已满足当前 RTL/testbench 功能仿真需求。
- `SALT_LICENSE_SERVER` 系统变量已经配置并验证有效。

### Python

- Python 3.12。
- Tkinter 已验证可用。
- 可用于实现电脑端 GUI、参数检查、调用 Quartus Tcl，以及解析 SignalTap CSV/VCD。

## 3. Windows 环境变量状态

已经配置并验证的主要系统变量包括：

```text
QUARTUS_ROOTDIR
QSYS_ROOTDIR
SOPC_KIT_NIOS2
SALT_LICENSE_SERVER
```

现有自动化脚本主要使用 Quartus/Questa 的绝对路径，因此不会完全依赖 `Path`。若后续新开终端无法直接执行 `quartus_sh`、`quartus_stp` 等命令，可以继续使用完整路径：

```text
C:\altera_lite\25.1std\quartus\bin64\quartus_sh.exe
C:\altera_lite\25.1std\quartus\bin64\quartus_stp.exe
C:\altera_lite\25.1std\quartus\bin64\quartus_pgm.exe
C:\altera_lite\25.1std\quartus\bin64\jtagconfig.exe
```

## 4. 工程路径约束

科研资料和本文档位于：

```text
C:\Users\Lizzie\OneDrive\科研\2607_晶振测试
```

FPGA 可执行工程统一放在纯英文 OneDrive 路径：

```text
C:\Users\Lizzie\OneDrive\FPGA\<project_name>
```

已于 2026-07-28 在该路径完成 Questa 仿真和 Quartus 完整编译验证。Questa 为 0 errors、0 warnings，Quartus 的综合、布局布线、Assembler 和时序分析均成功，并重新生成 `.sof`。

仍应避免把实际构建工程放入含中文的科研资料目录，因为 Questa 工作库/SQLite 文件对中文路径的兼容性不稳定。后续工程统一使用上述 `C:\Users\Lizzie\OneDrive\FPGA` 根目录；科研说明和测试数据仍可保存在当前中文目录。

OneDrive 复制出的目录可能带有 `ReadOnly` 属性，Quartus 会因此报告无法写入数据库目录。迁移时已经清除此属性；若后续从其他电脑或压缩包重新同步工程并出现相同错误，应先检查工程目录是否被标记为只读。

当前已验证参考工程位于：

```text
C:\Users\Lizzie\OneDrive\FPGA\jtag_led_demo
```

该工程可作为后续模拟芯片测试工程的脚本和通信框架参考，但不需要继承其具体 LED 功能逻辑。

## 5. 已确认可复用的 FPGA—电脑交互能力

### JTAG/ISSP

已验证可以使用 `altsource_probe` 和 `quartus_stp` Tcl 接口，在电脑与 FPGA 之间传递少量控制字和状态量。

推荐后续继续采用：

```text
电脑写入 source
    → FPGA 时钟域同步
    → request/ack 握手
    → FPGA 执行命令
    → probe 回读执行状态和结果
```

JTAG 与 FPGA 用户时钟属于异步时钟域，不能直接把 source 总线作为功能逻辑输入。已经验证可用的处理方式是：

- 双触发器同步；
- request toggle / ack toggle 握手；
- FPGA 在识别到新 request 后一次性锁存完整控制字；
- 主机等待 `ack == request`，并检查 `busy` 和回读数据。

这一结构适合后续从电脑下发：

- 模拟芯片寄存器控制字；
- 串行发送位数、时钟频率和等待时间；
- 测试模式、重复次数和启动命令；
- 扫描参数或校准码。

### SignalTap

已验证 SignalTap 能够：

- 随 FPGA 设计一起编译进入 `.sof`；
- 以 FPGA 内部时钟采样内部数字信号；
- 等待指定触发条件；
- 通过 JTAG 自动读取采集 RAM；
- 导出 CSV 和 VCD；
- 使用 Python 对波形进行自动断言。

后续模拟芯片工程建议监控：

- request/ack/busy/done；
- 串行发送状态机；
- bit 计数器；
- 待发送控制字；
- GPIO `DATA`、`CLK`、`CS/LOAD/LATCH`；
- 错误和超时标志。

SignalTap 与 ISSP 共用同一条 JTAG。两个工具不能同时独占设备。若需要“ISSP 制造事件、SignalTap 捕获事件”，应继续使用已经验证的流程：

```text
ISSP 下发延迟执行命令
→ ISSP 释放 JTAG
→ SignalTap 布防
→ FPGA 延迟结束后执行
→ SignalTap 捕获
→ ISSP 重新连接并回读结果
```

## 6. JTAG 使用注意事项

同一时间尽量只保留一个活跃的 JTAG 会话。以下工具可能相互争用：

- Quartus Programmer；
- SignalTap Logic Analyzer；
- In-System Sources and Probes Editor；
- `quartus_stp` Tcl 脚本；
- System Console。

常见占用错误包括：

```text
JTAG chain in use
There is already an active In-System Sources and Probes session
```

出现这类错误时，应先停止 SignalTap 采集并关闭其他调试窗口，再重试脚本。

快速检查下载线和器件：

```powershell
C:\altera_lite\25.1std\quartus\bin64\jtagconfig.exe --debug
```

正常情况下应看到：

```text
DE-SoC [USB-*]
4BA00477 SOCVHPS
02D120DD 5CSE...
```

USB 编号可能变化，后续脚本不应硬编码 `USB-1`，而应动态匹配 `DE-SoC [USB-*]`。

## 7. 后续模拟芯片测试工程建议结构

建议保留以下分层：

```text
host/
  Python GUI、批量测试和数据记录

rtl/
  JTAG 命令接收与握手
  GPIO 串行发送器
  时序/状态控制
  回读与错误状态

sim/
  Questa testbench

quartus/
  QPF/QSF/SDC/STP 和官方管脚约束

scripts/
  仿真、编译、下载、SignalTap 捕获和一键验收
```

电脑端只负责下发高层参数和读取结果；严格的串行位间隔、GPIO 边沿和芯片时序应由 FPGA 状态机产生，避免依赖 Windows/Python 的实时性。

## 8. 下一阶段开始前必须明确的信息

在连接模拟芯片之前，应先获得并确认：

1. 模拟芯片的数字接口类型和完整时序图；
2. 控制字宽度、位序（MSB/LSB first）和锁存方式；
3. `DATA`、`CLK`、`CS/LOAD/LATCH`、复位等信号定义；
4. 最大/最小时钟频率、建立时间、保持时间和脉宽；
5. 模拟芯片数字 I/O 电压和 FPGA GPIO 电压；
6. 是否需要电平转换、串联电阻、上拉/下拉或隔离；
7. DE1-SoC GPIO 的实际连接管脚。

特别注意：在电气电压和绝对最大额定值未确认前，不应直接连接 FPGA GPIO 与模拟芯片。管脚约束必须从 Terasic Rev.F/G 官方资料提取，不凭记忆填写。

## 9. 当前环境结论

以下前置工作已经完成，后续任务无需重复：

- Quartus、Cyclone V 支持和 Questa FSE 安装；
- Questa 许可证配置；
- Python/Tkinter 验证；
- Windows 虚拟机 USB-Blaster 透传；
- HPS/FPGA JTAG 链识别；
- `.sof` 编译和下载；
- 电脑通过 ISSP 控制 FPGA 并回读；
- SignalTap 实际硬件采集、CSV/VCD 导出和自动解析；
- JTAG 工具争用问题及延迟执行解决方案验证。

下一阶段可以直接从模拟芯片接口定义、GPIO 电气连接和串行发送器 RTL 设计开始。
