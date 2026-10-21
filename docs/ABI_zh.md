# RF-clock PL 编程手册（ABI 1.3）

本文是 `docs/ABI.md`（英文，标准版）的中文翻译，两者内容一致；有出入时以英文版为准。

本手册说明 ARM 软件如何控制 RF-clock 的 PL：第一部分是寄存器级协议（相当于仪器的命令集），第二部分是封装这些协议的 Python 库 `rf_clock.pl`。

- 协议在 RTL 中的唯一来源：`PL/src/p_regs.vh`（地址、位域）和 `PL/src/p_defs.vh`（状态、返回码、标志位）。Python 的 `rf_clock.pl.abi` 由 `tools/generate_abi.py` 从这两个文件生成。三者改动时必须同步修改本文，并按 §14 更新 `VERSION`。
- 设计背景见 `plan/RF_clock_modules_zh.md`，未确认事项见 `plan/checklist_zh.md`。

---

# 第一部分 协议

## 1. 概述

### 1.1 通路

| 通路      | PL 侧硬件                              | 传什么                       | ARM 怎么访问                       |
| --------- | -------------------------------------- | ---------------------------- | ---------------------------------- |
| 控制/状态 | P2，AXI4-Lite 寄存器                   | 命令、时间戳、状态           | 32 位 MMIO 读写（§4.1–§4.6）    |
| TX 波形   | P3 片上 buffer，经 AXI BRAM Controller | 发射波形（写一次，反复使用） | 按内存写入（§4.5.8）              |
| RX 样本   | P4 → 异步 FIFO → AXI DMA S2MM → DDR | 每个采集窗口的样本           | DMA buffer + DMA 寄存器（§4.6.9） |
| 时钟状态  | AXI GPIO（`pl_clk0` 域）             | fabric MMCM 是否锁定         | 32 位 MMIO 读（§4.7）             |

### 1.2 地址

基地址在 `PL/bd/create_rf_clock_bd.tcl` 中固定分配。软件应从 HWH（PYNQ `overlay.ip_dict` / `overlay.mem_dict`）读取，不要写死。

| 地址空间       | HWH 名称                         | 基地址      | 大小                  | 时钟域  |
| -------------- | -------------------------------- | ----------- | --------------------- | ------- |
| P2 寄存器      | `p_top_0`                      | 0xA000_0000 | 4 KiB（只用前 256 B） | fabric  |
| AXI DMA 寄存器 | `rx_dma`                       | 0xA001_0000 | 64 KiB                | pl_clk0 |
| 时钟状态 GPIO  | `clk_status_gpio`              | 0xA002_0000 | 4 KiB                 | pl_clk0 |
| RFDC 寄存器    | `rfdc`                         | 0xA004_0000 | 256 KiB               | pl_clk0 |
| TX 波形 buffer | `tx_bram_ctrl`（`mem_dict`） | 0xA040_0000 | 4 MiB 窗口，前 69632 beat（2.125 MiB）有存储 | fabric  |

> **警告：总线卡死。** fabric 时钟由 LMK04828 的 PL_CLK 经 MMCM 产生。fabric 时钟没在
> 运行时访问 P2 或 TX buffer，AXI 访问永远得不到回应，整个系统卡死，只能重启
> （远程用 `tools/jtag_reset_board.tcl`）。**每次访问这两个地址空间之前，先确认时钟
> 状态 GPIO bit0 = 1**（§4.7）。`pl_clk0` 域的地址不受此限制。

## 2. 通信约定

| 项目     | 约定                                                                                                                                                       |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 字节序   | 小端                                                                                                                                                       |
| 访问宽度 | P2 只接受对齐的 32 位访问，写必须整字（`wstrb = 0xF`）                                                                                                   |
| 错误响应 | 部分写、写只读寄存器、访问未映射地址返回 SLVERR，寄存器不变。Linux 上可能表现为 Bus error，软件不得依赖它                                                  |
| 64 位值  | LO 在 +0，HI 在 +4。会变化的值（`TICK`、`PPS_TICK`）**必须先读 LO 再读 HI**：PL会在读 LO 的同一拍锁存 HI，因此两半属于同一时刻，无需担心数据撕裂 |
| 命令提交 | 先写 shadow 寄存器，再写`CONTROL` 的 doorbell 位，然后读 `*_RESULT` 确认（§5.2）                                                                      |
| 中断     | 不用，全部轮询                                                                                                                                             |

**单位**

| 量     | 值                                                       |
| ------ | -------------------------------------------------------- |
| tick   | 10 ns（100 MHz fabric 时钟，由本节点 FS725 10 MHz 派生） |
| beat   | 32 字节 = 256 bit = 8 个复样本                           |
| 复样本 | 16 bit I + 16 bit Q，800 MS/s，间隔 1.25 ns              |
| 1 tick | 正好 1 beat = 8 个样本                                   |

**哪些时间可以用来测量。** raw_tick 从 PL 复位开始计数，不回绕，失锁或换 epoch 时也不清零。ARM 读一次寄存器的延迟从几百 ns 到毫秒不等，所以读到的 `TICK` 只用来安排未来的 target。参与测量的时间一律用 PL 在事件那一拍锁存的值：`PPS_TICK`、`TX_ACTUAL`、`RX_ACTUAL`。

## 3. 命令条目格式

第 4 节每个寄存器（"命令"）按下面的格式给出：

| 字段   | 内容                                                                           |
| ------ | ------------------------------------------------------------------------------ |
| 地址   | 相对 P2 基地址的字节偏移；64 位值给出 LO/HI 两个偏移                           |
| 访问   | RO 只读；RW 读写；WO 只写（读出恒为 0）；shadow 表示写入后要按 doorbell 才生效 |
| 格式   | u32 / u64 / 位域                                                               |
| 复位值 | PL 复位后的值；"—" 表示随运行变化                                             |
| 语法   | 用下面的记法写出的访问序列                                                     |
| Python | `rf_clock.pl` 中对应的调用（第二部分）                                       |

语法记法：

```text
R32 <off>              读 32 位，返回值写在 → 后
W32 <off> <value>      写 32 位
R64 <off>              R32 <off>（LO），再 R32 <off+4>（HI），拼成 (HI << 32) | LO
```

## 4. 命令参考

### 4.1 系统

#### 4.1.1 `ID` — 设计标识

| 地址 | 访问 | 格式 | 复位值               |
| ---- | ---- | ---- | -------------------- |
| 0x00 | RO   | u32  | 0x52434C4B（"RCLK"） |

```text
R32 0x00  → 0x52434C4B
```

确认加载的是 RF-clock 的 bitstream。值不对就停止，不要继续访问其他寄存器。Python：`PLClient.validate()`。

#### 4.1.2 `VERSION` — 接口版本

| 地址 | 访问 | 格式                        | 复位值            |
| ---- | ---- | --------------------------- | ----------------- |
| 0x04 | RO   | [31:16] major，[15:0] minor | 0x00010003（1.3） |

```text
R32 0x04  → 0x00010003
```

major 不等于软件支持的值就拒绝运行；`PPS_INPUTS`、`PPS_CONFIG` 需要 minor ≥ 1，`BUILD_VERSION` 需要 minor ≥ 2，`TX_REPEAT` 需要 minor ≥ 3（§14）。Python：`PLClient.validate()`、`DeviceInfo.version`。

#### 4.1.3 `CONTROL` — 命令脉冲

| 地址 | 访问 | 格式 | 复位值     |
| ---- | ---- | ---- | ---------- |
| 0x08 | WO   | 位域 | 读出恒为 0 |

```text
W32 0x08 <bits>
```

写入的每个为 1 的位产生一个单拍脉冲，可以一次写多位（例如 `0x5` 同时触发 TX 和 RX）。

| 位 | 名称            | 作用                                                     | 详见    |
| -- | --------------- | -------------------------------------------------------- | ------- |
| 0  | `TX_DOORBELL` | 取走`TX_TARGET`/`TX_LENGTH` 并校验，接受则进入 ARMED | §4.5.3 |
| 1  | `TX_CANCEL`   | TX 为 ARMED 时取消，回到 IDLE                            | §4.5.4 |
| 2  | `RX_DOORBELL` | 取走`RX_TARGET`/`RX_LENGTH` 并校验                   | §4.6.3 |
| 3  | `RX_CANCEL`   | RX 为 ARMED 且还没到开窗那一拍时取消                     | §4.6.4 |
| 4  | `CLEAR_FAULT` | 清除故障，TX 从 FAULT 回到 IDLE                          | §4.2.4 |

#### 4.1.4 `TIME_EPOCH` — 时间纪元

| 地址 | 访问 | 格式 | 复位值 |
| ---- | ---- | ---- | ------ |
| 0x10 | RO   | u32  | 0      |

```text
R32 0x10  → epoch
```

时钟每次重新锁定后加 1；0 表示时间还从未有效过。变化说明中途失锁过，变化前后的时间戳不可比，跨 epoch 的测量必须丢弃（§6.4）。Python：`PLClient.time_epoch()`、`remember_epoch()`、`require_same_epoch()`。

#### 4.1.5 构建参数 `TX_DEPTH`、`RX_MAX_BEATS`、`MIN_LEAD_TICKS`

| 地址 | 名称               | 访问 | 值      | 含义                                                     |
| ---- | ------------------ | ---- | ------- | -------------------------------------------------------- |
| 0x84 | `TX_DEPTH`       | RO   | 69632   | TX buffer 容量（beat）                                   |
| 0x88 | `RX_MAX_BEATS`   | RO   | 2097151 | 单个 RX 窗口最大长度（beat），受 DMA 26 位长度寄存器限制 |
| 0x8C | `MIN_LEAD_TICKS` | RO   | 64      | target 至少要比 doorbell 处理时的 tick 晚这么多          |

启动时读一次，之后用它们校验参数。Python：`PLClient.validate()` → `DeviceInfo`。`TX_DEPTH` = 69632 = 17 × 4096：65535 码片的突发加 RRC 拖尾是 65545 beat，放得下。

#### 4.1.6 `BUILD_VERSION` — 构建时的软件包版本（1.2 起）

| 地址 | 访问 | 格式                                    | 复位值                          |
| ---- | ---- | --------------------------------------- | ------------------------------- |
| 0x94 | RO   | [31:24] A，[23:16] B，[15:8] C，[7:0] D | 构建时`pyproject.toml` 的版本 |

```text
R32 0x94  → 0x01030000（rf-clock 1.3.0.0）
```

构建脚本 `PL/bd/create_rf_clock_bd.tcl` 读取 `pyproject.toml` 的 `version = "A.B.C.D"`，写成常量 `0xAABBCCDD`；A.B 就是 `VERSION`，不相等时构建脚本报错。软件把它和自己安装的 rf-clock 版本比较，A.B.C 不同就拒绝运行（§14.1）。Python：`PLClient.build_version()` → `"A.B.C.D"`。

### 4.2 状态与故障

#### 4.2.1 `STATUS` — 状态字

| 地址 | 访问 | 格式 | 复位值 |
| ---- | ---- | ---- | ------ |
| 0x0C | RO   | 位域 | —     |

```text
R32 0x0C  → status
```

| 位    | 名称               | 含义                                              |
| ----- | ------------------ | ------------------------------------------------- |
| 0     | `time_valid`     | 时钟已锁定并稳定，时间戳有效                      |
| 1     | `clocks_locked`  | 时钟锁定信号的当前值（未经稳定延时）              |
| 2     | `pps_locked`     | 已连续收到`PPS_LOCK_COUNT`（4）个周期正确的 PPS |
| 3     | `tick_exhausted` | raw_tick 已饱和（正常运行不会出现）               |
| 4     | `any_fault`      | `FAULT_BITS` 非 0                               |
| 5     | `dma_ready`      | S2MM 正在接收（`tready` 为高）                  |
| 10:8  | `tx_state`       | TX 状态，编码见 §5.1                             |
| 14:12 | `rx_state`       | RX 状态，编码见 §5.1                             |

状态字段是 TX/RX 进度的权威信息（`*_RESULT` 可能被 `REJ_BUSY` 改写，§5.2）。Python：`PLClient.status()` → `Status`。

#### 4.2.2 `FAULT_BITS` — 粘滞故障位

| 地址 | 访问 | 格式       | 复位值 |
| ---- | ---- | ---------- | ------ |
| 0x14 | RO   | [3:0] 位域 | 0      |

```text
R32 0x14  → bits
```

编码见 §5.4。只能由 `CLEAR_FAULT` 或新的 `TIME_EPOCH` 清除。Python：`abi.FaultBit`。

#### 4.2.3 `FAULT_INFO` — 故障信息

| 地址 | 访问 | 格式                                     | 复位值 |
| ---- | ---- | ---------------------------------------- | ------ |
| 0x18 | RO   | [3:0] 第一个故障源，[31:16] 故障事件总数 | 0      |

```text
R32 0x18  → info
```

- [3:0]：清除以来第一个故障的位号 + 1，0 = 无。
- [31:16]：永不清零的故障事件计数。比较两次轮询的值，就知道中间是否发生过新故障。

#### 4.2.4 `CONTROL.CLEAR_FAULT` — 清除故障

```text
W32 0x08 0x10
```

清除 `FAULT_BITS` 和 `FAULT_INFO[3:0]`；TX 若处于 FAULT，回到 IDLE，`TX_RESULT` 变为 NONE。与清除同一拍发生的新故障会保留。清除前应先读出并记录 `FAULT_BITS`/`FAULT_INFO`。Python：`PLClient.clear_fault()`。

