# 调试总览：沿 10 MHz 分发路径发现的问题和所做的改动

本文为中文译本，以英文版 `docs/debug_overview.md` 为准。

从第一次上板（2026-10-13）到 rf-clock 1.3.0.10（2026-10-25）在实验台上发现的全部问题，按 FS725 的 10 MHz 被分发的顺序排列：先是实验台，然后是 LMK04828，再是它的三路分支（PL_CLK 进 FPGA，480 MHz 参考经 LMX2594 进 RFDC，SYSREF 两边都进），最后是两块板之间的射频链路和建立在其上的测量。PPS 在 FPGA 捕获它的地方汇入这条链。每节开头是该模块的框图，图上 `[K2]` 这样的标号标出问题所在的位置；图下逐条写明看到了什么、原因、改动和验证方法。本文是索引：从原因到测量量的完整因果链（每一环注明实测还是推断）在所链接的报告里（`results/`，不进 Git），以及 `plan/checklist_zh.md` 里；后者还记录了每个已完成项的依据。框图中的标注保留英文。

## 0. 全链一览

```text
bench
  FS725 10 MHz ──> EPG1 REF IN;  EPG1 REF OUT (10 MHz square) ──> splitter ──> each REF SMA       [B1][B2]
  FS725 1PPS ───> EPG1 TRIG IN ──> retimed, delayed ──> EPG1 outputs 1, 2 ──> each J7             [B3][B4]

each board
  REF SMA ──> LMK04828: PLL1 ──> VCXO 160 MHz ──> PLL2 ──> VCO 2400 MHz                           [K1][K3]
              └─ dividers, all counting that VCO                                                  [K2]
                 ├─ ÷24  ──> PL_CLK 100 MHz ──> FPGA: MMCM ──> fabric clock                       [F1][F2][F3]
                 │                    fabric clock ──> raw_tick; PPS capture <── J7               [F4][F5]
                 │                    fabric clock ──> TX buffer, RX capture, AXIS                [F7]-[F9]
                 ├─ ÷480 ──> SYSREF 5 MHz ──> RFDC tiles: SYSREF capture, MTS                     [R1]-[R4]
                 │                        └─> FPGA PL_SYSREF                                      [F6]
                 ├─ ÷5   ──> 480 MHz ──> LMX2594 ──> 480 MHz ──> ADC tile PLLs ──> 4.8 GS/s       [X1][X2][X3]
                 └─ ÷5   ──> 480 MHz ──> LMX2594 ──> 480 MHz ──> DAC tile PLLs ──> 9.6 GS/s
                                                             RFDC data path, NCOs                 [R5]-[R7]

RF path
  DAC_A (5.8 GHz and 3.8 GHz) ──> band-pass ──> attenuator ──> ADC_B of the other board           [A1]-[A3]
```

