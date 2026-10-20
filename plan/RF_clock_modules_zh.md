# RF-clock 设计计划：5.8 GHz 双向时间传递驯服 FS725

> 本文是 `plan/RF_clock_modules.md`（英文，标准版）的中文翻译，两者内容一致。

> 本文档是重新设计的版本，不继承以前 H00-H19 的模块划分和已有 RTL。
> 原则：**算法全部在 ARM，PL 只负责确定性的时间**——维护 raw tick、
> 在确定的 tick 发射/采集、给样本块打时间锚点。

## 1. 目标

两个节点 A（主）和 B（从），各有一台 SRS FS725 铷钟和一块 RFSoC 板。目标是**修正 B 的 FS725，使 B 的 10 MHz 和 1PPS 跟随 A**：

- 10 MHz：B 与 A 同频（syntonization）；
- 1PPS：B 的 PPS 沿与 A 的 PPS 沿对齐（time synchronization）。

测量手段是 5.8 GHz 无线双向时间传递（two-way time transfer, TWTT）：A、B 互相发送已知 PRN 突发，各自测量对方突发的到达时间，两个方向相减即可消去传播延时，得到钟差。

## 2. 系统结构

~~~text
 节点 A（主）                                         节点 B（从）
 FS725_A                                              FS725_B  <── RS-232 修正 ──┐
  │10 MHz   │1PPS                                     │10 MHz   │1PPS              │
  v         v                                         v         v                  │
 板上时钟链  PL: PPS 捕获                             板上时钟链  PL: PPS 捕获      │
  │(LMK/LMX)                                          │                            │
  v                                                   v                            │
 RFDC DAC ─ BPF ─ PA ─ TX天线 ~~~~ 5.8 GHz ~~~~> RX天线 ─ LNA ─ BPF ─ RFDC ADC     │
 RFDC ADC ─ BPF ─ LNA ─ RX天线 <~~~ 5.8 GHz ~~~~ TX天线 ─ PA ─ BPF ─ RFDC DAC     │
  │                                                   │                            │
 ARM_A: 生成波形/相关 ─────── 以太网（交换测量值）─── ARM_B: 相关/TWTT/Kalman ────┘
~~~

关键前提：**每块 RFSoC 的 DAC/ADC 采样时钟、PL fabric 时钟和 raw tick 全部由本节点 FS725 的 10 MHz 经板上 PLL 派生**。这样 raw tick 就是 FS725 的时间刻度；调整 FS725_B，节点 B 的整个时间基准随之移动，环路闭合。

### 2.1 职责划分

| 职责 | ARM（Python 为主） | PL |
| --- | --- | --- |
| PRN 生成、RRC 成形、定点量化、打包 | 是 | 否 |
| 每秒 TDD 时隙规划，计算 target_tick | 是 | 否 |
| 相关、峰值质量、分数延时估计 | 是 | 否 |
| TWTT 计算、Kalman 滤波、FS725 修正 | 是 | 否 |
| raw tick 计数、PPS 捕获 | 否 | 是 |
| 在 target_tick 从片上 buffer 连续回放 TX 突发 | 否 | 是 |
| 在 target_tick 开始 RX 采集窗口 | 否 | 是 |
| 记录实际开始 tick、故障检测 | 否 | 是 |
| RFDC 配置和 MTS | 通过驱动配置 | 提供 IP 和时钟 |

PL 里**没有**任何 DSP 算法：没有 PRN 发生器、没有 FIR、没有相关器、没有载波恢复。

## 3. 射频频率规划（RFDC IP 已接受；板上待验证）

| 项目 | 取值 | 说明 |
| --- | --- | --- |
| 载波 | 5.8 GHz | 5.725–5.875 GHz ISM 频段中心，带宽约 150 MHz |
| RFDC 参考时钟 | 480 MHz | ADC tile 0/2、DAC tile 0/2 各自的参考时钟，由 LMK/LMX 从 FS725 10 MHz 派生 |
| DAC 采样率 | 9.6 GS/s | DAC tile 2 通道 0（`vout20`）；IMR 高通数据通路，插值 ×6 再 ×2（共 ×12），NCO 3.8 GHz，mix-mode。5.8 GHz 是第二 Nyquist 区（4.8–9.6 GHz）里 3.8 GHz 的镜像，约在 0.604 fs；3.8 GHz 分量由 TX BPF 抑制 |
| ADC 采样率 | 4.8 GS/s | ADC tile 2 通道 0（`vin2_01`）；5.8 GHz 欠采样，位于第三 Nyquist 区（4.8–7.2 GHz），折叠到 1.0 GHz，NCO −1.0 GHz 混到基带；RX BPF 必须抑制会折叠进来的带外信号 |
| 复基带采样率 Fs | 800 MS/s | DAC 插值 ×12、ADC 抽取 ×6 |
| PRN 码片率 | 100 Mchip/s | 每码片 8 个样本，一个码片正好一个 tick |
| RRC 滚降 β | 0.25 | 占用带宽 100 × 1.25 = 125 MHz，留在 ISM 频段内 |
| fabric / raw tick 时钟 | 100 MHz | 每 tick 正好 8 个复样本；tick = 10 ns |
| AXIS 位宽 | 256 bit | 8 × (16-bit I + 16-bit Q)，DAC 和 ADC 相同 |