### 4.3 时间

#### 4.3.1 `TICK` — 当前 raw_tick

| 地址                   | 访问 | 格式      | 复位值 |
| ---------------------- | ---- | --------- | ------ |
| 0x20（LO）、0x24（HI） | RO   | u64，tick | —     |

```text
R64 0x20  → tick          读 LO 时锁存 HI
```

只用来安排未来的 target，不用于测量（§2）。Python：`PLClient.read64(ABI.REG.TICK_LO)`。

### 4.4 PPS

#### 4.4.1 `PPS_TICK`、`PPS_COUNT` — 最近一次 PPS

| 地址                   | 名称          | 访问 | 格式      | 复位值 |
| ---------------------- | ------------- | ---- | --------- | ------ |
| 0x28（LO）、0x2C（HI） | `PPS_TICK`  | RO   | u64，tick | 0      |
| 0x30                   | `PPS_COUNT` | RO   | u32       | 0      |

```text
R64 0x28  → pps_tick      读 LO 时锁存 PPS_TICK_HI 和 PPS_COUNT
R32 0x30  → pps_count     与上面的 pps_tick 属于同一个 PPS 沿
```

`PPS_TICK` 是所选 PPS 信号（§4.4.5）计时沿到达管脚那一拍的 tick，已扣除同步器的固定延迟。要成对使用 tick 和计数，必须先读 `PPS_TICK_LO`。时间语义见 §6.3。

#### 4.4.2 `PPS_LAST_PERIOD` — 最近一个周期

| 地址 | 访问 | 格式      | 复位值 |
| ---- | ---- | --------- | ------ |
| 0x34 | RO   | u32，tick | 0      |

```text
R32 0x34  → 100000000     正常值
```

最近两次 PPS 沿之间的 tick 数。恒为 100000000 说明 fabric 时钟与 FS725 同源。

#### 4.4.3 `PPS_JITTER_CNT` — 抖动计数

| 地址 | 访问 | 格式 | 复位值 |
| ---- | ---- | ---- | ------ |
| 0x38 | RO   | u32  | 0      |

周期偏差为 ±1 tick（容差内但不为 0）的次数。P1 在硬件里检查每一个周期，所以它覆盖软件没采样到的周期。持续增长说明 PPS 沿正好落在时钟采样沿附近（§6.3）。

#### 4.4.4 `PPS_INPUTS` — 板上两路 PPS 探针（1.1 起）

| 地址 | 访问 | 格式 | 复位值 |
| ---- | ---- | ---- | ------ |
| 0x3C | RO   | 位域 | —     |

```text
R32 0x3C  → inputs
```

| 位    | 名称           | 含义                                        |
| ----- | -------------- | ------------------------------------------- |
| 0     | `COMP_LEVEL` | `IRIG_COMP_OUT`（比较器）当前电平，已同步 |
| 1     | `TRIG_LEVEL` | `IRIG_TRIG_OUT`（施密特触发器）当前电平   |
| 15:8  | `COMP_EDGES` | `IRIG_COMP_OUT` 上升沿计数，8 位回绕      |
| 23:16 | `TRIG_EDGES` | `IRIG_TRIG_OUT` 上升沿计数，8 位回绕      |

不需要示波器就能判断 PPS 是否到达、极性如何：PPS 脉宽只有 10–30 µs，读到的电平几乎总是空闲电平；空闲为 0 说明是正脉冲，空闲为 1 说明是负脉冲（需 `INVERT = 1`）。隔几秒读两次，计数应每秒加 1；两路都不动说明 PPS 没进到板上的 PPS 口（J7）。Python：`PLClient.pps_inputs()` → `PPSInputs`。

#### 4.4.5 `PPS_CONFIG` — PPS 来源和极性（1.1 起）

| 地址 | 访问 | 格式                                   | 复位值 |
| ---- | ---- | -------------------------------------- | ------ |
| 0x90 | RW   | [1:0] 位域，其余位写入被忽略、读出为 0 | 0      |

```text
W32 0x90 <cfg>
R32 0x90  → cfg
```

| 位 | 名称         | 含义                                                              |
| -- | ------------ | ----------------------------------------------------------------- |
| 0  | `SRC_TRIG` | 0 =`IRIG_COMP_OUT`（AJ13，默认），1 = `IRIG_TRIG_OUT`（AH13） |
| 1  | `INVERT`   | 1 = 对所选信号的下降沿计时                                        |

板上电路：PPS SMA（J7）有 49.9 Ω 对地终端，经 100 Ω 和 10 nF 交流耦合到偏置在 1.65 V 的节点，再进比较器（LMV7235，约 ±0.17 V 迟滞）和施密特触发器（SN74AUP1G17），两路经 NVT2008 转到 1.8 V（FPGA 侧用内部上拉）。FS725 的正脉冲在两路上都是上升沿，用默认值 0。

写入不同的值会让 P1 重新开始 PPS 捕获：`pps_locked` 清 0，之后要再收到一个参考沿和 4 个正确周期才重新锁定，不记故障。只在启动时设置。Python：`PLClient.set_pps_config(source_trig=..., invert=...)`。

### 4.5 TX（发射）

#### 4.5.1 `TX_TARGET` — 开始时刻（shadow）

| 地址                   | 访问      | 格式      | 复位值 |
| ---------------------- | --------- | --------- | ------ |
| 0x40（LO）、0x44（HI） | RW shadow | u64，tick | 0      |

```text
W32 0x40 <target[31:0]>
W32 0x44 <target[63:32]>
```

第 0 个 beat 被 DAC 接收的那一拍（§6.1）。按 doorbell 时必须满足 `target ≥ 当时的 tick + MIN_LEAD_TICKS`，否则拒绝为 `REJ_STALE`。

#### 4.5.2 `TX_LENGTH` — 突发长度（shadow）

| 地址 | 访问      | 格式      | 复位值 |
| ---- | --------- | --------- | ------ |
| 0x48 | RW shadow | u32，beat | 0      |

```text
W32 0x48 <beats>          1 … TX_DEPTH
```

从 buffer 的第 0 个 beat 开始连续发出这么多 beat。0 或超过 `TX_DEPTH` 拒绝为 `REJ_LENGTH`。重复播放见 `TX_REPEAT`（§4.5.9）。

#### 4.5.3 `CONTROL.TX_DOORBELL` — 提交 TX 命令

```text
W32 0x40 <target_lo>
W32 0x44 <target_hi>
W32 0x48 <beats>
W32 0x5C <passes>         1.3 起；不写则沿用上次的值（复位为 1）
W32 0x08 0x01
R32 0x4C  → 0x01 (ARMED)  否则见 §5.2
```

只在 TX 状态为 IDLE 或 DONE 时处理。校验顺序：`time_valid` → 长度和重复次数 → 是否过期。第一条不满足就给出对应拒绝码，状态不变。只有 doorbell 那一拍的 shadow 值有效，之后再改 shadow 不影响已接受的命令。target 很近时，读 `TX_RESULT` 前命令可能已经进入 RUNNING 甚至 DONE（看 `TX_SEQ` 是否增加来区分）。Python：`PLClient.arm_tx(target, beats, *, repeat=1)`。

#### 4.5.4 `CONTROL.TX_CANCEL` — 取消

```text
W32 0x08 0x02
```

只在 ARMED 时有效：回到 IDLE，`TX_RESULT = CANCELLED`。进入 RUNNING 后不能取消，只能正常结束或进入 FAULT。Python：`PLClient.cancel_tx()`。

#### 4.5.5 `TX_RESULT` — 返回码

| 地址 | 访问 | 格式         | 复位值 |
| ---- | ---- | ------------ | ------ |
| 0x4C | RO   | [7:0] 返回码 | 0      |

编码见 §5.2。

#### 4.5.6 `TX_SEQ` — 完成计数

| 地址 | 访问 | 格式 | 复位值 |
| ---- | ---- | ---- | ------ |
| 0x50 | RO   | u32  | 0      |

成功完成（DONE）的突发数。提交前后比较它，可以确认结果属于本次命令。

#### 4.5.7 `TX_ACTUAL` — 实际开始时刻

| 地址                   | 访问 | 格式      | 复位值 |
| ---------------------- | ---- | --------- | ------ |
| 0x54（LO）、0x58（HI） | RO   | u64，tick | 0      |

```text
R64 0x54  → actual        只在 tx_state = DONE 后读
```

第 0 个 beat 被 DAC 接收那一拍的 tick。正常情况下等于 target，软件应校验。Python：`PLClient.tx_actual()`。

#### 4.5.8 TX 波形 buffer（存储空间）

| 地址                              | 访问 | 格式                        |
| --------------------------------- | ---- | --------------------------- |
| `tx_bram_ctrl` 基地址 + i × 32 | 写   | 第 i 个 beat，32 字节，小端 |

```text
W32 base+i*32+0  <beat i, 字节 0..3>
...
W32 base+i*32+28 <beat i, 字节 28..31>
```

- buffer 在 UltraRAM 里，分成 17 个 4096 beat 的 bank，容量 `TX_DEPTH` = 69632 beat。地址窗口是 4 MiB（2^17 beat），`TX_DEPTH` 及以上的写入被丢弃，读出无意义。
- PL 把每个 beat 原样交给 RFDC DAC。DAC 按 **I/Q 逐字交错**读取：第 n 个样本（n = 0…7，先发的在前）占第 n 个 32 位字，`I = [32n+15:32n]`，`Q = [32n+31:32n+16]`（`TX_LAYOUT`）。依据：M3 lane check（2026-10-14，见 §13）；按平面格式打包时第 k 条 lane 的冲激延迟变为 D − ⌈k/2⌉，正是 DAC 交错读取的结果。冲激只放在 I 上，所以一对 16 位里哪个是 I、哪个是 Q 尚未单独验证（对 BPSK 实信号无影响）。
- 波形不变时只写一次，之后每次发射只提交 TX 命令。
- TX 为 ARMED 或 RUNNING 时**不得**写 buffer，否则 TX 进入 FAULT，返回码 `FAULT_BUF_WRITE`。
- 属于 fabric 时钟域，写之前确认时钟状态 GPIO（§1.2）。

Python：`TXBuffer.write_beats(beats)`。

#### 4.5.9 `TX_REPEAT` — 重复播放（shadow，1.3 起）

| 地址 | 访问      | 格式            | 复位值 |
| ---- | --------- | --------------- | ------ |
| 0x5C | RW shadow | u32，1 … 65535 | 1      |

```text
W32 0x5C <passes>         1 … TX_REPEAT_MAX (65535)
```

一条 TX 命令把 buffer 的第 0 … `TX_LENGTH`−1 个 beat 连续播放 `TX_REPEAT` 遍：每一遍最后一个 beat 的下一拍就是下一遍的第 0 个 beat，中间没有空拍。`TX_ACTUAL` 是第一遍的开始时刻；全部播完才进入 DONE，`TX_SEQ` 只加 1。0 或大于 65535 时 doorbell 拒绝为 `REJ_LENGTH`。总时长 `TX_LENGTH × TX_REPEAT` 拍，最长约 45 s；RUNNING 中不能取消。

shadow 在 doorbell 时才被取用，不写就沿用上次的值。`PLClient.arm_tx` 在 1.3 起的 bitstream 上每次都写它（默认 1）；在更早的 bitstream 上这个地址不存在（写入返回 SLVERR），所以不写，`repeat` 不为 1 时抛 `IdentityError`。

首尾相接的波形要用周期波形（`WaveformGenerator(periodic=True)`，§11.9）：普通突发两端各有 RRC 拖尾，直接重复会在接缝处不连续。Python：`PLClient.arm_tx(target, beats, repeat=n)`。

### 4.6 RX（接收）

> RX 通路已上板验证（2026-10-14）：定时开窗、DMA 写入、`RX_ACTUAL` 和字节数校验正常；
> 64 MB（20 ms）窗口在 ARM 约 4 GB/s 内存流量下没有溢出（`rf_clock.checks.m3_loopback --stress`）。

#### 4.6.1 `RX_TARGET` — 开窗时刻（shadow）

| 地址                   | 访问      | 格式      | 复位值 |
| ---------------------- | --------- | --------- | ------ |
| 0x60（LO）、0x64（HI） | RW shadow | u64，tick | 0      |

窗口第 0 个 beat 是 `raw_tick == RX_TARGET` 那一拍 ADC 总线上的数据（§6.2）。过期规则同 TX。

#### 4.6.2 `RX_LENGTH` — 窗口长度（shadow）

| 地址 | 访问      | 格式      | 复位值 |
| ---- | --------- | --------- | ------ |
| 0x68 | RW shadow | u32，beat | 0      |

1 … `RX_MAX_BEATS`；否则拒绝为 `REJ_LENGTH`。

#### 4.6.3 `CONTROL.RX_DOORBELL` — 提交 RX 命令

```text
（先配置 S2MM，§4.6.9）
W32 0x60 <target_lo>
W32 0x64 <target_hi>
W32 0x68 <beats>
W32 0x08 0x04
R32 0x6C  → 0x01 (ARMED)  0x14 表示 S2MM 还没就绪
```

只在 RX 状态为 IDLE 或 DONE 时处理。校验顺序：`time_valid` → 长度 → 是否过期 → DMA 就绪。Python：`pl.ctrl.arm_rx(target, beats)`，之前先 `pl.rx_dma.prepare(beats)`（§11.8）。

#### 4.6.4 `CONTROL.RX_CANCEL` — 取消

```text
W32 0x08 0x08
```

