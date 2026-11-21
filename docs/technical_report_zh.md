# RF-clock：两个 RFSoC 节点之间的皮秒级双向时间传递

**技术报告与交接文档**

Haoyu Dong（h.dong@yale.edu），Tang Lab，Yale University · 2026-11-06 · rf-clock 2.3.0.2 · 英文原文（以其为准）：[technical_report.md](technical_report.md)

## 摘要

RF-clock 通过无线链路同步两台钟。两个节点各是一块 Real Digital RFSoC 4x2 板子，各由自己的一台 SRS FS725 铷频标提供时钟。两个节点每 2 ms 轮流向对方发送 5.8 GHz 的扩频 burst。每个节点用自己 PL 上的 100 MHz 时钟测出对方 burst 的到达时刻，并在自己下一个 burst 里把这个测量发回去；由两个方向，两个节点都算出 θ，即节点 B 的钟相对节点 A 的钟的偏差，传播延时被抵消掉。节点 B 再修正自己的 FS725，使 θ 保持在标定好的目标值上；两个节点都用自己 PL 上的 100 MHz 时钟产生输出 PPS。在台架上，两块板经滤波器和衰减器用线缆相连：θ 每秒测 250 次，每次测量的噪声为 0.8 ps；B 的钟跟随 A 的钟，θ − θ₀ 为每秒 5.3-5.6 ps rms（本报告最后一步通过直接驱动 FS725 的晶振，从 15 ps 降到 5 ps）；示波器上两个节点的输出 PPS 之差为每秒 9.2 ps rms，即示波器本身的分辨率。本报告写给不熟悉 RFSoC 和时间测量的读者，说明系统做什么、为什么这样做、怎样搭建、怎样运行和维护、测到了什么、途中发现了哪些问题，以及计划中横跨长岛海峡 48 km 的链路还剩什么要做。

## 目录

