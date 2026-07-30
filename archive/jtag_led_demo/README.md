# DE1-SoC JTAG LED 全流程验证工程

本工程验证下面这条完整链路：

```text
Windows GUI/脚本
  → USB-Blaster/JTAG
  → In-System Sources and Probes (ISSP)
  → 双触发器同步与 request/ack 握手
  → FPGA 锁存 10 位控制字
  → DE1-SoC LEDR[9:0]
  → ISSP probe 回读
  → SignalTap 自动触发、CSV/VCD 导出与断言
```

目标器件固定为 Terasic DE1-SoC Rev.F/G 上的
Cyclone V SoC `5CSEMA5F31C6`。本阶段只通过 JTAG 下载 `.sof`，
断电后设计会失效，不写入非易失配置器件。

## 已验证的环境

- Windows 虚拟机
- Quartus Prime Lite 25.1std
- Questa – Altera FPGA Starter Edition 2025.2
- Python 3.12 与 Tkinter
- 下载线 `DE-SoC [USB-1]`（脚本不会硬编码 USB 编号）
- JTAG 链：
  - HPS TAP `4BA00477`
  - FPGA TAP `02D120DD`
- 调试节点：
  - ISSP `Source/Probe #0`
  - SignalTap `Node 30106E00`

虚拟机的 USB 透传规则应同时覆盖 Terasic 下载线的初始化 PID
`09FB:6810` 和运行 PID `09FB:6010`。Quartus、Programmer、
In-System Sources and Probes Editor 与 SignalTap 不要同时占用同一条
JTAG 链。

脚本使用工具的绝对安装路径，因此不依赖用户 `Path`。Questa 许可证仍由
系统变量 `SALT_LICENSE_SERVER` 提供。

## 硬件接口

ISSP 使用一个 12 位 source 和一个 12 位 probe：

```text
source[9:0]  = 控制字
source[10]   = 延迟执行
source[11]   = request_toggle

probe[9:0]   = 已锁存/显示值
probe[10]    = busy
probe[11]    = ack_toggle
```

普通写入立即执行。全流程验证写入延迟命令，FPGA 等待 5 秒后更新；
主机在此期间释放 ISSP 会话并布防 SignalTap，从而避免两个 JTAG 工具争用。

时钟、按键和 LED 管脚取自
[Terasic DE1-SoC Rev.F/G 官方 System CD](https://download.terasic.com/downloads/cd-rom/de1-soc/)：

| 信号 | FPGA 管脚 |
|---|---|
| `CLOCK_50` | `PIN_AF14` |
| `KEY[0]` | `PIN_AA14` |
| `LEDR[0..9]` | `V16, W16, V17, V18, W17, W19, Y19, W20, W21, Y21` |

全部采用 `3.3-V LVTTL`。`KEY0` 是低有效复位。

## 快速使用

在 PowerShell 中执行：

```powershell
cd C:\FPGA\jtag_led_demo
```

只运行 Questa 仿真：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\simulate.ps1
```

只进行 Quartus 完整编译：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

只检查 USB-Blaster/JTAG：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\jtag_check.ps1
```

下载 `.sof`：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\program.ps1
```

启动 GUI：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_gui.ps1
```

一键执行仿真、编译、JTAG 检查、下载、延迟写入、SignalTap 捕获、
ISSP 回读和波形断言：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_full_flow.ps1 -Value 1
```

若当前 RTL 已经编译且只想重复硬件验证，可加 `-SkipBuild`。

GUI 接受十进制 `0–1023` 或十六进制 `0x000–0x3FF`。例如：

- `1`：仅 `LEDR0` 点亮；
- `3`：`LEDR1:0` 点亮；
- `1023` 或 `0x3FF`：10 个红色 LED 全亮。

非法字符、负数和大于 1023 的输入只会在 GUI 报错，不会访问硬件。

## SignalTap 配置与验收

SignalTap 是 HDL 实例，参数如下：

- 采样时钟：`CLOCK_50`
- 深度：512
- 数据宽度：45
- 中心触发：`update_pulse` 上升沿
- 信号：请求字、待处理值、busy、ack、锁存值、`LEDR`、`update_pulse`

每次全流程验证都会生成：

```text
artifacts\full_flow.csv
artifacts\full_flow.vcd
artifacts\full_flow_runtime.stp
```

自动解析器检查：

1. deferred 位为 1，请求值正确；
2. `busy` 在更新周期由 1 清零；
3. `update_pulse` 只保持一个 `CLOCK_50` 周期；
4. `pending_value`、锁存值和 LED 等于期望值；
5. `ack_toggle == request_toggle`；
6. ISSP probe 回读值、busy 和 ack 与波形一致。

任何 JTAG 断开、链路不唯一、器件不匹配、调试节点缺失、工具占用、
SignalTap 超时、导出失败或回读不一致都会返回非零退出码。

## 工程结构

```text
rtl\       独立命令接收模块与硬件顶层
sim\       Questa testbench
quartus\   QPF/QSF/SDC/STP
host\      ISSP/SignalTap Tcl、GUI、CSV 验证器
scripts\   仿真、编译、下载、GUI 与一键验证入口
artifacts\ 硬件采集输出（运行后生成）
```

`jtag_command_receiver.sv` 不依赖厂商 IP，可直接在 Questa 中验证。
厂商相关的 `altsource_probe` 和 `sld_signaltap` 只位于硬件顶层，方便后续把
LED 执行部分替换为模拟芯片 GPIO 串行发送器，同时保留 GUI、握手、JTAG
和自动验证框架。

## 官方接口资料

- [In-System Sources and Probes Tcl 接口](https://docs.altera.com/r/docs/683552/18.1/intel-quartus-prime-standard-edition-user-guide-debug-tools/tcl-interface-for-the-in-system-sources-and-probes-editor)
- [SignalTap 命令行捕获](https://docs.altera.com/r/docs/683819/26.1/quartus-prime-pro-edition-user-guide-debug-tools/data-capture-from-the-command-line)
- [SignalTap HDL 实例生成 STP](https://docs.altera.com/r/docs/683552/18.1/intel-quartus-prime-standard-edition-user-guide-debug-tools/creating-a-.stp-file-from-a-design-instance)
