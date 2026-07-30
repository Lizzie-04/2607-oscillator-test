# GPIO 表生成过程归档

`build_gpio_workbook.original.mjs` 是早期生成
`PCB/GPIO端口_RevG_FPGA补充.xlsx` 的一次性脚本，仅用于追溯表格来源。

该脚本依赖当时 Codex 会话提供的 `@oai/artifact-tool`，并保留了生成时的输出
目录假设和旧 `N=1500` 说明，不能作为当前工程的可执行工具。正式端口映射与
三个采样长度定义应以 `PCB/GPIO端口_RevG_FPGA补充.xlsx`、
`FPGA_V1/README.md` 和 RTL 为准。
