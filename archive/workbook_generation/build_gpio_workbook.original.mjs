import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = process.cwd();
const workspaceRoot = path.resolve(outputDir, "..", "..");
const sourcePath = path.join(workspaceRoot, "PCB", "GPIO端口.xlsx");
const outputPath = path.join(outputDir, "GPIO端口_RevG_FPGA补充.xlsx");

const input = await FileBlob.load(sourcePath);
const workbook = await SpreadsheetFile.importXlsx(input);
const mode = process.argv[2] ?? "inspect";

if (mode === "inspect") {
  const summary = await workbook.inspect({
    kind: "workbook,sheet,table,region,computedStyle",
    maxChars: 12000,
    tableMaxRows: 45,
    tableMaxCols: 12,
    tableMaxCellChars: 120,
  });
  console.log(summary.ndjson);
  const firstSheet = workbook.worksheets.getItemAt(0);
  const preview = await workbook.render({
    sheetName: firstSheet.name,
    autoCrop: "all",
    scale: 2,
    format: "png",
  });
  await fs.writeFile(
    path.join(outputDir, "gpio-original.png"),
    new Uint8Array(await preview.arrayBuffer()),
  );
  process.exit(0);
}

if (mode !== "build") {
  throw new Error(`Unknown mode: ${mode}`);
}

const originalSheet = workbook.worksheets.getItemAt(0);
const originalValues = originalSheet.getRange("A1:E41").values;

const gpio1Pins = [
  "PIN_AB17", "PIN_AA21", "PIN_AB21", "PIN_AC23", "PIN_AD24",
  "PIN_AE23", "PIN_AE24", "PIN_AF25", "PIN_AF26", "PIN_AG25",
  "PIN_AG26", "PIN_AH24", "PIN_AH27", "PIN_AJ27", "PIN_AK29",
  "PIN_AK28", "PIN_AK27", "PIN_AJ26", "PIN_AK26", "PIN_AH25",
  "PIN_AJ25", "PIN_AJ24", "PIN_AK24", "PIN_AG23", "PIN_AK23",
  "PIN_AH23", "PIN_AK22", "PIN_AJ22", "PIN_AH22", "PIN_AG22",
  "PIN_AF24", "PIN_AF23", "PIN_AE22", "PIN_AD21", "PIN_AA20",
  "PIN_AC22",
];

function physicalInfo(position) {
  if (position === 11) return ["VCC5", "GPIO2", "5 V 电源", "电源", "DE1-SoC GPIO header 固定电源脚"];
  if (position === 12) return ["GND", "GPIO2", "GND", "FPGA↔PCB", "公共地参考"];
  if (position === 29) return ["VCC3P3", "GPIO2", "3.3 V 电源", "电源", "DE1-SoC GPIO header 固定电源脚"];
  if (position === 30) return ["GND", "GPIO2", "GND", "FPGA↔PCB", "公共地参考"];
  let index;
  if (position >= 1 && position <= 10) index = position - 1;
  else if (position >= 13 && position <= 28) index = position - 3;
  else if (position >= 31 && position <= 40) index = position - 5;
  else return ["", "", "", "", ""];
  return [`GPIO_1[${index}]`, "GPIO2", gpio1Pins[index], "未使用", "Rev G 物理位置已确认，V1 未连接逻辑"];
}

const signalOverrides = {
  Address1: ["GPIO_1[0]", "GPIO2", "PIN_AB17", "PC→FPGA→PCB", "SV: Address1"],
  Address2: ["GPIO_1[1]", "GPIO2", "PIN_AA21", "PC→FPGA→PCB", "SV: Address2"],
  Address3: ["GPIO_1[3]", "GPIO2", "PIN_AC23", "PC→FPGA→PCB", "SV: Address3"],
  SR_CLK: ["GPIO_1[5]", "GPIO2", "PIN_AE23", "PC→FPGA→PCB", "474 个上升沿，0.5 ms/半周期"],
  SR_IN: ["GPIO_1[7]", "GPIO2", "PIN_AF25", "PC→FPGA→PCB", "SV: SR_IN"],
  SR_EN1: ["GPIO_1[9]", "GPIO2", "PIN_AG25", "PC→FPGA→PCB", "芯片 0 使能"],
  SR_EN2: ["GPIO_1[11]", "GPIO2", "PIN_AH24", "PC→FPGA→PCB", "芯片 1 使能"],
  SR_EN3: ["GPIO_1[13]", "GPIO2", "PIN_AJ27", "PC→FPGA→PCB", "芯片 2 使能"],
  SR_EN4: ["GPIO_1[15]", "GPIO2", "PIN_AK28", "PC→FPGA→PCB", "芯片 3 使能"],
  SR_EN5: ["GPIO_1[17]", "GPIO2", "PIN_AJ26", "PC→FPGA→PCB", "芯片 4 使能"],
  START_DLL: ["GPIO_1[19]", "GPIO2", "PIN_AH25", "PC→FPGA→PCB", "SV: START_DLL"],
  START_ANA: ["GPIO_1[21]", "GPIO2", "PIN_AJ24", "PC→FPGA→PCB", "SV: START_ANA"],
  FS_DATA_OUT33: ["GPIO_1[23]", "GPIO2", "PIN_AG23", "PCB→FPGA", "μ1/μ2 采样时钟；SDC 当前按 500 kHz"],
  RESET: ["GPIO_1[25]", "GPIO2", "PIN_AH23", "PC→FPGA→PCB", "SV: RESET"],
  CLK_3P3: ["GPIO_1[28]", "GPIO2", "PIN_AH22", "FPGA→PCB", "50 MHz / 25 = 2 MHz"],
  BS_T_OUT33: ["GPIO_1[29]", "GPIO2", "PIN_AG22", "PCB→FPGA→PC", "连续三角窗 μ1"],
  DSM_3P3: ["GPIO_1[30]", "GPIO2", "PIN_AF24", "FPGA→PCB", "on_chip DigOsc DSM 输出"],
  BS_C_OUT33: ["GPIO_1[31]", "GPIO2", "PIN_AF23", "PCB→FPGA→PC", "连续三角窗 μ2"],
  OSC_OUT33_TO_FPGA: ["GPIO_1[32]", "GPIO2", "PIN_AE22", "PCB→FPGA", "V1 预留，未参与算法"],
  SAR: ["JTAG/GPIO1 命令", "GPIO1/JTAG", "无 GPIO2 专用脚", "PC→FPGA", "单 bit：1=实测偏低，0=实测偏高"],
};

