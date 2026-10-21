# RF-clock 待确认清单

本文是 `plan/checklist.md`（英文，标准版）的中文翻译，两者内容一致。

逐项确认，完成后打勾，并在该项下写明**结论和依据**（文件、命令输出、测量记录或文档出处）。没有依据的不打勾。确认结果若改变接口或设计，同步修改 `docs/ABI.md`（及 `docs/ABI_zh.md`）、`plan/RF_clock_modules.md`（及中文版）。

最后更新：2026-10-14

## 1. 板卡与参考资料

- [x] **板卡型号**：Real Digital RFSoC 4x2，芯片 xczu48dr-ffvg1517-2-e
  - 依据：用户确认；旧脚本 `FPGA/ip/generate_rfdc_ip.tcl`（已删除）中的 part；ssh 配置中的 `RFSoC-4x2-ai`（192.168.2.99）
- [x] **Vivado board files 来源**：RealDigitalOrg/RFSoC4x2-BSP `board_files/`，board part `realdigital.org:rfsoc4x2:part0:1.0`
  - 依据：已 clone 到 `RF-clock-PC:~/ref/RFSoC4x2-BSP`（不进仓库）
- [x] **PL_CLK / PL_SYSREF 管脚**：LVDS 差分对，`FPGA_REFCLK_IN_P/N` = AN11/AP11，`SYS_REF_FPGA_P/N` = AP18/AR18（4x2 手册附录 A；HP bank 65/66，GC 管脚）
  - run2/run3 照抄 RFSoC-MTS `mts.xdc`，只把 P 端当 LVCMOS18 单端收。run2（未用管脚 PULLNONE）MMCM 时锁时不锁；run3（PULLUP）180 s 内反复复位都不锁，最可能是 N 端被上拉
  - run4：两对都用 IBUFDS、LVDS、`DIFF_TERM_ADV TERM_100`、`DQS_BIAS TRUE`；`UNUSEDPIN` 改回 PULLNONE。完成标准：上板 MMCM 每次都锁，`PPS_LAST_PERIOD` 恒为 1e8
  - 依据：run4 上板两次 bring-up，MMCM 都在 `pl_resetn0` 后 1 s 内锁定；M1 见 §5（2026-10-13）
- [x] **PPS 输入管脚**：`IRIG_COMP_OUT` = AJ13，LVCMOS18，为板上 1PPS 比较器输出
  - 依据：RFSoC4x2-BSP `hw/constraints/4x2_1PPS.xdc`
- [x] **1PPS 比较器阈值和输入电平**：比较器阈值是固定的，还是需要 PL 通过 `IRIG_TRIG_OUT` / `IRIG_ADC_*` 设置；FS725 的 1PPS 幅度是否在该输入允许范围内
  - FS725 侧已确认（手册 Specifications）：1 pps 输出 2.5 V 接 50 Ω、5 V 接高阻，脉宽 10 µs，50 Ω 源阻抗
  - 4x2 侧未知：手册 Figure 6 只画了“SMA → 电阻分压 → LMV7235，阈值由 3.3 V 分压给出”，没有阻值；需上板测 PPS SMA 对地电阻和比较器输出极性
  - 完成标准：查 4x2 板卡手册或原理图得出结论；如需 PL 控制，补充管脚和逻辑
  - 首次上板（2026-10-13，run2 bitstream）：`PPS_COUNT` = 0，PL 一个沿都没收到。LMV7235 是开漏输出（推挽的是 LMV7239），而 run2 的 `pps_in` 没有上拉
  - run3 修改：AJ13 加 `PULLTYPE PULLUP`；加第二路 `IRIG_TRIG_OUT`（AH13，施密特触发器）；ABI 1.1 新增 `PPS_CONFIG`（选路/反相）和 `PPS_INPUTS`（两路电平和沿计数），极性和可用的那一路由软件确定，不再重出 bitstream；`UNUSEDPIN` 改为 BSP 的 PULLUP
  - FS725 1PPS 用示波器看到输出，脉宽约 30 µs；幅度读数 500 mV 与手册（高阻 5 V）不符，待确认探头倍率/输入阻抗
  - 原理图（`RF-clock-PC:~/ref/docs/sch/rfsoc4x2_sch.pdf` 第 10 页 1PPS）：J7 → 49.9 Ω 对地 → 100 Ω → 10 nF 交流耦合 → 1.65 V 偏置节点 → LMV7235（迟滞约 ±0.17 V，输出 1 kΩ 上拉到 3.3 V）和 SN74AUP1G17；NVT2008 转 1.8 V，1.8 V 侧无上拉（FPGA 上拉必要）。J7 上零点几伏的脉冲就够比较器翻转。前面"比较器开漏、板上无上拉"的判断不准确
  - run4 上板（2026-10-13）：comp 空闲 0、trig 空闲 1，两路 0 个沿，即 J7 上没有脉冲。待查：线是否接在 J7（网络名 IRIG），板端用 T 头量幅度（50 Ω 终端下应约 2.5 V）
  - 结论：之前 PPS 线没接到 J7；接好后板端约 2.5 V，两路都每秒一个上升沿、空闲为 0（正脉冲），用默认 `PPS_CONFIG` = 0（比较器、不反相）
- [x] **4x2 板上引出的转换器**：DAC tile 0、2 各 1 路（`vout00`、`vout20`）；ADC tile 0、2 各 2 路（`vin0_01`、`vin0_23`、`vin2_01`、`vin2_23`）。tile 0 和 tile 2 都有自己的参考时钟，PLL 都可用
  - SMA 口：`vout20`（`DAC_VOUT0_230`）= **DAC_A**，`vout00`（`DAC_VOUT0_228`）= DAC_B（原理图 `~/ref/docs/sch/rfsoc4x2_sch.pdf` 与手册引脚表一致；手册框图写反了，2026-10-14）。ADC：`vin2_01`（tile 226 `VIN_I01`）= **ADC_B**，`vin2_23` = ADC_A，`vin0_01` = ADC_D，`vin0_23` = ADC_C。原理图旧标号 DAC0/DAC1、ADC0…ADC3 依次对应 DAC_A/DAC_B、ADC_A…ADC_D（DAC1 = DAC_B，ADC1 = ADC_B）。板上丝印就是 DAC_A/B、ADC_A…D（用户确认）：发射 DAC_A，接收 **ADC_B**
  - 依据：Xilinx RFSoC-PYNQ `boards/RFSoC4x2/base/base.tcl`（`RF-clock-PC:~/ref/pynq4x2/`），`ADC0/ADC2/DAC0/DAC2_PLL_Enable true`，以上 6 个模拟端口
  - 修正：此前从 RFSoC-MTS 参考设计推断"只有 tile 2 接时钟"是错的；该参考设计只是选择了从 tile 2 分发时钟
- [ ] **RFSoC 4x2 板卡手册/原理图**：拿到正式文档，确认外部参考 SMA 接的是 LMK04828 的 CLKin0、各 LMX2594 分别给哪个 RFDC tile 供参考时钟

## 2. 时钟链（FS725 → LMK04828 → LMX2594 → RFDC / PL）

- [x] **4x2 能否接外部 10 MHz**：可以，接 LMK04828 CLKin0
  - 依据（二手，尚未在本板验证）：PYNQ 论坛，CASPER 文件 `rfsoc4x2_lmk_CLKin0_extref_10M_PL_122M88_LMXREF_245M76.txt` 已被多人验证可用
