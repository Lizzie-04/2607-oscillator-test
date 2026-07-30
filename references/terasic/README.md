# Terasic DE1-SoC 参考资料

本目录保存用于核对 DE1-SoC Rev F/G 连接器、封装管脚和基础时序约束的厂商资料：

- `DE1-SoC_User_manual.pdf`：DE1-SoC 用户手册；
- `DE1_SoC_Default.qsf`：厂商默认工程的管脚约束参考；
- `DE1_SoC_Default.sdc`：厂商默认工程的时序约束参考。

当前可综合工程不会直接引用这些文件。实际 V1 约束位于
`FPGA_V1/quartus/fpga_v1.qsf` 与 `FPGA_V1/quartus/fpga_v1.sdc`。

原始资料来自 Terasic DE1-SoC Rev F/G System CD。完整安装压缩包未纳入
Git 仓库，避免提交约 178 MB 的可重复下载第三方文件。
