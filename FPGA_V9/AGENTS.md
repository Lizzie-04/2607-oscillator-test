# FPGA V4 开发约束

## 不变量

- 474 位控制字必须与 `Matlab_Scripts/drive.m` 的字段顺序一致，并在发送前整体反转。
- `SR_CLK` 必须严格产生 474 个上升沿，第 474 个上升沿锁存 `BYPASS`。
- 默认半周期为 25,000 个 50 MHz 时钟，即 0.5 ms；默认 `N_DAQ=1500`，
  即默认总事务为750 ms。
- SAR 只接收外部单 bit 判决，不在 FPGA 内加入多数投票。
- SAR code 和 adder A/B 保持 29-bit signed Q16，SAR 搜索范围为 -0.5…+0.5，
  启动首码为0，两个端点均可达。
- 三角窗总长 `L_FILTER=2×N_FILTER`、权重和为 `N_FILTER²`；
  `N_FILTER` 只能在窗口边界切换。
- 默认 `L_FILTER=2048`，所以 RTL/opcode 内部默认 `N_FILTER=1024`。
- GUI可调 `L_FILTER=2048…100000`，协议内部半长
  `N_FILTER=1024…50000`。
- `N_DAQ` 仅指原 MATLAB/LabVIEW 控制字驱动波形容器的总样点数，通用默认1500，
  多温度点校准默认配置默认20000；GUI/主机可按次设置；禁止把它用作 Filter 默认值。
- `START_DLL` 和 `START_ANA` 在 `N_DAQ` 最末尾10个0.5 ms样点拉高，事务结束
  后继续保持为高，直到下一轮控制字驱动开始或FPGA复位。
- `RESET` 在 `START_DLL`/`START_ANA` 已经拉高后的 `N_DAQ` 倒数第五个
  0.5 ms样点拉高一拍。
- 该 `RESET` 脉冲同时作为片内数字算法域运行复位；主机/JTAG命令通路、
  控制字驱动器和已同步的控制字配置不得被该脉冲清空。
- V4 不加入 SignalTap。
- 统计缓存每批严格捕获100次`mu_valid`；点击前已开始的窗口必须丢弃。
- 统计任务期间不得切换`N_FILTER`；每批地址严格为0…99。
- 统计样本固定打包为`{mu2[17:0], mu1[17:0]}`并拆成三个16-bit响应字。
- 连续趋势读取允许跳过 FPGA 中间产生的 μ 点，但必须先用
  `OP_LATCH_LIVE_SAMPLE` 在 50 MHz 域锁存四路快照；GUI 不得显示来自不同窗口的
  μ1、μ2、片内μ1、片内μ2、样本序号或 Filter 长度组合。
- V4只读协议版本固定为`0x0004`，实现修订固定为`0x0003`。
- `FS_DATA_OUT33`只作为`clk_SAMPLE`驱动两路BS降采样前端；
  SAR及全部On-Chip后级使用FPGA产生的2 MHz `clk_DATA`。
- FS域完整窗口结果必须通过稳定数据与完成翻转位送入2 MHz域，禁止多比特直跨域。
- 校准界面的 DSM 覆盖只能位于 `filter_up` 到 `digital_DSM` 的输入 mux；
  覆盖时 Divider、Polynomial、Adder 和 Filter-up 仍继续运行。
- DSM实际输入保留完整29-bit signed Q16；`<=-0.5`强制全0、`>=+0.5`强制
  全1，中间范围使用有界16-bit相位累加。GUI不得把超范围原值裁剪成±0.5。
- 校准看板必须先用`OP_LATCH_CALIBRATION_SAMPLE`锁存寄存器32…41，四路值、
  序号、有效位和覆盖状态必须来自同一个2 MHz域邮箱批次。

## 片内代码兼容性

`rtl/on_chip` 用于和未来流片版本做错误复现。修改前必须：

1. 先在独立分支或新模块证明修改；
2. 保留原版本作为可选择的 reference path；
3. 增加能体现位宽、符号和截断差异的 testbench；
4. 在 README 记录是否仍可复现原行为。

V4修正后的`digital_DSM`是默认功能路径；原始`±(0.5+1 LSB)`宽积分器保存在
`digital_DSM_reference.sv`并可由`USE_REFERENCE_DSM`选择。不要仅为消除
Quartus warning 而改变`filter_down`、参考DSM或`sar_calibrator`的截断行为。

## 验证要求

