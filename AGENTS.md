# 工程协作说明

处理本工程时先阅读：

1. `MATLAB驱动芯片逻辑与FPGA-Python重构说明.md`
2. `FPGA_V4/README.md`
3. `FPGA_V4/AGENTS.md`

所有 FPGA V4 修改必须遵守子目录中的位序、时序、Q16、on-chip 兼容性和
验证约束。暂不部署 SignalTap。当前实测频率默认使用 Keysight/Agilent
53220A 的 Python/PyVISA 后端；该后端必须保持可替换并只位于 host 层，通过
VISA 资源枚举和 `*IDN?` 主动检索仪器，不得依赖固定 USB 端口或把仪器型号耦合到
RTL/SAR 逻辑。若保留 LabVIEW，只通过清晰的 IPC 接口把测量结果返回主框架。

当前有效工程只位于本仓库的 `FPGA_V4`。`FPGA_V1`、`FPGA_V2`、`FPGA_V3`
是只读基线，除非任务明确要求修补旧版，否则不得修改。不得恢复或引入
`C:\Users\Lizzie\OneDrive\FPGA` 等用户专属工作目录；Quartus/Questa
中间文件应由脚本放入系统临时目录，正式产物回写仓库内的
`FPGA_V4/quartus/output_files`。

`archive/` 与 `docs/history/` 是只读追溯资料，不属于当前构建路径。除非任务
明确要求复原历史原型，否则不要从这些目录复制固定路径或 SignalTap 流程到 V4。