| 标号 | 模块 | 问题 | 大小 | 改动 | 起始 | 状态 |
|---|---|---|---|---|---|---|
| B1 | 实验台 | 电阻功分后到每块板只剩 0.7 Vpp，LMK 进入 holdover | tick −0.1 ppm | 10 MHz 改走 EPG1 的 REF OUT | 2026-10-16 | 已修复 |
| B2 | 实验台 | EPG1 没锁定，它的重定时让 PPS 跨 tick 游走 | ±1 tick | EPG1 REF IN 接 FS725 的 10 MHz | 2026-10-16 | 已修复 |
| B3 | 实验台 | EPG1 触发阈值正好落在它自己的时钟沿上 | 2.5 ns，一半的脉冲 | 阈值 1.0 → 1.3 V | 2026-10-19 | 已修复 |
| B4 | 实验台 | 哪路输出接哪块板 | 接线 | 逐路延时核对；输出 2 为 F5 加 6.6 ns | 2026-10-18 | 已修复；改线后要复查 |
| K1 | LMK04828 | PLL1 不在零延时模式 | tick 相位不受 10 MHz 约束 | 嵌套零延时 | 1.2.1.1 | 已修复 |
| K2 | LMK04828 | PLL2 起振后分频器没有同步 | 4–5 个 VCO 周期（1.67–2.08 ns），0.4 % 的上电 | 按数据手册做 SYNC | 1.3.0.9 | 已修复 |
| K3 | LMK04828 | 在我们的 overlay 之前写时钟，系统卡死 | 卡死 | 先 overlay，再时钟 | 2026-10-13 | 已修复 |
| F1 | FPGA | PL_CLK/PL_SYSREF 按单端接收 | MMCM 锁不上 | LVDS 输入（run4） | 2026-10-13 | 已修复 |
| F2 | FPGA | 改时钟后 MMCM 锁不上 | 没有 fabric 时钟 | 拉一下 `pl_resetn0` | 2026-10-13 | 已修复 |
| F3 | FPGA | 开机约一分钟后 PYNQ 的 base overlay 覆盖我们的设计 | 设计消失 | 等系统启动完成 | 2026-10-14 | 已修复 |
| F4 | FPGA | 收不到 PPS 沿 | 没有 PPS | 线接到 J7；上拉、第二路输入、`PPS_CONFIG`（run3） | 2026-10-13 | 已修复 |
| F5 | FPGA | 887329260072A 的 PPS 沿落在 tick 采样沿上 | 1 tick（10 ns） | EPG1 输出 2 延时 6.6 ns | 2026-10-18 | 已修复；漂移需关注 |
| F6 | FPGA | PL_SYSREF 的输入时序约束抄自 500 MHz 的设计 | — | 无 | — | 没核对 |
| F7 | FPGA | TX 按平面方式打包，DAC 按交织读取 | 波形被打乱 | `TX_LAYOUT` 改为交织 | 2026-10-14 | 已修复 |
| F8 | FPGA | 69 632 拍的 TX 缓冲没有被推断成 URAM | 548 个 BRAM36 | 17 个 4096 拍的 bank（run8） | 2026-10-14 | 已修复 |
| F9 | FPGA | arm 太晚到 PL 时 M2 中止 | M2 中止 | 记为漏发 | 1.3.0.3 | 已修复 |
| X1 | LMX2594 | 通道分频器从任意 VCO 沿开始 | 26 ps 格点（DAC 上 52 ps） | 相位同步 | 1.3.0.4 | 已修复 |
| X2 | LMX2594 | 每次写时钟都重新校准 VCO 档位 | 每档 8–10 ps | 按板级配置强制档位 | 1.3.0.6 | 已修复；其他温度下能否锁定待查 |
| X3 | LMX2594 | 设备树把两片芯片的名字标反了 | 只影响标签 | 实验数据重新标注 | 2026-10-20 | 已记录 |
| R1 | RFDC | MTS latency 随 bitstream 变化 | 2 个采样 | 固定目标延时 | 2026-10-14 | 已修复 |
| R2 | RFDC | 每次上电重新选 SYSREF 捕获窗口 | 1 个 DAC 采样（104 ps） | 每块板记录窗口 | 1.3.0.8 | 已修复 |
| R3 | RFDC | K2 的状态只表现为 MTS offset 不同 | — | 记录并检查 offset | 1.3.0.9 | 已修复 |
| R4 | RFDC | K2 修复后没有余量 | MTS 会失败 | DAC 目标 1896 → 1944 | 1.3.0.10 | 已修复 |
| R5 | RFDC | NCO 在任意时刻起相 | 载波 120° 台阶 | 在 SYSREF 沿重新起相 | 1.3.0.5 | 已修复 |
| R6 | RFDC | MTS 要求每个 tile 打开通道 0；分发时钟限制 DAC 速率 | 构建失败或 7 GS/s | tile 0、2 各用自己的 PLL | 2026-10-13 | 设计约束 |
| R7 | RFDC | PYNQ 的 `xrfdc` 在 Python 层没有 MTS | — | 用 cffi 调 `libxrfdc.so` | 2026-10-13 | 设计约束 |
| A1 | 射频链路 | DAC 的 3.8 GHz 分量在 ADC 里折叠到 5.8 GHz 上 | 每次运行的 theta：有滤波器 1.5 ps，没有 120–150 ps | 每条交叉链路一个带通滤波器 | 设计 | 必须接滤波器 |
| A2 | 射频链路 | 包络计时相对载波相位移动 | 断电后 theta +37–59 ps | 尚无 | — | 未解决 |
| A3 | 射频链路 | ADC 输入接头；DAC_A 极性 | — | ADC_B（`vin2_01`）；DAC_A 相对 I 反相 | 2026-10-14/19 | 已记录 |
| M1 | 测量 | 示波器读数依赖载波相位 | 最多 10 ns | 基带方波，后改包络计时 | 2026-10-14/16 | 已修复 |
| M2 | 测量 | 示波器 math 量程和远程接口的坑 | 延时读错、USB 卡死 | 脚本里已处理 | 2026-10-16/17 | 已修复 |
| M3 | 测量 | M3 回环的发射幅度太低 | 每次运行 4–6 ps | 幅度 16000 | 1.3.0.5 | 已修复 |
| E1 | 环境 | 每块板变热时时钟变慢 | 1–1.5 ps/K | 记录芯片温度 | 1.3.0.9 | 未解决 |
| E2 | 环境 | m_AB 有一小时高了 0.6 ns，只出现一次 | 0.6 ns | 无 | — | 原因不明 |

## 1. 实验台：FS725 → EPG1 → 板子

