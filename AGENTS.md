# 工程协作说明

处理本工程时先阅读：

1. `MATLAB驱动芯片逻辑与FPGA-Python重构说明.md`
2. `FPGA_V1/README.md`
3. `FPGA_V1/AGENTS.md`

所有 FPGA V1 修改必须遵守子目录中的位序、时序、Q16、on-chip 兼容性和
验证约束。暂不部署 SignalTap。真实仪器未确定前，仪器控制应保持为可替换
Python 后端；若保留 LabVIEW，只通过清晰的 IPC 接口把测量结果返回主框架。

当前有效工程只位于本仓库的 `FPGA_V1`。不得恢复或引入
`C:\Users\Lizzie\OneDrive\FPGA` 等用户专属工作目录；Quartus/Questa
中间文件应由脚本放入系统临时目录，正式产物回写仓库内的
`FPGA_V1/quartus/output_files`。

`archive/` 与 `docs/history/` 是只读追溯资料，不属于当前构建路径。除非任务
明确要求复原历史原型，否则不要从这些目录复制固定路径或 SignalTap 流程到 V1。