const annotatedRows = originalValues.map((row, rowIndex) => {
  if (rowIndex === 0) {
    return [
      ...row,
      "DE1 接口",
      "FPGA 封装管脚",
      "信号方向",
      "V1 SystemVerilog / 备注",
    ];
  }
  const position = Number(row[0]);
  const signal = String(row[1] ?? "").trim();
  const info = signalOverrides[signal] ?? physicalInfo(position);
  const base = [...row];
  base[4] = info[0];
  return [...base, info[1], info[2], info[3], info[4]];
});

const supplement = workbook.worksheets.add("RevG端口补充");
supplement.getRange("A1:I41").values = annotatedRows;
supplement.tables.add("A1:I41", true, "RevGPortTable");
supplement.freezePanes.freezeRows(1);

const headerFormat = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
supplement.getRange("A1:I1").format = headerFormat;
supplement.getRange("A2:I41").format = {
  verticalAlignment: "center",
  wrapText: true,
};
supplement.getRange("A1:A41").format.columnWidth = 11;
supplement.getRange("B1:B41").format.columnWidth = 24;
supplement.getRange("C1:C41").format.columnWidth = 20;
supplement.getRange("D1:D41").format.columnWidth = 18;
supplement.getRange("E1:E41").format.columnWidth = 17;
supplement.getRange("F1:F41").format.columnWidth = 13;
supplement.getRange("G1:G41").format.columnWidth = 20;
supplement.getRange("H1:H41").format.columnWidth = 20;
supplement.getRange("I1:I41").format.columnWidth = 38;
supplement.getRange("A2:A41").format.horizontalAlignment = "center";
supplement.getRange("E2:H41").format.horizontalAlignment = "center";