只在 ARMED 且还没到开窗那一拍时有效：回到 IDLE，`RX_RESULT = CANCELLED`。已经启动的 S2MM 传输不会自己结束，需要复位 DMA（`PynqS2MMDMA.abort()`）。Python：`PLClient.cancel_rx()`。

#### 4.6.5 `RX_RESULT` — 返回码和窗口标志

| 地址 | 访问 | 格式                          | 复位值 |
| ---- | ---- | ----------------------------- | ------ |
| 0x6C | RO   | [7:0] 返回码，[11:8] 窗口标志 | 0      |

返回码见 §5.2，标志见 §5.3。窗口结束时 `DONE` 才有效；`RX_INVALID` 表示本次测量必须丢弃。Python：`PLClient.rx_result()` → `(ResultCode, RxFlag)`。

#### 4.6.6 `RX_SEQ`、`RX_INVALID_CNT` — 计数

| 地址 | 名称               | 访问 | 格式 | 含义                           |
| ---- | ------------------ | ---- | ---- | ------------------------------ |
| 0x70 | `RX_SEQ`         | RO   | u32  | 结束的窗口数（有效和作废都计） |
| 0x80 | `RX_INVALID_CNT` | RO   | u32  | 作废窗口总数                   |

#### 4.6.7 `RX_BEATS` — 送进 DMA 的 beat 数

| 地址 | 访问 | 格式      | 复位值 |
| ---- | ---- | --------- | ------ |
| 0x74 | RO   | u32，beat | 0      |

本窗口送进 DMA 流的 beat 数，含作废窗口补发的尾拍。有效窗口应等于 `RX_LENGTH`，并且 S2MM 实际收到的字节数应为 `RX_BEATS × 32`。Python：`PLClient.rx_beats()`。

#### 4.6.8 `RX_ACTUAL` — 实际开窗时刻

| 地址                   | 访问 | 格式      | 复位值 |
| ---------------------- | ---- | --------- | ------ |
| 0x78（LO）、0x7C（HI） | RO   | u64，tick | 0      |

窗口第 0 拍在 ADC 总线上时的 tick，只在 `rx_state = DONE` 后读，应等于 target。Python：`PLClient.rx_actual()`。

#### 4.6.9 RX 样本 buffer 和 AXI DMA S2MM

样本 buffer 在 DDR 中：

- 软件分配物理连续、可被 DMA 访问的 buffer（PYNQ `allocate`），至少 `RX_LENGTH × 32` 字节。
- 第 n 个 beat 位于 buffer + n × 32。beat 内是**平面**格式：低 128 位是 8 个 I，高 128 位是 8 个 Q，lane 0（先采的样本）在各自的最低 16 位（`RX_LAYOUT`，由 BD 中 I/Q 两路流拼接而成）。lane 顺序由 M3 lane check 确认；I/Q 归属同 §4.5.8 尚未单独验证。
- 作废窗口可能缺 beat，最后可能多一个全 0 尾拍（用来带上 TLAST），内容不得使用。
- DMA 写入不经过 CPU cache，读之前必须 invalidate（PYNQ `buffer.invalidate()`）。

DMA 是 Xilinx AXI DMA（simple 模式），偏移以 AMD PG021 为准，地址空间 `rx_dma`：

| 偏移 | 名称            | 用法                                                       |
| ---- | --------------- | ---------------------------------------------------------- |
| 0x30 | `S2MM_DMACR`  | bit0 = RS（运行），初始化时置 1；bit2 = 软复位（自动清零） |
| 0x34 | `S2MM_DMASR`  | bit1 = Idle；bit12 = IOC（完成）；bit4–6 = 错误           |
| 0x48 | `S2MM_DA`     | 目标物理地址低 32 位                                       |
| 0x4C | `S2MM_DA_MSB` | 目标物理地址高 32 位                                       |
| 0x58 | `S2MM_LENGTH` | 写入即开始传输；完成后读出的是实际收到的字节数（26 位）    |

### 4.7 时钟状态 GPIO

| 地址                                       | 访问 | 格式 |
| ------------------------------------------ | ---- | ---- |
| `clk_status_gpio` + 0x0（`GPIO_DATA`） | RO   | bit0 |

```text
R32 0xA002_0000  → 1     fabric MMCM 已锁定，可以访问 P2 和 TX buffer
```

Xilinx AXI GPIO（全输入，1 位），在 `pl_clk0` 域，任何时候都可以读。bit0 = 1 说明 PL_CLK 存在且 MMCM 已锁定，**不说明 LMK04828 锁到了 FS725**：LMK 没有外部参考时会靠板上 VCXO 继续输出。LMK 锁到 FS725 的证据是 `PPS_LAST_PERIOD` 恒为 100000000；板上 PLL1/PLL2 LED（本项目的 LMK 文件把 Status_LD1/2 设为锁定指示）只作现场参考，LMK 没有配置 SPI 读回。Python：`PLClient.fabric_clock_locked()`、`require_fabric_clock()`。

## 5. 状态与错误报告

### 5.1 TX/RX 状态（`STATUS[10:8]`、`STATUS[14:12]`）

| 值 | 名称    | TX                      | RX                              |
| -- | ------- | ----------------------- | ------------------------------- |
| 0  | IDLE    | 空闲                    | 空闲                            |
| 1  | ARMED   | 已接受，等待 target     | 已接受，等待 target             |
| 2  | RUNNING | 正在发射                | 窗口打开                        |
| 3  | DRAIN   | —                      | 窗口已关闭，等待 DMA 收到 TLAST |
| 4  | DONE    | 完成，可提交下一条      | 结束，看`RX_RESULT` 是否有效  |
| 5  | FAULT   | 故障，需`CLEAR_FAULT` | —（RX 不进入 FAULT）           |

```text
TX:  IDLE/DONE ─doorbell(接受)→ ARMED ─target→ RUNNING ─最后一拍→ DONE
                               ARMED ─cancel→ IDLE
                     ARMED/RUNNING ─故障→ FAULT ─CLEAR_FAULT→ IDLE
RX:  IDLE/DONE ─doorbell(接受)→ ARMED ─target→ RUNNING ─最后一拍→ DRAIN ─TLAST→ DONE
                               ARMED ─cancel（开窗前）→ IDLE
```

TX 和 RX 相互独立，可以同时 ARMED，也可以用同一次 `CONTROL` 写触发。

### 5.2 返回码（`TX_RESULT` / `RX_RESULT` [7:0]）

| 值   | 名称              | 类别 | 含义                                          |
| ---- | ----------------- | ---- | --------------------------------------------- |
| 0x00 | NONE              | 状态 | 复位后或`CLEAR_FAULT` 后                    |
| 0x01 | ARMED             | 状态 | 命令已接受                                    |
| 0x02 | RUNNING           | 状态 | 已开始                                        |
| 0x03 | DONE              | 状态 | 成功完成                                      |
| 0x04 | CANCELLED         | 状态 | 在 ARMED 时被取消                             |
| 0x10 | REJ_BUSY          | 拒绝 | 状态不是 IDLE/DONE 时按了 doorbell            |
| 0x11 | REJ_STALE         | 拒绝 | target < 当前 tick +`MIN_LEAD_TICKS`        |
| 0x12 | REJ_LENGTH        | 拒绝 | 长度为 0 或超过上限                           |
| 0x13 | REJ_TIME_INVALID  | 拒绝 | `time_valid = 0`                            |
| 0x14 | REJ_DMA_NOT_READY | 拒绝 | RX：S2MM 未就绪（先配置 DMA 再按 doorbell）   |
| 0x20 | FAULT_TREADY      | 故障 | TX：发射中 DAC`tready` 为 0，波形出现空洞   |
| 0x21 | FAULT_BUF_WRITE   | 故障 | TX：ARMED/RUNNING 期间写了 TX buffer          |
| 0x22 | FAULT_TIME        | 故障 | TX：ARMED/RUNNING 期间`time_valid` 变为 0   |
| 0x30 | RX_INVALID        | 结果 | RX：窗口结束但有标志位（§5.3），测量必须丢弃 |

拒绝不改变状态，正在进行的命令不受影响。但 ARMED/RUNNING 期间被拒绝的 doorbell（`REJ_BUSY`）会改写 `*_RESULT` 的显示值，直到下一次状态转换再写入新的返回码。所以判断进度看 `STATUS` 的状态字段，判断某次提交是否被接受，看提交后立即读到的 `*_RESULT`。

### 5.3 RX 窗口标志（`RX_RESULT[11:8]`）

任何一位为 1，窗口结果就是 `RX_INVALID`；只作废这一次测量，系统继续运行。

| 位 | 名称      | 含义                                        |
| -- | --------- | ------------------------------------------- |
| 8  | OVERFLOW  | 窗口内 FIFO 满，丢了 beat（DDR 写入跟不上） |
| 9  | GAP       | 窗口内 ADC`tvalid` 为 0                   |
| 10 | TIME      | ARMED 或窗口期间`time_valid` 为 0         |
| 11 | OVERRANGE | 窗口内 ADC 过量程                           |

### 5.4 故障位（`FAULT_BITS`）

| 位 | 名称           | 含义                                                     |
| -- | -------------- | -------------------------------------------------------- |
| 0  | LOCK_LOST      | 时钟失锁（`time_valid` 1→0）                          |
| 1  | PPS            | PPS 周期超出容差，或 PPS 丢失（同时`pps_locked` 清 0） |
| 2  | TICK_EXHAUSTED | raw_tick 饱和                                            |
| 3  | TX             | TX 进入 FAULT                                            |

## 6. 时间语义

### 6.1 TX

第 0 个 beat 被 DAC AXIS 接收的那一拍 `raw_tick == TX_TARGET`，此后每拍一个 beat，中间没有空隙（`TX_REPEAT` > 1 时遍与遍之间也没有）；突发以外 DAC 输入为 0。突发中的第 n 个样本（重复播放时 n 从第一遍起连续计数）：

```text
t_tx(n) = TX_TARGET × 10 ns + n × 1.25 ns
```

### 6.2 RX

窗口第 0 个 beat 是 `raw_tick == RX_TARGET` 那一拍 ADC AXIS 上的数据，覆盖连续 `RX_LENGTH` 拍。窗口中的第 n 个样本：

```text
t_rx(n) = RX_TARGET × 10 ns + n × 1.25 ns
```

以上都是 **RFDC 数字接口处的时间**。DAC/ADC 内部数据通路的延时由 MTS 固定（§7.1 步骤 7），线缆、放大器、天线的延时不包含在内，需要按计划 §8 标定。未接环回线时，板上 DAC tile 2 到 ADC tile 2 的串扰测得两个数字接口之间的延时约 226.0 个样本（282.5 ns，2026-10-14）。这是 MTS 目标延时固定（§7.1 步骤 7）时的值，run4 和 run6 相同；不固定目标时它随 bitstream 变（212.07、214.08）。

### 6.3 PPS

`PPS_TICK` 的量化粒度是 10 ns。`PPS_LAST_PERIOD` 恒为 100000000 说明 fabric 时钟与 FS725 同源；LMK 靠板上 VCXO 运行时每秒会偏差几十到上百 tick，PPS 锁不上。P1 的判断：

| 周期与 100000000 之差        | 处理                                    |
| ---------------------------- | --------------------------------------- |
| 0                            | 正确周期；连续 4 个后`pps_locked = 1` |
| ±1                          | 计入`PPS_JITTER_CNT`，锁定保持        |
| 更大，或超过一个周期没有 PPS | `FAULT_BITS.PPS`，`pps_locked` 清 0 |

`PPS_JITTER_CNT` 持续增长说明 PPS 沿贴着采样沿，需要调整 PL 输入延时。PPS 沿相对 tick 的亚 tick 相位每次上电后都需要标定。

### 6.4 epoch

`TIME_EPOCH` 变化说明时钟中途失锁又重新锁定。raw_tick 不清零，但变化前后的时间戳和测量不可比，跨 epoch 的数据必须丢弃。

## 7. 编程流程

### 7.1 上电与启动

顺序不能调换：

- PYNQ 开机会加载自带的 base overlay，它的一部分逻辑也用 LMK 的时钟。先用本设计替换它，再改时钟（板上实测：先改时钟时出现过整机卡死）。
- 时钟一变，fabric MMCM 的输入就变了，要用 `pl_resetn0` 复位让它重新锁定（板上实测：不复位时首次加载后 MMCM 可能不锁）。
- RFDC tile 启动需要 LMX 已经输出参考时钟；P2 和 TX buffer 需要 fabric 时钟已经运行。