FS725 是唯一的参考源。它的 10 MHz 经 EPG1 到达板子：EPG1 锁定到它，再输出方波；它的 1PPS 也经 EPG1 到达板子：EPG1 对它重定时，并可给每路输出加延时（`plan/checklist_zh.md` 第 6 节，`experiments/epg1.py`）。

```text
  FS725
   ├─ 10 MHz out 1 (sine) ──> EPG1 REF IN ──> EPG1 clock, locked (2.5 ns steps)                   [B2]
   ├─ 10 MHz out 2 (sine) ··· not used: through the splitter 0.7 Vpp at each board                [B1]
   └─ 1PPS (2.5 V, 10 µs) ──> EPG1 TRIG IN ──> threshold ──> retimed (EPG1 clock)                 [B3]
                                                             └─> delay per output ──> out 1, 2, 4 [B4]

  EPG1 REF OUT (10 MHz square) ──> resistive splitter ┬─> REF SMA of 887329260072A
                                                      └─> REF SMA of 887329260101
  EPG1 output 1 ──> J7 of 887329260101
  EPG1 output 2 (+6.6 ns) ──> J7 of 887329260072A
  EPG1 output 4 ──> scope C1 (trigger)
```

**[B1] 功分后的 10 MHz 太弱。**
- 现象：FS725 的正弦经电阻功分后，PLL1 反复失锁，LMK04828 进入 holdover；tick 慢约 0.1 ppm（一块板周期恒为 99 999 990 tick，另一块 0/−10 交替）。
- 原因：功分器 6 dB 的损耗让每块板的 CLKin0 只剩约 0.7 Vpp 的慢正弦。
- 改动：改由 EPG1 锁定后的 10 MHz 方波（REF OUT）接功分器。
- 验证：两块板同时跑 M1 120 s，每个周期都是 100 000 000，jitter 0。

**[B2] PPS 跨 tick 游走。**
- 现象：M1 上出现 99 999 999 / 100 000 000 / 100 000 001 tick 的周期和 jitter 计数。
- 原因：EPG1 用自己的时钟（2.5 ns 一步）重采样 TRIG IN；不锁定时，这个时钟相对板子的 tick 漂移。
- 改动：EPG1 REF IN 接 FS725 的 10 MHz（`REFCLK:IN:STATUS` = 1）。

**[B3] EPG1 触发阈值**（2026-10-19）。
- 现象：EPG1 一半的输出脉冲早 2.5 ns；示波器触发抖动 1.2 ns rms。
- 原因：`TRIGIN:THRESH` 为 1.0 V 时，FS725 1PPS 的慢上升沿正好在 EPG1 的一个时钟沿处越过阈值。
- 改动：阈值 1.3 V（离两侧时钟沿各约 1.25 ns）：19 ps rms。

**[B4] 哪路输出接哪块板。** 方法是把每路输出延时 50 ns，看哪块板的 PPS_TICK 跟着动（2026-10-18；之前的记录把它写反了）。输出 2 上加的是 F5 的 6.6 ns 延时。板子装壳后两根 PPS 线接反过一次（2026-10-21，已接回），第一次上板时 PPS 线没接在 J7 上（F4）。每次改线之后，先跑 M1 和一次短的 M4，再相信测量结果。

## 2. LMK04828

LMK04828 用 PLL1 和它的 160 MHz VCXO 净化 10 MHz，用 PLL2 倍频到 2400 MHz，再把这一个 VCO 分频成 PL_CLK（÷24）、SYSREF（÷480）和两路 480 MHz 的 LMX 参考（÷5）（`src/rf_clock/clocks/files/README.md`）。每路输出相对其他输出的相位等于一个固定延时加整数个 VCO 周期（416.7 ps），整数部分由该分频器何时开始计数决定。

```text
                    PLL1                                     PLL2
  CLKin0 ──> ÷12 ──> PFD ──> loop ──> VCXO 160 MHz ──> ÷2 ──> PFD ──> loop ──> VCO 2400 MHz ──┐
  10 MHz              ^ 833 kHz                                ^ 80 MHz                       │
  [B1]                │                                        └── ÷30 <──────────────────────┤
                      └── ÷120 <── FB_MUX <── DCLKout8  [K1]                                  │
                                                 ^                                            │
  dividers, each counting the VCO <──────────────┼────────────────────────────────────────────┘
    ÷24  ──> DCLKout8 = PL_CLK 100 MHz ──────────┴──> FPGA (section 3)
    ÷480 ──> SYSREF 5 MHz ──> RFDC tiles (section 5), FPGA PL_SYSREF (section 3)
    ÷5   ──> 480 MHz ──> LMX2594 of the ADC tiles (section 4)
    ÷5   ──> 480 MHz ──> LMX2594 of the DAC tiles (section 4)
  each divider's reset: one shared line, a switch per divider (figure: LMK report, section 2)     [K2]
  SPI: xrfclk writes the register file in address order, after our overlay is loaded              [K2][K3]
```