- [x] **FS725 10 MHz 接入 4x2 的外部参考 SMA**：幅度、阻抗符合输入要求
  - 依据：写入 `LMK04828_100.0` 后 PLL1、PLL2 和两个 LMX LED 全亮（2026-10-13）；CLKin0 是手动选择，PLL1 只能锁在 FS725 上
  - FS725：正弦，0.5 Vrms ±10%（约 1.41 Vpp，+7 dBm）接 50 Ω
  - `LMK04828_100.0.txt`：CLKin0 手动选择、送 PLL1；CLKin0_R = 12，PLL1_N = 192（10 MHz/12 = 160 MHz VCXO/192 = 833.33 kHz）
- [x] **生成 LMK04828 寄存器文件**（开发机 Python 生成，待 TICS Pro/上板复核）：CLKin0 = 10 MHz，PLL1 锁定到外部参考；PLL2 VCO 2400 MHz；PL_CLK 100 MHz；给 LMX 的参考；RFDC 与 PL 的 SYSREF
  - 依据：`src/rf_clock/clocks/lmk04828.py`、`src/rf_clock/clocks/files/LMK04828_100.0.txt`；目标字段为 VCO0、PLL2_R=2、PLL2_P=2、PLL2_N=15，160 MHz VCXO 推得 2400 MHz；DCLKout8=100 MHz，LMX 参考支路=480 MHz，SYSREF 分频=480。TICS Pro 显示和 SPI/锁定尚未完成。
- [x] **确定 SYSREF 频率**：5 MHz；按 PG269 的 MTS 要求核对它是 PL 时钟（100 MHz）、RFDC 参考时钟（480 MHz）、采样时钟（9.6 / 4.8 GS/s）等的整数分之一
  - 依据：AMD PG269 v2.6 SYSREF Signal Requirements（频率严格 <10 MHz）；`tests/test_clocks.py` 验证 5 MHz 整除 100/480/4800/9600 MHz。10 MHz 因严格小于 10 MHz 的要求排除；MTS 连续运行和相位仍待上板。
- [x] **生成 LMX2594 寄存器文件**：输出 480 MHz 给 ADC tile 0/2、DAC tile 0/2
  - 依据：`src/rf_clock/clocks/files/LMX2594_480.0.txt`；整数-N 配置在 480 MHz 参考下为 7.68 GHz VCO、16 分频得到 480 MHz；`tests/test_clocks.py` 解码复核。
- [x] **板上验证 PLL1 真正锁定到 FS725**：拔掉 10 MHz 后 PLL1 LED 熄灭；`PPS_LAST_PERIOD` 恒为 100000000（防止 LMK 靠板上 VCXO 继续输出而误以为正常）
  - 依据：run4 M1 60 个周期全部 100000000，fabric 时钟与 FS725 同源（2026-10-13）；拔线测试未做（远程时可以跳过）
  - 本项目的 LMK 文件没有配置 SPI 读回；Status_LD1/2 = PLL1/PLL2 锁定，驱动板上 LED
- [ ] **所有时钟同源**：DAC/ADC 采样时钟、PL_CLK、fabric 时钟都由 FS725 10 MHz 派生；用频率计或示波器核对
- [x] **LMX2594 相位同步**（2026-10-19，rf-clock 1.3.0.4）：没有它时，每次写时钟后 LMX 的通道分频器都从一个任意相位开始，480 MHz 的 tile 参考按整数个 VCO 周期（130.2 ps）移动，MTS 之后转换器落在 26.04 ps 格点（1/4 个 DAC 采样、1/8 个 ADC 采样）的某个位置上，而 MTS 看不到：示波器上 DAC 出现的是相距 52 ps 的两个位置（最初被解读为 260.4 ps 的步长，后由 M3 回环改正，见 `results/lmx-sync-loopback-20261019/`）。这是在通宵重启测试中发现的，并追查到写时钟这一步（绕过混频器后依然存在；`--no-clocks` 时状态不变，写时钟就会变）。现在的配置为 VCO_PHASE_SYNC = 1、PLL_N 40（IncludedDivide 4，VCO 不变），`program_clocks` 写完后翻转一次 VCO_PHASE_SYNC：属于 TI SNAS696C 的 SYNC 第 2 类，纯软件，时序不关键。已验证：十次写时钟和十次重启中，两块板都各自保持同一个起点（轮间 sd 分别为 2.4-3.5 ps 和 5-7 ps）。报告：`results/lmx-phase-sync-20261019/`
  - [x] 两种转换器，用接好线的 M3 回环测量（2026-10-19/20）：两片 LMX2594 都同步时，十次写时钟后回环时间在 0.10 ps（sd，载波相位法）以内。不开 SYNC 时，DAC 在 80 次写时钟中出现格点上相邻的三个位置（间隔 26.3 和 27.0 ps）；按 1.3.0.4 之前的写法重启 15 次，只出现两头的两个，相距 52.5 ps，复现了示波器上那一对。ADC 在写时钟时出现相邻的两个位置；按旧写法重启时，出现当初预测的四个相距 52 ps 的状态中的三个。步长是一个 VCO 周期；旧写法在重启后按两个周期移动。PYNQ 设备树把两片 LMX2594 的名字标反了（`lmxdac`，即 spi0.1，驱动的是 ADC；方法是分别关掉一片，读各 tile PLL 的锁定状态）；rf-clock 对两片写同样的内容，不受影响。251 轮的 MTS latency 全部相同。报告：`results/lmx-sync-loopback-20261019/`
  - [ ] **每片 LMX2594 的 VCO 电容档**（2026-10-20，887329260072A）：每次写时钟都会重新校准 VCO，在相邻两个电容档（VCO_CAPCTRL；ADC 那片 117/118，DAC 那片 122/123）中选一个，重启与否都会发生。档位每高一档，对应的转换器晚 8–10 ps（ADC 117 为 +9.6 ps，DAC 123 为 +7.7 ps）；通过 SPI 回读（`experiments/m3_vco_band.py`）确认，强制档位后复现。相位 SYNC 管不到它。"9.7 ps 重启状态"和低 TX 幅度下偶尔多出的 7.6 ps 都是它。rf-clock 1.3.0.6（报告第 5 节）：每块板一个配置文件 `/etc/rf-clock/board.json`，以本板 AT24MAC402 的序列号标明是哪块板，其中给出每片的档位，`PL.open()` 强制使用、回读并要求锁定；没有配置文件时 `PL.open()` 发出警告并记下校准选的档位。`rf_clock.checks.vco_bands scan` 建议每片校准最常选的档位，并找出能锁定的档位（887329260072A 上 ADC 109–118、DAC 113–123，校准总选最上面两档之一）；`delays` 测出每个档位的回环延时，写入配置的档位表（docs/ABI_zh.md §11.1、§13）。887329260072A（2026-10-20）：同一档位内回环时间在 2.5 小时里保持在 0.1 ps（芯片 42.1–43.8 °C），而 120 次写时钟中档位变了 31 次；配置强制 ADC 118 / DAC 122（档位表：ADC 117 +9.42 ps、116 +17.48 ps；DAC 123 +7.76、121 -9.71、120 -17.62 ps；`results/vco-bands-20261020_214500/`），带配置上电后 M3 通过。887329260101：20 次写时钟校准都选 ADC 125 / DAC 128，已写入它的配置，但当时它的 10 MHz 参考断开（tick 慢 73 ppm，PPS 锁不上）：参考接回后要重新扫描。待定：强制档位在其他温度下能否锁定（ADC 118 是能锁定的最高一档）；887329260101 的档位表要等它自己接回环线
  - [ ] 工作模式（3.8/5.8 GHz 数据通路）：确认跨重启只有一个状态；重做 1.3.0.4 之前的绝对延迟标定

## 3. Vivado 构建（`PL/bd/create_rf_clock_bd.tcl`）

- [x] **RTL 仿真**：P0–P4、P6 与 `p_top` 的 5 个 testbench 全部通过
  - 依据：`./PL/sim/run_pl_tests.ps1`（iverilog），含 TX 延迟改错 1 拍的反向检查