```text
加载 overlay
 0. 开机后先等系统启动完成（systemctl is-system-running --wait）：PYNQ 的 bootpy.service
    约在内核启动 1 分钟后加载 base overlay，会覆盖此前加载的设计
 1. 加载 rf_clock.bit + rf_clock.hwh（PYNQ Overlay）；此时不访问 P2 和 TX buffer。
    随包的 overlay 加载前先核对 manifest.json（§14.1）
时钟
 2. 用 xrfclk 把本项目的 LMK04828 / LMX2594 寄存器文件（`rf_clock/clocks/files/`）写入时钟芯片。
    LMK 用嵌套零延时模式（PLL1 反馈取 DCLKout8 = PL_CLK），使 100 MHz tick 相对 FS725 10 MHz/PPS
    的相位每次上电都相同
    （lmk_freq = 100.0，lmx_freq = 480.0）。板级配置（§11.1）强制了 VCO 档位的 LMX2594 随后
    重写一遍，R8 VCO_CAPCTRL_FORCE = 1、档位写在 R19；两片 LMX2594 再做相位 SYNC（R0
    VCO_PHASE_SYNC 0 -> 1）。1 s 后回读每片 LMX2594 的 VCO（R110–R112），有一片没锁定就停止
    （rf-clock 1.3.0.6 起）。留给校准自选的档位下次上电可能不同，每差一档对应的转换器移动 8–10 ps
 3. 现场可看：PLL1、PLL2 和两个 LMX（ADC/DAC）LED 都亮
 4. 拉低 pl_resetn0（EMIO GPIO 95）约 10 ms 再释放：复位 fabric MMCM 和各 proc_sys_reset
 5. 轮询时钟状态 GPIO bit0，1 s 内变为 1；为 0 时不得访问 P2 和 TX buffer
RFDC
 6. 等 ADC、DAC 的 tile 0 和 tile 2 都进入启动完成状态（TileState 0xF）；pl_resetn0 之后
    IP 会自行重新启动，超时才调用 StartUp
 7. 先 DAC、后 ADC 执行 MTS（tile 0、2），目标延时固定为 DAC 1896、ADC 1296，使数据通路
    延时与 bitstream 无关；状态非 OK 或任一 tile 没有落在目标上则停止。每个 tile 在其捕获延迟
    （DTC）中离板级配置所记码最近的窗口里采 SYSREF（rf-clock 1.3.0.8 起；没有记录时取离码 64
    最近的窗口，两个窗口离 64 差不多远时，上电之间可能整整差一个采样）。然后在同一个 SYSREF 沿上
    让四个 tile 的 NCO 重新起相（PG269 "Use Case 1"），使每次上电后载波相位都相同（rf-clock
    1.3.0.5 起；此前它会落在相差 120° 的三个值之一）
PL
 8. 读 ID == 0x52434C4B，VERSION 的 major == 1；BUILD_VERSION 的 A.B.C 与
    安装的 rf-clock 相同（§14.1）
 9. 读 TX_DEPTH、RX_MAX_BEATS、MIN_LEAD_TICKS
10. 写 PPS_CONFIG（本板用默认值 0）
11. 等 STATUS.time_valid = 1 和 STATUS.pps_locked = 1（约 5 s）；记下 TIME_EPOCH。
    pps_locked 不来时读 PPS_INPUTS 判断 PPS 是否到达
12. 若 STATUS.any_fault：读 FAULT_BITS/FAULT_INFO 并记录，写 CONTROL.CLEAR_FAULT
13. 生成波形，写入 TX buffer
14. 分配 RX DDR buffer；S2MM_DMACR.RS = 1
```

### 7.2 安排一次 TX

```text
1. 确认 tx_state 为 IDLE 或 DONE
2. R64 0x28 → pps_tick（最近一次 PPS）
3. target = pps_tick + 100000000 + 时隙偏移；下一个 PPS 之后，必然 ≥ 当前 tick + MIN_LEAD_TICKS
4. W32 0x40 / 0x44 / 0x48（1.3 起再写 0x5C 重复次数），再 W32 0x08 0x01
5. R32 0x4C，确认为 0x01（ARMED）
6. 轮询 STATUS 直到 tx_state = DONE 或 FAULT
7. DONE：R64 0x54，校验 TX_ACTUAL == target
```

### 7.3 安排一次 RX

```text
1. 确认 rx_state 为 IDLE 或 DONE
2. 配置 S2MM：写 S2MM_DA / S2MM_DA_MSB，再写 S2MM_LENGTH = RX_LENGTH × 32
3. W32 0x60 / 0x64 / 0x68，再 W32 0x08 0x04
4. R32 0x6C，确认为 0x01；为 0x14 说明 S2MM 还没就绪；被拒绝时复位 DMA
5. 等 S2MM 完成，再轮询直到 rx_state = DONE
6. RX_RESULT[7:0] == 0x03 才有效；0x30 时丢弃本次测量，记录 [11:8] 标志
7. 校验 RX_ACTUAL == target，S2MM_LENGTH（实际字节数）== RX_BEATS × 32
8. invalidate buffer，读样本
```

### 7.4 运行中的监视

每个测量周期都要确认：时钟状态 GPIO bit0 仍为 1、`TIME_EPOCH` 未变、`STATUS.pps_locked` 仍为 1（LMK 失去 FS725 参考后 PPS 周期会漂出容差）。任何一项不满足，丢弃这段时间的测量。

## 8. 寄存器速查表

偏移相对 P2 基地址。

| 偏移 | 名称                | 访问 | 复位值     | 含义                                                | 详见    |
| ---- | ------------------- | ---- | ---------- | --------------------------------------------------- | ------- |
| 0x00 | `ID`              | RO   | 0x52434C4B | 设计标识 "RCLK"                                     | §4.1.1 |
| 0x04 | `VERSION`         | RO   | 0x00010003 | [31:16] major，[15:0] minor                         | §4.1.2 |
| 0x08 | `CONTROL`         | WO   | —         | 命令脉冲                                            | §4.1.3 |
| 0x0C | `STATUS`          | RO   | —         | 状态字                                              | §4.2.1 |
| 0x10 | `TIME_EPOCH`      | RO   | 0          | 时钟重新锁定次数                                    | §4.1.4 |
| 0x14 | `FAULT_BITS`      | RO   | 0          | 粘滞故障位                                          | §4.2.2 |
| 0x18 | `FAULT_INFO`      | RO   | 0          | 第一个故障源、故障事件总数                          | §4.2.3 |
| 0x20 | `TICK_LO`         | RO   | —         | 当前 raw_tick 低 32 位；读它时锁存 HI               | §4.3.1 |
| 0x24 | `TICK_HI`         | RO   | —         | 锁存的高 32 位                                      | §4.3.1 |
| 0x28 | `PPS_TICK_LO`     | RO   | 0          | 最近一次 PPS 的 tick；读它时锁存 HI 和`PPS_COUNT` | §4.4.1 |
| 0x2C | `PPS_TICK_HI`     | RO   | 0          | 锁存的高 32 位                                      | §4.4.1 |
| 0x30 | `PPS_COUNT`       | RO   | 0          | 锁存的 PPS 沿计数                                   | §4.4.1 |
| 0x34 | `PPS_LAST_PERIOD` | RO   | 0          | 最近一个 PPS 周期（tick）                           | §4.4.2 |
| 0x38 | `PPS_JITTER_CNT`  | RO   | 0          | ±1 tick 周期的次数                                 | §4.4.3 |
| 0x3C | `PPS_INPUTS`      | RO   | —         | 两路 PPS 的电平和沿计数（1.1）                      | §4.4.4 |
| 0x40 | `TX_TARGET_LO`    | RW   | 0          | TX 开始 tick 低 32 位（shadow）                     | §4.5.1 |
| 0x44 | `TX_TARGET_HI`    | RW   | 0          | TX 开始 tick 高 32 位（shadow）                     | §4.5.1 |
| 0x48 | `TX_LENGTH`       | RW   | 0          | TX 长度（beat，shadow）                             | §4.5.2 |
| 0x4C | `TX_RESULT`       | RO   | 0          | TX 返回码                                           | §4.5.5 |
| 0x50 | `TX_SEQ`          | RO   | 0          | 完成的 TX 突发数                                    | §4.5.6 |
| 0x54 | `TX_ACTUAL_LO`    | RO   | 0          | 实际开始 tick 低 32 位                              | §4.5.7 |
| 0x58 | `TX_ACTUAL_HI`    | RO   | 0          | 实际开始 tick 高 32 位                              | §4.5.7 |
| 0x5C | `TX_REPEAT`       | RW   | 1          | 重复播放遍数（shadow，1.3）                         | §4.5.9 |
| 0x60 | `RX_TARGET_LO`    | RW   | 0          | RX 开窗 tick 低 32 位（shadow）                     | §4.6.1 |
| 0x64 | `RX_TARGET_HI`    | RW   | 0          | RX 开窗 tick 高 32 位（shadow）                     | §4.6.1 |
| 0x68 | `RX_LENGTH`       | RW   | 0          | RX 窗口长度（beat，shadow）                         | §4.6.2 |
| 0x6C | `RX_RESULT`       | RO   | 0          | [7:0] 返回码，[11:8] 窗口标志                       | §4.6.5 |
| 0x70 | `RX_SEQ`          | RO   | 0          | 结束的窗口数                                        | §4.6.6 |
| 0x74 | `RX_BEATS`        | RO   | 0          | 送进 DMA 的 beat 数                                 | §4.6.7 |
| 0x78 | `RX_ACTUAL_LO`    | RO   | 0          | 实际开窗 tick 低 32 位                              | §4.6.8 |
| 0x7C | `RX_ACTUAL_HI`    | RO   | 0          | 实际开窗 tick 高 32 位                              | §4.6.8 |
| 0x80 | `RX_INVALID_CNT`  | RO   | 0          | 作废窗口总数                                        | §4.6.6 |
| 0x84 | `TX_DEPTH`        | RO   | 69632      | TX buffer 容量（beat）                              | §4.1.5 |
| 0x88 | `RX_MAX_BEATS`    | RO   | 2097151    | RX 窗口上限（beat）                                 | §4.1.5 |
| 0x8C | `MIN_LEAD_TICKS`  | RO   | 64         | target 最小提前量（tick）                           | §4.1.5 |
| 0x90 | `PPS_CONFIG`      | RW   | 0          | PPS 来源和极性（1.1）                               | §4.4.5 |
| 0x94 | `BUILD_VERSION`   | RO   | 构建时定   | 构建时的 rf-clock 版本 0xAABBCCDD（1.2）            | §4.1.6 |

---

# 第二部分 Python 库 `rf_clock.pl`

## 9. 环境

| 场合               | 怎么用                                                                                                                                                                                             |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 板上（PYNQ 3.1.1） | PYNQ 的环境 `/usr/local/share/pynq-venv`（§9.1），以 root 运行：`sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m <模块>`。`xrfclk` 需要 `BOARD`，PYNQ 需要 `XILINX_XRT` |
| 开发机             | 用`MemoryMMIO` 作假寄存器测试协议逻辑（`tests/`）。`uv sync` 会装与板子同版本的 `pynq==3.1.1`（dev 依赖），只用于 IDE 补全和离线读 HWH，不能控制硬件；它不是运行依赖，板上用系统自带的那份 |

### 9.1 板上环境

板子不联网。rf-clock 装在 PYNQ 自己的环境 `/usr/local/share/pynq-venv` 里（Python 3.10，pynq 3.1.1，numpy 1.21.5），PYNQ 的 Jupyter 和板上其他人都直接用它。它唯一的依赖 numpy 就用 PYNQ 那份（`pyproject.toml` 固定的也是 1.21.5），所以安装时不带依赖，这个环境里的其他东西都不变。

安装或更新，以 root 执行（这个环境属于 root）：

```bash
sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index rf_clock-<版本>-py3-none-any.whl
```

wheel 用发布的那个（§14.1）；开发中的版本在板上从仓库打包：`~/.local/bin/uv build --wheel --offline -o /tmp/rfw`（能离线打包，因为 uv_build 在板上的 uv 缓存里）。

要点：

- 用 `--no-deps --no-index`：不往 PYNQ 环境里装、也不升级任何别的包；板子也不联网。不要在那里做可编辑安装：那样板上所有人跑的都是工作目录里当时的代码。也不要在板上用 `uv sync`。
- 时钟寄存器文件在包里（`rf_clock/clocks/files/`），由 `PL.open()` 交给 xrfclk 写入，不需要拷进 xrfclk 的目录。
- `BOARD` 环境变量（`RFSoC4x2`）由 `/etc/profile.d/boardname.sh` 设置，只在登录 shell 里有；`xrfclk` 在 import 时就读它。访问 `/dev/mem`、下载比特流、写 SPI 时钟芯片都需要 root，而 `sudo` 会清掉环境变量，所以写成 `sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python ...`。
- Jupyter：PYNQ 的 Jupyter 就运行在这个环境里，直接导入装好的 rf_clock。
- `~/RF-clock/.venv` 是 1.3.0.6 之前用的独立 venv（通过 `.pth` 文件看到 PYNQ 的包），里面已不装 rf-clock；用它启动的脚本（比如 `experiments/` 在板上运行的部分）导入的是 PYNQ 里那份。
- PYNQ 镜像升级后，重新安装 wheel。
- 两块板的系统时间经 NTP 跟随 RF-clock-PC（那里的 chrony 为 192.168.2.0/24 提供时间；板上 `/etc/systemd/timesyncd.conf.d/rf-clock.conf` 设 `NTP=192.168.2.1`）。`rf_clock.twtt` 靠它为两块板的秒配对（§11.12）；板子断电后不保留时间。
- 远程恢复：访问时钟没在跑的 fabric 域会卡死 AXI 总线，板子 ping 不通。可以从 JTAG 线另一端的机器复位：`source /tools/Xilinx/Vivado/2024.1/settings64.sh; xsdb tools/jtag_reset_board.tcl <JTAG 线缆序列号>`，约 40 s 后 ssh 恢复；重启后 PL 里是 PYNQ 的 base overlay。

模块一览：

