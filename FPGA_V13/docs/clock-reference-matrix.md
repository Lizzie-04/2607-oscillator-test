# FPGA V4 时钟与参考信号使用矩阵

## 1. 结论

V2 按 `filter_down_two_channel.v` 的 `clk_SAMPLE / clk_DATA` 职责重新划分：

```text
外部 FS_DATA_OUT33（clk_SAMPLE）
    └─ 只在上升沿采样 BS_T_OUT33、BS_C_OUT33
       并推进两路三角窗降采样累加器
             │
             └─ 稳定数据 + 完成翻转位 CDC
                         ↓
FPGA 生成的 CLK_3P3（clk_DATA，2 MHz）
    ├─ 接收完整 μ 窗口结果
    ├─ Divider / Polynomial / Adder
    ├─ SAR
    ├─ Filter-up / Digital DSM
    └─ 实时 μ 与 100 点统计写入
                         │
                         └─ CDC / 双时钟 RAM
                                     ↓
CLOCK_50（50 MHz，电脑交互与控制域）
    ├─ JTAG ISSP / GPIO1
    ├─ 协议、寄存器和仲裁
    ├─ 474-bit、N_DAQ 样点芯片驱动
    └─ 统计 RAM 读口
```

因此，FS 是一个窄范围的外部采样时钟/采样参考，不再是 SAR 或整个数字
算法链的系统时钟。FS 停止时不会产生新的 μ 数据，但 SAR、JTAG、GPIO、
474-bit 驱动和 2 MHz 数字后级仍然可以运行。

## 2. 与参考代码的对应关系

用户提供的参考代码连接为：

| 参考端口 | 原顶层连接 | 含义 | V2 对应 |
|---|---|---|---|
| `clk_SAMPLE` | 外部 `In_26` | 在上升沿采样 `BS` 并推进降采样累加 | `FS_DATA_OUT33` |
| `clk_DATA` | `clk_SYS` | 通道/完成状态及全部后级数字处理 | FPGA 生成的 `CLK_3P3`，2 MHz |
| `bs_in` | `In_24` | 被采样的比特流 | `BS_T_OUT33`、`BS_C_OUT33` 两路独立输入 |
| `sample` | `In_22` | 单根 BS 输入的通道选择 | V2 不需要；两根 BS 在同一个 FS 上升沿同时采样 |

参考模块直接让 `path`、`valid_0/1` 和 `filter_out0/1` 跨越两个时钟域。
V2 不复制这种未同步跨域，而是让 FS 域在窗口结束时锁存完整结果并翻转
事件位；2 MHz 域先同步事件位，并在数据已稳定多个 2 MHz 周期后一次性
接收多比特结果。

## 3. 修改前：原 V2/V1 时钟使用

| 模块/逻辑 | 修改前时钟或参考 | 修改前行为 |
|---|---|---|
| `fpga_v2_top` / ISSP | `CLOCK_50` | JTAG source/probe 及顶层主机接口 |
| `jtag_request_receiver` | `CLOCK_50` | 同步 JTAG 异步邮箱并产生请求 |
| `gpio_host_bridge` | `CLOCK_50` | 同步 GPIO1 请求 |
| `host_command_arbiter` | `CLOCK_50` | JTAG/GPIO 仲裁 |
| `fpga_v2_core` 协议寄存器 | `CLOCK_50` | opcode、寄存器、错误状态 |
| `chip_control_driver` | `CLOCK_50` | 474-bit 与可配置 `N_DAQ` 时序 |
| `clock_generator_2mhz` | 输入 `CLOCK_50` | 产生 `CLK_3P3=2 MHz` |
| `triangular_decimator_runtime` | `FS_DATA_OUT33` | 采样两路 BS、累加、归一化并产生 μ |
| `sample_mailbox_cdc` | `FS → CLOCK_50` | μ 直接从 FS 域送到主机域 |
| `statistics_capture_buffer` | 写口 `FS`，读口 `CLOCK_50` | 统计捕获依赖 FS |
| On-Chip 两个 `filter_down` | `FS_DATA_OUT33` | 两路固定三角窗 |
| `divider` | `FS_DATA_OUT33` | 除法后级依赖 FS |
| `poly_engine_system` | `FS_DATA_OUT33` | Batch 多项式依赖 FS |
| `poly_engine_freq` | `FS_DATA_OUT33` | Frequency 多项式依赖 FS |
| `adder` | `FS_DATA_OUT33` | 误差和校准码求和依赖 FS |
| `sar_calibrator` | `FS_DATA_OUT33` | SAR 启动、每步判决依赖 FS |
| `filter_up` 数据口 | `FS_DATA_OUT33` | 输入更新和积分依赖 FS |
| `filter_up` DSM口 | `CLK_3P3` | 2 MHz 读积分输出 |
| `digital_DSM` | `CLK_3P3` | 2 MHz 产生 `DSM_3P3` |
| SAR 命令 CDC | `CLOCK_50 → FS` | 没有 FS 时启动/判决不能到达 SAR |
| SAR 状态 CDC | `FS → CLOCK_50` | 没有 FS 时状态不能更新 |
| `OSC_OUT33_TO_FPGA` | 未使用 | 仅保留 PCB 输入端口 |