上述所有频率都是 10 MHz 的整数倍，保证"样本编号 ↔ raw tick"是精确的整数关系。

**MTS 对 tile 的要求：** 延时确定性需要 RFDC 的 MTS。RFDC 2.6（Vivado 2024.1）只有在同类至少两个 tile 参加、且每个参加的 tile 都启用通道 0 时才允许 MTS。4x2 板上引出的是 ADC/DAC 的 tile 0 和 tile 2，两者都有自己的参考时钟，所以 tile 0 以相同配置、各自的 PLL 参加 MTS，数据不用；不使用片上时钟分发（参与分发的 DAC tile 采样率上限为 7.0 GS/s）。依据见 `plan/checklist_zh.md` §1、§3。

**频谱翻转：** 5.8 GHz 是第二 Nyquist 区镜像，发射的 RF 频谱相对基带是翻转的；ADC 在第三（奇数）Nyquist 区，不翻转。所以接收基带是发射基带的共轭。BPSK PRN 经实系数 RRC 后是实信号，共轭不影响；若以后换成复数波形，相关模板要取共轭。
### 3.1 RRC 脉冲成形参数（ARM 侧生成波形使用）

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `RRC_CONFIG_ID` | 2134958710 | 滤波器的标识：RRC 参数的哈希，系数在运行时现算（`rrc_config()`） |
| `RRC_SAMPLES_PER_SYMBOL` | 8 | 每码片 8 个样本 |
| `RRC_SPAN_CHIPS` | 10 | 滤波器跨度 10 码片 |
| `RRC_TAP_COUNT` | 81 | = 10 × 8 + 1 |
| `RRC_TAIL_SAMPLES` | 73 | = 81 − 8；突发总长 = 码片数 × 8 + 73 |
| `RRC_BETA` | 0.25 | 滚降系数 |

- ARM 用 `RRC_coeff` 表中的 float64 系数做 BPSK 码片的上采样卷积，再把结果量化到 DAC 的 16-bit I/Q，打包成 256-bit beat 写入 TX buffer；
- 接收端相关使用的本地模板必须由**同一个 config_id** 的波形生成，`config_id` 写入每条测量记录，两端不一致时拒绝该测量；
- 发射端只做一次 RRC。接收端第一版直接与"PRN ⊛ RRC"模板做相关（等效于匹配滤波），不再单独做一次接收 RRC。

**必须核实：** ADC 在 5.8 GHz 的输入带宽和性能、DAC 在 0.604 fs 处（mix-mode）的输出功率、板上时钟链能否接受外部 10 MHz。发射功率需满足当地 5.8 GHz ISM 法规。

## 4. 时间模型和 TWTT 方程

### 4.1 本地时间

每个节点的 PL 维护一个从不复位的 64-bit `raw_tick`（100 MHz）。PL 在每个 PPS 沿捕获 `pps_tick[k]`。本地时间以"第 k 秒 + 相对 PPS 的偏移"表示：

~~~text
T_local = k + (tick - pps_tick[k]) * 10 ns + (样本内偏移)
~~~

TX 突发在 `tx_tick` 开始；RX 窗口在 `rx_tick` 开始；ARM 相关得到峰值位置 τ̂（单位：样本，含分数部分）。于是：

~~~text
发射时刻 T_tx = tx_tick 对应的本地时间
到达时刻 T_rx = rx_tick 对应的本地时间 + τ̂ / Fs
~~~

### 4.2 双向方程

定义 θ = T_B − T_A（同一真实时刻下 B 的读数减 A 的读数），δt/δr 为各节点发射/接收硬件延时，p 为空中路径延时：

~~~text
m_AB = T_rx,B − T_tx,A = δt_A + p + δr_B + θ      （B 测量）
m_BA = T_rx,A − T_tx,B = δt_B + p + δr_A − θ      （A 测量，经以太网发给 B）