| 模块                     | 内容                                                                      |
| ------------------------ | ------------------------------------------------------------------------- |
| `rf_clock.pl.board`    | `PL`：上电，并持有各硬件部件（ctrl、tx_buffer、rx_dma、rfdc、clock_gpio） |
| `rf_clock.pl.board_config` | 每块板的配置文件和本板序列号（§11.1） |
| `rf_clock.pl.abi`      | 由 RTL 生成的地址、位号和编码                                             |
| `rf_clock.pl.overlay`  | 随包的 overlay 路径、manifest 和版本检查（§14.1）                        |
| `rf_clock.pl.mmio`     | MMIO 接口和实现（PYNQ / 内存假件）                                        |
| `rf_clock.pl.client`   | `PLClient`：P2 寄存器的全部协议                                         |
| `rf_clock.pl.bringup`  | `Bringup`：§7.1 的上电流程，以及 PYNQ 辅助函数                         |
| `rf_clock.pl.rfdc`     | RFDC tile 启动和 MTS                                                      |
| `rf_clock.pl.txbuf`    | `TXBuffer`：写 TX 波形 buffer                                           |
| `rf_clock.pl.rxdma`    | `RXDMA`：RX 窗口的 S2MM 一侧（buffer、DMA 配置和完成检查）              |
| `rf_clock.pl.waveform` | `WaveformGenerator`：PRN + RRC 发射波形                                 |
| `rf_clock.pl.beats`    | 256 bit beat 的打包和解包                                                 |
| `rf_clock.correlator`  | 互相关时延估计（不依赖 PL）                                               |
| `rf_clock.twtt`        | 两块板之间的双向时间比对（§11.12） |

## 10. 快速上手

板上：上电，按 PPS 发一个突发，同拍收一个窗口（`python -m rf_clock.examples.pl.03_pynq_bringup`，2026-10-14 板上运行通过）。

```python
from rf_clock.pl import PL, WaveformGenerator

pl = PL.open()                                 # §7.1 全部上电步骤，默认用随包的 overlay

beats = WaveformGenerator().generate_beats()   # TX_LAYOUT
rx_beats = len(beats) + 1250
pl.tx_buffer.write_beats(beats)                # TX buffer（片上 BRAM）
pl.rx_dma.prepare(rx_beats)                    # S2MM：buffer 地址和长度，先于 RX doorbell

_, pps = pl.ctrl.wait_next_pps()               # 刚过一个 PPS
target = pps + 100_000_000 + 100_000           # 下一个 PPS 之后 1 ms
pl.ctrl.arm_tx(target, len(beats))             # TX_TARGET、TX_LENGTH、doorbell
pl.ctrl.arm_rx(target, rx_beats)               # RX_TARGET、RX_LENGTH、doorbell
t_tx = pl.ctrl.wait_tx(timeout=2.5)            # TX_ACTUAL（已核对 == target）
window = pl.ctrl.wait_rx(timeout=2.5)          # RX_ACTUAL（已核对）、RX_BEATS、窗口标志

x = pl.rx_dma.wait(window.beats).samples       # complex64，800 MS/s，从 window.actual 开始
```

命令、时间戳和状态都在 `pl.ctrl`；`tx_buffer`、`rx_dma` 只管数据通路（写波形、配 DMA）。两次 wait 的先后不影响数据：开窗之后 P4 → FIFO → DMA → DDR 由硬件搬运。

开发机：用假寄存器验证协议逻辑。

```python
from rf_clock.pl import abi
from rf_clock.pl.client import PLClient
from rf_clock.pl.mmio import MemoryMMIO

regs = MemoryMMIO(initial={abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
                           abi.ABI.REG.VERSION: abi.ABI.VERSION})
client = PLClient(regs, MemoryMMIO(initial={0: 1}))     # GPIO bit0 = 1
client.validate()
```

## 11. API 参考

### 11.1 `rf_clock.pl.PL`（上电和硬件部件）

`PL.open()` 完成上电，返回的对象只负责持有各硬件部件，一个部件对应 §1.1 的一个地址空间，操作都通过部件自己的方法：

| 属性           | 类型                    | 对应的硬件                                                                                                                         |
| -------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `ctrl`       | `PLClient`（§11.4）  | P2 寄存器：全部命令（arm、wait、cancel）、时间戳、PPS、状态                                                                        |
| `tx_buffer`  | `TXBuffer`（§11.7）  | 片上 TX 波形 buffer（AXI BRAM）                                                                                                    |
| `rx_dma`     | `RXDMA`（§11.8）     | AXI DMA S2MM：RX 窗口写入 DDR，buffer 自动分配并复用                                                                               |
| `rfdc`       | `RFDC`                | `rfdc.driver`：xrfdc 驱动（`driver.dac_tiles[2].blocks[0]` 等）；`rfdc.mts`：MTS 结果（§11.6），`mts=False` 时访问抛 `RuntimeError` |
| `clock_gpio` | MMIO                    | 时钟状态 GPIO（`pl_clk0`），bit0 = fabric MMCM 已锁定                                                                            |
| `overlay`    | PYNQ`Overlay`         |                                                                                                                                    |
| `bringup`    | `BringupResult`       | 上电时发现并清除的故障（`initial_fault_bits`、`initial_fault_info`）                                                           |
| `board`      | `BoardSetup`          | `serial`：本板 AT24MAC402 的序列号；`config`：板级配置，没有时为 `None`；`lmx_vco`：按 `"adc"`/`"dac"` 给出回读的 LMX2594 VCO（`band`、`forced`、`locked`、`capctrl`、`vco_sel`、`daciset`、`ld_vtune`），`write_clocks=False` 时为空 |

#### `PL.open(bitstream=DEFAULT_BITSTREAM, *, write_clocks=True, mts=True, pps_source_trig=False, pps_invert=False, ref_tile=0, dac_target_latency=DAC_TARGET_LATENCY, adc_target_latency=ADC_TARGET_LATENCY, check_version=True, board_config=None, progress=None)`

按 §7.1 完成全部上电步骤。固定的硬件选择都是默认值：overlay 为随包的 `rf_clock/overlay/rf_clock.bit`（§14.1）；时钟用包里 `rf_clock/clocks/files/` 的 `LMK04828_100.0.txt` 和 `LMX2594_480.0.txt`（经 xrfclk 写入，板上不必把文件拷进 xrfclk 目录）；`pl_resetn0` 复位；RFDC tile 0、2 启动和 MTS。

| 参数                                | 用途                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------ |
| `write_clocks=False`              | 开机后已写过 LMK/LMX 时跳过                                              |
| `mts=False`                       | 不做 RFDC 启动和 MTS（只用时间基准时，如 M1）                            |
| `pps_source_trig`、`pps_invert` | 写入`PPS_CONFIG`（§4.4.5）                                            |
| `ref_tile`、`*_target_latency`  | MTS 参数（§11.6）；目标延时默认 DAC 1896、ADC 1296（§7.1），-1 为对齐到实测最大值 |
| `check_version=False`             | 不做 §14.1 的 manifest 和`BUILD_VERSION` 检查（1.2 之前的 bitstream） |
| `board_config`                    | 一个 `BoardConfig`，或一个板级配置文件的路径，代替默认文件（见下）     |
| `progress`                        | 每一步回调一行文字，如`print`                                          |

失败时抛出原异常（`BringupError`、`BoardConfigError`、`RFDCError`、`VersionMismatch` 等），异常上带 `.overlay`，MTS 跑过时还带 `.mts`。开发机上可以直接 `PL(ctrl=..., tx_buffer=..., ...)` 用假件构造。

**板级配置**（`rf_clock.pl.board_config`，1.3.0.6 起）。属于某一块板、而不属于设计的东西，放在这块板上的一个 JSON 文件里：`/etc/rf-clock/board.json`，或环境变量 `RF_CLOCK_BOARD_CONFIG` 指定的文件。`PL.open()` 在加载 overlay 之前读它：

| 键 | 内容 |
| --- | --- |
| `serial` | 本板 AT24MAC402 EEPROM 的 128 位序列号（PS I2C1，地址 0x58，偏移 0x80），32 个十六进制数字；不是本板的就由 `PL.open()` 抛 `BoardConfigError` |
| `label` | 自由文字，例如本板的 S/N |
| `lmx_vco_band` | 每片 LMX2594 强制使用的 VCO 档位（VCO_CAPCTRL，0–183），`{"adc": 117, "dac": 122}`（§7.1 第 2 步） |
| `mts_dtc` | 由 `rf_clock.checks.mts_dtc`（§13）写入：MTS 选窗所依据的 SYSREF 捕获码（§7.1 第 7 步），按转换器（`dac`、`adc`）和时钟（`t1`；ADC 另有 `pll`），每个 tile 0–3 一个（-1 为不用），以及 `recorded`（时间、上电次数、对应的 VCO 档位）。选到的码离记录超过 6 个码（约 32 ps）、记录时的 VCO 档位与现在不同、或没有记录时，`PL.open()` 发出警告 |
| `band_table` | 由 `rf_clock.checks.vco_bands`（§13）写入：每片芯片校准选中各档位的次数（`calibrated`）、能锁定的档位（`locked`），以及每个档位下 DAC → ADC 回环延时的变化，单位 ps（`loopback_ps`），相对两片都在 `measured.reference` 时；另记扫描（`scanned`）和测量（`measured`）的时间与芯片温度 |

没有这个文件，或文件没写某片芯片时，那片 LMX2594 用校准自选的档位，`PL.open()` 发出警告（`UserWarning`），`pl.board.lmx_vco` 记下这个档位：本板的频率落在相邻两档都够得着的地方时，校准每次上电在两档之间任选其一，而高一档会让那个转换器的时钟晚 8–10 ps（results/lmx-sync-loopback-20261019 第 5 节）。指定的文件不存在、格式不对或含未知的键，抛 `BoardConfigError`。无论哪种情况，有一片芯片没锁定都抛 `BringupError`。档位表不被 `PL.open()` 使用，它记录的是每个档位的代价。

### 11.2 `rf_clock.pl.abi`

由 `python tools/generate_abi.py --write` 从 `p_regs.vh`、`p_defs.vh` 生成，不要手改；`--check` 检查是否过期（`tests/test_abi_sync.py`）。

| 名称                               | 内容                                                                                              |
| ---------------------------------- | ------------------------------------------------------------------------------------------------- |
| `ABI.REG.<名称>`                 | 寄存器偏移，如`ABI.REG.PPS_TICK_LO == 0x28`                                                     |
| `ABI.CTRL.<名称>`                | `CONTROL` 位号：`TX_DOORBELL`、`TX_CANCEL`、`RX_DOORBELL`、`RX_CANCEL`、`CLEAR_FAULT` |
| `ABI.STAT.<名称>`                | `STATUS` 位号                                                                                   |
| `ABI.PPSCFG`、`ABI.PPSIN`      | `PPS_CONFIG`、`PPS_INPUTS` 的位号                                                             |
| `ABI.DESIGN_ID`、`ABI.VERSION` | 期望的`ID` 和本库对应的 `VERSION`                                                             |
| `TxState`、`RxState`           | §5.1 的状态（IntEnum，二者相同）                                                                 |
| `ResultCode`                     | §5.2 的返回码（IntEnum）                                                                         |
| `RxFlag`                         | §5.3 的窗口标志（IntFlag）                                                                       |
| `FaultBit`                       | §5.4 的故障位（IntFlag）                                                                         |

### 11.3 `rf_clock.pl.mmio`

| 名称                                        | 说明                                                                                                           |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `RegisterIO`                              | 协议：`read(offset) -> int`、`write(offset, value)`，32 位、偏移按字对齐                                   |
| `PynqMMIO(base, length=0x1000)`           | 板上实现，包装`pynq.MMIO`                                                                                    |
| `MemoryMMIO(initial=None, on_write=None)` | 内存假件：`values` 字典、`read_log`、`write_log`；`on_write(offset, value)` 用来模拟 doorbell 等副作用 |

### 11.4 `rf_clock.pl.client`

#### `PLClient(regs, clock_status, *, clock_status_offset=0, clock_locked_mask=1, expected_major=1)`

P2 寄存器协议的唯一入口。构造时不访问任何寄存器。**每次访问 P2 之前都先读时钟状态 GPIO**，未锁定时抛 `ClockUnavailable`，不会把总线卡死。

