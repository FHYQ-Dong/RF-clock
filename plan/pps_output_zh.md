# 提议：由 PL 从它的 tick 产生输出 PPS

英文原文：`plan/pps_output.md`。已在 rf-clock 2.1.0.0 中实现为模块 P10（`plan/RF_clock_modules_zh.md` 第 6 节，寄存器见 `docs/ABI_zh.md` 第 4.13 节）；决定见第 7 节，实现了什么、与提议有哪些不同见第 8 节。第 5 节的台架测试还没有做。

## 1. 问题

跟随方的 PPS 要跟随主方的（M5，计划 §9）。现在每个节点的 PPS 是它那台 FS725 的 1PPS 输出；2026-11-04 的两项测量（`results/m5-v2-20261103/REPORT_zh.md` 第 9.5、9.6 节）说明了这样做的代价：

1. 双向链路对齐的是每个节点的时间尺度，它从锁存这个节点 PPS 的那个 tick 开始：T = P + ℓ + d（P 是 FS725 输出端的边沿，ℓ 是电缆和输入通路，d 是边沿比锁存它的 tick 早多少）。修正保持的是比较 T_A 和 T_B 的 θ；只有 ℓ 和 d 不变时，PPS 边沿 P 才跟着走（实测：PPS_B − PPS_A 以斜率 −1.1 随 θ 变化）。
2. 捕获用 PP 移动 B 的 FS725 的 1PPS。PPS_TICK（以及 T）正好移动要求的整 tick 数，但 1PPS 移动的是 PP 实际做到的量，与指令最多差 0.23 ns（151 ps rms；对照设备自己的 10 MHz 测得）。这个误差进入 d_B，所以在给定 θ 下标定的常数 P_B − P_A 每做一次 PP 就变一次（实测：两次运行之间变了 −145 ps）。
3. FS725 的 1PPS 本身相对它自己的 10 MHz 逐秒抖动 22 ps（A 那台）和 34 ps（B 那台）（实测），而由转换器时钟得到的 θ 跟随 10 MHz 到几 ps。

一个由 T 本身产生的输出边沿不受这两者影响：它跟随 θ 对齐的时间尺度，所以 PP 的误差进不去；它由转换器的时钟定时，而不是由 FS725 的 1PPS 电路。FS725 的 1PPS 仍是现在的角色，即启动时间尺度的那个输入。

## 2. 机制

```text
FS725 1PPS ──> J7 ──> P1：PPS_TICK(k) ─────────────┐
FS725 10 MHz ──> LMK04828 ──> fabric 时钟：raw_tick ┴─> P10：在 raw_tick = PPS_TICK(k) + OFFSET 时
                         └──> LMX2594 ──> DAC tile 0 采样时钟            │ 在 DAC tile 0 的数据流上输出一个阶跃的若干拍
                                                       └──> DAC_B ──> 100 nF ──> 巴伦 ──> 100 nF ──> J20（SMA）
```