修改前的问题不是“FS 不能采样 BS”，而是 FS 同时承担了采样参考和几乎全部
算法系统时钟。FS 缺失或停顿时，SAR、Polynomial、Adder 等与 BS 采样无关
的逻辑也会停住。

## 4. 修改后：当前 V4 全部模块时钟使用

### 4.1 50 MHz 电脑交互与芯片控制域

| 模块/实例 | 当前时钟 | 参考/数据信号 | 说明 |
|---|---|---|---|
| `altsource_probe host_issp` | `CLOCK_50` source clock | JTAG source/probe | 主机交互保持不变 |
| `jtag_request_receiver` | `CLOCK_50` | 异步 JTAG 64-bit 邮箱 | toggle 后等待 payload 稳定 |
| `gpio_host_bridge` | `CLOCK_50` | GPIO1 请求和数据 | 两级同步/握手保持不变 |
| `host_command_arbiter` | `CLOCK_50` | JTAG、GPIO 请求 | JTAG 优先，保持型请求只消费一次 |
| `fpga_v2_core` 协议逻辑 | `CLOCK_50` | opcode/index/data | 寄存器、错误、命令应答 |
| DSM输入覆盖寄存器 | `CLOCK_50` | 保留的29-bit signed Q16覆盖值 | 通过toggle提交并符号扩展到37 bit |
| FPGA算法系数影子库 | `CLOCK_50` | 16个37-bit signed Q20.16系数 | 48分片完整后与PCB字原子提交 |
| `chip_control_driver` | `CLOCK_50` | 474-bit 控制字 | 产生 0.5 ms 半拍和 `N_DAQ` 事务 |
| `control_word_unpack` | 组合逻辑 | 已提交的 474-bit 字 | 无时钟 |
| 统计 RAM 读口 | `CLOCK_50` | 地址 0～99、三个 16-bit 分片 | 仅 done 后读取 |
| 状态同步寄存器 | `CLOCK_50` | 2 MHz 域 SAR/统计/Filter 状态 | 两级采样后供寄存器和 GUI 使用 |
| 实时 μ 批次快照寄存器 | `CLOCK_50` | μ1、μ2、片内μ1、片内μ2、样本序号、`N_FILTER` | `OP_LATCH_LIVE_SAMPLE` 原子复制，寄存器21…31只读快照 |
| 校准批次快照寄存器 | `CLOCK_50` | 两个Polynomial、SAR输入、DSM实际输入、序号、状态 | `OP_LATCH_CALIBRATION_SAMPLE`原子复制，寄存器32…41只读快照 |

### 4.2 FS 外部采样域

| 模块/实例 | 当前时钟 | 被采样信号 | 说明 |
|---|---|---|---|
| `reset_synchronizer sample_reset` | `FS_DATA_OUT33` | 全局复位 | 异步置位、FS 域同步释放 |
| `triangular_decimator_runtime` 采样部分 | `FS_DATA_OUT33` | `BS_T_OUT33`、`BS_C_OUT33` | 每个 FS 上升沿同时采样两路 BS，并推进可调三角窗 |
| `filter_down_two_clock fd0` 采样部分 | `FS_DATA_OUT33` | `BS_T_OUT33` | 保持 On-Chip 固定 N、位宽和截断行为 |
| `filter_down_two_clock fd1` 采样部分 | `FS_DATA_OUT33` | `BS_C_OUT33` | 与 fd0 同时采样 |
| `configured_length` 接收 | `CLOCK_50 → FS` | `N_FILTER` | 两级同步，只在窗口边界生效 |

FS 不连接 SAR、Divider、Polynomial、Adder、Filter-up、Digital DSM、统计
状态机、JTAG 或 474-bit 驱动。

### 4.3 FPGA 生成的 2 MHz 数据域