| 方法                                                   | 对应协议                                                                                  | 返回 / 异常                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `validate()`                                         | 读`ID`、`VERSION`、`TX_DEPTH`、`RX_MAX_BEATS`、`MIN_LEAD_TICKS`、`TIME_EPOCH` | `DeviceInfo`，之后也可用 `info` 属性取（`validate()` 之前访问抛 `IdentityError`）；`IdentityError`（ID 不对或 major 不符） |
| `status()`                                           | R32`STATUS`                                                                             | `Status`                                                             |
| `read32(off)` / `write32(off, v)`                  | R32 / W32                                                                                 | 原始访问                                                               |
| `read64(lo_off)`                                     | R64（先 LO 后 HI）                                                                        | int                                                                    |
| `fabric_clock_locked()`                              | 读 GPIO bit0                                                                              | bool                                                                   |
| `require_fabric_clock()`                             | 同上                                                                                      | 未锁定时`ClockUnavailable`                                           |
| `time_epoch()`                                       | R32`TIME_EPOCH`                                                                         | int                                                                    |
| `remember_epoch()`                                   | 记下当前 epoch                                                                            | int                                                                    |
| `require_same_epoch(expected=None)`                  | 与记下的 epoch 比较                                                                       | 变化时`EpochChanged`                                                 |
| `arm_tx(target, beats, *, repeat=1)`                 | §4.5.3、§4.5.9                                                                           | 接受则返回；拒绝抛`CommandRejected`；立即 FAULT 抛 `PLClientError` |
| `arm_rx(target, beats)`                              | §4.6.3                                                                                   | 同上                                                                   |
| `wait_tx(timeout=1.0, poll_interval=0.001)`          | 轮询到`tx_state = DONE`，读 `TX_ACTUAL`                                                 | `TX_ACTUAL`；≠ target 抛 `TargetMissed`；FAULT 抛 `PLClientError`；超时 `WaitTimeout` |
| `wait_rx(timeout=1.0, poll_interval=0.001)`          | 轮询到`rx_state = DONE`，读 `RX_RESULT`、`RX_ACTUAL`、`RX_BEATS`（§7.3 第 5–7 步）    | `RXWindow(target, actual, beats)`；作废抛 `RXWindowInvalid`；≠ target 抛 `TargetMissed`；超时 `WaitTimeout` |
| `tx_actual()` / `rx_actual()`                      | R64`TX_ACTUAL` / `RX_ACTUAL`                                                          | int                                                                    |
| `rx_beats()`                                         | R32`RX_BEATS`                                                                           | int                                                                    |
| `rx_result()`                                        | R32`RX_RESULT`                                                                          | `(ResultCode, RxFlag)`                                               |
| `cancel_tx()` / `cancel_rx()`                      | W32`CONTROL`                                                                            | —                                                                     |
| `clear_fault()`                                      | W32`CONTROL` 0x10                                                                       | —                                                                     |
| `latest_pps()`                                       | R64`PPS_TICK`，再 R32 `PPS_COUNT`                                                     | `(count, tick)`，同一个沿                                            |
| `wait_next_pps(timeout=2.5, poll_interval=0.005)`    | 轮询到`PPS_COUNT` 变化                                                                  | 新沿的`(count, tick)`；超时 `WaitTimeout`                          |
| `pps_inputs()`                                       | R32`PPS_INPUTS`                                                                         | `PPSInputs`；ABI minor < 1 时 `IdentityError`                      |
| `set_pps_config(*, source_trig=False, invert=False)` | W32`PPS_CONFIG`                                                                         | 同上                                                                   |
| `build_version()`                                    | R32`BUILD_VERSION`                                                                      | `"A.B.C.D"`；ABI minor < 2 时 `IdentityError`                      |

`arm_tx`/`arm_rx` 的接受判断：提交后读到 ARMED 或 RUNNING 算接受；读到 DONE（RX 还有 RX_INVALID）时，只有 `*_SEQ` 比提交前增加才算接受，避免把上一条命令的结果当成这一条的。提交前若记下过 epoch，会先检查 epoch 未变。

数据类：

| 类             | 字段                                                                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `Status`     | `raw`、`time_valid`、`clocks_locked`、`pps_locked`、`tick_exhausted`、`any_fault`、`dma_ready`、`tx_state`、`rx_state` |
| `DeviceInfo` | `design_id`、`version`、`tx_depth`、`rx_max_beats`、`min_lead_ticks`                                                           |
| `PPSInputs`  | `comp_level`、`trig_level`、`comp_edges`、`trig_edges`                                                                           |

按 PPS 安排时隙的常用写法：

```python
count, pps_tick = client.wait_next_pps()            # 刚过一个 PPS，离下一个还有约 1 s
target = pps_tick + 100_000_000 + offset_ticks      # 下一个 PPS 之后 offset
```

### 11.5 `rf_clock.pl.bringup`

#### `Bringup(*, overlay_loader, clock_setup, register_factory, clock_status_factory, rfdc_setup=None, pl_reset=None, pps_source_trig=False, pps_invert=False, package_version=None, fabric_lock_timeout=1.0, time_valid_timeout=1.0, pps_lock_timeout=10.0, poll_interval=0.1)`

按 §7.1 的固定顺序调用注入的板上操作。`run(bitstream)` 依次执行：

| 步骤 | 调用                                                                         | 失败时                                              |
| ---- | ---------------------------------------------------------------------------- | --------------------------------------------------- |
| 1    | `overlay_loader(path)` → overlay                                          | 原样抛出                                            |
| 2    | `clock_setup()`                                                            | 原样抛出                                            |
| 3    | `pl_reset()`（给了才调）                                                   | 原样抛出                                            |
| 4    | 轮询`clock_status_factory(overlay)` bit0                                   | `fabric_lock_timeout` 秒内不锁抛 `BringupError` |
| 5    | `rfdc_setup(overlay)`（给了才调）                                          | 原样抛出（如`RFDCError`）                         |
| 6    | `PLClient.validate()`；给了 `package_version` 时比较 `build_version()` | `IdentityError`；A.B.C 不同抛 `VersionMismatch` |
| 7    | `set_pps_config(pps_source_trig, pps_invert)`                              | —                                                  |
| 8    | 等`time_valid`、`pps_locked`                                             | 超时抛`BringupError`                              |
| 9    | 有故障时先读`FAULT_BITS`/`FAULT_INFO`，再 `clear_fault()`；记下 epoch  | —                                                  |

返回 `BringupResult(overlay, client, info, initial_fault_bits, initial_fault_info)`。

辅助函数：

| 函数                                                                                 | 说明                                                                                 |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `wait_for_boot(timeout=300.0)`                                                     | 等`systemctl is-system-running --wait`，返回最终状态（PYNQ 上通常是 `degraded`） |
| `pynq_pl_reset(*, emio_index=95, pulse=0.01)`                                      | 拉低`pl_resetn0` 约 10 ms 再释放                                                   |
| `pynq_register_factories(*, p2_ip_name="p_top_0", gpio_ip_name="clk_status_gpio")` | 返回`(registers, clock_status)` 两个工厂，供 `Bringup` 使用                      |
| `mmio_from_overlay(overlay, name, *, default_length=0x1000)`                       | 按 HWH 名称建`PynqMMIO`；同时查 `ip_dict` 和 `mem_dict`                        |
| `overlay_address(overlay, name, *, default_length=0x1000)`                         | 返回`(base, length)`                                                               |

### 11.6 `rf_clock.pl.rfdc`

PYNQ 3.1.1 的 `xrfdc` 没有封装 MTS，本模块按 embeddedsw `xilinx-v2020.2` 的 `xrfdc_mts.h` 用 cffi 直接调用 `libxrfdc.so`。声明必须与该版本一致（新版的 `XRFdc_MultiConverter_Init` 多一个参数）。

#### `setup_rfdc_and_mts(overlay, *, rfdc_name="rfdc", tiles=(0, 2), signal_tile=2, ref_tile=0, dac_target_latency=-1, adc_target_latency=-1)`

§7.1 的步骤 6–7：等 tile 启动，先 DAC、后 ADC 做 MTS，两次 MTS 都成功时在 SYSREF 沿上让 NCO 重新起相（`sync_nco_phase`），再读回信号 tile 的混频器设置。可直接作为 `Bringup` 的 `rfdc_setup`。

- 返回 `RFDCSetupResult(dac, adc, started_after_startup_call, mixers, nco_synced)`。
- 任一 MTS 状态非 OK 时抛 `RFDCError`，异常的 `.result` 属性仍带着结果，便于记录延时。
- `*_target_latency = -1` 表示对齐到实测的最大延时，这个值随 bitstream 变；`PL.open()` 默认传固定值 `board.DAC_TARGET_LATENCY` = 1896、`board.ADC_TARGET_LATENCY` = 1296。目标低于某个 tile 的实测延时时 MTS 报 `TARGET_LOW`，此时调高目标并重新标定环回延时。

| 其他函数                                                                | 说明                                                                          |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `run_mts(rfdc, kind, tiles=(0, 2), *, ref_tile=0, target_latency=-1)` | `kind` 为 `"DAC"` 或 `"ADC"`，返回 `MTSResult`                        |
| `sync_nco_phase(rfdc, tiles=(0, 2))` | PG269 "Use Case 1"：在 SYSREF 接收关闭时，把每个 block 0 混频器的事件源设为 SYSREF 并挂起 ResetNCOPhase；打开接收器等一个 SYSREF 沿（200 ns，整数个 NCO 周期）再关闭；最后恢复原来的事件源，使之后的 `UpdateEvent` 照常可用。`XRFdc_MTS_Sysref_Config` 失败时抛 `RFDCError` |
| `start_tiles(rfdc, tiles=(0, 2), *, settle=1.0, timeout=5.0)`         | 等 TileState 0xF；`settle` 秒后仍未启动就调 StartUp。返回是否调用过 StartUp |
| `tile_states(rfdc)`                                                   | `{"ADC": [4 个 TileState], "DAC": [...]}`                                   |
| `rfdc_driver(overlay, name="rfdc")`                                   | 取`xrfdc.RFdc` 驱动；overlay 加载前没导入 `xrfdc` 时，按 HWH 描述自己构造 |
| `decode_mts_status(status)`                                           | 把 MTS 状态码拆成名称列表，如`["TARGET_LOW"]`                               |

`MTSResult` 字段：`kind`、`status`、`tiles`、`ref_tile`、`target_latency`、`latency`（tile 0–3）、`offset`（tile 0–3）、`dtc`（驱动为每个 tile 选定的 SYSREF 捕获延迟码 0–127 及找到的捕获窗口数，PLL 和 T1 时钟各一组：`pll_code`、`pll_windows`、`t1_code`、`t1_windows`；它们反映 tile 时钟相对 SYSREF 的相位，`latency` 和 `offset` 反映不了）；`describe()` 给出一行说明。`ok` 要求状态为 0 且每个 tile 都落在目标上（`on_target`）：驱动按整步（DAC、ADC 都是 6）加延时，目标与实测之差不是整步时会差一点而不报错。

板上实测（2026-10-14）：不设目标时 DAC tile 0/2 延时 1848，ADC 1236（run4）或 1248（run6），同一 bitstream 重启前后不变。目标设为 DAC 1896、ADC 1296 后，run4 和 run6 的串扰环回延时分别为 226.05 和 226.02 个样本，差 0.03 个样本，在标准差（0.13–0.16 个样本）以内。

### 11.7 `rf_clock.pl.txbuf`

#### `TXBuffer(memory, client)`

`memory` 是 `tx_bram_ctrl` 的 `RegisterIO`（`mmio_from_overlay(overlay, "tx_bram_ctrl")`）。

| 方法                        | 说明                                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------ |
| `write_beats(beats)`      | 从第 0 个 beat 起写入，返回 beat 数。一次整块拷贝（附录 A.2），整批只检查一次 TX 状态和时钟 GPIO |
| `write_beat(index, beat)` | 写单个 beat                                                                                      |

beat 可以是小于 2^256 的 int，或 32 字节的 bytes。超过 `TX_DEPTH`，或 TX 为 ARMED/RUNNING 时抛 `TXBufferError`（不会去写，避免触发 `FAULT_BUF_WRITE`）。4106 个 beat 的默认波形加载约 17 ms（其中拷贝约 0.5 ms），逐字写入时约 0.3–0.5 s。

### 11.8 `rf_clock.pl.rxdma`

> 已上板验证（2026-10-14，`rf_clock.checks.m3_loopback`，含 64 MB 窗口的压力测试）；
> `PynqS2MMDMA.abort()` 尚未在板上触发过。

#### `RXDMA(dma, *, buffer_factory=None)`

`pl.rx_dma`：RX 窗口的 S2MM 一侧。命令和窗口校验在 `pl.ctrl`（§11.4），一个窗口按 §7.3：

```python
pl.rx_dma.prepare(n)                     # 1. S2MM：buffer 地址和长度
pl.ctrl.arm_rx(target, n)                # 2. RX_TARGET、RX_LENGTH、doorbell
window = pl.ctrl.wait_rx(timeout=2.5)    # 3. DONE、标志、RX_ACTUAL == target
data = pl.rx_dma.wait(window.beats)      # 4. S2MM 完成、字节数 == RX_BEATS × 32、invalidate
x = data.samples                         # complex64，从 window.actual 开始
```

| 方法                                  | 作用                                                                                       |
| ------------------------------------- | ------------------------------------------------------------------------------------------ |
| `prepare(beats, *, buffer=None)`      | 启动 S2MM，返回 buffer。不传 `buffer` 时由 `buffer_factory` 分配，并在后续窗口中复用（窗口变大时才重新分配） |
| `wait(beats, *, timeout=1.0)`         | `beats` 取 `RXWindow.beats`；返回 `RXData(buffer, beats, received_bytes)`                  |
| `abort()`                             | 放弃已启动的传输                                                                           |

`RXData.samples` 在第一次访问时把窗口按 `RX_LAYOUT` 解码成 complex64。buffer 会被下一个窗口复用，所以下一次 `prepare` 之前先取走 `samples`。

`prepare` 必须在 RX doorbell 之前：S2MM 没就绪时 PL 拒绝 doorbell（0x14）。doorbell 被拒绝或窗口被取消时，已启动的传输等不到 TLAST；下一次 `prepare` 发现通道不空闲会先复位 DMA，也可以立即 `abort()`。窗口作废（`RXWindowInvalid`）时 DMA 照常结束（P4 在 DMA 收下 TLAST 后才报告 DONE），不需要处理。

| 异常              | 时机                                    |
| ----------------- | --------------------------------------- |
| `RXDMAError`      | DMA 字节数 ≠ `RX_BEATS × 32`；没有 `prepare` 就 `wait` |

`PynqS2MMDMA(dma, *, length_reader=None)` 把 PYNQ 的 `DMA` 对象适配成 `RXDMA` 需要的 `prepare`/`wait`/`abort`/`received_bytes`。`prepare` 只按窗口字节数启动传输，所以一块大 buffer 可以反复用于较短的窗口。`abort()` 用 `S2MM_DMACR` 的软复位位清掉一次不会结束的传输。`pynq_rx_dma(overlay, name="rx_dma")` 返回板上的适配器：overlay 加载前没导入 `pynq.lib.dma` 时，按 HWH 描述自己构造驱动。