1. P1 在 tick 上锁存每个 PPS 边沿：PPS_TICK(k)，即节点时间尺度第 k 秒的起点。PPS 锁定时，每个周期正好 100 000 000 个 tick（P1 逐个检查）。
2. P10 从开始第 k 秒的那个边沿起数这一秒的 tick：锁定的边沿把它的计数器设为 raw_tick − PPS_TICK(k)，在 raw_tick = PPS_TICK(k) + OFFSET 时它在 DAC tile 0 的数据流上开始一个阶跃。两个边沿之间计数器自己往下数，每 100 000 000 个 tick 回绕一次，所以某个 PPS 边沿缺失或出错时（这时 P1 失锁，这个边沿什么也不设），输出从最后一个正常的边沿起按 100 000 000 tick 的步长继续（飞轮；计数）。边沿在锁存它的 tick 之后三个 tick 才到达 P10，所以 OFFSET 至少为 8（第 8 节）。
3. 阶跃从它所在那一拍的第 SAMPLE 个采样（0 到 7）开始，所以输出边沿在 T(k) + OFFSET × 10 ns + SAMPLE × 1.25 ns + L，L 是从 PL 数据流到 DAC 引脚的延时。MTS 在每次上电时固定 L（同一机制让 DAC_A 的突发相对 PPS 的时刻在多次上电间保持在约 10 ps 以内，`results/m2-restarts-20261025_162709`）。
4. DAC tile 0（block 0）现在只为 MTS 存在：它和 tile 2 配置相同（9.6 GS/s、NCO 3.8 GHz、mix-mode），数据流接死为零（`PL/bd/create_rf_clock_bd.tcl`）。2.1 起它的数据流来自 P10，`PL.open()` 在 MTS 之后把它切到基带：旁路混频器、IMR 低通、第 1 奈奎斯特区（`rf_clock.pl.rfdc.baseband_dac`，`rf_clock.checks.m2_tx_timing --scope-nco 0` 对 tile 2 也用它）；这时 DAC 输出数据流的 I 采样，阶跃约一纳秒上升（内插滤波器的带宽，推断）。
5. 输出网络（RFSoC 4x2 原理图，`docs/rfsoc4x2_sch.pdf` 第 28 页 "RF DAC"）：DAC_VOUT0_228 → 每条腿 100 nF → MABA-011118 巴伦（T5）→ 100 nF → J20，标为 DAC_B。这条通路是交流耦合的：边沿能通过（M2 里 DAC_A 输出的 25 MHz 方波边沿干净），微秒级的平顶不能，所以输出是几十 ns 的短脉冲。DAC_A 的输出相对 I 是反相的（checklist 第 5 节）；DAC_B 大概也一样。
6. 输出边沿相对节点时间尺度的时刻是 T + OFFSET + D_board，D_board = L 加上模拟通路：一个板级常数，测一次（示波器或时间间隔计数器），存入板级配置。它的抖动就是转换器时钟的抖动：θ 每对中不随信号变化的 0.72 ps 包含四个转换器和参考（报告第 7 节），每个转换器约 0.5 ps（推断）。

## 3. 信号离开板子的其他途径

| 途径 | PL 和连接器之间有什么 | 用作精密 PPS |
| --- | --- | --- |
| DAC_B，J20（第 28 页 "RF DAC"） | RF DAC tile 0、交流耦合、巴伦 | 由采样时钟定时；交流耦合的脉冲，50 Ω 上几百 mV |
| Pmod+（手册 "Pmod+ Port" 第 15 页；附录 A 第 17 页；原理图第 5 页 "PMOD HEADERS"） | FPGA 引脚（LVCMOS18）→ NVT2008BQ 无源电平转换器，1.15 kΩ 上拉到 3.3 V | 上升沿靠上拉给负载充电（几十 ns，随负载变），只有下降沿是主动驱动的；只能接高阻负载；由经过 MMCM 的 fabric 时钟和 I/O 缓冲器定时（几十 ps，推断） |
| SYZYGY（原理图第 6 页 "SYZYGY HEADER"） | 直接是 FPGA 的 HP 引脚，可以用 LVDS | 需要 SYZYGY 转接板，台架上没有 |
| PL_CLK / CLK OUT | LMK 的输出，不经过 PL | 不带 tick；板上那个未焊的 "Clk_Out" SMA 没有装 |

## 4. 设计