修改 RTL 后至少运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\simulate.ps1
```

修改顶层、QSF、SDC、时钟或数据宽度后还必须运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

提交前应保持九组 testbench 全部 PASS，Quartus Full Compilation 0 error。

## 主机端

- GUI 和批量脚本必须共用 `control_word.py` 与 `jtag_transport.py`，避免两套位序。
- GUI 中除芯片信号名和 `FPGA/JTAG/ISSP/SOF/SAR/Q16/Filter/JSON`
  等工程专有名词外，面向用户的标签、提示、弹窗和状态信息使用中文。
- GUI中的`CS`和三个`TRIM`仍直接输入只含0/1的定长二进制码字，并严格检查
  2/3/3/4 bit。`ALPHA`输入十进制 unsigned Q2.16；Batch和Polynomial输入
  十进制 signed Q12.16；Python必须严格检查范围并换算为既有18/29-bit码字。
- GUI不得接受算法系数的`0x`前缀或二进制原始码字；signed Q16负数由十进制
  负号输入，最终打包为29-bit二进制补码位型。Python内部整数模型可以保留。
- GUI 输入和显示 Filter 的总样点数 `L_FILTER`，协议寄存器只传半长
  `N_FILTER=L_FILTER/2`；不得把裸变量 `N` 同时用于 DAQ 和 Filter。
- GUI “校准界面”输入 DSM 覆盖值时，十进制数按 signed Q16 解释；
  `0x`、`0b` 或 29-bit 二进制按原始二补码解释，并必须通过
  `jtag_transport.py` 公共API写入。
- GUI 实时趋势必须通过 `jtag_transport.py` 的 `read_live_sample()` 读取
  快照寄存器21…31；旧的多寄存器序号一致性重试只能作为诊断路径保留。
- GUI 校准趋势必须通过`read_calibration_snapshot()`取得同一批四路数据；纵轴
  自动包含真实数据和±0.5参考线，只有底层序号改变时才追加历史点，最多保留
  1000点。看板不得显示raw码字、快照有效位或快照序号。
- GUI 中的控制字驱动样点数 `N_DAQ` 属于芯片控制事务，范围968…65535；
  必须通过主机公共API发送，不得混入474-bit控制字或Filter设置。
- 多温度点校准中的 `N_DAQ` 必须放在“默认配置”窗口中，默认值为20000，并在
  启动时解析进 `MultiTemperatureSettings` 后传给每次控制字写入和关振写回。
- 当前实测频率默认使用 `host/instrument_backend.py` 中的
  `Keysight53220ABackend`；GUI 校准界面以 gate time=5 s 持续读取 53220A，
  读数只作为主机端频率测量输入，不得借此修改 SAR/RTL 逻辑。
- 53220A 连接必须通过 PyVISA 枚举资源并用 `*IDN?` 匹配 `53220A`；不得提交
  具体电脑的 USB 端口、序列号或 Connection Expert 别名。调试时可用
  `KEYSIGHT_53220A_RESOURCE` 环境变量或验证脚本 `--resource` 参数临时覆盖。
- 新仪器后端必须实现 `FrequencyInstrumentBackend` 并放在 host 层，不得把
  仪器型号耦合到 RTL。
- 趋势历史最多1000点；统计数据量必须是100的正整数倍。
- 每批CSV必须恰好100行，失败时不得发布部分CSV。
- JTAG/GPIO1 命令都要经过 toggle/ack 完整握手。
- JTAG的64-bit source属于多位异步邮箱：只能先检测同步后的toggle，再等待
  payload连续稳定后锁存opcode/index/data；禁止在toggle边沿同拍直接锁存总线。
- JTAG/GPIO1桥接器的`request_valid`可以保持到done；仲裁器必须保证同一
  次level-valid只被核心消费一次，修改握手时运行`tb_host_command_path`。
- 成功COMMIT后必须清空控制字块有效位图；存在粘滞传输错误时必须禁止
  COMMIT，避免后续批量测试把前后两轮控制字拼接到一起。
- 64-bit ISSP Tcl只允许用`%llx/%llX`解析和格式化source/probe；
  `%x/%X`会截断bit63的request/ack。会话结束时保持request/ack同步。
- 修改 opcode 或寄存器映射时，同步更新协议 package、Tcl、Python、README 和测试。

## 可移植路径

- `FPGA_V4` 必须能被整体复制到不同盘符、用户名或目录后运行。
- 工程源码和脚本中不得提交“盘符 + 用户目录”形式的绝对路径，不得绑定特定用户目录，
  也不得写死 Quartus/Questa 的安装版本路径。
- PowerShell 用 `$PSScriptRoot` 推导工程文件；Python 用
  `Path(__file__)` 推导工程文件；QSF 使用相对 QSF 所在目录的路径。
- 外部工具只允许通过 `PATH`、`QUARTUS_ROOTDIR`、
  `QUARTUS_ROOTDIR_OVERRIDE`、`QUESTA_FSE_ROOT` 或 `QUESTA_ROOT` 查找。
- 为规避 Quartus 对中文/OneDrive 路径的限制，可以在系统临时目录构建或
  暂存文件，但必须由系统 API 取得临时目录，不能写死本机临时路径。
- 新增脚本后，应从 `FPGA_V4` 根目录以 README 给出的命令实测；移动目录后
  不应要求修改源码。
- 复制到新电脑后，53220A 链路只应要求安装 Keysight IO Libraries Suite 与
  `pyvisa`，程序不得依赖固定 USB 地址。

## 主机端验证

修改 Python、Tcl 或 PowerShell 后，至少完成：

```powershell
python -m unittest discover -s .\host -p "test_*.py" -v
python -m py_compile .\host\control_word.py .\host\jtag_transport.py `
  .\host\acquisition.py .\host\instrument_backend.py .\host\gui.py
```

修改工具发现或仿真启动逻辑时还要运行 `scripts/simulate.ps1`；修改构建
启动逻辑时还要运行 `scripts/build.ps1`。没有硬件安全确认时，不要把
自动加载 `.sof` 作为回归测试步骤。