### 11.9 `rf_clock.pl.waveform`

#### `WaveformGenerator(amplitude=512, chip_count=4096, lfsr_seed=0xACE1, periodic=False, beta=0.25, span_chips=10, samples_per_symbol=8)`

在 ARM 上生成 BPSK PRN 波形（Q = 0）。RRC 系数由 `beta`、`span_chips`、`samples_per_symbol` 现算（`rrc_config()`，板上约 2 ms），不存数据文件。

| 成员                                                                               | 说明                                                         |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `generate_beats(chips=None, *, count=None, seed=None, layout=None)`              | 按`TX_LAYOUT` 打包好的 TX beat 列表，可直接给 `TXBuffer` |
| `generate(...)`                                                                  | int16 实数样本                                               |
| `generate_iq(...)`                                                               | `(N, 2)` int16，I/Q 两列                                   |
| `chips(count=None, seed=None)`                                                   | PRN 码片                                                     |
| `tap_count`、`tail_samples`、`expected_sample_count`、`config_id`             | 配置派生量                                                   |

默认配置：β = 0.25，跨 10 个码片，8 个样本/码片（81 抽头），4096 个码片，32841 个样本，4106 个 beat（约 41 µs）。`config_id` 是 RRC 参数的哈希（默认 2134958710），随每次测量记录，收发两端靠它确认用的是同一个滤波器；`tests/test_waveform.py` 用一份黄金系数防止实现漂移。`rrc_config(beta, span_chips, samples_per_symbol)` 返回 `(配置字典, 系数列表)`。

板上 `generate_beats()` 4096 码片约 50 ms，65535 码片约 0.7 s，主要花在计算样本上；打包对整个数组一次完成。

`periodic=True` 生成的是码序列无限重复时的一个周期：RRC 拖尾绕回到开头（循环卷积，`simulate_periodic_convolution`），长度正好 `chip_count × 8` 个样本 = `chip_count` 个 beat，没有拖尾。用 `arm_tx(..., repeat=n)` 重复播放时得到一段连续信号；65535 码片的 m 序列一个周期 65535 beat，放得下。

```python
gen = WaveformGenerator(chip_count=65535, periodic=True)
beats = gen.generate_beats()                  # 65535 beat
pl.tx_buffer.write_beats(beats)
pl.ctrl.arm_tx(target, len(beats), repeat=10)  # 10 遍首尾相接，6.55 ms
```

### 11.10 `rf_clock.pl.beats`

| 名称                                           | 说明                                                                                                                                                                                                       |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TX_LAYOUT`                                  | DAC 格式：I/Q 逐字交错（§4.5.8）                                                                                                                                                                          |
| `RX_LAYOUT`                                  | ADC 格式：平面`{Q[255:128], I[127:0]}`（§4.6.9），也是 `BeatLayout()` 的默认值                                                                                                                        |
| `BeatLayout(...)`                            | 一个 beat 内各样本的位位置；可用`lane_order`、`i_offsets`、`q_offsets` 描述其他格式                                                                                                                  |
| `BeatPacker(layout=None)`                    | `pack_beat`、`pack_samples`、`pack_bytes`；`unpack_beat`、`unpack_beats`、`unpack_bytes`、`unpack_numpy`、`unpack_complex`。不给 layout 时是 `RX_LAYOUT`，打包 TX 要显式传 `TX_LAYOUT`。`pack_samples` 收到整数 NumPy 数组（`(N,)` 只有 I 或 `(N, 2)` I/Q）时用数组运算打包，结果与传列表相同 |
| `unpack_complex(data, *, sample_count=None)` | 向量化解码 DMA buffer，返回 complex64`I + jQ`；大窗口用它                                                                                                                                                |
| `pack_tx_beats(samples, layout=None)`        | 样本 → beat 列表，默认`TX_LAYOUT`                                                                                                                                                                       |
| `unpack_rx_beats(...)`                       | beat → 样本，默认`RX_LAYOUT`                                                                                                                                                                            |

### 11.11 `rf_clock.correlator`

非相干时延估计（计划 §7 `correlator`），纯 NumPy，不依赖板子。

| 函数                                     | 说明                                                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------------ |
| `correlate(rx, ref)`                   | 复互相关`c[k] = Σ rx[k+n]·conj(ref[n])`，k = 0 … len(rx) − len(ref)，用补零 FFT 计算 |
| `find_peak(corr, *, guard=16)`         | 取幅度最大处，三点抛物线插值得到分数位置；峰两侧`guard` 个样本算主瓣，之外找最大旁瓣     |
| `estimate_delay(rx, ref, *, guard=16)` | 上面两步合起来，返回`Peak`                                                               |

`Peak` 字段：`index`（分数样本，参考波形在 rx 中的起点）、`integer_index`、`magnitude`、`peak_to_sidelobe`（及 `peak_to_sidelobe_db`）、`phase`。对默认 RRC 波形，抛物线插值的偏差在整个分数范围内不超过 0.002 个样本（无噪声仿真）。

### 11.12 `rf_clock.twtt`（双向时间比对，1.3.0.7 起）

计划第 4 节双向时间比对中一块板的那一侧，用于 M4（两块板共用一台 FS725，经衰减器交叉连接：A 的 DAC_A → B 的 ADC_B，B 的 DAC_A → A 的 ADC_B）。

| 模块 | 内容 |
| ---- | ---- |
| `timing` | 时隙（`SLOTS`：AB 在 PPS 沿后 100 ms，BA 在 600 ms；RX 窗口比时隙早 `PRE_TICKS` = 1.25 µs 打开）、记录格式、`two_way()`（m_AB、m_BA、θ = (m_AB − m_BA)/2、单程延时 (m_AB + m_BA)/2）和 `Pairer`（按系统时间把 A、B 对同一个 PPS 沿的记录配对） |
| `node` | PL 循环和启动命令：`PL.open()` 之后，每秒在 PPS 沿后立即按本节点的时隙收发（A 在 AB 发、在 BA 收，B 相反；AB 两个时隙都收发，即一块板经自己的回环线），对每个窗口做相关，每秒一条记录 |
| `link` | 第二个进程，由 `node` 启动，不碰硬件：与另一块板的 TCP 连接（A 在 50725 端口监听，B 连接 `--peer` 并在断开后重连）、配对、日志 |

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -m rf_clock.twtt.node A --seconds 600                       # 第一块板
$run -m rf_clock.twtt.node B --peer 192.168.2.99 --seconds 600   # 第二块板
$run -m rf_clock.twtt.node AB --seconds 60    # 一块板经自己的回环线
```

日志（`twtt_<节点>.jsonl`，或 `--log`）里有本板的 `info`（序列号、VCO 档位、MTS T1 码）、对方的 `hello`、两块板的记录（`local`、`peer`）、每个配对成功的秒一条 `result`（`m_ab_ns`、`m_ba_ns`、`theta_ns`、`delay_ns`；某个时隙失败或相关峰 PSL 低于 10 dB 时为 null）以及 `event`（连接、错误）。θ 是 B 的时钟读数减 A 的，不含非互易项 Δ。两块板的记录按 PPS 沿的系统时间配对，所以 A、B 的时钟都要经 NTP 同步（§9.1）；否则命令拒绝启动（`--no-ntp-check` 可跳过）。link 进程一旦结束，PL 循环也随之停止。`experiments/m4_twtt.py` 从 RF-clock-PC 在板上启动节点并汇总日志。

## 12. 异常与返回码

| 异常                                     | 基类              | 来源                                                                                       |
| ---------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------ |
| `PLClientError`                        | `RuntimeError`  | `client` 里所有协议错误的基类                                                            |
| `ClockUnavailable`                     | `PLClientError` | 时钟状态 GPIO bit0 = 0，拒绝访问 P2                                                        |
| `IdentityError`                        | `PLClientError` | `ID` 不对、major 不符，或 minor 太低                                                     |
| `EpochChanged`                         | `PLClientError` | `TIME_EPOCH` 变了                                                                        |
| `CommandRejected`                      | `PLClientError` | doorbell 被拒绝；`.operation`、`.result`（§5.2 的 0x1x 码）                           |
| `WaitTimeout`                          | `PLClientError` | 等 DONE 超时                                                                               |
| `TXBufferError`                        | `PLClientError` | 写 buffer 越界或时机不对                                                                   |
| `RXWindowInvalid`                      | `PLClientError` | `wait_rx`：窗口作废（`.flags`、`.result`），丢弃这次测量                                    |
| `TargetMissed`                         | `PLClientError` | `wait_tx`/`wait_rx`：`*_ACTUAL` ≠ target（`.target`、`.actual`）                            |
| `RXDMAError`                           | `RuntimeError`  | DMA 字节数 ≠ `RX_BEATS × 32`                                                                |
| `BringupError`                         | `RuntimeError`  | 上电流程某一步超时，或有 LMX2594 没锁定                                                    |
| `BoardConfigError`                     | `BringupError`  | 板级配置格式不对、指定了却不存在，或属于另一块板（§11.1）                                  |
| `RFDCError`                            | `RuntimeError`  | tile 未启动或 MTS 状态非 OK                                                                |
| `VersionMismatch`                      | `RuntimeError`  | overlay 或`BUILD_VERSION` 与安装的 rf-clock A.B.C 不同，或文件与 manifest 不符（§14.1） |

| 返回码（§5.2）  | Python 表现                                                                                  |
| ---------------- | -------------------------------------------------------------------------------------------- |
| ARMED、RUNNING   | `arm_*` 正常返回                                                                           |
| DONE、RX_INVALID | `arm_*` 正常返回（`*_SEQ` 已增加时）；`wait_rx` 对 RX_INVALID 抛 `RXWindowInvalid`  |
| REJ_*            | `CommandRejected`                                                                          |
| FAULT_*          | `arm_tx` 或 `wait_tx` 抛 `PLClientError`                                               |

## 13. 板上检查（`rf_clock.checks`）

随包安装，在板上以 root 运行：`sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.<模块> ...`，`--help` 查看全部选项。记录追加到当前目录下的 `m2_runs.jsonl`、`m3_runs.jsonl`。卡死时用 `tools/jtag_reset_board.tcl` 远程复位（§9.1）。

| 模块           | 里程碑             | 用法                                                                                                                                                                         |
| -------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `m1_check`     | M1：tick 与 PPS    | `[秒数] [--no-clocks]`；每秒一行，结束时 PASS/FAIL                                                                                                                         |
| `m2_tx_timing` | M2：MTS 与定时 TX  | `[突发数] [--offset-ticks N] [--amplitude A] [--waveform prn\|rect\|square] [--scope-nco MHZ]`；每个 PPS 发一个突发；每次运行的 MTS 延时追加到 `m2_runs.jsonl` |
| `m3_loopback`  | M3：环回、RX、相关 | `[次数]` 测环回延时；`--lane-check` 定位单 lane 冲激、核对 beat 布局；`--stress [--stress-beats N]` 在 DDR 压力下测长窗口；结果追加到 `m3_runs.jsonl`             |
| `mts_dtc`      | 板级配置（§11.1）  | `record [--write] [--runs N]` 不用已记录的码上电 N 次（默认 6），按转换器、时钟和 tile 取被选中次数最多的窗口；`--write` 把它的码存进板级配置。`check [--runs N]` 用记录的码上电，报告每个选到的码离记录多远。在 `vco_bands scan --write` 之后运行（它会丢掉这些码，因为码与档位有关） |
| `vco_bands`    | 板级配置（§11.1）  | `scan [--write] [--label S/N] [--calibrations N]` 写 N 次时钟（默认 20），建议每片 LMX2594 校准最常选的档位，使芯片保持在校准自己的工作点上；再从它向两边逐档强制，直到锁不住，以显示两边的余量；`--write` 把建议的档位、各档次数、能锁定的档位和本板序列号存进板级配置。`delays [--write] [--span N] [--runs N] [--captures N]`，需接回环线 DAC_A → ADC_B：用载波相位测配置档位两边各 N 档（默认 2）以内每个档位的回环延时，每次上电在单独的进程里；`--write` 存入档位表；记录追加到 `vco_bands_runs.jsonl` |

M2、M3 还记录本板序列号和两片 LMX2594 的 VCO 档位。M1–M3 三个模块共用 `--bitstream PATH`（默认用随包的 overlay）、`--no-version-check`（1.2 之前的 bitstream）、`--no-clocks`（开机后已写过时钟时跳过）、`--pps-trig`、`--pps-invert`。

带宽不够看射频的示波器用 `m2_tx_timing --scope-nco`：DAC 临时改为 IMR 低通、1 区，`--scope-nco 100` 把输出搬到 100 MHz，`--scope-nco 0` 旁路混频器、直接输出 I 样本；结束时恢复。量"PPS → 突发起点" 用 `--scope-nco 0 --waveform square --offset-ticks 0`：方波的第一个边沿与 NCO 相位无关（2026-10-14 测得 180.00 ns，重启 5 次不变；DAC_A 输出相对 I 反相，第一个边沿是下降沿）。

---

# 第三部分 版本与待定事项

## 14. 版本规则

- `VERSION` 的 minor 加 1：只增加寄存器或位，原有偏移和含义不变，旧软件仍可用。
- `VERSION` 的 major 加 1：任何改变原有偏移、位域、编码或时间语义的修改。
- 软件启动时检查 major，不匹配就拒绝运行；用到 1.x 新增功能的调用检查 minor。