1. [引言](#1-引言)
2. [背景知识](#2-背景知识)
3. [系统设计](#3-系统设计)
4. [硬件、台架与基础设施](#4-硬件台架与基础设施)
5. [运行与操作](#5-运行与操作)
6. [结果](#6-结果)
7. [发现的问题与经验](#7-发现的问题与经验)
8. [未解决的问题与后续工作](#8-未解决的问题与后续工作)
9. [交接清单](#9-交接清单)
10. [参考文献](#10-参考文献)

附录：[A. 术语表](#附录-a-术语表) · [B. 命令速查](#附录-b-命令速查) · [C. 故障排查](#附录-c-故障排查) · [D. 仓库结构](#附录-d-仓库结构) · [E. 报告索引](#附录-e-报告索引)

## 1. 引言

### 1.1 系统做什么

两个地方各有一台原子钟，要让其中一处（B）的钟以皮秒精度保持另一处（A）的时间，而两地之间没有线缆。RF-clock 分两步做到这一点，两步不断重复：

1. **测量** B 的钟离 A 的钟有多远。两个节点交换无线 burst；每个节点用自己的钟测对方的 burst，而无线路径延时在两个方向上相对钟差的符号相反，所以延时抵消、钟差留下（双向时间传递，第 2.4 节）。
2. **修正** B 的钟。B 调整自己 FS725 的频率，使钟差保持在目标值上（修正，第 2.7 节）。

```text
            节点 A（主）                                       节点 B（从）
  FS725 A ── 10 MHz, 1PPS ──> RFSoC A                RFSoC B <── 10 MHz, 1PPS ── FS725 B
                               │  ▲                    ▲  │                         ▲
                               │  └── 5.8 GHz burst ───┘  │                         │
                               └──── 每 2 ms 轮流 ────────┘── θ ── 修正 ───────────┘ RS-232
                    输出 PPS（J20）和 10 MHz                        输出 PPS（J20）和 10 MHz
                    = A 的时间                                      = B 的时间，保持在 A 上
```

节点用来测量的一切都由它自己 FS725 的 10 MHz 产生（第 3.2 节），所以修正 B 的 FS725 会移动 B 用来计时的一切：它的 10 MHz、采样时钟、PL 上的 100 MHz 时钟和 tick 计数器、输出 PPS。

### 1.2 为什么做

目标是一条 48 km 的无线链路，连接纽黑文的 Yale Kline Biology Tower 和 Stony Brook University Hospital，横跨长岛海峡（里程碑 M6，第 8.2 节）。海面上没有现实可行的光纤；卫星双向时间传递约能达到一纳秒；自由空间光链路只在公里量级上演示过。在这样的距离上实现皮秒级同步的地对地无线链路将是新的（2026-11-04 的文献检索）。这里报告的台架工作用线缆代替空中路径，搭建并测试了整个系统，这样 M6 只需换掉射频路径。

### 1.3 已经做到了什么

| 量（台架，线缆连接） | 数值 | 出处 |
| -------------------- | ------------------------- | --------------- |
| θ 的测量次数 | 每秒 250 次，每 4 ms 一次 | 第 6.1 节 |
| θ 的噪声，两块板共用一个参考 | 每次测量 0.81-0.87 ps | [链路报告](../results/v2-link-20261103/REPORT_zh.md) |
| θ − θ₀，改写 FC 来修正 B（2.3.0.2） | 每秒 5.34 和 5.56 ps rms，60 s 平均 1.7-1.9 ps | [精度报告](../results/precision-20261105/REPORT_zh.md)第 8 节 |
| 示波器上 B 的 10 MHz 相对 A 的 | 随 θ 移动（斜率 0.991），每秒 6.85 ps，含示波器噪声 | 同上 |
| B 的输出 PPS 相对 A 的，普通模式 | 每秒 9.19 ps rms，示波器分辨率 7.8-8.2 ps | 同上 |
| 输出 PPS，θ 补偿模式（2.3） | 每秒 8.6-8.8 ps rms，60 s 平均 0.9-1.0 ps | 同上，第 7 节 |
| 重新启动并重新捕获后输出 PPS 的常数 | 在 0.1-0.9 ps 以内 | 第 6.4、6.5 节 |
| 测量包 | 一整夜每个方向 490 万个，无 CRC 错误，无失锁 | [M5 报告](../results/m5-v2-20261103/REPORT_zh.md)第 8 节 |

设计中的里程碑（[plan/RF_clock_modules_zh.md](../plan/RF_clock_modules_zh.md) 第 9 节），从 M0（参考信号进板子）到 M5（两台钟，加修正），都已在台架上做过；[plan/checklist_zh.md](../plan/checklist_zh.md) 逐项给出证据，仍有一些子项未完成，主要是用时间间隔计数器测 PPS 之差（目前由示波器上的输出 PPS 代替），以及断电重启和温度阶跃（第 8.1 节）。M6，即空中链路，还没有开始。

### 1.4 怎样读这份报告

- 要**运行**现有的台架：第 5.1 节，然后第 5.2 节和附录 B。
- 要**理解**它：如果对这个领域不熟，先读第 2 节，再读第 3 节。
- 要**维护或修改**它：第 3、4 节，第 5.4-5.6 节，第 7 节和附录 D；编程手册 [docs/ABI_zh.md](ABI_zh.md) 有每个寄存器和每个函数。
- 要**继续研究**：第 6、7、8 节，以及附录 E 中的报告。

更深一层的文档是 [plan/RF_clock_modules_zh.md](../plan/RF_clock_modules_zh.md)（设计）、[docs/ABI_zh.md](ABI_zh.md)（ARM-PL 接口和 Python 库）、[docs/board_setup_zh.md](board_setup_zh.md)（一块新板子，逐步）、[docs/debug_overview_zh.md](debug_overview_zh.md)（时钟链上的每个问题）、[docs/fs725_principles_zh.md](fs725_principles_zh.md)（FS725 怎样工作）、[plan/checklist_zh.md](../plan/checklist_zh.md)（每一项及其证据）和 [AGENTS.md](../AGENTS.md)（任何改动都必须遵守的设计规则）。报告在 `results/<name>/REPORT.md`；`results/` 不在 Git 里（第 4.4 节）。

## 2. 背景知识

这一节给出读懂后文所需的知识，不假设读者懂钟或 RFSoC。

### 2.1 钟、频率与时间

钟是一个振荡器加一个计数器：振荡器产生周期信号，计数器数它的周期。实验室的钟输出两种信号：

- **10 MHz**：10 MHz 的正弦波，它的相位就是钟的时间，精确到它 100 ns 周期的一小部分。仪器把自己的振荡器锁到它上面。
- **1PPS**（每秒一个脉冲）：一个脉冲，它的边沿标出每一秒的开始。它说明 10 MHz 的*哪一个*周期开始这一秒。

两台钟的走速从来不会完全相同。如果钟 B 比钟 A 快一个比例 y，经过时间 t，B 就领先 A y × t。所以走速用无量纲的比例表示：

| y（相对频率） | B 每秒领先 | 每天 |
| --- | --- | --- |
| 1 × 10⁻⁹ | 1 ns | 86 µs |
| 1 × 10⁻¹² | 1 ps | 86 ns |
| 1 × 10⁻¹⁵ | 1 fs | 86 ps |

由此有一个有用的对应：**相对频率 10⁻¹² 就是每秒漂 1 ps**，所以“1 ps/s”的频率变化就是 10⁻¹²。作为尺度参考，光在 1 ps 里走 0.3 mm，5.8 GHz 载波的一个周期是 172 ps。

### 2.2 怎样描述稳定度

钟的频率不是常数，而是在起伏。它的稳定度用 **Allan 偏差** σ_y(τ) 描述：大致说，是长度为 τ 的相邻两段时间里平均相对频率的典型变化。它画在双对数坐标上，斜率说明噪声的类型：

```text
 log σ_y
   │╲ 斜率 −1：白相位噪声（测量本身的噪声；平均按 1/τ 改善）
   │  ╲__ 斜率 −1/2：白频率噪声（钟的走速抖动；时间误差按 √τ 增长）
   │      ‾‾‾‾‾‾‾  斜率 0：闪烁频率噪声（一个平台）
   │               ╱ 斜率 +1/2：随机游走频率噪声（走速漂走）
   └────────────────────────────────── log τ
```

读法：σ_y(1 s) = 1.6 × 10⁻¹¹ 表示两台钟的走速在一秒里典型地变化 1.6 × 10⁻¹¹，所以它们的时间差可以出现约 16 ps 无法预料的变化。**时间偏差**（TDEV）直接给出时间误差的同样信息。把一个时间差按 n 个白噪声样本平均，它的离散按 1/√n 下降；这就是报告里同时给出“每秒”和“60 s 平均”两种数字的原因。

### 2.3 FS725 铷频标

SRS FS725 里面是一台 PRS10 铷振荡器（[docs/fs725_principles_zh.md](fs725_principles_zh.md) 有完整说明）：

```text
  恒温槽里的石英晶振 ──> 10 MHz 输出，1PPS 输出
        ▲  调谐（FC，一个 22 位 DAC，经 1 s 的 RC 滤波器）
        │
  锁频环（FLL）：把晶振倍频到 6.834 GHz，与铷原子的跃迁比较，
  每 0.2 s 一个误差，积分进 FC（增益 GA）
        ▲
  SF：原子频率的一个小偏移（磁场），单位 10⁻¹²
```

- **晶振**在短时间内安静，但会漂；**铷原子**长期稳定，但它的检测器在短时间内噪声大。锁频环把两者结合起来：以约 2 s 的时间常数（正常增益 GA 7；GA 每加一档减半）把晶振拉向原子。
- **SF** 通过磁场移动原子的频率；锁频环随后把晶振拉过去，视 GA 不同晚 0.5-1.3 s。这是修正它的传统方式。
- **FC** 是晶振的调谐电压，平常归锁频环管。把环路放慢到 GA 1（时间常数 128 s）后直接写 FC，它只经过一个 1 s 的 RC 滤波器到达晶振（第 3.7 节）。把环路完全停掉（`LO 0` 或 GA 0）会让设备报告自己未锁定，并停掉 1PPS（第 7.4 节）。
- 它的 1PPS 由 10 MHz 经计数器和一级模拟延时产生；相对 10 MHz 抖动 22-44 ps，移动它的命令（PP）最多偏 0.23 ns，所以节点自己产生输出 PPS（第 3.8 节）。

台架上两台自由运行的 FS725 以约 65 ps/s 的速率相互漂开（它们的校准相差 6.5 × 10⁻¹¹），在这条线附近起伏，1 s 处的 Allan 偏差为 1.4-1.6 × 10⁻¹¹（[M5 报告](../results/m5-v2-20261103/REPORT_zh.md)第 4 节）。

### 2.4 双向时间传递

令 θ = T_B − T_A 为同一时刻 B 的钟读数相对 A 的偏差。A 在它的时间 T_tx,A 发出一个 burst；B 在自己的时间 T_rx,B 收到；B 在 T_tx,B 发回一个，A 在 T_rx,A 收到。设传播延时为 p，每个节点的发射硬件延时为 δt、接收硬件延时为 δr：

```text
  m_AB = T_rx,B − T_tx,A = δt_A + p + δr_B + θ      （B 测量；B 的钟领先 θ）
  m_BA = T_rx,A − T_tx,B = δt_B + p + δr_A − θ      （A 测量）

  θ = (m_AB − m_BA)/2 − Δ/2,       Δ = (δt_A + δr_B) − (δt_B + δr_A)
  单向延时 = (m_AB + m_BA)/2
```

路径延时 p 在两个方向上符号相同，在差中抵消，前提是两个方向的延时相同（互易性）；当两个 burst 在很短时间内走同一条路径时就是这样，这里两者相隔 2 ms。不能抵消的是 Δ，即两个方向硬件延时之差（线缆、滤波器、转换器）。它在两块板共用一台钟时测一次，那时真实的 θ 为零：此时看到的 θ 就是 **θ₀**，“零基线”常数，B 被修正到 θ₀ 而不是零。θ₀ 取决于两块板以及它们之间的每根线缆和每个滤波器，所以按板子对和接线存在 [links/](../links/README_zh.md) 里；目前的值是 −21.5 ps。

### 2.5 给一个无线 burst 计时

节点用相关来给对方的 burst 计时。burst 是一个伪随机二进制码（PRN）：4096 个 ±1 的码片，100 Mchip/s（每个 10 ns 的 tick 一个码片），经根升余弦（RRC）滤波器平滑，占据 5.8 GHz 附近 125 MHz 的带宽。接收端知道这个码，把它的副本在收到的采样上滑动，找到最吻合的位置：

```text
 收到的采样（800 MS/s，间隔 1.25 ns）──> 在每个延迟上与已知的 burst 相关
                                                       │
     相关幅度    │        ∧            峰：burst 的到达，精确到整采样
                 │       ╱ ╲           过峰的三点抛物线：采样的小数部分
                 │______╱   ╲______    峰处的载波相位：172 ps 载波周期的小数部分
                 └──────────────────── 延迟
```

相关把 burst 的全部 32 841 个采样加起来，把噪声平均下去；台架上线缆连接时峰值与噪声之比约 45 dB，插值后的峰给出的到达时刻远好于一皮秒，尽管一个采样是 1250 ps。峰处的载波相位也被测出；它能分辨 172 ps 周期的约千分之一，但每个周期重复一次，所以由包络峰决定是哪一个周期。目前 θ 用的是包络峰。

### 2.6 RFSoC 4x2

RFSoC（射频片上系统，这里是 AMD/Xilinx Zynq UltraScale+ XCZU48DR）把三样东西放在一块芯片上：

| 部分 | 是什么 | RF-clock 用它做什么 |
| ------------ | ----------------------- | ------------------------- |
| PS（处理系统） | 四个 ARM Cortex-A53 核，运行 Linux（PYNQ 3.1.1） | Python：配置、捕获、双向方程、修正、日志 |
| PL（可编程逻辑） | FPGA 逻辑，用 bitstream 编程 | 100 MHz 时钟上的 tick 计数，以及必须在 2 ms 内完成的一切：时隙、burst、相关、测量包、输出 PPS |
| RFDC（射频数据转换器） | 直接以 GHz 速率采样的 ADC 和 DAC | 9.6 GS/s 的 DAC 发射；4.8 GS/s 的 ADC 接收 |

它外面的 Real Digital RFSoC 4x2 板子加上了时钟芯片（一片 LMK04828 净化 10 MHz 参考并产生 PL 时钟和 SYSREF，两片 LMX2594 频率合成器产生转换器的参考时钟）、两个 DAC 和四个 ADC 的 SMA 接口、PPS 输入（J7）、以太网、USB，以及存放 Linux 的 micro-SD 卡。

本报告反复用到转换器的三个概念：

- **Nyquist 区。** 以 f_s 采样的转换器把频率划成宽 f_s/2 的区；高区的信号会折叠到第一区。9.6 GS/s 的 DAC 数字地产生 3.8 GHz，发射它在第二区 5.8 GHz 的镜像（由带通滤波器去掉 3.8 GHz）；4.8 GS/s 的 ADC 在第三区接收 5.8 GHz，它折叠到 1.0 GHz，再混频到基带。
- **确定的延时（MTS）。** 每个转换器 tile 有自己的时钟和 FIFO；如果不加处理，它从数字侧到接口的延时每次上电都会差整数个采样。多 tile 同步（MTS）用一个公共的 SYSREF 信号，让每个 tile 每次上电都有相同的延时，标定好的 θ₀ 才能在重启后保持不变。
- **所有时钟出自同一个 10 MHz。** 板上每个时钟，从 100 MHz 的 PL 时钟到 9.6 GHz 的 DAC 时钟，都由同一个 10 MHz 参考导出，所以 PL 的一个 tick（10 ns）正好是 800 MS/s 基带数据流的 8 个采样，采样序号和 tick 之间是精确的对应。

### 2.7 修正一台钟

B 不能瞬间移动自己的时间，只能改变频率，在随后几秒内移动时间。反馈环测量 θ − θ₀，并改变频率把它拉回来。难点在延迟：现在做的改动要过执行器的延迟才作用到钟上（经 FS725 的锁频环 1-2 s，经 FC 约 1 s），而在这段时间里两台钟会不可预料地起伏。所以环路必须预测钟在接下来一两秒的变化。最初用的 **Kalman 修正算法**估计 θ 和频率差，以一个时间常数去修正；现在用的**预测修正算法**为执行器建模，从 θ 里去掉自己的修正以看到自由运行的钟，用自回归（AR）模型预测它们，再用最小二乘规划接下来的改动（模型预测控制）。剩下的 θ − θ₀ 就是钟在执行器延迟期间无法预测的起伏（第 6.6 节）。

## 3. 系统设计

### 3.1 分工：PL 与 ARM

设计规则（[AGENTS.md](../AGENTS.md)）是：**ARM 跑算法；PL 负责时间和每个 burst 的测量链。** 测量在 2 ms 乒乓的 burst 里传递，所以每个测量必须在它所测的 burst 之后 2 ms 内准备好并发出去，而 ARM 上的 Linux 保证不了这一点；更慢的一切都留在 Python 里。

| 任务 | 在哪里 |
| ----------------------------------------- | ------------------- |
| 100 MHz 的 64 位 tick 计数器、PPS 捕获、时间戳 | PL |
| 时隙引擎：每个 burst 和每个接收窗口都在它的精确 tick 上开始 | PL |
| 由码片（PRN、保护间隔、测量包）经 RRC 滤波器生成 burst | PL（码片和滤波器系数由 ARM 计算） |
| 每个窗口的相关（跟踪器），失锁时整窗搜索 | PL |
| 测量包：组装、扩频、解扩、CRC；结果队列 | PL |
| 输出 PPS 及其有效状态线 | PL（由 ARM 告诉它何时有效） |
| 时钟（LMK、LMX）、RFDC 启动和 MTS | ARM，通过驱动 |
| 捕获：在长窗口里找到另一个节点 | ARM（PL 把窗口采进 DDR） |
| 两个方向配对、θ、日志 | ARM |
| 经 RS-232 修正 B 的 FS725 | ARM |

### 3.2 一个节点的时钟

```text
 FS725 10 MHz ──> LMK04828：PLL1（VCXO 160 MHz）──> PLL2，VCO 2400 MHz ──┬─ ÷24  ──> PL 时钟 100 MHz ──> tick（10 ns）
                                                                          ├─ ÷480 ──> SYSREF 5 MHz ──> RFDC tile（MTS）、PL
                                                                          ├─ ÷5 ──> LMX2594 ──> 480 MHz ──> ADC tile PLL ──> 4.8 GS/s
                                                                          └─ ÷5 ──> LMX2594 ──> 480 MHz ──> DAC tile PLL ──> 9.6 GS/s
 FS725 1PPS ──> J7 ──> 比较器 ──> PL：每个 tick 采样一次 ──> PPS_TICK（每秒边沿所在的 tick）、PPS_COUNT
```

节点的时间是“第 k 秒，加上从这一秒 PPS 边沿起的 tick 数，再加上 tick 内的采样”。PL 从不清零 tick；每一秒锚定在锁存其 PPS 边沿的那个 tick 上，PL 并检查每一秒正好是 100 000 000 个 tick。这棵树上的每个分频器和合成器都必须从确定的相位启动，起初有几个并不是（第 7.2 节）：板级配置（第 3.10 节）和时钟文件把它们固定下来。

### 3.3 频率规划

| 项目 | 取值 |
| ------------- | ----------------------------------------------- |
| 载波 | 5.8 GHz（5.725-5.875 GHz ISM 频段） |
| DAC | tile 2 block 0（DAC_A 接口），9.6 GS/s，NCO 3.8 GHz，5.8 GHz 是第二 Nyquist 区的镜像 |
| ADC | tile 2 block 0（ADC_B 接口），4.8 GS/s，5.8 GHz 在第三 Nyquist 区，NCO −1.0 GHz |
| 基带 | 800 MS/s 复数；每个 tick 8 个采样 |
| 码 | 4096 码片的 PRN，100 Mchip/s；RRC 滚降 0.25，81 抽头；占用 125 MHz |
| tile 0 | ADC 和 DAC 的 tile 0 为 MTS 而启用；DAC tile 0（DAC_B，J20）输出输出 PPS |

### 3.4 时隙协议

```text
  时隙 k = 0 … 499 从 PPS + k × 2 ms 开始
  k 为偶数：A 发射，B 的窗口打开          k 为奇数：B 发射，A 的窗口打开

        时隙 2j（A）                        时隙 2j+1（B）                      时隙 2j+2（A）
        |<------------ 2 ms ------------>|<------------ 2 ms ------------>|
 A TX   |o|PRN|g| 测量包：A 的上一个结果  |                                |o|PRN|g| 测量包 ...
 B RX   |o|d|[窗口：跟踪器][测量包]                                       |
 B TX   |                                 |o|PRN|g| 测量包：B 对 2j 的结果 |
 A RX   |                                 |o|d|[窗口：跟踪器][测量包]
```

- **burst**：PRN（40.96 µs），10 个码片的保护间隔，然后是**测量包**：36 字节（288 位），每一位扩展到另一个码的 64 个码片上（0.18 ms）。它带着发送方的秒（`PPS_COUNT`）和时隙、这个 burst 的发送时刻、对方上一个 burst 的到达时刻和载波相位、一个质量指标、状态标志和 CRC。
- **窗口**：B 在 A 的 burst 预计到达前 1.25 µs 打开接收窗口（54.8 µs，搜索范围 11 008 个延迟）。PL 的跟踪器在每个窗口里对预期峰值附近的几个延迟做相关，插值出峰并测量载波相位；解扩器解出它后面的测量包。
- **配对**：一对是相隔 2 ms 的两个方向。每个节点把自己的测量和下一个 burst 里到达的对方测量，按 `PPS_COUNT` 和时隙配成一对；不涉及网络，也不需要 NTP。两个节点从各自的队列算出相同的 θ。
- **搜索**：跟踪器没有锁定时（启动时，或失锁后），PL 用 1024 个并行相关器以整数运算搜索整个窗口（3.9 ms）；只有新峰在旧峰一个采样以内时它才自己重新锁定，否则由 ARM 决定。

### 3.5 PL 的设计

PL 模块编号为 P0-P10（设计 [plan/RF_clock_modules_zh.md](../plan/RF_clock_modules_zh.md) 第 6 节；寄存器 [docs/ABI_zh.md](ABI_zh.md) 第 4 节；RTL 在 `PL/src/`）：

| 模块 | 功能 |
| -------------- | ---------------------------------------------- |
| P0 时钟与复位 | 锁定监视、故障 |
| P1 tick 与 PPS | 64 位 tick、PPS 捕获及周期检查、PPS 来源和极性 |
| P2 控制寄存器 | AXI-Lite 寄存器表（`PL/src/p_regs.vh`），测试用的单条 TX/RX 命令 |
| P3 TX burst | 码片存储器、RRC FIR、从起始 tick 起不间断播放 |
| P4 RX 采集 | 定时窗口送进相关器，按需经 FIFO 和 DMA 送进 DDR |
| P5 RFDC | 射频数据转换器 IP |
| P6 状态 | 锁存的故障 |
| P7 时隙引擎 | 每个 PPS 起的 2 ms 乒乓 |
| P8 相关器 | 每个窗口的跟踪器，按需整窗搜索 |
| P9 测量包 | 测量包的组装、扩频、解扩、CRC；测量队列 |
| P10 输出 PPS | 在 DAC_B 上每秒一个脉冲，飞轮，有效状态线，边沿分数延迟（2.3） |

在同样的窗口上，PL 的相关结果与 ARM 的 float64 相关逐位一致（`rf_clock.plmodel`，[链路报告](../results/v2-link-20261103/REPORT_zh.md)）。PL 设计遵守的规则（[AGENTS.md](../AGENTS.md)）：burst 由片上数据生成，不间断地播放（DAC 不再接收数据就是故障，绝不暂停）；接收窗口内不能丢任何采样（窗口内 FIFO 溢出使这个窗口无效，计数，但不进入故障）；每个 burst 和窗口都有一个起始 tick，每个采样的时间由它推出。

### 3.6 捕获

时隙开始之前，B 必须知道 A 的秒从哪里开始，精确到窗口的一小部分：两台自由运行的钟可能差任意多。B 采一个 11 ms 的窗口进 DDR，ARM 对它做相关（Python 中约 3 s，rf-clock 2.3.0.4 之前约 25 s），找到 A 的三四个 burst，解出它们的测量包，包里带着 A 的发送时刻和秒的编号，由此把 A 的 PPS 放到 B 的时间上，误差在假设的单向延时（`--arrival-ticks`）以内。B 再用 FS725 的 PP 命令把自己的 1PPS 移过这个偏差，移整数个 tick，然后两个节点开始各自的时隙。之所以是整数个 tick，是因为 PL 在自己的 tick 上锁存 PPS：移动整数个 tick 恰好把 PPS_TICK、连同 PL 从它数起的所有时间，移动这么多，而移动小数部分会不声不响地改变边沿在它那个 tick 里的位置（[M5 报告](../results/m5-v2-20261103/REPORT_zh.md)第 2 节）。剩下不到半个 tick 的偏差由修正去掉。

### 3.7 修正 B

B 的 ARM 把 θ 交给修正算法，经 USB-RS232 转接器写 FS725。有两种执行器：

```text
 经 SF（到 2.3.0.1 为止）：修正算法 ──> SF ──> 磁场 ──> 锁频环检测器（0.2 s 一帧）──> 积分器 ──> FC ──> 1 s RC ──> 晶振
 经 FC（2.3.0.2）：        修正算法 ──> FC ──────────────────────────────────────────────────────────> 1 s RC ──> 晶振
                                      （设备处于 GA 1：锁频环自己 128 s 的回拉，由每 0.1 s 写一次 FC 抹掉）
```

- **经 SF** 时，一次修正要经过锁频环的积分器和死区，1-2 s 后才起作用；0.2 s 步长的预测修正算法把 θ − θ₀ 保持在约 15 ps。
- **经 FC**（2.3.0.2 起的常规做法，“改写 FC 来修正”），修正算法在 B 的设备处于 GA 1 时每 0.1 s 写一次晶振的调谐量；晶振只经过它那个 1 s 的 RC 跟随（实测滞后 1.12 s，无死区，FC 每个细步 0.86 ps/s），θ − θ₀ 为 5.3-5.6 ps。修正算法的模型故意比实测的晶振慢（滞后乘 1.25，死区加 0.1 s）：重放中，模型比 FS725 的响应快就会振荡。
- 修正算法（[docs/ABI_zh.md](ABI_zh.md) 第 11.12 节；`rf_clock.twtt.steer.PredictiveSteering`）：每 0.1 s 取这一步最后 0.05 s 的 θ，减去自己过去改动的作用，用 AR(40) 模型预测自由运行的钟在 3 s 内的变化，按每次改动的代价用最小二乘规划四次改动，发出第一次。运行结束时，或修正停止时，GA 改回板级配置里的增益。

### 3.8 输出 PPS

节点给其他设备的 PPS 由 PL（P10）用自己的 100 MHz 时钟产生，而不是取自 FS725：每一秒，DAC tile 0 数据流上 PPS_TICK + OFFSET 处的那一拍带一个阶跃，交流耦合的 DAC_B 输出（J20）把它变成一个脉冲，50 Ω 负载上约 0.19 V，上升时间 1 ns。它跟随 PL 的 100 MHz 时钟，而 θ 对齐的正是两个节点的这个时钟，所以 FS725 1PPS 的抖动和 PP 的误差都不会进入，而且它随 tick 精确地移动整数个 tick。飞轮在缺少输入边沿时把它维持下去，有效状态线（Pmod J1 第 1 针，高 = 无效；一个 LED）说明它能不能用。两种模式（[plan/pps_output_zh.md](../plan/pps_output_zh.md)）：

- **普通模式**（默认）：边沿相对节点自己的 10 MHz 位置固定。改写 FC 来修正 B 时，两个节点的普通输出 PPS 之差为每秒 9.2 ps rms。
- **θ 补偿模式**（`m4c_link --pps-out-comp`，2.3 起）：每个边沿之前，B 把自己的边沿推迟刚测得的 θ − θ₀，推迟量是 1.25 ns DAC 采样的若干分之一（`PPS_OUT_FRAC`）。B 的 PPS 于是按测量而不是按修正跟随 A 的（用 SF 修正时每秒 8.6-8.8 ps），但相对 B 的 10 MHz 位置不再固定；同时使用两种信号的设备应该用普通模式。

### 3.9 软件与版本

Python 包 `rf_clock` 在板子上（PYNQ 的环境里）和开发机上（测试时用一个假的 PL）运行：

| 模块 | 内容 |
| -------------- | ---------------------------------------------- |
| `rf_clock.pl` | 上电（`PL.open()`：overlay、时钟、RFDC、MTS、PPS 锁定）、寄存器、时隙、burst、相关器、队列、捕获、输出 PPS、板级配置 |
| `rf_clock.twtt` | 双向方程（`pairs`）、节点的 FS725 和 SYNCED（`control`）、修正算法（`steer`）、读日志（`logs`） |
| `rf_clock.fs725` | 经 RS-232 访问 FS725（查询、SF、GA、FC、PP） |
| `rf_clock.checks` | M1-M3 的板级检查和链路节点 `m4c_link`，标定（`vco_bands`、`mts_dtc`、`fs725_loop`、`pps_pp_scan`） |
| `rf_clock.examples` | 带注释的示例 |
| `rf_clock.plmodel`、`correlator` | 用 Python 复现 PL 的运算；float64 相关 |

包里带着它的 bitstream（`src/rf_clock/overlay/`）。版本号 **A.B.C.D** 把两者绑在一起：A.B 是寄存器表版本（`VERSION`），C 数这一版寄存器表编过的 bitstream，D 数只改 Python 的发布；`PL.open()` 拒绝 A.B.C 与包不一致的 bitstream。目前的发布是 2.3.0.2，bitstream 为 2.3.0.0。

### 3.10 标定常数及其存放位置

| 常数 | 属于 | 存放在 | 由谁测 |
| ------------------ | ----------- | ---------------- | --------------- |
| 每片 LMX2594 的 VCO 档位 | 一块板 | 板级配置 `/etc/rf-clock/board.json`（[boards/](../boards/README_zh.md)） | `rf_clock.checks.vco_bands scan --write` |
| MTS 的 SYSREF 捕获码和 MTS 偏移 | 一块板 | 板级配置 | `rf_clock.checks.mts_dtc record --write` |
| FS725、它的端口、它在所用增益下的锁频环、它的晶振对 FC 的响应 | 一块板（它的那台设备） | 板级配置，`fs725` | `rf_clock.checks.fs725_loop --write`；`fc` 用 `experiments/m5_fll_off.py` |
| θ₀（Δ/2 和两路 PPS 之差） | 一对板子和一套接线 | `links/<A>_<B>.json` | 两块板共用一个参考，多次上电 |
| 输出 PPS 分数的斜率 | DAC_B 输出（在 B 上测） | `m4c_link` 默认值，每单位 0.0275 ps | `experiments/m5_pps_frac.py` |
| PPS 捕获余量 | 一块板及其 PPS 线缆 | 不存放；由 EPG1 延时和 A 上的 `--pps-trig` 保持 | `experiments/m1_pps_phase_scan.py`、`rf_clock.checks.pps_pp_scan` |

原则是绝不减去一个猜出来的常数：每个常数都是测出来的，连同测量方式和时间一起存放，放在它所属的地方（一块板的放在它的配置里，一条链路的放在 `links/` 里）。

## 4. 硬件、台架与基础设施

### 4.1 清单

| 项目 | 标识 | 用途与说明 |
| ------------ | ---------------- | -------------------------------- |
| RFSoC 4x2 | S/N 887329260072A，192.168.2.99 | 节点 A |
| RFSoC 4x2 | S/N 8873292600AB，192.168.2.100 | 节点 B（2026-11-03 起） |
| RFSoC 4x2 | S/N 887329260101，192.168.2.101 | 备用；1.x 时作为 B；配置里没有 FS725；它的 VCO 档位表缺各档延时（需要自己的回环线） |
| SRS FS725 | S/N 41175 | A 的钟，GA 7 |
| SRS FS725 | S/N 107129 | B 的钟，GA 8，改写 FC 来修正；它对 FC 的响应在 B 的配置里 |
| Thorlabs EPG1 | 脉冲与延时发生器 | 共用一个参考的实验中，把一台 FS725 的 10 MHz（方波，锁定）和 1PPS（各输出可设延时）分给两块板；不在 M5 的接线里 |
| Teledyne LeCroy HDO9404-MS | 示波器，USB 接 RF-clock-PC（需要 root） | 外部检验：两路 10 MHz、1PPS 或输出 PPS（`experiments/m5_hdo_phase.py`） |
| Signal Hound SA124B | 频谱仪，USB 接 RF-clock-PC | DAC 频谱、滤波器检查 |
| Mini-Circuits ZVBP-5800-S+ | 带通 5725-5875 MHz，两个 | 每条交叉路径一个；必需（第 7.3 节） |
| 衰减器 | 10 dB 和 20 dB | 每个方向一个 |
| USB-RS232 转接器（FTDI） | 在每块板的 USB 上 | 每台 FS725 的 RS-232 接到它的板子 |
| RF-clock-PC | Ubuntu 22.04，台架网络上 192.168.2.1（`eno1`） | 台架主机：运行 `experiments/`、示波器和其他仪器、Vivado 2024.1、构建目录 `~/build/`、参考资料 `~/ref/RFSoC4x2-BSP` |

### 4.2 目前的台架接线（M5）

```text
 FS725 41175 ── 10 MHz ──> 887329260072A（A）的 REF SMA         FS725 107129 ── 10 MHz ──> 8873292600AB（B）的 REF SMA
             ── 1PPS ───> A 的 J7（A 用施密特触发器副本计时）                  ── 1PPS ───> B 的 J7
             ── 10 MHz ──> 示波器 C2                                          ── 10 MHz ──> 示波器 C1
                                                                              ── RS-232 <── B 的 USB 上的 USB-RS232
 A 的 DAC_A ──> ZVBP-5800-S+ ──> 衰减器 ──> B 的 ADC_B
 B 的 DAC_A ──> ZVBP-5800-S+ ──> 衰减器 ──> A 的 ADC_B      （一个方向 10 dB，另一个 20 dB；哪个在哪边没有记录）
 A 的 DAC_B（J20，输出 PPS）──> 示波器 C3（50 Ω）            B 的 DAC_B（J20）──> 示波器 C4（50 Ω）
 板子的以太网 ──> 交换机 ──> RF-clock-PC eno1；  板子的 USB（JTAG + 串口控制台）──> RF-clock-PC
```

这套接线的名字是 `cabled-20261103`，它的 θ₀ 是 −21.5 ps（[links/887329260072A_8873292600AB.json](../links/887329260072A_8873292600AB.json)）。改动任何射频线缆、衰减器或滤波器都会改变 θ₀，需要在那里新加一条（第 5.4 节）。目前的运行中示波器的 C3/C4 接输出 PPS；`--scope-pps` 的运行要改接 FS725 的 1PPS，经每个 J7 旁边的 T 型头引出。

### 4.3 访问

- **板子**：用户 `xilinx`（密码 `xilinx`，PYNQ 的默认值），RF-clock-PC 的 SSH 密钥；root 经 `sudo`。串口控制台在 RF-clock-PC 上，115 200 波特，`/dev/serial/by-id/usb-Xilinx_JTAG+Serial_<serial>-if01-port0`（绝不要往 `if00` 写，那是 JTAG 通道）。访问 PL 的程序以 root 运行：`run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"`。
- **RF-clock-PC**：实验用的项目目录是 `~/Program/RF-clock`，带它的 `.venv`；Vivado 从 `/tools/Xilinx/Vivado/2024.1/settings64.sh` 加载。示波器需要 root（`sudo`）。它经 NTP 给板子对时（chrony）：两个节点在约定的 Unix 时间启动各自的时隙引擎，所以两块板的系统时间必须相差远小于半秒（[docs/board_setup_zh.md](board_setup_zh.md) 第 1 节第 5 步）。
- **板子挂死**（PL 时钟关着时访问 PL 会挂死总线）：在 RF-clock-PC 上用 JTAG 复位，`xsdb tools/jtag_reset_board.tcl <JTAG serial>`，约 40 s；不行就断电重启。

### 4.4 代码、发布与数据

- **仓库**：在 GitHub 上是 `FHYQ-Dong/RF-clock`，在实验室的 Gitea 服务器（`kinabalu2.stdusr.yale.internal`，SSH 端口 2222）上是 `haoyu.dong/RF-clock`；`v2-rf-data-transfer` 分支是 rf-clock 2。二进制文件（bitstream、数据手册）在 Git LFS 里：克隆之后运行 `git lfs install` 和 `git lfs pull`。
- **发布**：推送一个 `vA.B.C.D` 标签就会构建 wheel 并在 GitHub 上发布（推送到 Gitea 时也在 Gitea 上发布）；板子只需要这个 wheel。
- **结果**：每个实验都写出 `results/<name>-<time>/`，含日志；主要问题还有 `REPORT.md`（和 `REPORT_zh.md`）；附录 E 列出了各份报告。`results/` 不在 Git 里。它在作者的电脑和 RF-clock-PC 上，实验室 NAS 上有一份镜像：
  - `smb://kinabalu2.stdusr.yale.internal/kinabalu/Haoyu/RF-clock/results`
  - `\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results`（Windows 下）

## 5. 运行与操作

### 5.1 在现有台架上运行链路

两块板按第 4.2 节的 M5 接线，空闲（没有别人在运行 PL 程序），装有 rf-clock 2.3.0.2。在 RF-clock-PC 上：

```bash
cd ~/Program/RF-clock
.venv/bin/python experiments/m4c_v2_link.py --scenario steer --seconds 1500 "--node-args=--pps-out 20" \
    "--a-args=--pps-trig --fs725" --scope --scope-out-pps
```

依次发生：

1. 大约两分钟后，两个节点在同一个 Unix 时间启动 `rf_clock.checks.m4c_link`。每个节点运行 `PL.open()`（bitstream、时钟、RFDC、MTS、PPS 锁定，约 20 s），按板级配置核对它的 FS725 并设定增益。
2. B 捕获 A（约 3 s；rf-clock 2.3.0.4 之前约 25 s），把自己的 PPS 移动整数个 tick，然后两个节点开始各自的时隙；两个 PL 在一秒内搜索并锁定。
3. B 改写 FC 来修正（steer 场景的默认做法）：它的 FS725 改到 GA 1，θ − θ₀ 在一两分钟内稳定下来。两个节点都在 J20 上输出输出 PPS（`--pps-out 20`：每个 PPS 边沿之后 20 个 tick）。
4. 加上 `--scope --scope-out-pps` 后，示波器每秒记录一次两路输出 PPS 和两路 10 MHz。
5. 结束时每个节点打印一行结果，B 的 FS725 改回 GA 8，日志汇集到 `results/m4c-v2-steer-<time>/`。

结束时打印的摘要给出最后 600 s 的 θ（它的均值应为 θ₀，标准差就是 θ − θ₀ 的 rms）。要改用 SF 修正，加上 `"--b-args=--steer-actuator sf --steer-step 0.2 --steer-late-slots 50"`。其他场景：`free`（不修正）、`sf-step`（SF 的一次阶跃，检查响应）、`acquire`（保留每一条队列条目），以及协议测试（脚本里的 `--scenario` 列表）。

### 5.2 读结果

每个节点的日志每行一个 JSON 对象（[docs/ABI_zh.md](ABI_zh.md) 第 13 节）：最前面是 `config`，然后每秒一行 `second`（这一秒的 θ、单向延时、它们的离散度、配对数），修正算法的每个 0.1 s 步长有 `steer` 和 `step` 行，FC 每改一次有一行 `fc_set`，补偿输出 PPS 有 `pps_comp`，最后是一行 `result`。在开发机上：

```python
from rf_clock.twtt import logs
b = logs.read("results/m4c-v2-steer-20261106_080117/m4c_B.jsonl", ("config", "second"))
print(logs.steering_error(b, settle_s=300))       # {'rms_ps': 5.56, ...}
```

`results/precision-20261105/fcsteer.py <run>` 分析一次带示波器的修正运行：θ − θ₀、修正算法用到的 FC，以及示波器上的 10 MHz 或输出 PPS 相对 θ 的关系。`experiments/m4c_check.py scenario results/<run>` 评判一次协议运行。

### 5.3 用示波器做外部检验

θ 是系统自己的测量；必须由外部仪器来证实。`experiments/m5_hdo_phase.py`（由 `m4c_v2_link.py --scope` 启动）每秒记录一两次：

- 默认：两台 FS725 的 10 MHz（C1 是 B 的，C2 是 A 的），它们的相位差随 θ 移动；
- `--pps`：两台 FS725 的 1PPS（C3 是 B 的，C4 是 A 的，经每个 J7 旁的 T 型头）；
- `--out-pps2`：两路输出 PPS（C3 是 A 的 J20，C4 是 B 的），以及每一路相对自己 10 MHz 的位置，它的离散就是示波器本身的分辨率（每条记录约 8 ps）。

### 5.4 新板子、新接线、台架有变动

[docs/board_setup_zh.md](board_setup_zh.md) 带一块新板子从 SD 卡走到链路，每一步都有命令和通过标准：系统和网络、安装 wheel、接线，然后按顺序做 tick 和 PPS（M1）、PPS 捕获余量、VCO 档位、MTS 捕获窗口、定时发射（M2）、回环（M3）、档位表；两台钟时还有 PPS 在 tick 里的位置、把 FS725 记进板级配置和修正。它的第 6 节说明每种改动之后要重做什么；简表如下：

| 改动 | 重做 |
| ------------------------ | ------------------------------------ |
| A.B.C 相同的新 wheel | 什么都不用 |
| 新 bitstream | MTS 窗口、M2、M3、档位表、θ₀ |
| PPS 线缆或 EPG1 延时 | M1、PPS 捕获余量、θ₀ |
| 射频线缆、衰减器或滤波器 | M3（如果是回环线）、θ₀ 作为 `links/` 里的新一条 |
| 板子换了一台 FS725 | `fs725_loop --write`（对 B 还有 FC 响应） |
| SD 卡重写 | 系统和安装，然后放回 `boards/<S/N>/board.json` |

新接线的 θ₀：两块板共用一台 FS725（[docs/board_setup_zh.md](board_setup_zh.md) 第 3 节的 EPG1 分配），在多次上电中运行 `experiments/m4c_v2_link.py --seconds 60`，θ 的均值作为 `links/` 里的新一条（[links/README_zh.md](../links/README_zh.md)）。

### 5.5 构建、版本与发布

1. **先定版本**（[docs/ABI_zh.md](ABI_zh.md) 第 14.1 节）：寄存器改动把 A.B 与 `PL/src/p_regs.vh` 里的 `VERSION` 一起升级，并在 `docs/ABI.md` 里写明；新的 bitstream 升 C；只改 Python 升 D。新版本的第一次修改之前，`pwsh -NoProfile -File tools/snapshot_version.ps1` 给已完成的版本做快照（在临时 worktree 里跑测试、类型检查并打 wheel，树存为 `refs/snapshots/<version>`）。
2. **RTL 测试**：`./PL/sim/run_pl_tests.ps1`（iverilog）。
3. **bitstream**，在 RF-clock-PC 上：`vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl -tclargs <new build dir> ~/ref/RFSoC4x2-BSP/board_files impl`（约一小时），然后 `python3 tools/package_overlay.py <build dir>`，它拒绝时序不满足或版本不对的构建。
4. **Python 检查**：`uv run pytest`、`uv run pyright`。
5. **装到板子上**，并做这次改动要求的检查（第 5.4 节）。
6. **提交和发布**：每个版本一个签名提交，由它的快照生成，然后推送签名标签 `vA.B.C.D`；发布工作流构建并发布 wheel。

### 5.6 保护硬件和测量的规则

- 板子在用 FS725 的 1PPS 时，绝不要用 `LO 0` 或 `GA 0` 停掉它的环路：设备会报告自己未锁定并停掉 1PPS（第 7.4 节）。改写 FC 来修正时用的是 GA 1。
- 绝不要写 FS725 的 EEPROM（以 `!` 结尾的命令）；rf-clock 从来不写，所以断电重启会恢复每台设备（它们的设置备份在 `boards/<S/N>/fs725-<unit>-20261106.json`）。
- 在 B 上装 2.3.0.2 或更新的版本：更早的版本会拒绝 B 的板级配置，因为其中有 FS725 的 FC 响应。
- 发射前，确认接线，并确认没有别人在用板子；没有带通滤波器的射频路径会给出错误的 θ（第 7.3 节），ADC 的输入也绝不能超过其最大值。
- PL 的时钟关着时不要访问 PL（会挂死板子，第 4.3 节）；`PL.open()` 按安全的顺序做事。
- 每一次射频改动都对应一个新的 θ₀；绝不要把新接线修正到旧的 θ₀ 上。
- 在空中（M6），发射功率和占用带宽必须符合 5.8 GHz ISM 频段的规定。

## 6. 结果

除注明外，所有数字都来自线缆连接的台架（第 4.2 节）。

### 6.1 测量噪声

两块板共用一个参考时（真实 θ 不变），θ 在相邻两对之间离散 0.81-0.87 ps，每 4 ms 一对；平均到一秒远低于 0.1 ps。单向延时在 12 次运行中为 292.0040-292.0050 ns。1.x 系统经以太网每秒交换一对，每秒离散 3.03 ps（[链路报告](../results/v2-link-20261103/REPORT_zh.md)、[M4 报告](../results/m4-twtt-20261021/REPORT_zh.md)）。每对的离散约一半来自转换器的时钟抖动，一半来自信噪比（[M5 报告](../results/m5-v2-20261103/REPORT_zh.md)第 7 节）。所以测量比钟自己一秒内的起伏精细 20-30 倍：修正受限于钟，而不是测量。

### 6.2 自由运行的钟

两台 FS725 自由运行时，θ 以它们的频率差（约 −65 ps/s）漂移，并在这条线附近起伏。θ 的 Allan 偏差（[M5 报告](../results/m5-v2-20261103/REPORT_zh.md)第 4 节；[精度报告](../results/precision-20261105/REPORT_zh.md)第 8 节）：

| τ | 0.1 s | 1 s | 2 s | 5 s | 20 s | 256 s |
| --- | --- | --- | --- | --- | --- | --- |
| σ_y，两台合起来 | 7 × 10⁻¹² | 1.6 × 10⁻¹¹ | 1.8 × 10⁻¹¹ | 8.5 × 10⁻¹² | 2.4 × 10⁻¹² | 4.3 × 10⁻¹³ |

1-2 s 处的鼓包，是每台设备的晶振已经起伏、而它的锁频环还没来得及拉回来的地方（第 2.3 节）。放慢任何一台的环路（B 到 GA 1，A 到 GA 5），1-2 s 处不变，5-20 s 处升高，所以 0.1-2 s 上的起伏是晶振自己的。

### 6.3 修正：每一步得到了什么

| B 的 FS725 的修正方式 | θ − θ₀，每秒 rms | 报告 |
| ---------------------------------- | -------------- | ------------ |
| Kalman 修正算法，τ 50 s，每 10 s 修正一次，经 SF | 66 ps | [M5](../results/m5-v2-20261103/REPORT_zh.md) 第 6 节 |
| Kalman，τ 5 s，每 2 s 一次，模型里加入 FS725 的延迟 | 39 ps（一整夜 5.5 h：均值 +0.1 ps） | 同上，第 6.5、8 节 |
| B 的锁频环用 GA 8（延迟减半） | 33-35 ps | [锁频环](../results/fs725-fll-20261105/REPORT_zh.md) |
| 预测修正算法，1 s 步长，θ 取每秒末尾 | 27-29 ps（一小时 28.6 ps） | [预测修正](../results/predictive-steering-20261105/REPORT_zh.md) |
| 预测修正算法，0.2 s 步长 | 14.7-16.8 ps | [双环](../results/two-loops-20261105/REPORT_zh.md)、[精度](../results/precision-20261105/REPORT_zh.md) |
| 预测修正算法，0.1 s 步长，改写 FC（B 处于 GA 1） | 5.34 和 5.56 ps | [精度](../results/precision-20261105/REPORT_zh.md) 第 8 节 |

每一步都缩短了修正算法需要预测钟的时间，或者把钟预测得更准。θ − θ₀ 平均到 60 s 为 1.7-1.9 ps；在数小时尺度上，θ 的均值保持在目标值上，偏差约 0.1 ps。

### 6.4 输出 PPS

在一块板上，相对它自己的 10 MHz，输出 PPS 每秒离散 7.7 ps，即示波器的分辨率，并按白噪声平均到 64 s 时的 1.0 ps；经过十次 PP 移动，它正好移动 PPS_TICK 移动的整数个 tick（偏差 3.1 ps rms），而 FS725 的 1PPS 偏了 40-260 ps（[输出 PPS 报告](../results/pps-out-20261104/REPORT_zh.md)）。两个节点之间，B 在被修正：

| B 的输出 PPS 相对 A 的 | 每秒 | 60 s 平均 |
| ---------------------------------- | -------------- | ------------ |
| 普通模式，改写 FC 来修正 B | 9.19 ps | 2.0 ps |
| θ 补偿模式，经 SF 修正 B | 8.75 和 8.62 ps | 0.92 和 1.00 ps |
| 示波器本身的分辨率（一路输出相对它的 10 MHz） | 7.8-8.2 ps | |

### 6.5 可重复性

- **多次上电**：1.x 中，零基线 θ 在 20 次上电中保持在 7.2 ps 以内（[M4 报告](../results/m4-twtt-20261021/REPORT_zh.md)）；目前这套接线的 θ₀ 在四次上电中标准差为 1.95 ps。背后的修正见第 7.2 节。
- **重新启动并重新捕获**：两路输出 PPS 之差，扣掉 θ − θ₀ 后，在两块板都重新 PL.open 并用 PP 重新捕获之后回到 0.9 ps 以内，而 FS725 的 1PPS 变了 145 ps（[输出 PPS 报告](../results/pps-out-20261104/REPORT_zh.md)第 8 节）；θ 补偿输出的常数回到 0.1 ps 以内（[精度报告](../results/precision-20261105/REPORT_zh.md)第 7 节）。
- **还没有测**：整个台架的断电重启、温度阶跃、持续数天的运行（第 8.1 节）。

### 6.6 现在受什么限制

θ − θ₀ 就是钟在执行器滞后期间无法预测的起伏。改写 FC 时，滞后是 FS725 的 1 s RC；仅凭 θ 自己的历史，提前 1 s 预测 θ 的误差为 12-13 ps，更长的 AR 模型和小型神经网络在样本外都预测得不比它好（剩下的是不可预测的噪声）。在重放里，告诉修正算法接下来 1 s 钟会怎样变化，它的误差就减半。所以剩下的出路是：一个更快的执行器加上一个更安静的振荡器，例如一台带快速调谐输入、由链路驯服的外部恒温晶振（OCXO）（第 8.3 节）；或者把晶振的响应测得更准（换一个不那么保守的修正算法；比较脆弱）。测量本身（每对 0.8 ps）不是限制。

## 7. 发现的问题与经验

这一节保留改变了测量或设计的问题，以及从中得到的经验；完整的因果链在 [docs/debug_overview_zh.md](debug_overview_zh.md) 和各份报告里。

### 7.1 台架

- **功分后的正弦参考太弱。** FS725 的 10 MHz 经电阻功分器后，每块板只剩 0.7 Vpp；LMK04828 进入保持模式，tick 慢了 0.1 ppm。改用 EPG1 锁定的方波输出后解决。*经验：参考路径有任何改动后，都要检查 tick 和 PPS（M1：每秒正好 100 000 000 个 tick）。*
- **未锁定的脉冲发生器会让 PPS 来回移动。** EPG1 用自己的时钟对触发重新定时；不锁定时，这个时钟相对板子漂移，PPS 就在 tick 之间来回移动。它的参考输入必须接 FS725，它的触发阈值必须离开它自己的时钟沿（1.3 V，而不是 1.0 V）。

### 7.2 时钟链：上电时会移动的皮秒

双向测量抵消两个方向共有的一切，所以上电时变化的时钟相位只会表现为 θ 的一个台阶。下面每一个都是作为这样的台阶被发现的，并被修正，使每次上电都进入同一个状态：

| 位置 | 什么在变 | 大小 | 修正 |
| ----------- | ---------------------- | -------- | ------------------ |
| LMK04828 PLL1 | 不在零延时模式：tick 相位没有绑定在 10 MHz 上 | 最多一个 tick | 嵌套零延时（1.2.1.1） |
| LMK04828 分频器 | PLL2 启动后没有同步，0.4 % 的上电 | 1.67-2.08 ns | 写完后做数据手册的 SYNC（[LMK 报告](../results/lmk-divider-sync-20261025/REPORT_zh.md)） |
| LMX2594 输出分频器 | 从任意一个 VCO 沿启动 | 26 ps 的台阶 | VCO 相位 SYNC（[LMX 报告](../results/lmx-phase-sync-20261019/REPORT_zh.md)） |
| LMX2594 VCO 档位 | 每次写时钟都重新校准，在两档之间 | 8-10 ps | 按板级配置强制档位（[回环报告](../results/lmx-sync-loopback-20261019/REPORT_zh.md)） |
| RFDC SYSREF 捕获 | 每次上电重新选窗口 | 一个 DAC 采样，104 ps | 按板记录捕获码（[M4 报告](../results/m4-twtt-20261021/REPORT_zh.md)第 4 节） |
| RFDC NCO | 在任意时刻启动 | 载波相位 120° | 在 SYSREF 沿上重启 |
| RFDC 延时 | 随 bitstream 变化 | 2 个采样 | 固定 MTS 目标值 |

*经验：只标定一次的常数，必须证明它在上电和写时钟之后保持不变；要在数百次上电中测试（一个 0.4 % 的事件用了 1200 次才看清）；芯片在启动时自己决定的东西，都要按板记录下来。*

### 7.3 射频路径

- **DAC 的 3.8 GHz 分量。** DAC 发出的 3.8 GHz 和 5.8 GHz 差不多强，4.8 GS/s 的 ADC 把两者折叠到同一个 1.0 GHz；每条路径里不放带通滤波器时，θ 每次运行离散 120-150 ps，而不是 1.5 ps（[带通滤波器报告](../results/bpf-compare-20261022/REPORT_zh.md)）。滤波器是设计的一部分。
- **包络相对载波移动。** 多次断电重启后，包络计时移动了 37-59 ps，而载波相位保持在 ±6 ps 以内；信道的群延时在 burst 的频带内变化 ±0.5-0.9 ns，所以它形状的微小变化就会移动包络（未解决，第 8.1 节）。

### 7.4 PPS 和 FS725

- **PPS 边沿落在采样沿上**时会在相邻 tick 之间跳：θ 在两次上电之间跳了 10 ns。余量必须测出来（`experiments/m1_pps_phase_scan.py`），并保持在约 3 ns 以上；A 用 PPS 的施密特触发器副本计时（`--pps-trig`），因为它的比较器副本落在采样沿上。
- **FS725 的 PP 会偏**，最多 0.23 ns，它的 1PPS 抖动 22-44 ps：在 1PPS 上测出的常数只保持到下一次 PP。这就是要做输出 PPS 的原因。
- **FS725 的锁频环决定了修正的延迟**（一个积分器围着一个 1 s 的滤波器）；它的模型必须是这台设备自己在所用增益下的（同一增益下一台的积分增益是另一台的 1.7 倍），由 `fs725_loop` 只用 FC 测出。
- **停掉环路会停掉 1PPS。** `LO 0` 或 GA 0 让设备报告自己未锁定，而它的 LOCK/1PPS 引脚只在锁定时输出脉冲（PRS10 手册第 20 页），于是一直停在高电平；B 的 PL 失去了 PPS，时隙引擎停了。GA 1 让设备保持锁定，并让写进去的 FC 保持。
- **上电以来没有收到过 SF 命令的设备，从校准电位器读 SF**，它的读数会在一个单位上跳动；第一条 SF 命令把 SF 接管过来，直到下一次重启。

### 7.5 测量方法

- **外部检验能发现 θ 看不到的东西**：两块板一起移动（共同的时钟链状态）对双向测量是不可见的；示波器看到了它。
- **示波器的坑**：LeCroy 的 math 迹线保留第一次计算时的量程（被截顶的迹线给出错误的延时），它的远程接口在 pyvisa-py 下会卡死；脚本处理了这两点。用载波给 burst 计时（“第 N 个峰”）会随载波相位移动，最多 10 ns；要用包络。
- **温度**：每块板的时钟随它的芯片温度每升高 1 K 滞后约 1-1.5 ps；芯片温度每秒记录一次。
- **先重放，再上台架**：把录下的自由运行的钟重放进修正算法，每次都把台架结果预测到约 1 ps 以内，并在不冒硬件风险的情况下发现了失败的方式（模型比 FS725 的响应快就会振荡）。

## 8. 未解决的问题与后续工作

### 8.1 台架上尚未解决的

- 用时间间隔计数器测 PPS 之差，以及输出 PPS 到 J20 的绝对延时（D_board），按板存放。
- 整个台架的断电重启、一次只给一块板做温度阶跃、持续数天的运行。
- 包络相对载波相位（第 7.3 节）：信道形状在哪里变化；是否改用载波相位给 θ 计时。
- 按目前的 θ − θ₀ 重新定 SYNCED 的阈值；J20 上交流耦合的脉冲够不够作为系统的 PPS，还是需要逻辑电平的 PPS。
- 共用参考的实验中，不经 EPG1 重新定时的 PPS 分配；强制的 VCO 档位在其他温度下是否锁定；887329260101 的档位表。

### 8.2 走向 M6：横跨长岛海峡 48 km

- **天线**：未定。长链路用两面抛物面天线；建议买一对 L-com HG4958-19DP 平板天线（5.85 GHz 时 17 dBi）做本地空中测试。收发分开的天线，还是一面天线加收发开关（PL 要在它的 tick 上驱动开关）。
- **路径**：在水面上，海面在掠射角下强烈反射，反射波只比直达波晚约 0.2 ns；大气波导和潮汐会改变路径。只有当每个节点的信道是互易的（两个方向同一面天线、同一频率，在 2 ms 乒乓之内），双向测量才能抵消路径；PL 搜索报告的最早峰就是为多径准备的。
- **链路预算和测量包**：测量包的扩频因子（现在每位 64 个码片）和接收窗口长度，取决于测出的信噪比，以及距离知道得有多准（A 第一次抓到 B 需要延时在 ±3.4 µs 以内）。
- **外部检验**：隔着 50 km 的海面不能用同一台示波器：需要在两地之间搬运一台钟，或者用 GNSS 共视。
- **法规**：5.8 GHz ISM 频段的发射功率和占用带宽。

### 8.3 更高的精度

- **在 B 上用更快、更安静的振荡器**：一台带几十 Hz 带宽电子调谐输入的 OCXO，由链路驯服（链路断开时由它的 FS725 驯服），可以把执行器的滞后从 1 s 缩短到约 0.1 s，在这个时间上预测误差约 1 ps，而且它在 1 s 上比 PRS10 里的晶振安静；估计可达 1-2 ps（推断）。这改变了“每个节点的时钟都由它的 FS725 导出”这条设计规则（[AGENTS.md](../AGENTS.md)），需要决定。
- **第三台参考钟**，用三角帽法分清是哪一台的晶振占主导。
- **把晶振的响应测准**（滞后和增益到 ±2 %），换一个不那么保守的修正算法（重放中约 3-4 ps；比较脆弱）。

### 8.4 安全性与鲁棒性

[plan/ideas_zh.md](../plan/ideas_zh.md) 讨论了：用共享密钥让码每个时隙都变化（这样没人能在真 burst 之前发出它）、给测量包加认证码、把单向延时与它的标定值比较，以及经环形器共用一条互易的射频路径。它的写法早于 rf-clock 2（假设的是 1.x 经以太网的交换）；rf-clock 2 中记录在 burst 里传递，所以认证应该加在测量包上。

## 9. 交接清单

作者离开会改变什么，以及实验室应该做什么：

| 项目 | 现在 | 要做的 |
| ----------- | ------------------------- | ------------------------- |
| 仓库 | GitHub 上在个人账号下（`FHYQ-Dong/RF-clock`）；实验室 Gitea 上有一份（`haoyu.dong/RF-clock`） | 把两者迁到或 fork 到实验室所有的组织下；以实验室的那份作为发布来源 |
| `results/` | 在作者的电脑和 RF-clock-PC 上；NAS 上的镜像由作者的电脑每 4 小时更新一次 | 镜像会停止；保留 NAS 上的那份作为存档；新的实验写在 RF-clock-PC 上 |
| RF-clock-PC 的项目目录 | `~/Program/RF-clock` 由 Syncthing 与作者的电脑同步，没有 Git 元数据 | 在那里重新克隆仓库（含 Git LFS），建好它的 `.venv`（`uv sync`），停掉 Syncthing 共享 |
| SSH 访问 | RF-clock-PC 上有一个 AI 工具用的 SSH 密钥（`RF-clock-PC-ai`）；板子接受 RF-clock-PC 的密钥 | 删掉不再需要的密钥；给实验室成员在 RF-clock-PC 上开自己的账号 |
| 板级配置 | 在每块板的 SD 卡上，`boards/` 里有副本 | 每次标定后保持副本最新 |
| 签名 | 提交和标签用作者的密钥签名 | 实验室成员用自己的密钥签名 |
| 发布的 wheel | 在 GitHub 和 Gitea 上 | 从实验室的仓库继续发布 |

## 10. 参考文献

仓库中或网上的数据手册和手册：

1. Stanford Research Systems，*PRS10 Rubidium Frequency Standard, Operation and Service Manual*（`docs/PRS10m.pdf`），以及 *FS725 Rubidium Frequency Standard* 手册。
2. Real Digital，*RFSoC 4x2* 参考手册和原理图（`docs/rfsoc4x2_sch.pdf`）；板级文件 `RealDigitalOrg/RFSoC4x2-BSP`。
3. AMD/Xilinx，*Zynq UltraScale+ RFSoC RF Data Converter LogiCORE IP Product Guide*（PG269），多 tile 同步。
4. Texas Instruments，*LMK04828* 和 *LMX2594* 数据手册。
5. Mini-Circuits，*ZVBP-5800-S+* 数据手册。
6. IEEE Std 1139，*Standard Definitions of Physical Quantities for Fundamental Frequency and Time Metrology*，Allan 偏差的定义。
7. D. W. Allan、N. Ashby、C. C. Hodge，*The Science of Timekeeping*，HP Application Note 1289（1997），入门读物。
8. ITU-R TF.1153，*The operational use of two-way satellite time and frequency transfer employing pseudorandom noise codes*。
9. H. Hong 等，“Quantum two-way time transfer over a 103 km urban fiber”，J. Lightwave Technol. 42, 1479 (2024)。
10. L. Narula、T. E. Humphreys，“Requirements for secure clock synchronization”，IEEE J. Sel. Topics Signal Process. 12, 749 (2018)。

项目文档：[plan/RF_clock_modules_zh.md](../plan/RF_clock_modules_zh.md)、[docs/ABI_zh.md](ABI_zh.md)、[docs/board_setup_zh.md](board_setup_zh.md)、[docs/debug_overview_zh.md](debug_overview_zh.md)、[docs/fs725_principles_zh.md](fs725_principles_zh.md)、[plan/pps_output_zh.md](../plan/pps_output_zh.md)、[plan/checklist_zh.md](../plan/checklist_zh.md)、[plan/ideas_zh.md](../plan/ideas_zh.md)、[docs/handoff_2x_zh.md](handoff_2x_zh.md)、[AGENTS.md](../AGENTS.md)。

## 附录 A. 术语表

| 术语 | 含义 |
| --------------- | --------------------------------------------- |
| θ（theta） | 同一时刻 B 的钟读数减 A 的，由双向测量得到 |
| θ₀ | 两块板共用一台钟时的 θ：B 被修正到的目标；按板子对和接线存放（`links/`） |
| θ − θ₀ | B 被修正时：两台钟的相对起伏中环路还没有去掉的部分 |
| 起伏 | 自由运行的钟在它们漂移线附近的移动 |
| 双向时间传递（TWTT） | 双向发送计时信号，使路径延时抵消 |
| 单向延时 | (m_AB + m_BA)/2：路径加硬件延时，一个诊断量 |
| 非互易性 Δ | 两个方向硬件延时之差 |
| 时隙 | 乒乓中 2 ms 的一个轮次，由 `PPS_COUNT` 和时隙编号（0-499）标识 |
| burst | 节点在自己的时隙里发射的东西：PRN、保护间隔、测量包 |
| 窗口 | 节点在对方时隙里接收的那一段 |
| 测量包 | burst 中 PRN 后面的数据：发送方最近的测量和它的发送时刻 |
| 捕获 | 时隙开始之前，B 在长窗口里找到 A 的秒；在这之前 burst 用一个固定的已知码 |
| 跟踪器、搜索 | PL 在每个窗口里围绕预期峰值的相关；失锁时的整窗相关 |
| PRN、码片、RRC | 伪随机码；它的一个 ±1 符号（10 ns）；脉冲成形滤波器 |
| tick | PL 100 MHz 时钟的一个周期（10 ns）；`PPS_TICK` 是一秒边沿所在的 tick，`PPS_COUNT` 是秒的编号 |
| 输出 PPS | PL 按自己的 tick 在 DAC_B（J20）上产生的 PPS 边沿；不是 FS725 的 1PPS，后者是节点的 PPS 输入 |
| 普通 / θ 补偿输出 PPS | 相对 10 MHz 位置固定的输出 PPS / 每个边沿前按 θ − θ₀ 移动的输出 PPS |
| FS725、PRS10 | 铷频标；它里面的振荡器 |
| 锁频环（FLL） | FS725 把晶振保持在铷跃迁上的环路（不是它的 1 pps 锁相环，PL） |
| SF、FC、GA、PP | FS725 的频率偏移命令；它晶振的调谐量；它的环路增益；它的 1PPS 位置 |
| 改写 FC 来修正 | 预测修正算法在 B 的设备处于 GA 1 时设定 FC（`--steer-actuator fc`） |
| 预测 / Kalman 修正算法 | 两种修正方式：`PredictiveSteering`（模型、预测、规划改动）和 `Steering`（Kalman 滤波器、时间常数） |
| Allan 偏差、TDEV | 在平均时间 τ 上频率 / 时间误差的稳定度 |
| PS、PL、RFDC | RFSoC 的 ARM 处理器、FPGA 逻辑和射频数据转换器 |
| MTS、SYSREF | 多 tile 同步；它用来对齐各转换器 tile 的公共信号 |
| VCO 档位 | LMX2594 的 VCO 校准到的档位；按板强制 |
| 捕获码（T1 / PLL 捕获码） | RFDC tile 捕获 SYSREF 的位置；按板记录 |
| MTS 偏移 | MTS 为让 tile 达到目标延时而加的延时 |
| 回环延时 | 一块板自己 DAC → 线缆 → ADC 的延时（M3） |
| 板级配置 | `/etc/rf-clock/board.json`：属于一块板的东西 |
| M0-M6 | 设计的里程碑，第 1.3 节 |
| EPG1 | 用来分配同一个参考的 Thorlabs 脉冲与延时发生器 |

## 附录 B. 命令速查

在板子上（`run` 见第 4.3 节）：

```bash
$run -m rf_clock.checks.m1_check 60                    # tick 对 PPS
$run -m rf_clock.checks.m2_tx_timing 10                # MTS 和定时 burst
$run -m rf_clock.checks.m3_loopback 20                 # 回环延时（自己 DAC_A -> ADC_B 的线缆）
$run -m rf_clock.checks.vco_bands scan --write --label <S/N>
$run -m rf_clock.checks.mts_dtc record --write; $run -m rf_clock.checks.mts_dtc check
$run -m rf_clock.checks.fs725_loop --port PORT --gain 8 --write   # FS725、端口和环路记进板级配置
$run -m rf_clock.checks.pps_pp_scan --fs725 PORT       # PPS 边沿在 tick 里的位置
$run -m rf_clock.fs725 status --port PORT              # FS725 的状态；还有：gain N、sf N
$run -m rf_clock.checks.pps_out 60 --offset 20 --amplitude -24000   # 单独输出输出 PPS
```

在 RF-clock-PC 上（`cd ~/Program/RF-clock`）：

```bash
.venv/bin/python experiments/m4c_v2_link.py --scenario steer --seconds 1500 "--node-args=--pps-out 20" \
    "--a-args=--pps-trig --fs725" --scope --scope-out-pps
.venv/bin/python experiments/m4c_v2_link.py --scenario free --seconds 600 "--a-args=--pps-trig" --scope
.venv/bin/python experiments/m4c_check.py scenario results/<运行>
.venv/bin/python experiments/m5_pps_frac.py            # 在 B 上标定输出 PPS 的分数
.venv/bin/python experiments/m5_fll_off.py             # B 的晶振处于 GA 1 及其对 FC 的响应
sudo .venv/bin/python experiments/m5_hdo_phase.py --seconds 600 --out results/x/hdo.jsonl [--pps|--out-pps2]
.venv/bin/python experiments/epg1.py status
source /tools/Xilinx/Vivado/2024.1/settings64.sh; xsdb tools/jtag_reset_board.tcl <JTAG serial>
```

在开发机上：

```bash
uv sync; uv run pytest; uv run pyright; ./PL/sim/run_pl_tests.ps1
uv run python -m rf_clock.examples.twtt.02_fake_steer
```

## 附录 C. 故障排查

| 现象 | 可能的原因 | 怎么办 |
| ------------------- | ------------------- | ---------------------- |
| SSH 连不上板子 | 一次 PL 访问挂死了总线 | JTAG 复位（第 4.3 节）；不行就断电重启 |
| `PL.open()`：`BringupError: PL pps_locked not set` | J7 上没有 PPS，或者没有 10 MHz | 跑 M1；检查线缆 |
| `BringupError: LMX2594 not locked` | 某个强制的 VCO 档位不锁（温度？） | 重做 `vco_bands scan --write`、`mts_dtc record --write` 和档位表 |
| `BoardConfigError` | 配置是另一块板的、格式错误，或来自更新的版本 | 核对 S/N；在 B 上装 2.3.0.2 或更新的版本 |
| 关于 MTS 偏移或捕获码的警告 | 转换器以另一个状态启动 | 重新打开 PL；仍然出现就重做 `mts_dtc record` |
| θ 在两次上电之间跳 10 ns | PPS 边沿靠近采样沿 | 测 PPS 捕获余量；移动边沿（EPG1 延时、`--pps-trig`、PP） |
| θ 离散几十到几百 ps | 某条路径上没有带通滤波器，信号弱 | 检查滤波器、衰减器和质量指标 |
| 链路一直锁不上 | 单向延时不对、对方没在发射、线缆接错 | 检查 `--arrival-ticks`、接线、另一个节点的日志 |
| 修正停了（`steer_stopped`） | FS725 重启了、失锁了，或被别人改了 | 看原因；用 `rf_clock.fs725 status` 检查设备 |
| 发了一条 FS725 命令后 B 的时隙引擎停了 | FS725 的环路被停掉了（`LO 0`/`GA 0`）：没有 1PPS | `GA 8`（或配置里的增益）和 `LO 1` |
| `PL.open()` 拒绝 bitstream | 包和 bitstream 的版本不一致 | 装对应的 wheel |

## 附录 D. 仓库结构

| 路径 | 内容 |
| ---------------------- | -------------------------------------- |
| `PL/src/`、`PL/sim/`、`PL/bd/`、`PL/constraints/` | RTL（P0-P10；`p_regs.vh`、`p_defs.vh`）、testbench、Vivado 块设计脚本、约束 |
| `src/rf_clock/` | Python 包（第 3.9 节）；`overlay/` 是 bitstream；`clocks/` 是 LMK/LMX 寄存器文件 |
| `tests/` | 单元测试（不需要硬件） |
| `experiments/` | 在 RF-clock-PC 上运行的台架实验，`m<N>_*.py` 各回答里程碑 M<N> 的一个问题 |
| `tools/` | ABI 生成器、overlay 打包、发布说明、版本快照、结果备份、JTAG 复位 |
| `boards/` | 每块板的配置和它 FS725 的设置 |
| `links/` | 按板子对和接线存放的 θ₀ |
| `docs/` | 本报告、ABI 手册、新板子搭建、调试总览、FS725 原理、1.x → 2.x 交接文档、数据手册 |
| `plan/` | 设计、清单、输出 PPS 提案、想法 |
| `results/`（不在 Git 里） | 每一次运行及其报告；NAS 镜像（第 4.4 节） |

## 附录 E. 报告索引

| 报告 | 日期 | 问题与答案 |
| ---------------- | ----- | ---------------------------------------- |
| [dac-spectrum](../results/dac-spectrum-20261017/REPORT_zh.md) | 10-17 | DAC 的输出频谱：3.8 GHz 和 5.8 GHz 差不多强 |
| [pps-phase-scan](../results/pps-phase-scan-20261018_203329/README_zh.md) | 10-18 | 每块板的 PPS 边沿在它的 tick 里的位置 |
| [lmx-phase-sync](../results/lmx-phase-sync-20261019/REPORT_zh.md) | 10-19 | LMX2594 分频器造成的半采样计时状态；相位 SYNC 修正 |
| [lmx-sync-loopback](../results/lmx-sync-loopback-20261019/REPORT_zh.md) | 10-19 | 经回环看同一问题；VCO 档位的 8-10 ps；NCO 相位 |
| [vco-bands](../results/vco-bands-20261020_214500/README_zh.md) | 10-20 | 887329260072A 的 VCO 档位扫描和各档延时 |
| [m2-restarts](../results/m2-restarts-20261016_212509/REPORT_zh.md) | 10-16 | 105 次重启中的 burst 定时 |
| [m4-twtt](../results/m4-twtt-20261021/REPORT_zh.md) | 10-21 | 1.x 在一台 FS725 上的双向传递：每秒 3 ps；SYSREF 窗口翻转；温度 |
| [bpf-compare](../results/bpf-compare-20261022/REPORT_zh.md) | 10-22 | 用哪种带通滤波器：必须去掉 3.8 GHz 镜像 |
| [lmk-divider-sync](../results/lmk-divider-sync-20261025/REPORT_zh.md) | 10-25 | LMK04828 分频器造成的偶发 1.7-2.1 ns 跳变，及其修正 |
| [pl-fft-fixed-point](../results/pl-fft-fixed-point-20261031/README_zh.md) | 10-31 | PL 里的 FFT 相关器（研究过，没有采用） |
| [v2-link](../results/v2-link-20261103/REPORT_zh.md) | 11-03 | 两块板上的 rf-clock 2：每对 0.8 ps，协议测试，三个故障 |
| [m5-v2](../results/m5-v2-20261103/REPORT_zh.md) | 11-03 | 两台钟：捕获、PPS 在 tick 里的位置、第一次修正（66 → 39 ps）、两路 PPS |
| [pps-out](../results/pps-out-20261104/REPORT_zh.md) | 11-04 | 台架上的输出 PPS：相对它的 10 MHz 7.7 ps，重启前后 0.9 ps |
| [fs725-fll](../results/fs725-fll-20261105/REPORT_zh.md) | 11-05 | 经 FC 看 B 的 FS725 锁频环；GA 8 |
| [predictive-steering](../results/predictive-steering-20261105/REPORT_zh.md) | 11-05 | 预测修正算法：27-29 ps |
| [two-loops](../results/two-loops-20261105/REPORT_zh.md) | 11-05 | 两个环路与 θ 并排；0.2 s 步长的修正算法：15 ps |
| [precision](../results/precision-20261105/REPORT_zh.md) | 11-05/06 | 什么限制了修正；θ 补偿输出 PPS；改写 FC 来修正：5.3-5.6 ps |