- **P10 pps_out**，在 fabric 时钟域、P1 旁边：按第 2 节第 2 步安排；带阶跃的那一拍（从 SAMPLE 起 I 采样为 AMPLITUDE，Q 为零），然后 WIDTH − 1 拍 AMPLITUDE，然后为零；TVALID 一直为高（2.1 之前 tile 0 的数据流接死为零）。飞轮计数是在不由锁定边沿开始的那些秒里发出的脉冲数（边沿缺失，或者边沿偏离周期，这会让 P1 失锁），另外计已发出的脉冲数。
- **寄存器**（按实现，`docs/ABI_zh.md` 第 4.13 节，0x140 块）：PPS_OUT_CONTROL（ENABLE；SYNCED，即 ARM 的"已同步"位）、PPS_OUT_OFFSET（tick，8 到 99 999 999）、PPS_OUT_SAMPLE（0 到 7）、PPS_OUT_WIDTH（拍，1 到 65535）、PPS_OUT_AMPLITUDE（有符号 16 位），以及只读的 PPS_OUT_STATUS、PPS_OUT_CNT、PPS_OUT_FLYWHEEL_CNT 和 PPS_OUT_TICK（上一个脉冲的 tick）。改动在下一秒生效，SYNCED 立即生效。只新增寄存器：VERSION 2.0 → 2.1，rf-clock 2.1.0.0（docs/ABI_zh.md 第 14 节）。
- **Block design**：tile 0 的数据流来自 P10，而不是接死为零。
- **Python**：`pl.pps_out`（配置、使能、已同步位、状态）；tile 0 的基带设置从 `m2_tx_timing` 移到 `rf_clock.pl.rfdc`，由 `PL.open()` 完成；D_board 测出后存入板级配置（`/etc/rf-clock/board.json`），和其他标定常数一样。
- **Pmod+ 排针上的有效状态线**（2026-11-04 决定）：使用输出 PPS 的其他设备必须知道什么时候不该信它。P10 驱动一个 Pmod 引脚（PMOD0_0，FPGA 引脚 AF16，J1 第 1 针），输出 PPS 无效时为高：PPS 输入没有锁定、输出在飞轮、时钟失锁、DAC tile 0 不接收数据流，或者 ARM 的"已同步"位（PPS_OUT_CONTROL）没有置位；链路锁定且修正收敛后由 ARM 置位。高电平表示无效，是因为 FPGA 不驱动这个引脚时（没有 bitstream、板子断电），NVT2008BQ 到 3.3 V 的上拉会把它拉高，所以一块无法担保自己 PPS 的板子读出来就是无效。对很少变化的电平来说，Pmod 慢的上升沿（第 3 节）无所谓。用户 LED W_LED_0（FPGA 引脚 AR11，丝印 PL_LED0）在输出 PPS 有效时点亮。
- **不变**：链路（DAC_A，tile 2）、时隙引擎，以及作为 PPS 输入的 FS725 的 1PPS。

## 5. 怎样检验

1. 仿真（iverilog）：阶跃出现在第 2 节给出的那一拍、那个采样；缺一个边沿时的飞轮；未使能时什么都没有。已完成，见第 8 节。
2. 一块板，B（它的 FS725 受它控制，PPS 捕获裕量已知，5.0-5.5 ns）：J20 接示波器，和 B 的 10 MHz、B 的 FS725 1PPS 一起看。输出边沿相对 B 的 10 MHz 必须 (a) 逐秒保持在约一皮秒以内，(b) 经过 B 的 FS725 的 PP 步进时正好按整 tick 移动（FS725 的 1PPS 会多移或少移最多 0.23 ns），(c) 经过 `PL.open()` 和上电保持不变，(d) 对照芯片温度。
3. 两块板，修正中：两路输出都接示波器；它们之间标定一次的常数，必须在多次重新捕获（带 PP）和上电之后保持不变，而 FS725 的 1PPS 在这里丢了 145 ps。

## 6. 代价

RTL（P10 和它的测试平台，约 150 行），`p_regs.vh` / `p_defs.vh` / `docs/ABI.md` 里的寄存器表，block design 的一处改动，一次 Vivado 编译（在 RF-clock-PC 上约一小时），约 100 行 Python，以及每块板 J20 到示波器的一根同轴电缆（单板测试时接 C2 或 C4，替换 A 的一路信号）。

## 7. 决定

已决定（用户，2026-11-04）：

- 每秒一个脉冲。
- 没有 PPS 锁定时输出继续，靠飞轮，计数，并由第 4 节的有效状态线表示它无效。
- 这个边沿的术语"输出 PPS"（output PPS）；已加入 `AGENTS.md` 的术语表。

已决定（用户，2026-11-05）：