| 版本 | 变化                                               |
| ---- | -------------------------------------------------- |
| 1.0  | 初版                                               |
| 1.1  | 新增`PPS_INPUTS`（0x3C）、`PPS_CONFIG`（0x90） |
| 1.2  | 新增`BUILD_VERSION`（0x94）                      |
| 1.3  | 新增`TX_REPEAT`（0x5C）                          |

### 14.1 软件包与 overlay

overlay 随 Python 包一起发布，放在 `src/rf_clock/overlay/`，由 `tools/package_overlay.py` 从构建目录复制过来：

| 文件                               | 内容                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------ |
| `rf_clock.bit`、`rf_clock.hwh` | PYNQ 加载用                                                                                |
| `rf_clock_hw.xsa`                | Vitis/PetaLinux 用的硬件平台（含 bitstream）。不能叫 `rf_clock.xsa`：PYNQ 3.1 看到同名 `.xsa` 会改走 XSA 分支，加载 `.bit` 失败 |
| `manifest.json`                  | `build_version`、构建目录、`build_status`（status.txt，含 WNS/WHS）、三个文件的 sha256 |

三个二进制文件用 Git LFS 保存（`.gitattributes`）。克隆后要先 `git lfs install`、`git lfs pull`，否则拿到的是指针文件，`PL.open()` 的 sha256 校验会拒绝它。

rf-clock 的版本号有四段 `A.B.C.D`（PEP 440 允许任意段数），手改 `pyproject.toml`：

| 段  | 含义                           | 什么时候加 1                              |
| --- | ------------------------------ | ----------------------------------------- |
| A.B | 寄存器协议，等于`VERSION`    | 按 §14 改`VERSION` 时同步改，C、D 清零 |
| C   | 这一版协议下的第几个 bitstream | 重新出 bitstream（寄存器不变），D 清零    |
| D   | 只改 Python                    | 不重新构建                                |

先改版本号再构建，构建脚本把它写进 `BUILD_VERSION`。A.B 与 `P_VERSION` 不一致时构建脚本报错，`tests/test_abi_sync.py` 也检查这一点。

`PL.open()` 检查两次，不一致时抛 `VersionMismatch`（`check_version=False` 关闭）：

1. 加载前：bitstream 同目录有 `manifest.json` 时，它的 `build_version` 与 `importlib.metadata.version("rf-clock")` 的 A.B.C 必须相同，`.bit`、`.hwh` 的 sha256 必须与 manifest 一致。直接从构建目录加载（没有 manifest）时跳过这一步。
2. 加载后：`BUILD_VERSION` 与安装的 rf-clock 的 A.B.C 相同。

只有 D 可以不同：只改 Python 的发布继续用原来的 bitstream。

可编辑安装记录的是安装那一刻的版本；开发机上改了 `pyproject.toml` 后 `uv run` 会自动重装。板上的 rf-clock 是用 wheel 装进 PYNQ 环境的（§9.1），所以新版本就要装新的 wheel。

发布一个新 bitstream：

```text
1. pyproject.toml：version 的 C 加 1、D 清零（改了寄存器则按上表改 A.B）
2. RF-clock-PC：vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl \
       -tclargs ~/build/<新目录> ~/ref/RFSoC4x2-BSP/board_files impl
3. python3 tools/package_overlay.py ~/build/<新目录>
   （要求 impl 完成、时序满足、status.txt 的 build_version 与 pyproject.toml 相同）
4. 提交，打 tag 并推送（见下）；在板上把新 wheel 装进 PYNQ 环境（§9.1）
```

发布：推送 `vA.B.C.D` 形式的 tag 时，GitHub Action（`.github/workflows/release.yml`）先用 `tools/release_notes.py` 校验（tag 等于 `v` + `pyproject.toml` 的版本；overlay 不是 LFS 指针、与 manifest 一致、A.B.C 相同），再 `uv build`，并建立 GitHub Release，附上 wheel、sdist 和 `manifest.json`。发布说明由 tag 的说明文字、overlay 构建记录、上一个 tag 以来的提交和安装步骤组成。

```text
git tag -a v1.3.0.1 -m "说明这次改了什么"
git push origin v1.3.0.1
```

组里的 Gitea 服务器用自己的 runner 执行 `.gitea/workflows/release.yml` 中相同的步骤（Gitea 读取 `.gitea/workflows`，忽略 `.github/workflows`），并通过 Gitea API 建立带同样文件的 Gitea Release，同时把 wheel 和 sdist 上传到仓库所有者的 Gitea PyPI 软件包仓库。上传需要一个名为 `PACKAGE_TOKEN` 的 Actions 密钥（有软件包写入权限的访问令牌；任务自带的令牌不能写入软件包）。服务器地址是 `http://kinabalu2.stdusr.yale.internal/gitea/`，而 `actions/checkout` 不能从子路径下的服务器 clone（它只保留协议、主机和端口），所以 1.3.0.6 起 Gitea 的 workflow 直接用 git 检出；1.3.0.3–1.3.0.5 在 Gitea 上没有 release，只有 GitHub 上的。用一个有读取软件包权限的 Gitea 访问令牌即可安装：

```text
uv pip install --no-deps --index-url http://<用户名>:<令牌>@<gitea>/api/packages/<所有者>/pypi/simple rf-clock==1.3.0.2
```

tag 推到哪个远端，哪个远端就发布 Release。

wheel 里有库、随包的 overlay、时钟寄存器文件、`rf_clock.examples` 和 `rf_clock.checks`；在板上装好（§9.1）即可使用，不需要仓库。

## 15. 尚未确定

统一记录在 `plan/checklist.md`，确认后回填本文。主要有：

- beat 内一对 16 位里哪个是 I、哪个是 Q（lane 顺序已确认，§4.5.8、§4.6.9），以及接收基带是否是发射基带的共轭；BPSK 实信号不受影响。
- 接上环回线（DAC → 衰减器 → ADC）后的环回延时标准差和重启一致性（M3 验收）；`PynqS2MMDMA.abort()` 在板上的行为。
- MTS 延时的标定目标值，以及 PPS 沿相对 tick 的亚 tick 相位。

---

# 附录 A 底层访问：不用 `rf_clock.pl` 时怎么做

本附录说明第一部分的三种访问方式在 Linux 上是怎么实现的，以及只用 PYNQ 原生接口或 C++ 时的写法。示例在 `rf_clock/examples/native/`，2026-10-14 在板上运行通过（以 root 运行，overlay 已加载、fabric 时钟在运行）。

## A.1 原理

每个 IP 在 PS 的物理地址空间里占一段（§1.2）。CPU 对这段地址的读写，经 PS 的 `M_AXI_HPM0_FPD` 端口和 SmartConnect 变成发给该 IP 的 AXI 读写事务。Linux 用户进程不能直接用物理地址，要先把 `/dev/mem` 的这一段 `mmap` 进进程（需要 root）；以 `O_SYNC` 打开时得到的是不经 CPU cache 的设备内存映射，此后每一次 32 位读或写就是总线上一次 AXI 事务。PYNQ 的 `MMIO` 就是这样实现的（`/dev/mem` + `mmap` + numpy uint32 视图）。

| 通路                                                   | 对面是什么                                      | 怎么访问                                                                           |
| ------------------------------------------------------ | ----------------------------------------------- | ---------------------------------------------------------------------------------- |
| 32 位 MMIO 读写（P2、时钟状态 GPIO、DMA 寄存器、RFDC） | 寄存器，读写有副作用（doorbell、读 LO 锁存 HI） | 一次一个对齐的 32 位字，按协议顺序；不能缓存或合并                                 |
| 按内存写入（TX buffer）                                | AXI BRAM 控制器后面的一块 RAM                   | 同样的映射，但没有副作用，可以整块写                                               |
| DMA buffer + DMA 寄存器（RX）                          | PL 的 S2MM 作为总线主机，经 HP0 端口直接写 DDR  | 分配物理连续的 DDR buffer → 用 MMIO 写 DMA 寄存器启动 → 处理 cache → 读普通内存 |

PYNQ 的 `allocate()` 经 XRT/zocl 从 CMA 分配物理连续内存，返回带 `.physical_address` 的 numpy 数组；默认不经 CPU cache，`cacheable=True` 时读得更快，但每次读之前必须 `invalidate()`。

板上实测（2026-10-14）：

| 操作                                      | 耗时                                                 |
| ----------------------------------------- | ---------------------------------------------------- |
| C++ 读一个 P2 寄存器                      | 约 0.54 µs（一次 AXI 往返，含 SmartConnect 跨时钟） |
| PYNQ`MMIO.read()`                       | 约 7.7 µs（多出来的是 Python 开销）                 |
| 写 128 KB TX buffer：逐字`MMIO.write()` | 0.31 s                                               |
| 写 128 KB TX buffer：numpy 切片整块赋值   | 0.5 ms，回读一致                                     |

## A.2 PYNQ 原生接口（`rf_clock.examples.native.raw_pynq`）

```python
import numpy as np
from pynq import MMIO, allocate

gpio = MMIO(0xA002_0000, 0x1000)          # 时钟状态 GPIO（pl_clk0 域，随时可读）
p2   = MMIO(0xA000_0000, 0x1000)          # P2 寄存器（fabric 域）
txb  = MMIO(0xA040_0000, 4 << 20)         # TX buffer（4 MiB 窗口）
dma  = MMIO(0xA001_0000, 0x10000)         # AXI DMA 寄存器

assert gpio.read(0) & 1                   # 先确认 fabric 时钟在跑，否则访问 P2 会卡死总线

# 寄存器：一次一个 32 位字，按协议顺序
lo = p2.read(0x20); hi = p2.read(0x24)    # TICK：先读 LO，同时锁存 HI
tick = (hi << 32) | lo
p2.write(0x40, target & 0xFFFF_FFFF); p2.write(0x44, target >> 32)
p2.write(0x48, nbeats); p2.write(0x08, 0x1)   # 先写 shadow，最后按 doorbell
assert p2.read(0x4C) & 0xFF == 0x01           # ARMED

# TX buffer：整块写（beat_bytes 是按 TX_LAYOUT 打包的字节串）
words = np.frombuffer(beat_bytes, dtype=np.uint32)
txb.array[:words.size] = words

# RX DMA：分配连续内存 → 写 DMA 寄存器 → 再按 RX doorbell
buf = allocate(shape=(nbytes,), dtype=np.uint8)
dma.write(0x30, 0x1)                                # S2MM_DMACR.RS
dma.write(0x48, buf.physical_address & 0xFFFF_FFFF) # S2MM_DA
dma.write(0x4C, buf.physical_address >> 32)         # S2MM_DA_MSB
dma.write(0x58, nbytes)                             # S2MM_LENGTH：写入即开始
# ……写 RX_TARGET、RX_LENGTH，再 W32 CONTROL 0x4……
while not dma.read(0x34) & 0x2: pass                # S2MM_DMASR.Idle
received = dma.read(0x58)                           # 实际字节数
buf.invalidate()                                    # 可缓存 buffer 读之前必须做
data = np.array(buf[:received])
```

地址最好从 HWH 读（`overlay.ip_dict["p_top_0"]["phys_addr"]` 等），这里写死只为说明。

## A.3 C++（`rf_clock/examples/native/mmio_demo.cpp`）

```cpp
int fd = open("/dev/mem", O_RDWR | O_SYNC);
auto *gpio = (volatile uint32_t *)mmap(nullptr, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0xA0020000);
auto *p2   = (volatile uint32_t *)mmap(nullptr, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0xA0000000);
if (!(gpio[0] & 1)) return 1;                   // fabric 时钟没跑，不碰 P2
uint32_t lo = p2[0x20 / 4], hi = p2[0x24 / 4];  // 先 LO 后 HI
p2[0x40 / 4] = target; p2[0x44 / 4] = target >> 32; p2[0x48 / 4] = nbeats;
p2[0x08 / 4] = 0x1;                              // doorbell
```

编译：在该文件所在目录 `g++ -O2 -o mmio_demo mmio_demo.cpp`，以 root 运行。`volatile` 保证每次访问都真的发到总线上、编译器不调换顺序。

DMA buffer 在 C++ 里需要物理连续内存。可以用 XRT 的 C++ 接口（PYNQ 底层用的就是它：`xrt::bo`、`bo.address()` 取物理地址、`bo.map()`、`bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE)`；板上有 XRT 2.17，**这条路尚未在板上试过**），或者由 Python 分配、流程控制留在 Python，只把大数据量的计算写成 C++ 扩展。

## A.4 注意事项

1. 都需要 root（`/dev/mem`）。
2. 访问 fabric 域（P2、TX buffer）之前先读时钟状态 GPIO，C++ 也一样，否则总线卡死只能复位整板。
3. P2 只用对齐的 32 位访问：8/16 位写返回 SLVERR；64 位写可能被拆成两次，与 doorbell 的顺序语义冲突。
4. TX buffer 整块写要 4 字节对齐；不要对映射区用 `memset` 或非对齐访问（可能用到设备内存不允许的指令，进程收到 SIGBUS）。numpy 切片赋值实测可用。
5. C++ 里普通内存（DMA buffer）与设备寄存器之间的先后，`volatile` 管不到：在"写完数据"和 "按 doorbell"之间、"确认 DMA 完成"和"读数据"之间加屏障（`std::atomic_thread_fence` 或 `__sync_synchronize()`）。