- [x] **RFDC 5.8 GHz 配置能被 IP 接受**：DAC 9.6 GS/s ×12、NCO 3.8 GHz、IMR 高通、mix-mode；ADC 4.8 GS/s ÷6、NCO −1.0 GHz；AXIS 100 MHz；tile 0/2 MTS 打开
  - 依据：`RF-clock-PC:~/build/probe/probe19.log`（仅 IP 参数校验，不代表板上验证）
- [x] **RFDC MTS 与 DAC 采样率**：最终用 tile 0 + tile 2、各自 PLL、DAC 9.6 GS/s（用户决定，2026-10-13；取代中间方案 B"tile 0–2 分发时钟、DAC 6.4 GS/s"）
  - 第一次 BD 运行失败原因：`ADC2_Multi_Tile_Sync=false, expected true`
  - 探测结论（Vivado 2024.1，RFDC 2.6，脚本在 `RF-clock-PC:~/build/probe/`）：
    - 只启用一个 tile 时，IP 禁用 MTS 参数，也不生成 `user_sysref_adc/dac` 引脚
    - 每个参加的 tile 的**通道 0（slice x0）必须启用**，否则该 tile 的 MTS 被禁用（ADC 放在 slice x2 时 MTS 一直打不开，换到 slice x0 后立即可以）
    - DAC tile 参与片上时钟分发时，采样率上限 7.0 GS/s
    - tile 0 + tile 2 各用自己的 PLL、不分发时钟：ADC、DAC 的 MTS 都能打开，DAC 9.6 GS/s 被接受（`probe19.log`）
  - 最终配置（`PL/bd/create_rf_clock_bd.tcl`）：ADC/DAC tile 0、2 启用，参考时钟各 480 MHz；DAC 9.6 GS/s、IMR 高通、插值 ×6（共 ×12）、NCO 3.8 GHz、mix-mode；ADC 4.8 GS/s、I/Q、抽取 ×6、NCO −1.0 GHz，输入为 `vin2_01`；tile 0 只为 MTS
- [ ] **tile 0 和 tile 2 的参考时钟同源**：MTS 要求两个 tile 的采样时钟出自同一参考（LMK/LMX），且 SYSREF 共用；按板卡原理图和 LMK/LMX 配置确认
- [x] **ADC 输入口**：ADC 从 `vin2_23` 改到 `vin2_01`（tile 2 通道 0），确认它对应 4x2 上哪个 SMA
  - 证据（2026-10-19，板子 887329260072A）：DAC_A 接 ADC_A 时 M3 只收到串扰（225.89 个采样，PSL 16.9 dB）；接 ADC_B 时为 230.45 个采样，PSL 21–22 dB。`vin2_01` 就是 ADC_B，与第 1 节一致
- [ ] **tile 0 的代价**：功耗、发热；DAC tile 0（`vout00`，板上的另一个 DAC 口）输出恒为 0，是否需要在运行时关闭
- [x] **BD 阶段通过 `validate_bd_design`**，无 critical warning
  - 依据：`RF-clock-PC:~/build/rf_clock_bd_run3`（status `bd=complete`）
  - 途中修复：AXI DMA `c_prmry_is_aclk_async` 为只读，已从脚本删除；`p_top` 的 BRAM 端口补充 `READ_WRITE_MODE` 等存储器参数
- [x] **RFDC over-range 引脚名**：`adc2_01_over_range`（ADC tile 2 通道 0）
  - 依据：`RF-clock-PC:~/build/rf_clock_bd_run3/rfdc_pins.txt`；需保持 `ADC_RTS = true`，否则 over-range 引脚不生成
- [x] **地址表与计划一致**：P2 0xA000_0000、DMA 0xA001_0000、时钟状态 GPIO 0xA002_0000、RFDC 0xA004_0000、TX buffer 0xA008_0000；DMA 可访问 HP0 的 DDR_LOW（0–2 GiB）和 DDR_HIGH（0x8_0000_0000 起）
  - 依据：`RF-clock-PC:~/build/rf_clock_bd_run3/address_map.txt`；`docs/ABI.md` §1.2 已去掉"计划值"说明
- [x] **综合通过**
  - 依据：`RF-clock-PC:~/build/rf_clock_impl_run2/status.txt`（`synth=complete`）
- [x] **实现通过且时序满足**：WNS 0.246 ns，WHS 0.012 ns；no_clock、未约束端点均为 0
  - 依据：`RF-clock-PC:~/build/rf_clock_impl_run2/timing_summary.rpt`
  - CDC：8 条 critical 全部在 RFDC IP 内部（AXI 配置寄存器到 tile 控制端口；fabric 复位和 SYSREF 进入 IP 自带的 `xpm_cdc` 同步器），由 IP 约束处理，与 RFSoC-MTS 参考设计一致；`p_top` 的跨时钟域路径均为安全（`cdc_critical_details.rpt`）
  - 约束中有 3 条 `set_max_delay` 没有匹配到对象（critical warning），已从 `PL/constraints/rf_clock_4x2.xdc` 删除；fabric 与 DMA 时钟同出一个 MMCM，这些路径按同步路径分析并已满足时序。下次构建应无 critical warning，待验证
- [x] **导出 `rf_clock.bit`、`rf_clock.hwh`、`rf_clock.xsa`**
  - 依据：`RF-clock-PC:~/build/rf_clock_impl_run2/`（tile 0 + tile 2、DAC 9.6 GS/s 配置）
- [x] **关闭构建机睡眠**：已 mask `sleep/suspend/hibernate/hybrid-sleep/ suspend-then-hibernate.target`，GNOME 空闲挂起设为 `nothing`
  - 依据：`systemctl is-enabled` 均为 masked（2026-10-13）
- [ ] **远程构建机网络稳定**
  - 日志显示本次开机后**没有发生过睡眠**。13:16 的重启是按了电源键关机；14:01 前后的断线是 USB Wi-Fi 网卡（`wlx38a38ca06de0`，YaleSecure）掉线，`reason=4 locally_generated=1`，并且 Wi-Fi 省电模式开着（`wifi.powersave = 3`）
  - 已将 `/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf` 改为 `wifi.powersave = 2`（用户同意，2026-10-13）；NetworkManager 不支持在线 reapply，下次 Wi-Fi 重新连接后生效。观察之后是否还掉线，再决定这一项是否打勾
- [ ] **Syncthing**：新建的 `FPGA/bd/`、`FPGA/constraints/` 没有同步到远程（目前用 scp 手动拷贝），需查明原因
- [x] **`include "src/..."` 在 Vivado 里可用**：module reference 的 HDL 解析器不看 `include_dirs`，按工作目录解析 include；脚本在 `create_bd_cell -reference` 之前 `cd` 到 `PL/`（探针 `RF-clock-PC:~/build/probe/inc_probe.tcl`，变体 d 通过）
- [x] **run3 构建**（ABI 1.1、PPS 上拉、第二路 PPS）：`RF-clock-PC:~/build/rf_clock_impl_run3`
  - WNS 0.372 ns，WHS 0.012 ns，无 critical warning；AJ13/AH13 为 LVCMOS18 + PULLUP（`*_io_placed.rpt`）；已部署到板上 `~/RF-clock/overlay/`，run2 留在 `overlay/run2/`
  - 上板：MMCM 不锁（见上面 PL_CLK 一项）；未用管脚上拉把 PL RGB 灯和 LED 点亮
- [x] **run4 构建**（PL_CLK/PL_SYSREF 差分接收、`UNUSEDPIN PULLNONE`）：`RF-clock-PC:~/build/rf_clock_impl_run4`
  - WNS 0.365 ns，WHS 0.012 ns，无 critical warning；四个时钟管脚 LVDS、100 Ω 差分终端、DQS_BIAS（`*_io_placed.rpt`）；已部署，run2/run3 留在板上 `overlay/run2|run3/`