θ = (m_AB − m_BA)/2 − Δ/2,   Δ = (δt_A + δr_B) − (δt_B + δr_A)
~~~

- 空中路径 p 被消去（同频、同天线位置下认为互易）；
- 非互易常数 Δ 必须通过零基线标定得到（见第 8 节）；
- 两个时隙不是同时的，ARM 用 Kalman 中的频偏估计把两次测量对齐到同一历元；
- `(m_AB + m_BA)/2` 是单程延时，作为诊断量记录。

### 4.3 载波频偏

两个 FS725 的相对频偏在 1e-11 量级，对应 5.8 GHz 下的载波频偏约 0.06 Hz，在几十到几百微秒的突发内可以忽略。因此第一版用**非相干相关**（复相关取幅度），不需要载波恢复。载波相位测量（更高精度，但有整周模糊）列为后续扩展。

## 5. 时隙规划（TDD）

每秒一个测量周期，以本地 PPS 为参考（数值为候选）：

~~~text
PPS + 100 ms : A 发射，B 采集窗口打开
PPS + 600 ms : B 发射，A 采集窗口打开
~~~

- 突发长度：PRN 4095 码片 × 8 + 73 = 32833 个复样本 ≈ 41 µs；若需要更高处理增益可改用 65535 码片（524353 个样本，≈ 655 µs）；
- RX 窗口 = 突发长度 + 钟差不确定度 + 滤波器拖尾，跟踪状态下约 100 µs（80000 个复样本，约 320 KB）；
- 节点在自身发射期间不开 RX 窗口；
- 测量周期和时隙偏移都由 ARM 规划，PL 只执行 target_tick。

### 5.1 初始捕获

刚启动时 θ 可能达到秒级，100 µs 的窗口覆盖不了。分两步：

1. **秒标签和粗时间**：两台 ARM 用以太网（NTP/PTP）对齐到毫秒级，并约定每个 PPS 对应的秒号；
2. **粗搜索**：B 用一个长采集窗口（例如几毫秒到几十毫秒，经 P4 直接写入 DDR）覆盖粗时间的不确定度，或在预期位置附近依次扫描多个窗口，直到相关峰通过质量门限；之后切换到跟踪窗口。

若 PPS_B 与 PPS_A 相差远大于伺服用频偏能纠正的范围，需要一次性的 PPS 粗对齐（见 §10 待确认项 4）。

## 6. PL 模块

所有决定时间的逻辑只在一个时钟域里：RFDC DAC AXIS、ADC AXIS、raw tick、TX buffer 和 RX 时间戳全部运行在同一个由 FS725 派生的 100 MHz fabric 时钟上，打时间戳之前的样本路径没有 CDC。唯一的 CDC 位于 RX 时间戳之后的异步 FIFO（进入 DMA 时钟域），它不影响计时。AXI 总线通过 BD 中的 clock converter 进入 fabric 时钟域。

### P0 clock_reset：时钟与复位

- 外部 10 MHz → 板上 LMK/LMX → RFDC 参考时钟和 fabric 时钟；
- 监控 PLL lock 和 RFDC 状态，失锁即锁存 FAULT，所有时间戳标记无效；
- 每次重新锁定后递增 `time_epoch`。

### P1 timebase_pps：raw tick 和 PPS 捕获

- 64-bit `raw_tick`，每个 fabric 时钟加一，不在帧边界复位，饱和而不回绕；
- 对 FS725 的 1PPS 输入做同步，捕获 `pps_tick`，统计周期是否正好是 100,000,000 tick，偏差或丢失即报告；
- **注意：** 10 MHz 和 PPS 同源，PPS 沿相对 fabric 时钟的相位每次上电后是固定的，但未知；若恰好落在采样边沿附近，捕获值可能在相邻两个 tick 间跳动。需要检测这种情况（统计 PPS 周期），必要时用 IDELAY 移开。亚 tick 相位属于每次上电都要标定的常数（见 §8）。

### P2 control_regs：AXI-Lite 控制和状态

- 只读：ID/版本、`raw_tick` 原子快照、最近的 `pps_tick` 和 PPS 计数、`time_epoch`、状态、故障位；
- 命令：`TX_TARGET_TICK`、`TX_LENGTH`、`RX_TARGET_TICK`、`RX_LENGTH`，先写 shadow 寄存器，再写 doorbell 原子提交；
- 结果：`tx_actual_tick`、`rx_actual_tick`、完成序号、返回码；
- 拒绝已过期（≤ 当前 tick + 安全余量）的 target_tick、超出 buffer 的长度、在 ARMED/RUNNING 状态下的重复提交。