**[K1] PLL1 不在零延时模式**（rf-clock 1.2.1.1，2026-10-14）。
- 现象：示波器上 PPS 到 burst 的延时在两次写时钟之间最多差约一个 tick。
- 原因：0x13F 的 FB_MUX_EN = 0，PLL1 锁的是 VCXO 而不是 DCLKout8，÷24 分频器可以从 24 个 VCO 沿中任意一个开始：tick 相对 10 MHz 和 PPS 可能落在相距 0.42 ns 的 24 个相位之一。后来发现那种示波器读法依赖载波相位（M1），所以相位确实变过这一点并没有得到证明。
- 改动：嵌套零延时（0x13F = 0x0B，PLL1_N 120）：PLL1 直接拿 DCLKout8 和 10 MHz 比较。`PL.open()` 自己写包内的时钟文件。
- 验证：示波器上 5 次重启都是 180.00 ns（基带方波）。

**[K2] PLL2 起振后分频器没有同步**（rf-clock 1.3.0.9；[报告](../results/lmk-divider-sync-20261025/REPORT_zh.md)）。
- 现象：约 0.4 % 的上电中，一块板的转换器相对它的 PPS tick 晚 4 或 5 个 VCO 周期（1.667 或 2.083 ns），发射和接收一起晚；theta 跳 ±1.67 或 ±2.08 ns；MTS 的 offset 不同，SYSREF 捕获码却不变（R3）。
- 原因：寄存器文件按地址顺序写入，在配置 PLL2 之前就断开了每个分频器的复位开关，之后再没有同步。零延时（K1）只把 DCLKout8 锁在 10 MHz 上；SYSREF 和 LMX 参考相对它移动，MTS 又让转换器跟着 SYSREF 移动。
- 改动：写完文件后按数据手册做 SYNC：把分频器共用的复位线从 SYSREF 切到 SYNC，所有分频器都接上这根线，用软件把线拉高再拉低，再恢复文件里的设置（框图见报告第 2 节）。第一版没有把线切走，线上仍是 SYSREF，SYNC 根本到不了分频器。
- 验证：紧接着各上电 1200 次：不做跳 10 次，做了 0 次（p = 0.001）；两块板都只有一个状态。
- 副作用：新状态下两块板相对 PPS 都早 10 个 VCO 周期（4.16 ns），双向 theta 看不出来；DAC 目标的调整见 R4。

**[K3] 在我们的 overlay 之前写时钟，板子卡死**（2026-10-13）。
- 现象：第一次写时钟后约 4 s 整个系统卡死；另一次没卡死，但 MMCM 没锁上。
- 原因：没有查明；当时 PL 里还是 PYNQ 的 base overlay，它也用 LMK 的时钟。
- 改动：先加载我们的 overlay，再写时钟，再拉一下 `pl_resetn0`（ABI §7.1 第 1–4 步）。卡死的板子用 JTAG 恢复（`tools/jtag_reset_board.tcl`）。

## 3. PL_CLK 进入 FPGA：fabric 时钟、PPS 捕获、数据通路

PL_CLK 和 PL_SYSREF 以 LVDS 差分对进入 FPGA。MMCM 把 PL_CLK 变成 fabric 时钟，它计 tick、捕获 PPS、在目标 tick 播放 TX burst 和采集 RX 窗口，也驱动 RFDC 的 AXIS 一侧。

```text
  FPGA: our bitstream, loaded after boot finishes                                                 [F3]
  PL_CLK 100 MHz  ──> AN11/AP11, IBUFDS (LVDS) ──> MMCM ──> fabric clock 100 MHz                  [F1][F2]
  PL_SYSREF 5 MHz ──> AP18/AR18, IBUFDS (LVDS) ──> synchronizer ──> RFDC IP                       [F1][F6]
  J7 PPS ──> 49.9 Ω ──> comparator ──> level translator ──> AJ13 (FPGA pull-up)                   [F4]

  fabric clock 100 MHz
   ├─ P0  raw_tick counter (10 ns)
   ├─ P1  PPS capture: AJ13 sampled on each tick ──> PPS_TICK                                     [F5]
   ├─ P2  control: TX and RX armed by the ARM for a target tick                                   [F9]
   ├─ TX buffer (URAM, 17 banks × 4096 beats) ──> TX player ──> AXIS ──> RFDC DAC                 [F7][F8]
   └─ RFDC ADC ──> AXIS ──> RX capture ──> async FIFO ──> S2MM DMA ──> DDR
```