- [x] **run6 构建**（ABI 1.2、`BUILD_VERSION`，rf-clock 1.2.0.0）：`RF-clock-PC:~/build/rf_clock_impl_run6`
  - WNS 0.335 ns，WHS 0.011 ns，无 critical warning；BRAM36 74.5/1080、URAM 0/80；管脚与 run4 相同；`BUILD_VERSION` = 0x01020000。已打包到 `src/rf_clock/overlay/` 并部署（run5 按旧编码构建，中途停掉）
  - 上板（2026-10-14）：`PL.open()` 版本检查通过；例子 03、M1（12 s）、M2（5 次）、M3（延时 10 次、压力 3 × 6.4 MB）全部 PASS
- [x] **run8 构建**（TX buffer 69632 beat，rf-clock 1.2.1.0）：`RF-clock-PC:~/build/rf_clock_impl_run8`
  - WNS 0.345 ns，WHS 0.011 ns，无 critical warning；URAM 68/80，BRAM36 10.5/1080；`BUILD_VERSION` = 0x01020100；`tx_bram_ctrl` 在 0xA040_0000；管脚与 run4/run6 相同。已打包并部署（2026-10-14）
  - 上板：例子 03、M1（12 s）、M2（5 次）、M3（延时 10 次：226.12 个样本、标准差 125 ps；压力 3 × 6.4 MB）全部 PASS；MTS 落在固定目标上（DAC 1896、ADC 1296）
  - 满深度：TX buffer 写入后经 AXI 读回全部正确；17 个 bank 各放一段不同的随机 BPSK（4086 码片 = 4096 beat），整段 69632 beat 播放后按段相关，17 个延时在 225.93–226.38 个样本（差 0.45），PSL 16.3–18.4 dB；65535 码片的整段突发延时 226.0 ± 0.3 个样本。单样本冲激在串扰下 SNR 太低，不能用来做这个检查
  - 待查：65535 码片整段突发的 PSL 只有 12 dB，比 4096 码片的 16–18 dB 低（延时不受影响）；串扰通道里 3.8 GHz 和 5.8 GHz 两路都折叠到 ADC 的 1.0 GHz，接线加滤波后再看
  - run7（同一版本）未打包：单块 69632 深的存储没被推断成 URAM（写使能带地址判断、深度不是 2 的幂），退回 548 个 BRAM36；改成 17 个 4096 beat 的 bank 后由 run8 解决

- [x] **板子卡死时远程恢复**：`RF-clock-PC` 上 `xsdb tools/jtag_reset_board.tcl 887329260072A`（JTAG `rst -system`，再让停在 reset catch 的 A53 #0 继续），约 40 s 后 ssh 恢复
  - 依据：2026-10-13 两次实测；单独 `rst -system` 会让板子停在 FSBL 入口不启动
- [ ] **断电级恢复**：JTAG 救不回来时（例如 JTAG/PMU 也挂住）只能断电；需要智能插座或可远程控制的电源

## 4. RFDC 与上电（板上）

- [x] **PYNQ 版本**：`RFSoC-4x2-ai` 上的 PYNQ 版本，`xrfdc` 是否支持 MTS 接口
  - PYNQ 3.1.1；`xrfdc` 的 Python 层没有 MTS，但 `libxrfdc.so`（embeddedsw xilinx-v2020.2）导出 `XRFdc_MultiConverter_Init/Sync`；`src/rf_clock/pl/rfdc.py` 按 2020.2 的 `xrfdc_mts.h` 用 cffi 调用
  - 开机约 1 分钟后 `bootpy.service` 会加载 base overlay，覆盖已加载的设计；加载前先等启动完成（`bringup.wait_for_boot`，2026-10-14 冷启动实测撞上过一次）
- [x] **上电顺序可行**：按 `docs/ABI.md` §7.1，先加载 overlay，再写 LMK/LMX，拉一下 `pl_resetn0`，等时钟状态 GPIO，再访问 P2
  - 旧顺序（先时钟后 overlay）的实测（2026-10-13）：第一次写完时钟约 4 s 内整机卡死（base overlay 仍在 PL 中，原因未定位）；第二次先停 20 s 再加载，不卡死但 MMCM 未锁；第三次重新加载后 MMCM 直接锁定，P2 读写正常
  - 新顺序（run4，2026-10-13）：两次完整 bring-up 均成功，无卡死，`pps_locked` 约 5 s 后置位
- [x] **RFDC tile 启动**：ADC/DAC 的 tile 0 和 tile 2 都启动完成，tile PLL 锁定（tile 0 只为 MTS，但它也必须有时钟并启动）
  - 依据：`rf_clock.checks.m2_tx_timing`，`pl_resetn0` 之后 IP 自行启动到 TileState 0xF，不需要调用 StartUp（2026-10-14，run4）
- [ ] **PL_SYSREF 采样稳定**：约束中 PL_SYSREF 的输入延时（2.000/2.031 ns）照抄自 RFSoC-MTS，那里 PL_CLK 是 500 MHz；现在 PL_CLK 100 MHz、SYSREF 分频也变了，两者的相对相位可能不同。上板用示波器看 PL_CLK 与 PL_SYSREF 的边沿关系，确认在 PL_CLK 采样沿附近有足够余量；必要时修改约束或 LMK 的 SYSREF 延时
- [x] **MTS 同步成功**：ADC/DAC MTS 返回成功，记录延时；多次重启后延时一致
  - 依据：DAC tile 0/2 延时 1848、调整 0；ADC tile 0/2 延时 1236、调整 0（ref tile 0，目标 -1）。3 次 PL 重载（其中 2 次重写时钟）和 2 次 JTAG 冷启动结果完全相同（板上 `~/RF-clock/m2_runs.jsonl`，2026-10-14）。尚未把它们固定为标定目标值
  - **延时随 bitstream 变**：run6 上 ADC 延时 1248（tile 0 调整 2），DAC 仍为 1848；两次上电一致。环回延时随之从 212.07 变为 214.08 个样本（+2.0 样本 = ADC 延时 +12 ÷ 抽取 6）
  - [x] **MTS 目标延时固定**（用户定，2026-10-14）：DAC 1896、ADC 1296（`board.py`，`PL.open()` 默认）；DAC、ADC 都按 6 一步调整。run4 和 run6 上串扰环回延时为 226.05 和 226.02 个样本，差 0.03，在标准差内；`MTSResult.ok` 另外要求每个 tile 都落在目标上
  - 混频器读回：ADC NCO −1000 MHz；DAC NCO 也读成 −1000 MHz，按 IMR 高通（NCO 在 fs/2 = 4.8 GS/s 上，3.8 GHz 折叠为 −1.0 GHz）理解，需在频谱仪上确认 5.8 GHz 输出
- [ ] **每次上电 NCO 相位固定**：每次 `PL.open()` 之后，DAC 和 ADC 的 NCO 各自从相差 120° 的三个相位之一开始（纯粹的载波相位旋转；MTS 和时序都不变）。在 MTS 之后让两者都在 SYSREF 沿上重新起相（mixer 事件源设为 SYSREF，再调用 ResetNCOPhase），M3 12 轮全部只剩一个相位；只做其中一个，仍有三个（2026-10-19，`results/lmx-sync-loopback-20261019/` 第 4 节）。IP 是在一次时刻任意的 tile 事件上启动 NCO 的（同样的重新起相改用 tile 事件做，仍有三个相位）；PG269 规定在 MTS 之后单独用 SYSREF 同步混频器和 NCO 相位复位。任何载波相位计时或标定之前都需要它
  - [x] 放进 `PL.open()`（rf-clock 1.3.0.5，`rf_clock.pl.rfdc.sync_nco_phase`，PG269 Use Case 1）：887329260072A 上 8 轮全部同一个相位，其中 7 轮在重启之后（2026-10-20）
  - [ ] 在 887329260101 上确认