const gpio0Pins = [
  "PIN_AC18", "PIN_Y17", "PIN_AD17", "PIN_Y18", "PIN_AK16", "PIN_AK18",
  "PIN_AK19", "PIN_AJ19", "PIN_AJ17", "PIN_AJ16", "PIN_AH18", "PIN_AH17",
  "PIN_AG16", "PIN_AE16", "PIN_AF16", "PIN_AG17", "PIN_AA18", "PIN_AA19",
  "PIN_AE17", "PIN_AC20", "PIN_AH19", "PIN_AJ20", "PIN_AH20", "PIN_AK21",
  "PIN_AD19", "PIN_AD20", "PIN_AE18", "PIN_AE19", "PIN_AF20", "PIN_AF21",
  "PIN_AF19", "PIN_AG21", "PIN_AF18", "PIN_AG20", "PIN_AG18", "PIN_AJ21",
];
const pinList = (start, end) => gpio0Pins.slice(start, end + 1).join(", ");
const pcRows = [
  ["SystemVerilog 端口", "GPIO_0", "FPGA 封装管脚", "信号方向", "协议含义"],
  ["PC_DATA[15:0]", "[15:0]", pinList(0, 15), "PC↔FPGA", "16-bit 双向数据；PC_WRITE=1 时由 PC 驱动"],
  ["PC_INDEX[5:0]", "[21:16]", pinList(16, 21), "PC→FPGA", "控制字块索引或读寄存器索引"],
  ["PC_OPCODE[3:0]", "[25:22]", pinList(22, 25), "PC→FPGA", "命令操作码"],
  ["PC_REQUEST_TOGGLE", "[26]", gpio0Pins[26], "PC→FPGA", "每条新命令翻转一次"],
  ["PC_WRITE", "[27]", gpio0Pins[27], "PC→FPGA", "1=PC 写入，0=FPGA 读回"],
  ["PC_ACK_TOGGLE", "[28]", gpio0Pins[28], "FPGA→PC", "完成命令后追随 request toggle"],
  ["PC_PENDING", "[29]", gpio0Pins[29], "FPGA→PC", "命令处理中"],
  ["PC_ERROR", "[30]", gpio0Pins[30], "FPGA→PC", "错误状态非零指示"],
  ["PC_SAMPLE_TOGGLE", "[31]", gpio0Pins[31], "FPGA→PC", "每次新 μ1/μ2 输出翻转"],
  ["PC_DRIVER_BUSY", "[32]", gpio0Pins[32], "FPGA→PC", "474-bit 驱动事务进行中"],
  ["PC_SAR_BUSY", "[33]", gpio0Pins[33], "FPGA→PC", "等待外部 SAR 比较位"],
  ["PC_SAR_DONE", "[34]", gpio0Pins[34], "FPGA→PC", "16 次比较完成"],
  ["PC_CONTROL_COMPLETE", "[35]", gpio0Pins[35], "FPGA→PC", "30 个控制字块均已写入"],
];
const pcSheet = workbook.worksheets.add("GPIO1_PC接口");
pcSheet.getRange(`A1:E${pcRows.length}`).values = pcRows;
pcSheet.tables.add(`A1:E${pcRows.length}`, true, "PcInterfaceTable");
pcSheet.freezePanes.freezeRows(1);
pcSheet.getRange("A1:E1").format = headerFormat;
pcSheet.getRange(`A2:E${pcRows.length}`).format = {
  verticalAlignment: "center",
  wrapText: true,
};
pcSheet.getRange(`A1:A${pcRows.length}`).format.columnWidth = 29;
pcSheet.getRange(`B1:B${pcRows.length}`).format.columnWidth = 14;
pcSheet.getRange(`C1:C${pcRows.length}`).format.columnWidth = 52;
pcSheet.getRange(`D1:D${pcRows.length}`).format.columnWidth = 18;
pcSheet.getRange(`E1:E${pcRows.length}`).format.columnWidth = 38;
pcSheet.getRange(`B2:B${pcRows.length}`).format.horizontalAlignment = "center";
pcSheet.getRange(`D2:D${pcRows.length}`).format.horizontalAlignment = "center";

const notes = [
  ["说明", "内容"],
  ["板卡版本", "DE1-SoC Rev F/G；器件 5CSEMA5F31C6"],
  ["GPIO2", "对应 Quartus GPIO_1；连接 Rev G PCB"],
  ["GPIO1", "对应 Quartus GPIO_0；可接电脑并行适配器"],
  ["本地 GUI", "V1 默认通过 USB-Blaster JTAG ISSP，无需占用 GPIO1 物理线"],
  ["N", "32…4096，默认 GUI 值 1500；仅在三角窗边界更新"],
  ["SAR", "PC 输入单 bit，不做 FPGA 多数投票；code 为 29-bit signed Q16，范围 0…1"],
  ["SignalTap", "V1 未部署"],
];
const noteSheet = workbook.worksheets.add("设计说明");
noteSheet.getRange(`A1:B${notes.length}`).values = notes;
noteSheet.tables.add(`A1:B${notes.length}`, true, "DesignNotesTable");
noteSheet.getRange("A1:B1").format = headerFormat;
noteSheet.getRange(`A2:B${notes.length}`).format = {
  verticalAlignment: "center",
  wrapText: true,
};
noteSheet.getRange(`A1:A${notes.length}`).format.columnWidth = 22;
noteSheet.getRange(`B1:B${notes.length}`).format.columnWidth = 72;

const exported = await SpreadsheetFile.exportXlsx(workbook);
await fs.writeFile(outputPath, exported.data);

for (const sheetName of ["Sheet1", "RevG端口补充", "GPIO1_PC接口", "设计说明"]) {
  const image = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 1.5,
    format: "png",
  });
  const safeName = {
    Sheet1: "gpio-final-original-sheet",
    RevG端口补充: "gpio-final-revg",
    GPIO1_PC接口: "gpio-final-pc",
    设计说明: "gpio-final-notes",
  }[sheetName];
  await fs.writeFile(
    path.join(outputDir, `${safeName}.png`),
    new Uint8Array(await image.arrayBuffer()),
  );
}

const finalInspection = await workbook.inspect({
  kind: "workbook,sheet,table,region",
  range: "RevG端口补充!A1:I41",
  maxChars: 14000,
  tableMaxRows: 45,
  tableMaxCols: 10,
  tableMaxCellChars: 100,
});
console.log(finalInspection.ndjson);

const errorInspection = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorInspection.ndjson);
console.log(`WROTE ${outputPath}`);