### P3 tx_player：TX 定时回放

- TX 波形 buffer 放在 BRAM/URAM 中，通过 AXI BRAM 接口由 ARM 写入；4095 码片突发约 128 KB；65535 码片约 2 MB，接近 URAM 总容量；波形不变时只需写入一次；
- 状态：IDLE → ARMED → RUNNING → DONE / FAULT；
- 在 `raw_tick == TX_TARGET_TICK` 的那一拍开始，以每时钟一个 beat（8 个复样本）连续输出，直到长度结束；突发之外输出 0；
- ARMED/RUNNING 期间 ARM 写 TX buffer → 报错，不允许修改正在发送的波形；
- 运行中 DAC `tready` 掉落 → FAULT，不暂停、不补发；
- 记录实际开始 tick（应等于 target）。

### P4 rx_capture：RX 定时采集

RX 样本写入 DDR，不占用大块 BRAM。窗口与窗口之间不要求连续，但**窗口内部不能丢样本**：样本时间按"起始 tick + 样本序号"推算，丢一拍会让后面所有样本的时间错位。

~~~text
ADC AXIS ─> 窗口门控 + 时间戳 ─> 异步 FIFO ─> 位宽转换 ─> AXI DMA S2MM ─> HP 端口 ─> DDR
  (100 MHz fabric 时钟域)                    (DMA 时钟域，例如 250 MHz)
~~~

- 在 `raw_tick == RX_TARGET_TICK` 打开窗口，把 ADC AXIS 的每个 beat 送入 FIFO，数够长度后关闭窗口并给最后一个 beat 打 `TLAST`；
- **时间戳在 ADC 一侧、FIFO 之前记录**：实际开始 tick、beat 数、ADC overflow/over-range 标志。DDR 写入的延迟和抖动不影响时间戳；
- 数据率 800 MS/s × 4 B = 3.2 GB/s。HP 端口为 128 bit，DMA 侧时钟需要 ≥ 200 MHz，并实测与 ARM 同时访问 DDR 时能否持续达到；
- FIFO 只用来吸收 DDR 的短时停顿（几 KB 到几十 KB），不承担缓存整个窗口；
- 提交命令前 ARM 必须已经给 S2MM 准备好足够长的 DDR buffer（PYNQ `allocate`）。DMA 未就绪时 PL 拒绝该命令，不打开窗口；
- **窗口内 FIFO 溢出 → 作废这个窗口**：置 `rx_window_invalid` 并计数，丢弃这次测量；不进入 FAULT，不影响 TX。这一点和 TX 不同：RX 丢一个窗口只是少一次测量；
- DMA 完成后，ARM 先对该 buffer 执行 cache invalidate，再读取样本；
- 同一条通路同时用于跟踪窗口（约 100 µs）和粗捕获用的长窗口。

### P5 rfdc：RFDC IP

- DAC 第二 Nyquist 区 mix-mode、ADC 抽取、NCO 设为 5.8 GHz 相关频点；
- **开启 Multi-Tile Sync（MTS）/SYSREF**，使 DAC/ADC 数字通路延时每次上电都一致；否则标定出来的常数在下次启动后失效；
- RFDC 由 ARM 用 PYNQ `xrfdc` 或 XRFdc 驱动初始化，PL 只提供 IP 和时钟。

### P6 status：状态和故障

- 汇总 P0–P5 的故障到一个锁存的状态字，带来源和序号；
- 故障只能由显式 CLEAR 命令或新的 `time_epoch` 清除；
- 第一版用轮询，不用中断。

### 6.1 第一版不需要的东西

- **TX 不从 DDR 流式读取**：发射波形固定且短，放在片上 buffer 回放即可，连续性靠结构保证；只有 RX 使用 S2MM DMA；
- 不需要在 PL 内做任何 DSP；
- 不需要 scatter-gather DMA 和中断：每个窗口一次 simple 模式传输，用轮询等待完成。

## 7. ARM 软件模块