- [ ] **DAC 输出 5.8 GHz**：频谱仪上看到 5.8 GHz（以及 3.8 GHz 分量，由 BPF 抑制）；记录输出功率，与 mix-mode 在 0.604 fs 的预期比较
- [ ] **ADC 能收 5.8 GHz**：注入 5.8 GHz 单音，确认在基带出现在预期频率；记录 SNR
- [x] **DAC beat 内布局**：8 个样本的 lane 顺序和 I/Q 位置
  - 完成标准：每条 lane 不同的 ramp + 单 lane 冲激，有线环回逐位核对，写入 `docs/ABI.md` §4.5.8
  - 结论：DAC 按 I/Q 逐字交错读取，第 n 个样本 = `{Q[32n+31:32n+16], I[32n+15:32n]}`（`TX_LAYOUT`）。依据：`rf_clock.checks.m3_loopback --lane-check`（2026-10-14，靠板上 DAC→ADC 串扰，未接环回线）：按平面格式打包时 8 条 lane 的延迟为 D − ⌈k/2⌉（211.83 … 208.05），改用交错格式后 8 条 lane 一致（极差 0.19–0.25 个样本）；软件模型 `tests/test_loopback_model.py` 复现了同一规律。冲激只放在 I 上，一对 16 位中 I/Q 的归属未单独验证
  - 极性：DAC 混频器旁路时 I = +满幅在 DAC_A 口输出负电压（示波器，2026-10-14），即板上输出相对 I 反相。做相位/共轭分析时计入
  - 影响：此前 TX 按平面格式打包，M2 发出的是被打乱的波形；M2 只验证时刻，结论不受影响。`WaveformGenerator.generate_beats` 现在默认用 `TX_LAYOUT`
- [x] **ADC I/Q 合并顺序**：combiner 输出是否为 {Q(m21), I(m20)}；写入 `docs/ABI.md` §4.6.9
  - lane 顺序：平面格式（低 128 位 8 个 I、高 128 位 8 个 Q，lane 0 在低位）与 lane check 一致（`RX_LAYOUT`）。I/Q 归属同上，未单独验证
- [ ] **频谱翻转**：确认接收基带是发射基带的共轭（计划 §3）；BPSK 实信号不受影响
- [x] **RX DMA 持续带宽**：3.2 GB/s 写入，ARM 同时访问 DDR 时窗口不溢出（计划 M3）
  - 依据：`rf_clock.checks.m3_loopback --stress`：5 个 2 ms（6.4 MB）窗口和 3 个 20 ms（64 MB）窗口，ARM 后台拷贝内存约 4.2 GB/s，全部有效（2026-10-14）

## 5. PL 功能（板上）

- [x] **raw_tick**：每秒正好 100,000,000 tick（以 PPS 为参照）
  - 依据：run4 M1，60 个 PPS 周期全部 100000000（2026-10-13）。ARM 原始时钟测得 +108 ppm，是 ARM 晶振的误差
- [x] **PPS 捕获**：`PPS_LAST_PERIOD` 恒为 100,000,000；`PPS_JITTER_CNT` 不增长（若增长，需用 IDELAY 调整 PPS 输入）
  - 依据：run4 连续 3 小时（10801 个 PPS 沿），`PPS_JITTER_CNT` 增量 0、故障增量 0、`pps_locked` 始终为 1（板上 `~/RF-clock/long_run.log`，2026-10-13/14）。P1 在硬件里检查每一个周期，所以这覆盖全部周期，不只是软件每秒采到的那些
  - 此前一次 10 分钟测试在约 23:43:40 UTC 出现 3 个偏离周期（+1590、+80、+130 tick），随后自行恢复、无需重写时钟；像是 10 MHz 参考被短暂扰动，原因未确认（`RF-clock-PC:~/build/m1_run4_600s.log`）
- [x] **TX 定时**：示波器以 PPS 触发，突发相对 PPS 的延迟固定，重启后不变（计划 M2）
  - 数字部分已通过：每个突发 `TX_ACTUAL` = target、= PPS_TICK + 偏移，包括冷启动后（`rf_clock.checks.m2_tx_timing`，2026-10-14）
  - 模拟部分第一次（2026-10-14，rf-clock 1.2.1.0，RIGOL DHO924 250 MHz）：CH1 = FS725 1PPS，CH2 = DAC_A。`m2_tx_timing.py --scope-nco 100 --amplitude 16000 --offset-ticks 1000`（DAC 临时改成 IMR 低通、1 区、NCO 100 MHz）；多次突发都在 PPS 上升沿后 **10.2 µs**（设定 10 µs + 约 200 ns 固定延时），没有抖动和跳变。突发可见长度 40.8 µs（按格读；理论 40.97 µs，>10% 包络）。50 ns/div 下 PPS **下降沿**到突发起点稳定在约 212 ns，即 FS725 1PPS 脉宽约 10.0 µs；P1 定时用的是上升沿，下降沿不作基准。
  - `--offset-ticks 0`（突发排在 PPS 上升沿那一拍），50 ns/div 光标测 PPS 上升沿 50% → 突发起点：222.5、226.5、226.5、225.5、222.5 ns，平均 **224.7 ns**，峰峰值 4 ns（手动读数误差量级；没有 10 ns 的 tick 跳变）。包含 PPS 输入链路、DAC 数据通路（scope 模式）、RRC 爬升约 35 ns。
  - **重启一致性（M2 验收）**：`m2_tx_timing.py --waveform rect --scope-nco 100 --amplitude 32000 --offset-ticks 0`（矩形波形，scope 模式细混频器缩放 1.0），不带 `--no-clocks`（每次重新加载 bitstream、重写 LMK/LMX、pl_resetn0、重做 MTS）重复 3 次；示波器 Measure → Delay（CH1 PPS 上升沿 → CH2 突发第二个峰；第一个峰只有半幅），每次 **192.5–193 ns**，统计标准差 < 1 ns（2026-10-14，rf-clock 1.2.1.0，MTS 目标 DAC 1896 / ADC 1296）。与 PRN 目测值 224.7 ns 相差约 32 ns，即 RRC 爬升段
  - 冷启动后（2026-10-14）：之后三次运行（各自重写时钟）MTS 结果与之前完全相同（DAC 1896/调整 8，ADC 1296/调整 10）。示波器读数：第三个峰 203.5–204 ns（干扰导致改用第三峰，= 第二峰 + 10 ns
    + 约 1 ns），第二个峰 194.00 ns，比冷启动前的 192.5–193 ns 多 1–1.5 ns。原因待分：测量阈值变化，或下面这一项（每 0.42 ns 一步）
  - **LMK 零延时模式未开**：`LMK04828_100.0.txt` 中 0x13F = 0x02（FB_MUX = DCLKout8 但 FB_MUX_EN = 0，PLL1 N 取 OSCin）。VCO 2400 MHz ÷ 24 = 100 MHz，分频器起点不固定时 tick 相对 FS725 10 MHz/PPS 可落在 24 个相位之一（间隔 0.42 ns），即"PPS 亚 tick 相位"可能随每次写时钟而变。实测约 6 次写时钟都在 1.5 ns 内，不像均匀随机。示波器设置不动、热重启 5 次：197.00、194.50、186.50、196.50、197.00 ns，跨约 10.5 ns ≈ 1 tick，MTS 每次相同 → 确认是 tick 相位随写时钟而变
  - [x] **打开 LMK 嵌套零延时**（2026-10-14）：`LMK04828_100.0.txt` 0x13F = 0x0B、PLL1_N = 120（R = 12，鉴相 833.3 kHz 不变）；`make_100mhz_config(mode="ext10m")` 生成；`PL.open()` 改为把包里的寄存器填进 xrfclk 的 `_Config` 再写（`board.program_clocks`），板上不再依赖 xrfclk 目录里的拷贝。板上：M1 30 s（周期全为 1e8、抖动 0）、M2 5 次 PASS，MTS 不变发布为 **rf-clock 1.2.1.1**（只改 Python/包数据，配 1.2.1.0 bitstream；用户定）；1.3 也包含这些改动
  - 零延时后用同样方法热重启 5 次：197.5、194.5、193.0、197.5、201.0 ns，且第一个峰前出现更小的峰。**测量方法有缺陷**：`--scope-nco 100` 每次运行重设 NCO，突发起点的载波相位每次不同（0–360° = 0–10 ns），按"第 N 个峰"/阈值读数会在一个载波周期内跳。零延时前的 186.5–197 ns 也可能主要是这个原因，所以"tick 相位随写时钟而变"未被证实（零延时按数据手册保留，无害）
  - 改用与相位无关的方法：`--scope-nco 0`（粗混频器旁路，DAC 直接输出 I）+ `--waveform square`（I 为 25 MHz 方波，第一个边沿即突发起点）。板上冒烟测试 PASS
  - [x] **示波器复测（方波、基带）**（2026-10-14，rf-clock 1.2.1.1，LMK 零延时，MTS 目标 DAC 1896 / ADC 1296）：`m2_tx_timing.py 120 --waveform square --scope-nco 0 --amplitude 32000 --offset-ticks 0`，热重启 5 次（每次重载 bitstream、重写时钟、重做 MTS），示波器 Delay（CH1 PPS↑ → CH2 第一个边沿）：**180.00、180.00、180.00、180.00、180.00 ns**。第一个边沿是下降沿：DAC_A 输出相对 I 反相截图 `images/tests/pl_m2_tx_timing.png`（CH2 = PPS，CH1 = DAC_A，1 MΩ；两路同开时采样率 625 MSa/s，1.6 ns/点，所以重启间差异在该分辨率以内）
  - 待做（可选）：冷启动一次确认 180.00 ns；用旧（非零延时）LMK 镜像加同样方法测几次，看零延时是否必需
  - 待做：正式 5.8 GHz 配置下的绝对延时（scope 模式数据通路不同，要用 ≥ 6 GHz 的示波器、检波器或 M3 接线环回标定）
