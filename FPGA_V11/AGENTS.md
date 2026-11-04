# FPGA V11 开发约束

## 不可改变的外部时序

- PCB控制字仍为474 bit、30个16-bit主机块、474个 `SR_CLK`
  上升沿，最后一位是BYPASS。
- `N_DAQ` 范围为968…65535；RESET/START/使能时序必须保持。
- PCB串行位 `[231:0]`、`[435:233]`、`[453:436]` 始终为0；
  `polarity` 和其他开关/TRIM不得被清零。

## FPGA内部数值规则

- ALPHA、7个Batch、8个Polynomial均为37-bit signed Q20.16。
- 主机分片顺序固定为ALPHA、Batch a0…a6、poly a0…a7；每个系数
  low16/mid16/high5，高分片其余位为0。
- 30个PCB块和48个系数分片必须原子提交；部分写入或粘滞错误禁止
  启动。
- Polynomial乘积为74 bit，算术移除16个小数位；不得恢复29-bit
  固定切片或固定补零。
- Adder的D、F、`calibrated_error` 均限制在Q16原始码
  `[-32768,+32768]`；不得先裁剪用于D积分的校准误差。
- DSM仅在开区间 `(-0.5,+0.5)` 内调制；两端点和越界值必须饱和并清相位。
- SAR保持16步及包含端点的 `[-0.5,+0.5]` 搜索。
- `digital_DSM_reference.sv` 必须保留原29-bit参考行为。

## 协议与工程标识

- 核心协议 `0x0005`，实现修订 `0x0001`，Avalon ABI `2`。
- Quartus工程/修订/产物统一名为 `fpga_v11`。
- JSON新写入必须是 `schema_version=2`、`project=FPGA_V11`；旧无版本
  V10 JSON只能通过明确迁移路径读取。

## 验证与安全

- 每次RTL更改至少运行Python单元测试、`py_compile`和全部Questa回归。
- 发布前运行Quartus Full Compilation，要求0 error且用户时钟setup/hold TNS为0。
- 所有仿真/综合中间文件必须位于V11专用系统临时目录。
- 不得启动GUI、System Console、VISA、JTAG硬件验证、Quartus Programmer
  或任何SOF加载命令。
- 不得修改或干扰 `FPGA_V10`；V10运行数据不参与哈希比较。
- 不部署SignalTap。
