# FPGA V11：FPGA内部Q20.16系数与Adder/DSM限幅

`FPGA_V11` 是从 `FPGA_V10` 静态源码独立复制的工程。V10的运行数据、日志、
`transcript`、`__pycache__` 和Quartus旧构建产物没有复制到V11。

## 两条独立配置通路

### FPGA内部算法系数

GUI/Avalon向FPGA内部写入16个37-bit signed Q20.16系数：

```text
ALPHA,
Batch_a0…Batch_a6,
poly_a0…poly_a7
```

数值范围为 `-1048576.0` 至 `1048575.9999847412109375`。Avalon窗口为
`0x0800…0x08BC`，共48个4字节步进的16-bit有效字。每个系数按低16位、
中16位、高5位传输，高分片 `[15:5]` 必须为0。核心opcode为 `0xE`。

系数先写入影子寄存器；48个分片未全部到齐时禁止提交。现有提交
opcode会同时锁定30个PCB控制字块和48个FPGA系数分片，然后才启动外部
串行时序和内部算法。成功后两张有效位图一起清空。

### PCB外部串行控制字

PCB接口保持不变：474 bit、30个16-bit主机传输块、474个 `SR_CLK`
上升沿，`N_DAQ` 最小值仍为968。主机打包和RTL提交会双重清零：

```text
poly    serial_bits[231:0]
Batch   serial_bits[435:233]
ALPHA   serial_bits[453:436]
```

`polarity` 位于 `serial_bits[232]`，其他开关、CS和TRIM保持GUI配置值。

## RTL数值路径

- 两支Polynomial的系数、状态和结果是37-bit signed Q20.16，乘积为74 bit，
  统一算术移除16个小数位。
- 频率支路的18-bit Q16 `mu` 零扩展进Polynomial。系统支路Divider接受
  37-bit signed ALPHA，输出仍为18-bit signed Q16，再符号扩展进Polynomial。
- SAR仍执行16步 `[-0.5,+0.5]` 搜索，在算法链中使用37-bit符号扩展值。
- Adder用扩展中间位宽计算 `A-B`、`A-B+SAR` 和积分，将 `F`、
  `calibrated_error` 和 `D` 分别裁到Q16原始码 `[-32768,+32768]`。
  D积分的是未预先裁剪的校准误差。
- Filter-up和有界DSM路径为37 bit。保留的29-bit DSM主机覆盖接口在内部
  符号扩展；`digital_DSM_reference.sv` 保持原29-bit参考行为。

DSM的确切输入规则是：

```text
input <= -0.5       -> 输出全0，清空相位
-0.5 < input < 0.5  -> density = input + 0.5
input >= +0.5       -> 输出全1，清空相位
```

## 协议与读回

- 核心协议：`0x0005`
- 实现修订：`0x0001`
- Avalon ABI：`2`
- 寄存器32…41保留四路快照的原低29位布局；42…45依次返回
  `poly_freq`、`poly_system`、SAR校正和DSM实际输入的 `[36:29]`。

V10和V11的主机程序会因协议/ABI不同而互相拒绝连接。

## GUI与JSON

ALPHA、Batch和Polynomial的现有输入框都表示“FPGA内部 signed Q20.16”，
不再存在独立PCB算法系数输入。新配置写入：

```json
{
  "schema_version": 2,
  "project": "FPGA_V11",
  "pcb_control": {},
  "fpga_algorithm_coefficients": {
    "alpha": 65536,
    "batch": [0, 0, 0, 0, 0, 0, 0],
    "poly": [0, 0, 0, 0, 0, 0, 0, 0]
  },
  "n_daq": 20000
}
```

系数用Q16缩放后的有符号整数保存。载入无版本V10 JSON时，旧Batch/
Polynomial的29-bit二补码先按原符号解释，再作为等值37-bit内部系数；
最终PCB串行算法字段仍全部为0。

## 多温度点测试

- 流程设置按“温度流程”“启动诊断”“正式频率测试”“输出设置”分组。写入尝试
  和每次写入后的3次读频仅用于启动诊断；正式重复测试次数是独立参数。
- 每个勾选的chip可分别选择 `config1`～`config6` 六份“芯片控制”页
  “保存JSON”生成的配置，允许少选，但至少选择一份。各配置的
  `N_DAQ` 独立生效；页面勾选的 `TRIM_C_COARSE` 始终覆盖JSON原值。
- 完整工况由chip、config、BYPASS和 `TRIM_C_COARSE` 共同确定。片内测试
  勾选后对BYPASS=1和BYPASS=0分别执行同一流程。
- GUI的“重复测试次数”要求输入正整数，默认为10。每个温度点的
  每种完整工况都执行该次数的频率读取。
- 实测频率看板以温度点为X轴、频率为Y轴。每次成功读数以小实心点
  显示；收齐某温度点的全部重复读数后才计算平均值，并以大空心点
  显示。只连接同一完整工况在不同温度点的平均值；原始重复点不连线。
  各完整工况使用稳定的独立颜色。启动失败和频率读取失败行不进入看板。
- 测试CSV记录 `config`、`bypass`、`trim_c_coarse`、重复序号/总次数、
  状态、温敏电阻、频率和DSM输入；不记录配置文件路径、SAR值、μ值或两个
  Polynomial engine计算值。明细文件名包含config序号，避免多配置覆盖。

## 构建和验证

在 `FPGA_V11` 目录运行：

```powershell
python -m unittest discover -s host -p "test_*.py"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\simulate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Questa和Quartus中间文件使用独立系统临时目录。Quartus正式输出为
`quartus/output_files/fpga_v11.sof`。构建脚本只执行Full Compilation，不调用
Quartus Programmer、System Console、VISA或任何SOF加载命令。

## 安全边界

- 不修改、不启动、不停止 `FPGA_V10` 任何进程。
- 不读写V10持续更新的 `data/` 目录。
- 不启动V11 GUI、System Console、VISA、JTAG验证或Quartus Programmer。
- 可生成 `fpga_v11.sof`，但绝不烧录或加载。
- 对V10的前后哈希只比较静态源码、脚本和工程文件，排除运行数据、
  日志和构建产物。

## 2026-08-08验证记录

- Python：79项单元测试全部通过，所有host Python文件通过 `py_compile`。
- Questa 2025.2：10个testbench全部通过，vlog 0 error / 0 warning。
- Quartus Prime 25.1std Full Compilation：0 error；23组用户setup/hold检查
  TNS全部为0。
- 资源：8,412 ALM（26%）、9,228寄存器、5,165 block-memory bits、3 RAM blocks、
  16 DSP blocks、67 pins。
- `fpga_v11.sof`：6,700,835 bytes，SHA-256
  `F66EE1D3AF0CB2363BD428B4BAFEE5DB6C1495846D0ADD5F773C532E89060272`。
- 本次只生成SOF，未执行任何烧录、加载或硬件连接。