- [ ] **有线环回**：本板 DAC → 衰减器 → ADC，相关得到的延时稳定（计划 M3）
  - 未接线时，板上 DAC tile 2 → ADC tile 2 的串扰已能测出相关峰：延时 212.07 个样本（265.1 ns），10 次标准差 156 ps，PSL ≥ 16 dB（`rf_clock.checks.m3_loopback`，2026-10-14）。待接线后测：标准差、多次重启（含冷启动）的一致性
  - 接线后（2026-10-19，887329260072A：DAC_A → 20 dB → 5725-5875 MHz 带通 → ADC_B）：288.1 ns，PSL ≥ 20 dB。TX 幅度 4096 时包络延迟每轮（30 个窗口）离散 4-6 ps，16000 时约 1 ps，所以 1.3.0.5 起 M3 的默认幅度为 16000。载波相位法在每片 LMX2594 档位不变时每轮约 0.1 ps；换一个 VCO 电容档会让时间移动 8–10 ps（第 2 节 LMX2594 各项）。冷启动和另一块板还没做
- [ ] **双向时间比对**（计划 M4）：`rf_clock.twtt`（rf-clock 1.3.0.7，docs/ABI_zh.md §11.12）。每秒 A 在自己的 PPS 沿后 100 ms 发、B 收，B 在 600 ms 发、A 收；每块板对自己的窗口做相关，另一个进程经 TCP 与另一块板交换记录，并按 PPS 沿的 NTP 系统时间配对（2026-10-20 起 RF-clock-PC 为两块板提供 NTP）。`experiments/m4_twtt.py` 从 RF-clock-PC 启动
  - [x] 887329260072A 上的自测，一块板经自己的回环线同时充当两个节点（2026-10-21，`results/m4-twtt-self-20261021_000846`）：300 秒全部有效，theta -0.2 ps（预期为 0），sd 4.51 ps，单程延时 288.0617 ns（即 M3 的回环延时）；theta 的 Allan 偏差从 1 s 的 8.1e-12 按 1/tau 降到 128 s 的 6.1e-14，是白相位噪声，没有漂移。两台机器之间的 link 用合成记录测过（887329260072A 和 RF-clock-PC）
  - [x] 两块板（2026-10-21，A = 887329260072A，B = 887329260101，A 的 DAC_A → B 的 ADC_B，反向同样，共用一台 FS725；`results/m4-twtt-pair-20261021_084328` 60 s，`_084510` 600 s）：每一秒都配对成功且有效。600 s 内 theta +9.8778 ns，sd 2.95 ps，单程延时 288.1620 ns；theta 的 Allan 偏差从 1 s 的 5.0e-12 按 1/tau 降到 256 s 的 1.9e-14。用三倍载波相位算，theta 的变化为 0.64 ps rms（Allan 偏差 1 s 1.5e-12，256 s 4.8e-15），每个方向约 1 ps，而单板回环只有 0.1 ps：推测是两块板的射频时钟相对漂移（推断）。theta 约为一个 tick，因为 887329260072A 的 PPS 晚一个 tick 被捕获（它那路 EPG1 输出延时 6.6 ns）
  - [x] 常数跨上电是否不变：上面两次运行之间 m_BA（B 的 DAC → A 的 ADC）变了 +104.1 ps，即一个 DAC 采样，theta 变了 -51.3 ps，而 m_AB 变化不到 1.5 ps。887329260101 的 DAC T1 捕获码从 54/53 变成了 73/73，MTS latency 不变：它的两个候选 T1 窗口（相隔一个 DAC 采样，约 20 个码）离码 64（驱动自己的目标）几乎一样远，相位稍有变化就会选到另一个（推断）。rf-clock 1.3.0.8 把每个 tile 的捕获码记进板级配置（`rf_clock.checks.mts_dtc`，docs/ABI_zh.md §7.1 第 7 步），让 MTS 选离它们最近的窗口（`XRFdc_MultiConverter_Init` 传入码，即驱动的 reload 扫描）。在 887329260101 上验证（2026-10-21）：故意把目标设成另一个窗口（DAC 73，ADC T1 96/99）时，MTS 选了 74/73 和 97/100；记录的码为 DAC 54/53、ADC T1 59/62、PLL 68/73（887329260072A：62/61、62/65、72/76）。两块板每次都重新上电、各 30 s 的 M4 跑了 6 次（`results/m4-twtt-pair-20261021_094100` 到 `_094542`）：theta 9.9256–9.9295 ns（均值 9.9280），m_BA 在 3.4 ps 以内，DAC 码都在记录值的 1 个码以内。这 6 次里不固定码时驱动多半也会选同一个窗口（不带码上电 8 次都选了 54）：这项修复的依据是 reload 测试
  - [ ] 更长时间：多次上电和不同温度下的 theta；零基线常数（Delta）存在哪里（计划第 8 节）

## 6. FS725