**[F1] PL_CLK 和 PL_SYSREF 按单端接收**（run2–run4）。
- 现象：run2 的 MMCM 只是有时能锁上，run3 一直锁不上。
- 原因：从 RFSoC-MTS 抄来的约束只把每对 LVDS 的 P 脚当作 LVCMOS18 接收；run3 加了上拉，N 脚被拉高。
- 改动：run4 两对都用 IBUFDS、LVDS、100 Ω 差分终端、DQS_BIAS。

**[F2] 改时钟后 MMCM 锁不上。** MMCM 的输入随时钟一起变；不复位的话，第一次加载后它可能锁不上。`PL.open()` 拉一下 `pl_resetn0`，再等时钟状态 GPIO（ABI §7.1 第 4–5 步）。

**[F3] PYNQ 的 base overlay 覆盖了我们的设计。**
- 现象：有一次冷启动后，我们加载的设计消失了。
- 原因：内核启动约一分钟后，`bootpy.service` 加载 base overlay。
- 改动：`PL.open()` 先等 `systemctl is-system-running --wait`（ABI §7.1 第 0 步）。

**[F4] 收不到 PPS 沿**（run2–run4）。
- 现象：`PPS_COUNT` = 0。
- 原因：PPS 线没接在 J7 上。排查中还发现输入链路（LMV7235 比较器和一个 1.8 V 侧没有上拉的电平转换器）需要 FPGA 的上拉。
- 改动：run3 加了上拉、第二路 PPS 输入和 `PPS_CONFIG` / `PPS_INPUTS`（ABI 1.1），由软件选择输入和极性。

**[F5] 887329260072A 的 PPS 沿落在采样沿上**（`results/pps-phase-scan-20261018_*`）。
- 现象：每 15 个沿抽样，40–60 % 的 PPS 沿早一个 tick 被捕获；M2 报出 `actual-PPS` ±1；通宵约 23 000 个沿里出现一次。
- 原因：它的 PPS 沿正好落在 100 MHz 的采样沿上；这个相位一天漂约 1 ns。
- 改动：EPG1 输出 2 延时 6.6 ns（B4）。这块板从此捕获后一个 tick，这就是 theta 约为 +10 ns 的原因。
- 待办：PPS 相位漂移相对 EPG1 重定时 ±1.25 ns 的余量；在 FS725 1PPS 上加一个扇出缓冲器，可以让 PPS 不再经过 EPG1。

**[F6] PL_SYSREF 的输入时序**（没核对）。它的输入延时约束（2.000/2.031 ns）抄自 RFSoC-MTS，那里的 PL_CLK 是 500 MHz。这里如果出错，fabric 一侧会整整错一个 10 ns 的 tick；从没见过这样的台阶。

**[F7] TX 的拍内布局**（2026-10-14，ABI §4.5.8）。
- 现象：回环 lane 检查中 8 个 lane 的延时相差 ⌈k/2⌉ 个采样。
- 原因：DAC 按每个字 I、Q 交织读取；TX 当时按平面方式打包，所以那之前发出的波形都是乱的（M2 的计时用的是沿，结论仍然成立）。
- 改动：`TX_LAYOUT` 默认交织。

**[F8] TX 缓冲的存储器**（run7、run8）。一整块 69 632 拍的存储器没有被推断成 URAM（写使能带地址条件，深度不是 2 的幂），退化成 548 个 BRAM36；改成 17 个 4096 拍的 bank 后解决。

**[F9] arm 太晚导致 M2 中止**（rf-clock 1.3.0.3）。在目标前不到 `MIN_LEAD_TICKS` 才 arm 的 burst 会以 `REJ_STALE` 被拒绝；进程在 PPS 和 arm 之间卡顿时，M2 会中止。现在记为漏发一次，再重新同步。

## 4. LMX2594

每片 LMX2594 把 480 MHz 参考倍频到 7.68 GHz 的 VCO，再分频回 480 MHz 送给它的 tile：一片给 ADC tile，一片给 DAC tile。

```text
  OSCin 480 MHz ──> pre-R ÷2 ──> R ÷5 ──> PFD 48 MHz ──> charge pump, loop ──> VCO 7.68 GHz ──┐
  (LMK ÷5)                                 ^                                   band [X2]      │
                                           └── N ÷40 <── SEG0 ÷2, SEG1 ÷2 <───────────────────┤
                                               VCO_PHASE_SYNC = 1 since 1.3.0.4  [X1]         │
                                               (before: N ÷160 counted the VCO itself)        │
  RFoutA, RFoutB 480 MHz <── SEG3 <── SEG2 <── SEG1 <── SEG0 ÷2 <─────────────────────────────┘
    │                        └─────── ÷16 in all ──────────┘
    └──> RFDC tile PLLs (section 5)
  two chips: spi0.1 clocks the ADC tiles, spi0.2 the DAC tiles (device tree swaps the names)      [X3]
```