- θ 补偿模式，等下次因为别的原因编译 bitstream 时一起加入：PPS_OUT_CONTROL 里一个默认清零的位，置位后 B 的输出沿还会按 ARM 写入的修正误差移动（沿的分数延迟，作为阶跃的第一个 beat 写入），这样输出 PPS 跟随 A 的时间尺度的精度取决于测量，而不是修正。普通模式仍是默认：此时输出沿相对节点自己的 10 MHz 位置固定，同时使用两者的设备可能依赖这一点。White Rabbit 主节点从 10 MHz 取细相位，PPS 只用来选周期，所以这个模式对它没有好处。
- 由 `m4c_link` 置位 SYNCED（rf-clock 2.2.0.4，`SyncedRule`）：节点 A 在链路锁定、能收到 B 的测量包时置位；节点 B 在链路锁定、修正在运行，并且修正误差 60 s 的 rms 小于 100 ps、均值小于 30 ps 时置位，链路或修正停止、或 10 s 的 rms 超过 200 ps 时清除（选项 `--synced-rms`、`--synced-mean`、`--synced-clear-rms`）。阈值要按台架的数据再定：用预测控制律时，60 s 的 rms 不超过 38 ps，10 s 的 rms 不超过 68 ps。

待定：

1. 50 Ω 上几百 mV 的交流耦合脉冲能否作为系统的 PPS 输出，还是必须有逻辑电平的 PPS（J20 上加一个外部缓冲器，或者由同一个 P10 从 Pmod 输出一个纳秒级精度的标记）？

## 8. 实现（rf-clock 2.1.0.0，2026-11-04）