- [ ] **RS-232 修正命令**：频率偏移命令的量程、分辨率、单位（FS725/PRS10 手册）
- [ ] **PPS 粗对齐能力**：能否一次性把 PPS 相位跳到目标附近（计划 §10 第 4 项）
- [ ] **ARM 能控制 FS725**：串口连接方式（USB 转串口等）和驱动
- [x] **M4 的共用参考**（2026-10-15/16）：FS725 10 MHz 输出 1 → EPG1 REF IN；EPG1 REF OUT（10 MHz 方波，已锁定）→ 电阻式功分器（Mini-Circuits ZFRSC-123-S+）→ 两块板的外部参考，线缆等长。FS725 1PPS → EPG1 TRIG IN，EPG1 输出 1 → S/N 887329260101（.101）的 J7，输出 2 → S/N 887329260072A（.99）的 J7（2026-10-18 核实）。`experiments/epg1.py pps` 设为外部触发、10 µs 正脉冲、50 Ω 上 2.5 V；`experiments/epg1.py status` 读回全部设置。两块板（rf-clock 1.3.0.1，重写时钟）同时跑 M1 120 s：每个周期都是 100 000 000 tick，抖动 0，故障 0，pps_locked 始终为真
  - 弃用的接法：FS725 10 MHz 输出 2 → 同一个电阻功分器 → 两块板。功分器 6 dB 损耗后每块板只有 0.7 Vpp 的慢边沿正弦，PLL1 反复失锁，LMK 进入 holdover（寄存器 0x150 = 0x11，holdover DAC 固定为 512，TRACK_EN = 0），VCXO 约偏 −0.1 ppm：一块板周期恒为 99 999 990 tick，另一块在 0/−10 之间交替（锁定/holdover）
  - EPG1 用自己的时钟重采样 TRIG IN（规格：触发输入到输出 2.5 ns 峰峰值抖动）。REF IN 还没接时，第一块板 M1（60 s）的周期在 99 999 999 / 100 000 000 / 100 000 001 tick 之间跳，抖动计数 18：延时在 tick 边界两侧来回。EPG1 必须锁到同一台 FS725 的 10 MHz（REF IN，`REFCLK:IN:STATUS` = 1），之后两块板再跑 M1
  - EPG1 命令集：没有公开手册，从 Thorlabs EPG Software 1.0.2 读出（见 `experiments/epg1.py`）。只在 DTR 置位时应答；停止状态下再发 `RUN: 0` 会报错；有些设置之后会短暂回 "Instrument busy"
  - [x] **PPS 捕获裕量**（2026-10-18）：上面的接线是这样确认的：把 EPG1 的每一路输出分别延迟 50 ns，看哪块板的 PPS_TICK 跟着移动 5 个 tick（之前的记录把两路写反了）。`experiments/m1_pps_phase_scan.py` 逐步改变输出延迟，并在每块板上记录 PPS_TICK mod 1e8（`results/pps-phase-scan-20261018_*`）。延迟为 0 时，887329260072A 的边沿压在 100 MHz 的采样沿上：每次采 15 个边沿，有 40-60 % 被提前一个 tick 捕获，M2 也报出 `actual-PPS` ±1。2026-10-16/17 的通宵测试里，这在约 23,000 个边沿中只出现过一次，所以一天之内相位移动了约 1 ns。它还有约 7 % 的边沿提前约 2.5 ns（延迟 2 ns 时仍被提前捕获，3-4 ns 时不再出现），看来是 EPG1 对输入的重采样造成的。887329260101 的边沿距离下一个采样沿约 3.4 ns。`experiments/epg1.py pps` 现在把输出 2 设为 6.6 ns（边沿落在 tick 内 6.25 ns 处，提前的那些在 3.75 ns 处），输出 1、4 为 0；之后两块板各采 68、67 个边沿，全部落在同一个 tick。从此 887329260072A 固定在两个 tick 中靠后的那个捕获 PPS：它相对 PPS 的绝对 TX 延迟可能与以前的测量差 10 ns
  - [x] **EPG1 触发阈值**（2026-10-19）：当天 11:16 起，EPG1 约一半的输出脉冲提前 2.5 ns（示波器触发相对两块板的 burst 有 1.2 ns rms 的抖动，而 burst 本身不动）。EPG1 用 2.5 ns 的时钟重采样 TRIG IN，`TRIGIN:THRESH` 为 1.0 V 时，FS725 1PPS 缓慢上升的边沿恰好在它的一个时钟沿上过阈值。阈值扫描（示波器测 burst 延迟，40 次采集的 rms）：0.25-0.75 V 为 189.02 ns、19-21 ps；1.0 V 为 188.21 ns、1172 ps；1.25-1.5 V 为 186.52 ns、20 ps；1.75 V 为 184.02 ns；2.0 V 时两块板都晚一个 tick 捕获 PPS。`experiments/epg1.py pps` 现在设为 1.3 V（距两侧 EPG1 时钟沿各约 1.25 ns）：19 ps rms
  - [ ] 关注 PPS 相位的漂移（约每天 1 ns）和 EPG1 重采样的裕量（只有 ±1.25 ns）；考虑不经重采样分配 PPS（在 FS725 1PPS 上接扇出缓冲器），使两处裕量都不依赖 EPG1

## 7. 系统设计中尚未决定的事（计划 §10）

- [x] **双向链路**：A、B 互相发送
  - 依据：用户确认
- [ ] **天线**：TX/RX 分开，还是共用加 T/R 开关（若共用，PL 需要定时输出开关控制）
- [ ] **节点间数据通道**：默认以太网，需确认两台 ARM 之间可达
- [ ] **目标精度**：ns 级（码相关即可）还是 100 ps 以下（需要载波相位）
- [ ] **法规**：5.8 GHz 发射功率和占用带宽限制

## 8. 仓库整理

- [x] **删除旧 H 架构的文件**（2026-10-13，用户要求）
  - 已删除：H 系列 RTL/testbench、`FPGA/ip/`（旧 RFDC 配置和 BD 脚本）、`FPGA/src/rf_clock_bd_wrapper.v`、`FPGA/compile_rtl.tcl`、`FPGA/sim/run_testbench.ps1`/`.sh`、`FPGA/data/H09_rrc_coeffs.vh`、`utils/compare_h09_rtl.py`、`handler/RFDC_config.md`
  - 修改：旧的 `utils/gen_h09_rrc_coeffs.py` 已删除，新生成器为 `tools/gen_rrc_coeffs.py`；`.vscode/tasks.json` 改用 `run_pl_tests.ps1`；`project.sigasi` 增加 `PL/src` 为 include 目录；`README.md` 重写
  - 保留：`config/RRC_config.db`（`RRC_config`/`RRC_coeff` 浮点 RRC 系数）；`utils/sim_h09_rrc_fir.py` 仅提供基于该数据库的可选 Plotly 诊断，不再维护旧数据库或旧 RTL/CSV 比对格式
- [x] **`FPGA/` 改名为 `PL/`**（2026-10-14，用户要求）：脚本、配置（`.vscode/tasks.json`、`project.sigasi`、`.gitignore`、`tools/generate_abi.py`）和文档中的路径已改；上面记录已删除文件的条目保留原路径；RF-clock-PC 上 Syncthing 已同步改名
- [x] **overlay 随包发布、版本绑定**（2026-10-14，用户要求；`docs/ABI.md` §14.1）：`src/rf_clock/overlay/`（`.bit`/`.hwh`/`.xsa` 用 Git LFS）+ `manifest.json`，由 `tools/package_overlay.py` 生成；构建脚本把 `pyproject.toml` 版本写进 P2 `BUILD_VERSION`（ABI 1.2，0x94），`PL.open()` 加载前查 manifest、加载后查 `BUILD_VERSION`。版本号四段 `A.B.C.D`（用户定）：A.B = `VERSION`，C = 该协议下第几个 bitstream，D = 只改 Python；A.B.C 必须与安装的包一致。从 1.2.0.0 开始（run6）
- [x] **PL 部件按地址空间划分**（2026-10-14，用户要求）：`pl.ctrl`（原 `pl.p2`，P2 寄存器：全部命令、时间戳、状态）、`pl.tx_buffer`（TX BRAM）、`pl.rx_dma`（`RXDMA`，只管 S2MM）。`ctrl.wait_tx()` 返回 `TX_ACTUAL`，`ctrl.wait_rx()` 返回 `RXWindow` 并做 §7.3 的校验；`rxcap.py` 改为 `rxdma.py`
  - [x] 上板复测 M2、M3（拆分后的 RX 顺序：`rx_dma.prepare` → `ctrl.arm_rx` → `ctrl.wait_rx` → `rx_dma.wait`）：run6 上全部 PASS（2026-10-14）