| 模块/实例 | 当前时钟 | 输入来源 | 说明 |
|---|---|---|---|
| `clock_generator_2mhz` | `CLOCK_50` | 50 MHz | ÷25，输出平均 2 MHz，12/13 个 50 MHz 周期高/低 |
| `reset_synchronizer core_2mhz_reset` | `CLK_3P3` | 全局复位 | 2 MHz 域同步释放 |
| `triangular_decimator_runtime` 接收部分 | `CLK_3P3` | FS 域 μ 保持寄存器/翻转位 | 输出单周期 `mu_valid` |
| `sample_mailbox_cdc` | `CLK_3P3 → CLOCK_50` | μ1、μ2 | 主机实时读数、sequence、toggle |
| `sample_mailbox_cdc` | `CLK_3P3 → CLOCK_50` | 片内μ1、片内μ2 | On-Chip `filter_down_two_clock` valid 后的直接输出 |
| `calibration_mailbox_cdc` | `CLK_3P3 → CLOCK_50` | 四路37-bit signed Q20.16校准快照和覆盖状态 | 稳定payload + toggle，覆盖应用也产生新序号 |
| `statistics_capture_buffer` 写口 | `CLK_3P3` | `mu_valid`、μ1、μ2 | 严格捕获 100 个完整窗口结果 |
| `sar_calibrator` | `CLK_3P3` | 50 MHz 域启动/判决 CDC | 固定 16 步；signed Q16范围-0.5～+0.5；不再依赖 FS |
| `filter_down_two_clock` 接收部分 | `CLK_3P3` | FS 域固定窗结果/翻转位 | 为 On-Chip 后级产生 `valid` |
| `divider` | `CLK_3P3` | BS_T 固定窗结果、ALPHA | On-Chip 后级 |
| `poly_engine_system` | `CLK_3P3` | Divider、Batch 系数 | On-Chip 后级 |
| `poly_engine_freq` | `CLK_3P3` | BS_C 固定窗结果、Polynomial | On-Chip 后级 |
| `adder` | `CLK_3P3` | 两个37-bit Polynomial、37-bit SAR code | 扩展位宽计算，D/F/校准误差分别限制到±0.5 |
| `filter_up` 数据口 | `CLK_3P3` | Adder 积分输出 | valid时锁存积分结果 |
| `filter_up` DSM口 | `CLK_3P3` | 积分保持值 | 当前与数据口同为 2 MHz |
| `digital_DSM` | `CLK_3P3` | Filter-up输出或GUI覆盖值 | ±0.5端点强制、范围内16-bit有界相位累加，产生`DSM_3P3` |
| SAR start/compare CDC | `CLOCK_50 → CLK_3P3` | GUI/JTAG 命令 | toggle 脉冲传递 |
| 统计 start CDC | `CLOCK_50 → CLK_3P3` | 统计启动命令 | 与 FS 无关地完成布防 |

### 4.4 外部参考和输出信号

| 信号 | 当前用途 |
|---|---|
| `FS_DATA_OUT33` | 仅为两路 BS 降采样前端的 `clk_SAMPLE` |
| `BS_T_OUT33` | 仅在 FS 上升沿被 T 通道采样 |
| `BS_C_OUT33` | 仅在 FS 上升沿被 C 通道采样 |
| `CLK_3P3` | FPGA 内部 2 MHz `clk_DATA/clk_DSM`，同时输出到 PCB |
| `DSM_3P3` | 2 MHz 数字 DSM 输出 |
| `OSC_OUT33_TO_FPGA` | 仍未参与算法，只保留端口 |
| `SR_CLK` | 由 50 MHz 状态机产生的 1 kHz 芯片移位输出，不作为 FPGA 内部时钟 |

## 5. 跨时钟域规则

| 跨域方向 | 传递内容 | 机制 |
|---|---|---|
| `CLOCK_50 → FS` | `N_FILTER` | 两级同步；窗口边界锁定 |
| `CLOCK_50 → 2 MHz` | 已清零算法字段的474-bit PCB字 + 16个37-bit系数 | 数据两级采样 + 共享toggle + 2 MHz域原子锁存 |
| `CLOCK_50 → 2 MHz` | DSM输入覆盖 enable/value | 数据两级采样 + toggle 同步 + 2 MHz 域原子锁存 |
| `FS → 2 MHz` | μ1、μ2、固定窗结果 | 数据保持 + toggle 两级同步 + 延迟捕获 |
| `CLOCK_50 → 2 MHz` | SAR start/compare、统计 start | `pulse_toggle_cdc` |
| `2 MHz → CLOCK_50` | 实时 μ 与片内 μ | `sample_mailbox_cdc`，再由 `OP_LATCH_LIVE_SAMPLE` 复制为主机批次快照 |
| `2 MHz → CLOCK_50` | 校准链路四路值和覆盖状态 | `calibration_mailbox_cdc`，再由`OP_LATCH_CALIBRATION_SAMPLE`复制为主机快照 |
| `2 MHz → CLOCK_50` | SAR/统计/Filter 状态 | 两级状态同步；主机完成条件允许收敛 |
| `2 MHz ↔ CLOCK_50` | 统计 RAM | 2 MHz 写口、50 MHz 同步读口；协议禁止同时读写 |

虽然 2 MHz 由 50 MHz 分频产生，但两个逻辑域之间的所有业务数据均使用
上述 CDC 机制，不依赖可变的全局时钟插入延迟或固定边沿相位。SDC 因此将
`CLOCK_50`、`CORE_CLK_2MHZ` 和外部 FS 分成三个逻辑异步组；每个域内部
仍分别执行完整 setup/hold 分析。

## 6. FS 停止时的确定行为

- 两路 BS 不再被采样；
- 当前三角窗停在原位置，FS 恢复后从该位置继续；
- 不产生新的 μ、趋势点或统计点；
- 已完成并锁存的窗口结果仍可被 2 MHz 域接收；
- 统计任务保持 busy，等待后续 FS 样点，不会伪造数据；
- SAR 可以正常启动、接收 16 次判决并结束；
- JTAG/GPIO、协议寄存器、474-bit 芯片配置和 `CLK_3P3/DSM_3P3`
  继续运行。