**[X1] 通道分频器从任意 VCO 沿开始**（rf-clock 1.3.0.4；[报告](../results/lmx-phase-sync-20261019/REPORT_zh.md)，[回环报告](../results/lmx-sync-loopback-20261019/REPORT_zh.md)）。
- 现象：通宵 104 次重启，每块板的 DAC 输出落在相距 52 ps 的两个时刻之一，MTS 看不到（[M2 报告](../results/m2-restarts-20261016_212509/REPORT_zh.md)）。
- 原因：没有相位同步时，通道分频器从任意 VCO 沿（130.2 ps）开始；MTS 只能消除整数个采样，剩下 26.04 ps 格点上的位置（DAC 采样的 1/4，ADC 采样的 1/8）。
- 改动：VCO_PHASE_SYNC = 1、PLL_N 40（SEG0 和 SEG1 进入反馈环），写完后用软件做一次 SYNC（TI 第 2 类）。
- 验证：十次写时钟和十次重启都在同一时刻（示波器 sd 2–7 ps）；M3 回环十次写时钟在 0.10 ps 以内。

**[X2] 每次写时钟都重新选 VCO 档位**（rf-clock 1.3.0.6；[回环报告](../results/lmx-sync-loopback-20261019/REPORT_zh.md)第 5 节）。
- 现象：X1 修复后，重启和写时钟时偶尔还有一个转换器跳 7.6–9.7 ps。
- 原因：每次写时钟都会重新校准 VCO，在 7.68 GHz 处它在相邻两个 VCO 档位中选一个（887329260072A：ADC 117/118，DAC 122/123）；档位高一档，对应的转换器晚 8–10 ps。
- 改动：每块板一个配置（`/etc/rf-clock/board.json`，以本板 AT24MAC402 的序列号命名），给出每片的档位，`PL.open()` 强制使用、回读并要求锁定；`rf_clock.checks.vco_bands` 找出档位并测量档位表。
- 验证：同一档位内回环延时 2.5 h 保持在 0.1 ps 以内。
- 待办：强制的档位在其他温度下能否锁定；887329260101 的档位表要等它自己接回环线。

**[X3] 设备树把两片芯片的名字标反了。** `lmxdac`（spi0.1）驱动 ADC tile，`lmxadc`（spi0.2）驱动 DAC tile，方法是分别关掉一片。rf-clock 对两片写同样的内容，不受影响；第一批回环序列的标签把 ADC 和 DAC 弄反了（已在[回环报告](../results/lmx-sync-loopback-20261019/REPORT_zh.md)中改正）。

## 5. RFDC tile：采样时钟、SYSREF、MTS、NCO

每个 tile 的 PLL 用它那片 LMX 的 480 MHz 产生采样时钟。来自 LMK 的 SYSREF 在这个采样时钟上被捕获；然后 MTS 测量每个 tile 经过 fabric 时钟与转换器时钟之间那个 FIFO 的延时，再补一个 offset 达到固定目标，于是转换器相对 SYSREF 固定。NCO 负责把频带搬到 5.8 GHz 和搬回来。

```text
  DAC tile (tile 2 carries the signal; tile 0 is there for MTS)                                   [R6]
    480 MHz ──> tile PLL ×20 ──> 9.6 GHz sample clock
    SYSREF ──> captured on the sample clock at a capture code ──> marker                          [R2]
    AXIS ──> FIFO ──> interpolation ×12 ──> mixer, NCO ──> IMR high-pass ──> DAC core ──> DAC_A
              │                                     └ NCO phase restart at a SYSREF edge          [R5]
              └ MTS: latency from the marker across the FIFO, plus an offset
                (steps of 6 samples) to reach the target                                          [R1][R3][R4]

  ADC tile
    480 MHz ──> tile PLL ×10 ──> 4.8 GHz sample clock
    SYSREF ──> captured at a capture code, as on the DAC                                          [R2]
    ADC_B ──> ADC core ──> mixer, NCO -1.0 GHz ──> decimation ×6 ──> FIFO ──> AXIS
                                   └ [R5]                             └ MTS [R1][R3]

  driver: XRFdc_MultiConverter_Init/Sync in libxrfdc.so, through cffi                             [R7]
```

**[R1] latency 随 bitstream 变化**（2026-10-14）。
- 现象：run6 的 ADC latency 为 1248，之前是 1236；回环延时随之移动 2.0 个采样。
- 改动：固定目标 DAC 1896、ADC 1296（用户决定）；tile 达不到目标时 MTS 失败。