- [x] **PYNQ 3.1 不能在 `.bit` 旁边放同名 `.xsa`**：它会改走 XSA 分支并报 `UnknownInputFileExtension`；随包的 XSA 改名为 `rf_clock_hw.xsa`（`tools/package_overlay.py`）
- [x] **TX buffer 加深到 69632 beat**（用户要求，2026-10-14）：17 × 4096，放得下 65535 码片的突发（65545 beat）；用 UltraRAM，分成 17 个 4096 beat 的 bank（单块 69632 深的存储 Vivado 不肯推断成 URAM，run7 退回了 548 个 BRAM36），AXI 窗口 0xA040_0000、4 MiB；P3/`p_top` 增加 `DEPTH`/`TX_DEPTH` 参数（不必是 2 的幂），窗口内超出部分的写入丢弃。rf-clock 1.2.1.0（run8）
- [x] **Python 类型标注清理**（2026-10-14，用户反馈 Pylance 报很多 `"X" is not a known attribute of "None"`）：`PL` 的部件、`RFDC.mts`、`PLClient.info` 不再是可空类型（未就绪时访问抛异常）；`ClockStatusIO` 与实现对齐；`pyproject.toml` 加 `[tool.pyright]`（`pythonPlatform = "Linux"`）。`uvx pyright --pythonpath .venv/Scripts/python.exe src tests examples tools`：0 错误
- [x] **TX 重复播放**（用户要求，2026-10-14；ABI 1.3，rf-clock 1.3.0.0）：新寄存器 `TX_REPEAT`（0x5C，shadow，1 … 65535，复位 1）；一条命令把 `TX_LENGTH` 个 beat 首尾相接播放 `TX_REPEAT` 遍，遍与遍之间没有空拍，`TX_ACTUAL` 为第一遍起点，全部播完才 DONE。Python：`arm_tx(target, beats, repeat=n)`（1.3 之前的 bitstream 上不写这个地址）、`WaveformGenerator(periodic=True)`（循环 RRC 卷积，无拖尾，65535 码片 = 65535 beat）
  - [x] RTL 和仿真：P3 testbench 测 3 遍 × 5 beat、2 遍 × 满深度（从最后一个 bank 绕回第一个）、65535 遍 × 1 beat，以及 0 和 65536 被拒；反向检查（新一遍从第 1 个 beat 开始）会失败
  - [x] 构建 run9（`RF-clock-PC:~/build/rf_clock_impl_run9`）：WNS 0.322 ns，WHS 0.012 ns，无 critical warning；URAM 68/80，BRAM36 10.5/1080
  - [x] 打包、部署（板上 1.3.0.0，2026-10-14）；例子 03、M1（12 s）、M2（5 次）、M3 压力（3 × 6.4 MB）PASS
  - [x] 上板：周期 65535 码片 × 10 遍由一条命令连续发出（6.55 ms），一个窗口收下；每遍单独相关，10 个延时在 231.990–232.061 个样本（差 0.071），PSL 22–23 dB；`TX_ACTUAL` = target，`TX_SEQ` 只加 1。遍间多一个空拍会让后面每遍延时累加 8 个样本，所以遍间无缝
  - M3 延时这次 FAIL（231.95 个样本、标准差 684 ps、最小 PSL 5.1 dB），run8 在同样条件下结果相同：原因是接线变了（示波器测试时改成 DAC → 示波器 CH1 三通 → 用户所说的 "ADC_1"），不是 run9。TX 幅度加大 31 倍 |rx| 仍在噪声水平，说明线缆没接到接收口 ADC_B（推断接的是 ADC_A），只改变了串扰通道。正式的 M3 要 DAC_A → SMA 线 → ≥ 20 dB 衰减器 → ADC_B，不经示波器三通
- [x] **提交**：rf-clock 1.2.1.1、换行统一为 LF、rf-clock 1.3.0.0（提交号见 `git log`；历史改写过不止一次）
- [x] **本地设置移出仓库**（2026-10-14，用户要求）：`.codegraph`、`.cursor`、`.sigasi`、`.vscode`、`.editorconfig`、`project.sigasi` 用 `git filter-repo` 从全部历史中删除，加入 `.gitignore`，文件只留在本机；全部提交重新签名，`main` 与 `v2-python-overlay` 已 force push
- [x] **示例和板上检查进包**（2026-10-14，用户要求）：`examples/` → `rf_clock.examples`，`tests/pl/` → `rf_clock.checks`（`python -m rf_clock.checks.m2_tx_timing ...`）；单元测试仍在 `tests/`，不进 wheel。wheel 里有库、overlay、时钟文件、examples 和 checks，板上装 wheel 即可用（`docs/ABI.md` §9.1）
- [x] **RRC 系数改为现算**（2026-10-14，用户定）：`WaveformGenerator(beta, span_chips, samples_per_symbol)` 用 `rrc_config()` 现算（板上 1.6 ms，开发机 75 µs）；`config_id` 仍为参数哈希（默认 2134958710）。删除 `config/RRC_config.db` 和 `tools/gen_rrc_coeffs.py`；`tests/test_waveform.py` 存一份原数据库里的 81 个系数作黄金值（现算结果与之逐位相同）
- [x] **beat 打包提速**（2026-10-14，用户要求）：`pack_samples()` 收到整数 NumPy 数组时用数组运算打包，不再逐个样本走 Python 循环。板上（在 `/tmp` 运行，未安装任何东西）4096 码片的打包从 1.22 s 降到约 20 ms，65535 码片从 19.5 s 降到约 0.18 s；`generate_beats()` 现在分别是 52 ms 和 0.70 s，主要花在计算样本上。两种长度的 beat 都与逐样本路径完全相同（SHA-256 一致），`tests/test_beats.py` 在三种 layout、不满的最后一个 beat 和 16 位边界值上比较两条路径
- [x] **删除 CASPER 时钟文件**（2026-10-14，用户要求；许可证未知）：从文件树和 git 历史中删除 `LMK04828_casper_ext10m.txt`；生成器以 `LMK04828_100.0.txt` 自身为输入能原样重新生成它，测试改为验证这一点；来源说明在 `src/rf_clock/clocks/files/README.md`
- [x] **文档以英文为标准**（2026-10-14，用户要求）：`docs/ABI.md`、`plan/checklist.md`、示例 README 为英文，中文翻译在 `*_zh.md`；代码里的文档字符串和注释全部英文。删除 `plan/handoff_*.md`，其中仍有用的内容移入 `docs/ABI.md` §9.1（板上环境）和 `src/rf_clock/clocks/files/README.md`（时钟方案）
- [x] **GitHub 发布**（2026-10-14）：推送 `vA.B.C.D` tag 时 `.github/workflows/release.yml` 校验版本和 overlay、`uv build`、建 Release（wheel、sdist、`manifest.json`）；发布说明由 `tools/release_notes.py` 生成（`docs/ABI.md` §14.1）。rf-clock 1.3.0.1