| 模块 | 功能 |
| --- | --- |
| `waveform` | m 序列 PRN、BPSK、RRC、定点量化、按 RFDC lane 格式打包，写入 TX buffer |
| `pl_client` | 读写 P2 寄存器，提交 TX/RX 命令，读取 RX buffer 和元数据 |
| `scheduler` | 根据最近的 `pps_tick` 计算每秒的 TX/RX target_tick |
| `correlator` | FFT 互相关取幅度、峰值质量（峰旁比）、抛物线或上采样插值得分数延时 |
| `twtt` | 用秒号和序号配对两个方向的测量，计算 θ 和单程延时 |
| `link` | 两节点 ARM 之间的以太网消息（测量值、秒号、序号、epoch） |
| `estimator` | Kalman 滤波，状态为钟差 θ 和相对频偏 y，带离群点剔除 |
| `fs725` | RS-232 驱动：设置频率偏移，读取状态，记录所有修正 |
| `calibration` | 保存 Δ、PPS 亚 tick 相位、RFDC 延时，带版本和日期 |
| `rfdc_mgr` | RFDC 初始化、MTS、NCO 设置和状态读回 |

测量和修正都是秒级，Python + NumPy 足够；只有实测证明不够时，才把瓶颈迁移到 C/C++/Rust。

### 7.1 FS725 修正策略

- 只有 B 被修正；A 自由运行；
- 第一版只通过 RS-232 调整 B 的频率偏移：频率误差直接修正，PPS 相位误差通过临时增加频偏、让相位逐渐滑过去来修正；
- 修正命令的量程、分辨率和单位以 FS725/PRS10 手册为准；
- 环路时间常数取数十到数百秒，与铷钟本身的稳定度相匹配。

## 8. 标定

| 量 | 方法 | 何时重做 |
| --- | --- | --- |
| 非互易常数 Δ | 零基线：两个节点共用同一台 FS725 的 10 MHz 和 PPS（PPS 分配线缆长度已知），此时真实 θ 已知，由 (m_AB − m_BA)/2 = θ + Δ/2 解出 Δ | 更换线缆、放大器、滤波器、天线或 RFDC 配置后 |
| PPS 相对 fabric 时钟的亚 tick 相位 | 示波器/时间间隔计数器，或把 PPS 接入一个空闲 ADC 通道，在 GS/s 级时间分辨率下测量 | 每次上电（除非证明板上 PLL 相位确定） |
| RFDC 通路延时 | 本板 DAC→衰减器→ADC 有线环回，多次重启后比较 | 修改 RFDC 配置后；用来验证 MTS 生效 |

标定值存储在软件中，必须带版本号；绝不能在代码里减一个凭感觉猜出来的常数。

## 9. 实施顺序和验收

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| M0 | FS725 10 MHz 接入板上时钟链，PPS 接入 PL 管脚 | 板上 PLL 锁定在外部参考；RFDC 时钟正确 |
| M1 | P0/P1/P2 | ARM 读到每秒正好 100,000,000 tick；拔掉参考时报 FAULT |
| M2 | P3 + P5，示波器以 PPS 触发观察 DAC 输出 | 突发相对 PPS 的延时固定；多次重启后不变（MTS 生效） |
| M3 | P4，本板 DAC→ADC 有线环回，ARM 做相关 | 环回延时测量的标准差和重启后的一致性；S2MM 在 ARM 同时访问 DDR 时持续写入不溢出 |
| M4 | 两块板，有线连接加衰减器，共用一台 FS725 | 零基线测得 Δ；θ̂ 的噪声水平 |
| M5 | 各用自己的 FS725，有线连接，闭合 Kalman + FS725 修正环路 | 用时间间隔计数器测量 PPS_A − PPS_B，长期收敛 |
| M6 | 换成 5.8 GHz BPF、放大器、天线的空中链路 | 与 M5 相同的指标，记录多径和 SNR 的影响 |

每一项证据都要注明来自仿真、综合、板上测量中的哪一种。最终指标是外部时间间隔计数器测得的 PPS_A − PPS_B，而不是系统自己报告的 θ̂。

## 10. 待确认项

逐项进度和依据记录在 `plan/checklist_zh.md`。

1. **板卡型号和外部参考输入**：板上时钟链能否接受 10 MHz 外部参考，RFDC 和 fabric 时钟能否都从它派生？
2. **天线**：TX/RX 分开天线，还是共用一副天线加 T/R 开关（若共用，PL 需要在 target_tick 输出开关控制，并保护 LNA）？
3. **节点间数据通道**：默认使用以太网；若没有，需要把测量值调制进 RF 突发。
4. **FS725 的 PPS 粗对齐能力**：能否一次性把 PPS 相位跳到目标附近（例如通过 1PPS 输入同步）？如果不能，需要别的方法完成初始粗对齐。
5. **目标精度**：ns 级可以用码相关完成；若需要 100 ps 甚至更好，需要加入载波相位测量，并更严格地处理 PPS 亚 tick 相位。
6. **法规**：5.8 GHz 发射功率和占用带宽限制。