**[R2] SYSREF 捕获窗口翻转**（rf-clock 1.3.0.8；[M4 报告](../results/m4-twtt-20261021/REPORT_zh.md) 第 4 节）。
- 现象：有一次运行 theta 低了 51.3 ps：887329260101 的 DAC 晚了一个采样（104 ps）上电；它的 T1 捕获码从 54/53 变成 73/73，latency 不变。
- 原因：两个候选窗口离驱动的目标码 64 差不多远，几 ps 的时钟相位变化就选中了另一个。
- 改动：每个 tile 的码记进板级配置（`rf_clock.checks.mts_dtc record`），MTS 选离它们最近的窗口（驱动的 reload 扫描）；某个码离记录超过 6 时 `PL.open()` 发出警告。
- 验证：故意把另一个窗口设为目标时，MTS 选中了它。

**[R3] K2 的状态只表现为 offset 不同**（rf-clock 1.3.0.9）。K2 让 SYSREF 和转换器时钟相对 fabric 时钟错开几个 VCO 周期时，T1 捕获码不变（SYSREF 和采样时钟一起动），只有 fabric 时钟与转换器时钟之间的 FIFO 看得到：DAC tile 0 offset 为 12（平时 8），ADC tile 0 为 8（平时 10）。现在板级配置记录每个 tile 平时的 offset，某次上电不同时 `PL.open()` 发出警告；每一次出现台阶的上电，offset 都不正常。

**[R4] K2 修复后没有余量**（rf-clock 1.3.0.10）。
- 现象：新状态下 DAC tile 对齐前的实测延时为 1896，正好等于目标（offset 0）：实测再大一点的 tile 会让 MTS 失败（`TARGET_LOW`）。
- 改动：DAC 目标 1944，高 8 步，和以前一样；DAC 输出晚 5 ns（示波器 +5.012/+4.998 ns），总计比 1.3.0.9 之前晚 0.83–0.85 ns。offset 重新记录为 DAC 8/8、ADC 12/12。
- 待办：相对 PPS 的绝对延时要在标定线缆和示波器通道之后重新测量。

**[R5] NCO 落在三个相位之一**（rf-clock 1.3.0.5；[回环报告](../results/lmx-sync-loopback-20261019/REPORT_zh.md)第 4 节）。
- 现象：每次 `PL.open()` 后，DAC 和 ADC 的 NCO 各自落在相差 120° 的三个相位之一（只影响载波相位，不影响计时）。
- 原因：IP 在一个 tile 事件上启动 NCO，发生的时刻是任意的。
- 改动：MTS 之后让两者在同一个 SYSREF 沿上重新起相（PG269 Use Case 1）。
- 验证：38 次 M3 运行（35 次在重启之后）、M4 的多次上电和重启、5 次断电，都只有一个相位。
- 待办（不需要）：是哪个 tile 时钟（300 还是 600 MHz）给出三个位置。

**[R6] MTS 对构建的要求**（`plan/checklist_zh.md` 第 3 节）。只有每个参与的 tile 都打开通道 0，MTS 才会启用；参与片上时钟分发的 DAC tile 最高 7.0 GS/s。因此 ADC 和 DAC 都用 tile 0 和 tile 2，各自用自己的 PLL，DAC 9.6 GS/s，tile 0 为 MTS 存在；2.1 起 DAC tile 0 还在 DAC_B 上发输出 PPS（AGENTS.md）。

**[R7] 通过 PYNQ 做 MTS。** PYNQ 3.1.1 的 `xrfdc` 在 Python 层没有 MTS；`src/rf_clock/pl/rfdc.py` 通过 cffi 调用 `libxrfdc.so` 里的 `XRFdc_MultiConverter_Init/Sync`。

## 6. 两块板之间的射频链路

```text
  887329260072A DAC_A ──> ZVBP-5800-S+ ──> 10 dB ──> 887329260101 ADC_B   (AB)
  887329260101 DAC_A  ──> ZVBP-5800-S+ ──> 20 dB ──> 887329260072A ADC_B  (BA)

  DAC_A, mix-mode:   3.8 GHz about as strong as 5.8 GHz; band-pass 5725-5875 MHz                  [A1]
  ADC at 4.8 GS/s:   5.8 - 4.8 = 1.0 GHz and 4.8 - 3.8 = 1.0 GHz, in the same form                [A1]
  across the burst:  group delay ±0.5 ns (AB), ±0.9 ns (BA)                                       [A2]
```

**[A1] DAC 的 3.8 GHz 分量**（[BPF 报告](../results/bpf-compare-20261022/REPORT_zh.md)，[DAC 频谱](../results/dac-spectrum-20261017/REPORT_zh.md)）。
- 现象：拆掉带通滤波器后，BA 方向的 PSL 从约 21 dB 降到 10–11 dB，theta 每次运行离散 118–148 ps，有滤波器时是 1.5 ps。
- 原因：mix-mode 下 DAC 输出的 3.8 GHz 分量和 5.8 GHz 差不多强（满幅时 −12.7 / −13.7 dBm），ADC 以 4.8 GS/s 采样，把两者都折叠到 1.0 GHz，而且形式相同，都是发射波形的共轭，所以相关器会把 3.8 GHz 副本也当成信号（[BPF 报告](../results/bpf-compare-20261022/REPORT_zh.md)第 4 节）。
- 改动：每条交叉链路一个带通滤波器（ZVBP-5800-S+，3.8 GHz 处抑制 94 dB）。更便宜的宽带滤波器（VBFZ-5500-S+）要做到 1 ps，3.8 GHz 处需要约 63 dB；没有采用。

