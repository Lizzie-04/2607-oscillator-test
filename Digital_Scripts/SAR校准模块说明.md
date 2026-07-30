# DigOsc SAR频率校准模块说明

## 1. 功能目标

SAR模块根据外部输入的一位比较结果，在 `0～1` 范围内逐次调整送入 `adder` 的校准量。

频率测量与目标频率比较不在FPGA内部完成：

```text
FPGA输出当前SAR试探码
        ↓
Python等待数字与模拟通路稳定
        ↓
频率仪器测量芯片输出频率
        ↓
Python比较实测频率与目标频率
        ↓
Python向FPGA提交一次1-bit比较结果
        ↓
FPGA立即执行一次SAR判断并给出下一试探码
```

FPGA内部不进行多数投票、平均或其他测量结果统计。每次 `SAR_compare_valid` 只采纳当前的一个 `SAR_bit_in`。

## 2. 文件组成

- `Digosc_code/sar_calibrator.v`
  - SAR逐次逼近状态机。
- `Digosc_code/adder.v`
  - 计算原始误差、加入SAR校准量并进行饱和积分。
- `Digosc_code/DigOsc.v`
  - 实例化SAR并将校准量接入adder。
- `sim/tb_sar_calibrator.sv`
  - 验证单比特搜索、数据格式、上下边界、状态保持和重新启动。
- `sim/tb_adder_calibration.sv`
  - 验证校准加法、积分极性及正负饱和。
- `scripts/simulate_sar.ps1`
  - Questa全量编译与仿真入口。

## 3. SAR与adder的数据格式

`adder` 的 `A`、`B` 以及多项式计算结果采用：

```text
总位宽：29 bit
类型：signed二进制补码
小数位：16 bit
```

这是由多项式模块中乘法结果取 `mult_out[44:16]` 确定的。

SAR不再使用单独的17位Q1.16端口。`SAR_code` 与 `A`、`B` 一样，直接使用：

```verilog
signed [DATA_WIDTH2-1:0]
```

默认即：

```verilog
signed [28:0]
```

SAR仅生成其中的非负 `0～1` 区间：

| 29位存储整数 | 实际数值 |
|---:|---:|
| `0` | 0 |
| `1` | \(1/65536\) |
| `32768` | 0.5 |
| `65535` | \(1-1/65536\) |
| `65536` | 1.0 |

因此：

```text
SAR_code、A、B和adder.calibration
```

具有相同的：

- 总位宽；
- signed属性；
- 二进制补码解释；
- 16位小数位置；
- 数值权重。

顶层不再进行17位到29位的零扩展格式转换，而是把 `SAR_code` 直接送入 `adder.calibration`。

## 4. 单比特SAR算法

搜索从0.5开始：

```text
第1个试探码：0.5
```

每个步骤只等待一次有效比较：

```text
SAR_compare_valid = 1
```

在该时钟沿读取：

```text
SAR_bit_in = 1：实测频率偏低，需要保留/增大SAR码
SAR_bit_in = 0：实测频率偏高，需要清除/减小SAR码
```

随后立即确定当前试探位，并置位下一位。例如：

```text
初始试探：0.500000

bit_in=1 → 保留0.5，下一试探为0.750000
bit_in=0 → 清除0.5，下一试探为0.250000
```

继续逐位逼近：

```text
0.5
 ↓
0.25或0.75
 ↓
0.125、0.375、0.625或0.875
 ↓
……
 ↓
1/65536分辨率
```

16个小数位对应固定16次比较。不存在FPGA内部重复采样：

```text
一次试探码 + 一次1-bit比较 = 完成一个SAR步骤
```

最后一位完成后：

- 普通结果位于 `0～65535`；
- 当当前码为 `65535` 且 `SAR_bit_in=1` 时，输出精确上限 `65536`，即1.0；
- `SAR_done` 置位，`SAR_busy` 清零。

## 5. 顶层接口

### 输入

| 信号 | 含义 |
|---|---|
| `SAR_enable` | 1时把SAR码加入adder；0时校准项强制为0 |
| `SAR_start` | 单周期脉冲；从0.5开始或重新开始搜索 |
| `SAR_compare_valid` | 单周期脉冲；表明本周期提交一个有效比较位 |
| `SAR_bit_in` | 唯一的比较数据位；1表示需要增大，0表示需要减小 |

### 输出

| 信号 | 含义 |
|---|---|
| `SAR_code[28:0]` | 当前试探码或最终码，与adder的A/B格式相同 |
| `SAR_trial_valid` | 当前试探码有效，可由上位机读取和测量 |
| `SAR_compare_ready` | 1表示SAR可以接收一个比较结果 |
| `SAR_busy` | 1表示16步搜索尚未结束 |
| `SAR_done` | 锁存型完成标志，保持到下一次 `SAR_start` |
| `SAR_step_index[7:0]` | 已完成的比较次数；结束时为16 |
| `calibrated_error_test[28:0]` | `A-B+SAR` 的饱和结果 |

所有SAR命令输入必须同步到 `clk`。未来通过JTAG与Python连接时，应由 `clk` 域中的命令接收逻辑产生 `SAR_start` 和 `SAR_compare_valid` 单周期脉冲。

## 6. Python控制流程

示意伪代码：

```python
driver.set_sar_enable(True)
driver.pulse_sar_start()

while driver.read_sar_busy():
    driver.wait_for_sar_trial()
    trial_code = driver.read_sar_code()

    # 等待新的试探码经过adder、DSM和模拟环路并稳定。
    wait_for_settling(trial_code)

    measured_hz = instrument.measure_frequency()

    # 每个试探码只向FPGA提交一个比较位。
    driver.submit_sar_compare(
        bit_in=(measured_hz < target_hz),
    )

final_code = driver.read_sar_code()
assert driver.read_sar_done()
```

这里假设“SAR码增大使输出频率增大”。如果实际芯片方向相反，Python应把比较位改为：

```python
bit_in = measured_hz > target_hz
```

频率容差、重复测量或软件滤波均可由Python在生成最终1-bit判断前完成。当前FPGA接口没有第二个“匹配/提前结束”数据位，因此必须收到16次比较才会置位 `SAR_done`；仅停止发送比较会让SAR保持当前试探码和 `SAR_busy=1`。

## 7. adder中的注入

原始差值：

\[
F=A-B
\]

校准后的误差：

\[
E_{cal}=A-B+C_{SAR}
\]

其中：

\[
0\le C_{SAR}\le1
\]

积分器根据 `polarity` 选择方向：

\[
D[n]=\operatorname{sat}(D[n-1]+E_{cal}[n])
\]

或：

\[
D[n]=\operatorname{sat}(D[n-1]-E_{cal}[n])
\]

`sat` 表示29位有符号饱和，防止溢出后正负号翻转。

`SAR_enable=0` 时：

\[
C_{SAR}=0
\]

此时系统退化为未加入SAR校准量的原始误差积分。

## 8. 仿真

在工程根目录运行：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\Digital_Scripts_On_Chip\scripts\simulate_sar.ps1
```

测试覆盖：

- SAR输出与adder A/B同为29位signed、16位小数格式；
- 每个小数位只消耗一次1-bit比较；
- 0和1.0两个闭区间端点；
- 任意中间目标码的逐次逼近；
- `busy`、`done`、`trial_valid` 和重新启动；
- adder校准项注入；
- 两种积分极性；
- 正负溢出的饱和处理；
- 包含 `DigOsc` 顶层在内的全部生产RTL编译。
