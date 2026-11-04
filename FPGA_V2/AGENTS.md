# FPGA V2 开发约束

## 不变量

- 474 位控制字必须与 `Matlab_Scripts/drive.m` 的字段顺序一致，并在发送前整体反转。
- `SR_CLK` 必须严格产生 474 个上升沿，第 474 个上升沿锁存 `BYPASS`。
- 默认半周期为 25,000 个 50 MHz 时钟，即 0.5 ms；默认 `N_DAQ=1500`，
  即默认总事务为750 ms。
- SAR 只接收外部单 bit 判决，不在 FPGA 内加入多数投票。
- SAR code 和 adder A/B 保持 29-bit signed Q16，SAR 搜索范围为 0.0…1.0。
- 三角窗总长 `L_FILTER=2×N_FILTER`、权重和为 `N_FILTER²`；
  `N_FILTER` 只能在窗口边界切换。
- 默认 `L_FILTER=2048`，所以 RTL/opcode 内部默认 `N_FILTER=1024`。
- GUI可调 `L_FILTER=2048…100000`，协议内部半长
  `N_FILTER=1024…50000`。
- `N_DAQ` 仅指原 MATLAB/LabVIEW 控制字驱动波形容器的总样点数，默认1500，
  GUI/主机可按次设置；禁止把它用作 Filter 默认值。
- `START_DLL` 和 `START_ANA` 在 `N_DAQ` 最末尾10个0.5 ms样点拉高，事务结束
  后继续保持为高，直到下一轮控制字驱动开始或FPGA复位。
- V2 不加入 SignalTap。
- 统计缓存每批严格捕获100次`mu_valid`；点击前已开始的窗口必须丢弃。
- 统计任务期间不得切换`N_FILTER`；每批地址严格为0…99。
- 统计样本固定打包为`{mu2[17:0], mu1[17:0]}`并拆成三个16-bit响应字。
- 连续趋势读取允许跳过 FPGA 中间产生的 μ 点，但必须先用
  `OP_LATCH_LIVE_SAMPLE` 在 50 MHz 域锁存快照；GUI 不得显示来自不同窗口的
  μ1、μ2、样本序号或 Filter 长度组合。
- V2只读协议版本固定为`0x0002`。
- `FS_DATA_OUT33`只作为`clk_SAMPLE`驱动两路BS降采样前端；
  SAR及全部On-Chip后级使用FPGA产生的2 MHz `clk_DATA`。
- FS域完整窗口结果必须通过稳定数据与完成翻转位送入2 MHz域，禁止多比特直跨域。
- “遍历”模式只能在 `filter_up` 到 `digital_DSM` 的输入端加入受协议控制的
  29-bit signed Q16 mux；不得为该模式修改 `adder`、`filter_up` 或
  `digital_DSM` 的既有截断/积分行为。

## 片内代码兼容性

`rtl/on_chip` 用于和未来流片版本做错误复现。修改前必须：

1. 先在独立分支或新模块证明修改；
2. 保留原版本作为可选择的 reference path；
3. 增加能体现位宽、符号和截断差异的 testbench；
4. 在 README 记录是否仍可复现原行为。

不要仅为消除 Quartus warning 而改变 `filter_down`、`digital_DSM`、
`sar_calibrator` 等模块的截断行为。

## 验证要求

修改 RTL 后至少运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\simulate.ps1
```

修改顶层、QSF、SDC、时钟或数据宽度后还必须运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

提交前应保持六组 testbench 全部 PASS，Quartus Full Compilation 0 error。

## 主机端

- GUI 和批量脚本必须共用 `control_word.py` 与 `jtag_transport.py`，避免两套位序。
- GUI 中除芯片信号名和 `FPGA/JTAG/ISSP/SOF/SAR/Q16/Filter/JSON`
  等工程专有名词外，面向用户的标签、提示、弹窗和状态信息使用中文。
- GUI中的`CS`、三个`TRIM`、`ALPHA`、Batch和Polynomial均直接输入
  只含0/1的定长二进制码字；分别严格检查2/3/3/4/18/29/29 bit。
- GUI不得接受这些字段的`0x`前缀、十进制值、负号或浮点值；signed Q16
  负数通过其定长二进制补码位型输入。Python内部整数模型可以保留。
- GUI 输入和显示 Filter 的总样点数 `L_FILTER`，协议寄存器只传半长
  `N_FILTER=L_FILTER/2`；不得把裸变量 `N` 同时用于 DAQ 和 Filter。
- GUI “遍历”页输入 DSM 覆盖值时，十进制数按 signed Q16 解释；
  `0x`、`0b` 或 29-bit 二进制按原始二补码解释，并必须通过
  `jtag_transport.py` 公共API写入。
- GUI 实时趋势必须通过 `jtag_transport.py` 的 `read_live_sample()` 读取
  快照寄存器21…26；旧的多寄存器序号一致性重试只能作为诊断路径保留。
- GUI 中的控制字驱动样点数 `N_DAQ` 属于芯片控制事务，范围968…65535；
  必须通过主机公共API发送，不得混入474-bit控制字或Filter设置。
- 新仪器后端放在 host 层，不得把仪器型号耦合到 RTL。
- 趋势历史最多2000点；统计数据量必须是100的正整数倍。
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

- `FPGA_V2` 必须能被整体复制到不同盘符、用户名或目录后运行。
- 工程源码和脚本中不得提交“盘符 + 用户目录”形式的绝对路径，不得绑定特定用户目录，
  也不得写死 Quartus/Questa 的安装版本路径。
- PowerShell 用 `$PSScriptRoot` 推导工程文件；Python 用
  `Path(__file__)` 推导工程文件；QSF 使用相对 QSF 所在目录的路径。
- 外部工具只允许通过 `PATH`、`QUARTUS_ROOTDIR`、
  `QUARTUS_ROOTDIR_OVERRIDE`、`QUESTA_FSE_ROOT` 或 `QUESTA_ROOT` 查找。
- 为规避 Quartus 对中文/OneDrive 路径的限制，可以在系统临时目录构建或
  暂存文件，但必须由系统 API 取得临时目录，不能写死本机临时路径。
- 新增脚本后，应从 `FPGA_V2` 根目录以 README 给出的命令实测；移动目录后
  不应要求修改源码。

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