**[A2] 包络相对载波相位**（未解决）。
- 现象：5 次断电后（2026-10-22），包络 theta 高了 37–59 ps，载波 theta 在 ±6 ps 以内，偏差主要在 m_AB（+58 到 +92 ps），而且板子变热的 3 小时里没有回去。同一天 14:30 到 23:09 之间，两个方向由波形形状得出的延时都变长了（包络峰 +52/+49 ps，群延时 +36/+37 ps），载波不变，中间没有测量。
- 已知：信道远不是纯延时：在 burst 频带内群延时变化约 ±0.5 ns（AB）和 ±0.9 ns（BA，像 6.7 ns 附近的一个回波），所以信道形状的小变化就能让包络移动几十 ps，而载波相位不动（`results/m4-twtt-pair-20261022_142951`，`experiments/m4_channel.py`）。形状在哪里变，还不知道。
- 结论：在约 50 ps 以下，应依靠载波相位计时；R5 让它在上电之间可用。

**[A3] 接头和极性。** ADC 输入从 `vin2_23` 改到 `vin2_01`，即 ADC_B 接头（2026-10-19 用线缆核对过）。DAC_A 的输出相对 I 是反相的（示波器，2026-10-14）。

## 7. 测量和环境

```text
  EPG1 output 4 (PPS) ──> C1, trigger
  887329260072A DAC_A ──> C2 ──> F1 = Filter(Square(C2)) ──> P3 = Delay at 50 %, C1 to F1         [M1][M2]
  887329260101 DAC_A  ──> C3 ──> F2 = Filter(Square(C3)) ──> P4 = Delay at 50 %, C1 to F2
```

**[M1] 示波器读数依赖载波相位**（M2）。最初的方法读 3.8 GHz burst 的"第 N 个峰"，而且每次运行都会重置 NCO，读数在一个载波周期内跳动（最多 10 ns）。先换成基带方波（2026-10-14），后换成包络 Filter(Square(C)) 的 50 % 点（2026-10-16）。

**[M2] 示波器的坑。** LeCroy 的 math 迹线保留第一次计算时的量程（被截顶的迹线给出错误的延时），它的远程接口在 pyvisa-py 下会卡死；两者都已在 `experiments/m2_restarts.py` 和 `tanglab_instruments.HDO9404` 里处理。

**[M3] 回环幅度**（M3，rf-clock 1.3.0.5）。TX 幅度 4096 时包络延时每次运行离散 4–6 ps；16000 时约 1 ps，现在是默认值。

**[E1] 温度**（[M4 报告](../results/m4-twtt-20261021/REPORT_zh.md)第 5 节）。每块板的时钟随芯片变热而变慢，约 1–1.5 ps/K，所以 theta 随 887329260072A 的芯片温度上升、随 887329260101 的下降（theta = k_A·T_A + k_B·T_B + C）。具体在时钟链的哪一环是推断，没有实测。从 rf-clock 1.3.0.9 起每秒记录芯片温度。待办：一次只给一块板做温度阶跃，测出各自的斜率。

**[E2] 只出现过一次、未解释的。** 在一组没有 K2 修复的上电序列中，m_AB 有 110 次运行比中位数高 0.60 ns（2026-10-24 01:39–02:43，没接滤波器），这期间所有 MTS offset 都正常，偏移量也不是整数个 VCO 周期（[LMK 报告](../results/lmk-divider-sync-20261025/REPORT_zh.md)第 7 节）。

## 8. 仍未解决

1. rf-clock 1.3.0.10 下相对 PPS 的绝对延时（R4）：先标定线缆和示波器通道（交换法，需要 SMA 三通或功分器），再测每块板 DAC 输出相对它的 PPS 的时刻。
2. 接回带通滤波器后的零基线常数和 Delta；Delta 还需要每块板的 PPS tick 内相位。
3. 包络相对载波相位（A2）：信道形状在哪里变；多次断电下的 H(f)。
4. 每块板的温度斜率（E1）。
5. 不经 EPG1 重定时的 PPS 分发（F5）。
6. 强制的 VCO 档位在其他温度下能否锁定，以及 887329260101 的档位表（X2）。
7. 还没核对：PL_SYSREF 的输入时序（F6），以及用校准过的仪器看 DAC 的 5.8 GHz 输出和 ADC 的接收（`plan/checklist_zh.md` 第 4 节）。