- `PL/src/p10_pps_out.v` 用每一秒自己的边沿安排这一秒的脉冲，而不是像最初提议的那样用前一个边沿。PPS 锁定时两种做法给出同一个 tick，因为这时 PPS_TICK(k) = PPS_TICK(k−1) + 100 000 000。它们的区别在 OFFSET 的下限：边沿在锁存它的 tick 之后三个 tick 才到达 P10（P1 的两级同步器和它的寄存器），所以 OFFSET 至少要是 8（`PPSOUT_MIN_OFFSET`，更小的值 P2 拒绝），边沿才会在它的脉冲之前到达，输出边沿也就不能落在锁存的那个 tick 里。没有什么需要那个 tick：输出边沿无论如何都在时间尺度之后 D_board，而 D_board 是要标定的。换来的是：不由锁定边沿开始的那一秒能被直接认出来，这正是飞轮计数和有效状态线需要的；PPS 提前时，同一秒里就跟着新的边沿走。
- 边沿把计数器设得比飞轮算的晚时（PPS 推后了，在一次 PP 和重新锁定之后），这一秒的脉冲可能已经发出；P10 与上一个脉冲相隔不到半秒时不发脉冲，所以它不会重发。
- 有效状态线还要求 DAC tile 0 的 TREADY，所以不接收数据流的 DAC 读出来是无效。
- 仿真，2026-11-04：`PL/sim/tb_p10_pps_out.v`（一秒 1000 个 tick）检查：脉冲在秒的起点 + OFFSET，阶跃和宽度正确；缺一个边沿时的飞轮脉冲和有效状态线；PPS 推后 25 个 tick 时没有第二个脉冲；提前 30 个 tick 时跟着新的边沿，并在这个提前的边沿上取用新配置（这一秒计数器不回绕，所以 P10 在每个锁定的边沿上也取一次配置）；新配置在下一秒生效；未使能时什么都没有；SYNCED 和 TREADY 对有效状态线的作用；每秒起点处有效状态线不闪。手工在 RTL 里放进四个错误（去掉半秒保护；有效状态线不给边沿留那三个 tick；计数器差一个 tick；只在计数器回绕处取配置），每一个都让它失败。`PL/sim/tb_p_top.v` 检查经 P2 访问的寄存器（超出范围的值被拒绝、回读）、tile 0 数据流上 PPS_TICK + 50 处的脉冲、PPS_OUT_TICK 以及有效状态线和 LED 引脚；PPS_CONFIG 改写后 P1 重新锁定期间发出的脉冲算作飞轮脉冲。
- Bitstream：`RF-clock-PC:~/build/rf_clock_v2_run5`（时序满足，WNS 0.245 ns），已随 rf-clock 2.1.0.0 打包。
- rf-clock 2.2（2026-11-04）新增空闲电平 `PPS_OUT_IDLE`：取 −AMPLITUDE 时阶跃加倍，这是让边沿更陡的唯一剩下的办法；给 tile 0 更快的数据流会失去 tile 2 的 MTS（报告第 9 节）。
- B 板上（2026-11-04，`results/pps-out-20261104/REPORT_zh.md`），第 5 节第 2 项：相对 B 的 10 MHz，输出边沿稳定在示波器每秒 7 ps 的分辨率以内，到两分钟的尺度上没有超过约 1 ps 的漂移；经过十次 PP 移动，它正好移动了 PPS_TICK 移动的整 tick 数（3.1 ps rms），而 1PPS 每次差 40-260 ps；六次 PL.open 后它在 8.7 ps 以内。DAC_B 相对 I 反相，所以负的 AMPLITUDE 给出上升的前沿（50 Ω 上约 0.19 V）。
- 两块板，B 在修正（2026-11-04，报告第 8 节），第 5 节第 3 项：扣掉 θ 的修正误差后，输出 PPS_B − 输出 PPS_A 在两次运行里分别是 5.1666 和 5.1657 ns，每次之前两块板都 PL.open、B 重新捕获并做 PP：相差 0.9 ps，而 FS725 的 1PPS 丢了 145 ps。
- 还没做：断电重启、更多次捕获、温度阶跃、D_board。rf-clock 2.2.0.4 起由 ARM 置位 SYNCED（第 7 节）；节点 A 在链路锁定后 LED（PL_LED0）点亮（2026-11-05，用户目视确认）。
- 在台架上模拟 θ 补偿模式（2026-11-05，`results/precision-20261105/REPORT_zh.md` 第 6 节）：示波器上的 out_B − out_A 加上每个边沿前刚测得的 θ，保持在示波器分辨率以内，每秒 7.3-9.4 ps rms，平均到 60 s 约 1 ps，而当时的修正误差是 15-17 ps；补偿本身的误差，用边沿前 2 ms 的对（在 PL 里做）时每秒 0.6 ps rms，用边沿前 50 ms 结束的窗口（ARM 写入延迟）时 1.4 ps，提前 100 ms 时 3 ps。所以边沿的延迟应由 PL 写入，或由 ARM 在边沿前不超过约 50 ms 写入。
- rf-clock 2.3（2026-11-05）实现了这个模式（第 7 节）：`PPS_OUT_CONTROL.FRAC` 和 `PPS_OUT_FRAC`（docs/ABI_zh.md §4.13）。FRAC 置位时阶跃的第一个采样变为 AMPLITUDE + (IDLE − AMPLITUDE) × FRAC / 2^16，边沿于是最多晚一个 1.25 ns 的采样；每单位多少 ps 在示波器上标定（`experiments/m5_pps_frac.py`）。延迟由 ARM 写入：节点 B 上的 `m4c_link --pps-out-comp`（`rf_clock.twtt.control.EdgeCompensation`）在每秒 0.94 s 处取此前 20 ms 的配对的 θ，按 θ 最近一秒的变化率推到下一个边沿，把 FRAC 设为 32768（普通边沿放在范围中间，延迟可以向两边各走半个采样，±625 ps）加上 θ − θ₀ 除以标定值。由 ARM 提前约 60 ms 写入的代价约 1.4 ps rms（上面的台架估计），远小于示波器的 7-8 ps；真有需要时可以改由 PL 提前 2 ms 写入。仿真：`tb_p10_pps_out.v` 第 13-16 秒（FRAC 写了但没有置位、0x4000、0xFFFF、幅度高于空闲电平）。
- 这个模式在台架上的表现（2026-11-05，`results/precision-20261105/REPORT_zh.md` 第 7 节）：分数的响应是 S 形的，0 … 65535 对应 0 到 1254 ps，32768 两侧 4096 以内每单位 0.0275 ps（板子 8873292600AB，`results/m5-pps-frac-20261105_214333`）。B 用 0.2 s 预测控制律修正时（修正误差 14.7 ps），示波器上的 out_B − out_A 在两次各 19 分钟的运行里每秒离散 8.75 和 8.62 ps rms，而示波器本身的分辨率是 7.8-7.9 ps，平均到 60 s 降到 0.9-1.0 ps；两次的均值相差不到 0.1 ps；B 的边沿相对它自己的 10 MHz 移动了写入延迟的 0.97-0.99 倍。补偿后的输出的常数 C 与普通模式相差基准的延迟，636 ps。
