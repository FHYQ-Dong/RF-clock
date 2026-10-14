# RF-clock 待确认清单

逐项确认，完成后打勾，并在该项下写明**结论和依据**（文件、命令输出、测量记录或
文档出处）。没有依据的不打勾。确认结果若改变接口或设计，同步修改
`docs/ABI.md`、`plan/RF_clock_modules_zh.md`（及英文版）。

最后更新：2026-10-13

## 1. 板卡与参考资料

- [x] **板卡型号**：Real Digital RFSoC 4x2，芯片 xczu48dr-ffvg1517-2-e
  - 依据：用户确认；旧脚本 `FPGA/ip/generate_rfdc_ip.tcl`（已删除）中的 part；ssh 配置中的
    `RFSoC-4x2-ai`（192.168.2.99）
- [x] **Vivado board files 来源**：RealDigitalOrg/RFSoC4x2-BSP `board_files/`，
  board part `realdigital.org:rfsoc4x2:part0:1.0`
  - 依据：已 clone 到 `RF-clock-ai:~/ref/RFSoC4x2-BSP`（不进仓库）
- [x] **PL_CLK / PL_SYSREF 管脚**：LVDS 差分对，`FPGA_REFCLK_IN_P/N` = AN11/AP11，
  `SYS_REF_FPGA_P/N` = AP18/AR18（4x2 手册附录 A；HP bank 65/66，GC 管脚）
  - run2/run3 照抄 RFSoC-MTS `mts.xdc`，只把 P 端当 LVCMOS18 单端收。run2（未用管脚
    PULLNONE）MMCM 时锁时不锁；run3（PULLUP）180 s 内反复复位都不锁，最可能是
    N 端被上拉
  - run4：两对都用 IBUFDS、LVDS、`DIFF_TERM_ADV TERM_100`、`DQS_BIAS TRUE`；
    `UNUSEDPIN` 改回 PULLNONE。完成标准：上板 MMCM 每次都锁，`PPS_LAST_PERIOD` 恒为 1e8
  - 依据：run4 上板两次 bring-up，MMCM 都在 `pl_resetn0` 后 1 s 内锁定；M1 见 §5（2026-10-13）
- [x] **PPS 输入管脚**：`IRIG_COMP_OUT` = AJ13，LVCMOS18，为板上 1PPS 比较器输出
  - 依据：RFSoC4x2-BSP `hw/constraints/4x2_1PPS.xdc`
- [x] **1PPS 比较器阈值和输入电平**：比较器阈值是固定的，还是需要 PL 通过
  `IRIG_TRIG_OUT` / `IRIG_ADC_*` 设置；FS725 的 1PPS 幅度是否在该输入允许范围内
  - FS725 侧已确认（手册 Specifications）：1 pps 输出 2.5 V 接 50 Ω、5 V 接高阻，脉宽 10 µs，
    50 Ω 源阻抗
  - 4x2 侧未知：手册 Figure 6 只画了“SMA → 电阻分压 → LMV7235，阈值由 3.3 V 分压给出”，
    没有阻值；需上板测 PPS SMA 对地电阻和比较器输出极性
  - 完成标准：查 4x2 板卡手册或原理图得出结论；如需 PL 控制，补充管脚和逻辑
  - 首次上板（2026-10-13，run2 bitstream）：`PPS_COUNT` = 0，PL 一个沿都没收到。
    LMV7235 是开漏输出（推挽的是 LMV7239），而 run2 的 `pps_in` 没有上拉
  - run3 修改：AJ13 加 `PULLTYPE PULLUP`；加第二路 `IRIG_TRIG_OUT`（AH13，施密特触发器）；
    ABI 1.1 新增 `PPS_CONFIG`（选路/反相）和 `PPS_INPUTS`（两路电平和沿计数），
    极性和可用的那一路由软件确定，不再重出 bitstream；`UNUSEDPIN` 改为 BSP 的 PULLUP
  - FS725 1PPS 用示波器看到输出，脉宽约 30 µs；幅度读数 500 mV 与手册（高阻 5 V）
    不符，待确认探头倍率/输入阻抗
  - 原理图（`RF-clock-ai:~/ref/docs/sch/rfsoc4x2_sch.pdf` 第 10 页 1PPS）：J7 → 49.9 Ω 对地
    → 100 Ω → 10 nF 交流耦合 → 1.65 V 偏置节点 → LMV7235（迟滞约 ±0.17 V，输出 1 kΩ 上拉到
    3.3 V）和 SN74AUP1G17；NVT2008 转 1.8 V，1.8 V 侧无上拉（FPGA 上拉必要）。J7 上零点几伏
    的脉冲就够比较器翻转。前面"比较器开漏、板上无上拉"的判断不准确
  - run4 上板（2026-10-13）：comp 空闲 0、trig 空闲 1，两路 0 个沿，即 J7 上没有脉冲。
    待查：线是否接在 J7（网络名 IRIG），板端用 T 头量幅度（50 Ω 终端下应约 2.5 V）
  - 结论：之前 PPS 线没接到 J7；接好后板端约 2.5 V，两路都每秒一个上升沿、空闲为 0
    （正脉冲），用默认 `PPS_CONFIG` = 0（比较器、不反相）
- [x] **4x2 板上引出的转换器**：DAC tile 0、2 各 1 路（`vout00`、`vout20`）；
  ADC tile 0、2 各 2 路（`vin0_01`、`vin0_23`、`vin2_01`、`vin2_23`）。tile 0 和
  tile 2 都有自己的参考时钟，PLL 都可用
  - SMA 口：`vout20`（`DAC_VOUT0_230`）= **DAC_A**，`vout00`（`DAC_VOUT0_228`）= DAC_B（原理图
    `~/ref/docs/sch/rfsoc4x2_sch.pdf` 与手册引脚表一致；手册框图写反了，2026-10-14）。ADC：
    `vin2_01`（tile 226 `VIN_I01`）= **ADC_B**，`vin2_23` = ADC_A，`vin0_01` = ADC_D，`vin0_23` = ADC_C。
    原理图旧标号 DAC0/DAC1、ADC0…ADC3 依次对应 DAC_A/DAC_B、ADC_A…ADC_D（DAC1 = DAC_B，ADC1 = ADC_B）。
    板上丝印就是 DAC_A/B、ADC_A…D（用户确认）：发射 DAC_A，接收 **ADC_B**
  - 依据：Xilinx RFSoC-PYNQ `boards/RFSoC4x2/base/base.tcl`（`RF-clock-ai:~/ref/pynq4x2/`），
    `ADC0/ADC2/DAC0/DAC2_PLL_Enable true`，以上 6 个模拟端口
  - 修正：此前从 RFSoC-MTS 参考设计推断"只有 tile 2 接时钟"是错的；该参考设计
    只是选择了从 tile 2 分发时钟
- [ ] **RFSoC 4x2 板卡手册/原理图**：拿到正式文档，确认外部参考 SMA 接的是
  LMK04828 的 CLKin0、各 LMX2594 分别给哪个 RFDC tile 供参考时钟

## 2. 时钟链（FS725 → LMK04828 → LMX2594 → RFDC / PL）

- [x] **4x2 能否接外部 10 MHz**：可以，接 LMK04828 CLKin0
  - 依据（二手，尚未在本板验证）：PYNQ 论坛，CASPER 文件
    `rfsoc4x2_lmk_CLKin0_extref_10M_PL_122M88_LMXREF_245M76.txt` 已被多人验证可用
- [x] **FS725 10 MHz 接入 4x2 的外部参考 SMA**：幅度、阻抗符合输入要求
  - 依据：写入 `LMK04828_100.0` 后 PLL1、PLL2 和两个 LMX LED 全亮（2026-10-13）；
    CLKin0 是手动选择，PLL1 只能锁在 FS725 上
  - FS725：正弦，0.5 Vrms ±10%（约 1.41 Vpp，+7 dBm）接 50 Ω
  - `LMK04828_100.0.txt`：CLKin0 手动选择、送 PLL1；CLKin0_R = 12，PLL1_N = 192
    （10 MHz/12 = 160 MHz VCXO/192 = 833.33 kHz）
- [x] **生成 LMK04828 寄存器文件**（开发机 Python 生成，待 TICS Pro/上板复核）：CLKin0 = 10 MHz，PLL1 锁定到
  外部参考；PLL2 VCO 2400 MHz；PL_CLK 100 MHz；给 LMX 的参考；RFDC 与 PL 的 SYSREF
  - 依据：`src/rf_clock/clocks/lmk04828.py`、`src/rf_clock/clocks/files/LMK04828_100.0.txt`；目标字段为
    VCO0、PLL2_R=2、PLL2_P=2、PLL2_N=15，160 MHz VCXO 推得 2400 MHz；DCLKout8=100 MHz，LMX 参考支路=480 MHz，
    SYSREF 分频=480。TICS Pro 显示和 SPI/锁定尚未完成。
- [x] **确定 SYSREF 频率**：5 MHz；按 PG269 的 MTS 要求核对它是 PL 时钟
  （100 MHz）、RFDC 参考时钟（480 MHz）、采样时钟（9.6 / 4.8 GS/s）等的整数分之一
  - 依据：AMD PG269 v2.6 SYSREF Signal Requirements（频率严格 <10 MHz）；`tests/test_clocks.py` 验证
    5 MHz 整除 100/480/4800/9600 MHz。10 MHz 因严格小于 10 MHz 的要求排除；MTS 连续运行和相位仍待上板。
- [x] **生成 LMX2594 寄存器文件**：输出 480 MHz 给 ADC tile 0/2、DAC tile 0/2
  - 依据：`src/rf_clock/clocks/files/LMX2594_480.0.txt`；整数-N 配置在 480 MHz 参考下为 7.68 GHz VCO、
    16 分频得到 480 MHz；`tests/test_clocks.py` 解码复核。
- [x] **板上验证 PLL1 真正锁定到 FS725**：拔掉 10 MHz 后 PLL1 LED 熄灭；
  `PPS_LAST_PERIOD` 恒为 100000000（防止 LMK 靠板上 VCXO 继续输出而误以为正常）
  - 依据：run4 M1 60 个周期全部 100000000，fabric 时钟与 FS725 同源（2026-10-13）；
    拔线测试未做（远程时可以跳过）
  - 本项目的 LMK 文件没有配置 SPI 读回；Status_LD1/2 = PLL1/PLL2 锁定，驱动板上 LED
- [ ] **所有时钟同源**：DAC/ADC 采样时钟、PL_CLK、fabric 时钟都由 FS725 10 MHz 派生；
  用频率计或示波器核对

## 3. Vivado 构建（`PL/bd/create_rf_clock_bd.tcl`）

- [x] **RTL 仿真**：P0–P4、P6 与 `p_top` 的 5 个 testbench 全部通过
  - 依据：`./PL/sim/run_pl_tests.ps1`（iverilog），含 TX 延迟改错 1 拍的反向检查
- [x] **RFDC 5.8 GHz 配置能被 IP 接受**：DAC 9.6 GS/s ×12、NCO 3.8 GHz、IMR 高通、
  mix-mode；ADC 4.8 GS/s ÷6、NCO −1.0 GHz；AXIS 100 MHz；tile 0/2 MTS 打开
  - 依据：`RF-clock-ai:~/build/probe/probe19.log`（仅 IP 参数校验，不代表板上验证）
- [x] **RFDC MTS 与 DAC 采样率**：最终用 tile 0 + tile 2、各自 PLL、DAC 9.6 GS/s
  （用户决定，2026-10-13；取代中间方案 B"tile 0–2 分发时钟、DAC 6.4 GS/s"）
  - 第一次 BD 运行失败原因：`ADC2_Multi_Tile_Sync=false, expected true`
  - 探测结论（Vivado 2024.1，RFDC 2.6，脚本在 `RF-clock-ai:~/build/probe/`）：
    - 只启用一个 tile 时，IP 禁用 MTS 参数，也不生成 `user_sysref_adc/dac` 引脚
    - 每个参加的 tile 的**通道 0（slice x0）必须启用**，否则该 tile 的 MTS 被禁用
      （ADC 放在 slice x2 时 MTS 一直打不开，换到 slice x0 后立即可以）
    - DAC tile 参与片上时钟分发时，采样率上限 7.0 GS/s
    - tile 0 + tile 2 各用自己的 PLL、不分发时钟：ADC、DAC 的 MTS 都能打开，
      DAC 9.6 GS/s 被接受（`probe19.log`）
  - 最终配置（`PL/bd/create_rf_clock_bd.tcl`）：ADC/DAC tile 0、2 启用，参考时钟
    各 480 MHz；DAC 9.6 GS/s、IMR 高通、插值 ×6（共 ×12）、NCO 3.8 GHz、mix-mode；
    ADC 4.8 GS/s、I/Q、抽取 ×6、NCO −1.0 GHz，输入为 `vin2_01`；tile 0 只为 MTS
- [ ] **tile 0 和 tile 2 的参考时钟同源**：MTS 要求两个 tile 的采样时钟出自同一
  参考（LMK/LMX），且 SYSREF 共用；按板卡原理图和 LMK/LMX 配置确认
- [ ] **ADC 输入口**：ADC 从 `vin2_23` 改到 `vin2_01`（tile 2 通道 0），确认它对应
  4x2 上哪个 SMA
- [ ] **tile 0 的代价**：功耗、发热；DAC tile 0（`vout00`，板上的另一个 DAC 口）输出
  恒为 0，是否需要在运行时关闭
- [x] **BD 阶段通过 `validate_bd_design`**，无 critical warning
  - 依据：`RF-clock-ai:~/build/rf_clock_bd_run3`（status `bd=complete`）
  - 途中修复：AXI DMA `c_prmry_is_aclk_async` 为只读，已从脚本删除；`p_top` 的
    BRAM 端口补充 `READ_WRITE_MODE` 等存储器参数
- [x] **RFDC over-range 引脚名**：`adc2_01_over_range`（ADC tile 2 通道 0）
  - 依据：`RF-clock-ai:~/build/rf_clock_bd_run3/rfdc_pins.txt`；需保持
    `ADC_RTS = true`，否则 over-range 引脚不生成
- [x] **地址表与计划一致**：P2 0xA000_0000、DMA 0xA001_0000、时钟状态 GPIO
  0xA002_0000、RFDC 0xA004_0000、TX buffer 0xA008_0000；DMA 可访问 HP0 的
  DDR_LOW（0–2 GiB）和 DDR_HIGH（0x8_0000_0000 起）
  - 依据：`RF-clock-ai:~/build/rf_clock_bd_run3/address_map.txt`；`docs/ABI.md` §1.2
    已去掉"计划值"说明
- [x] **综合通过**
  - 依据：`RF-clock-ai:~/build/rf_clock_impl_run2/status.txt`（`synth=complete`）
- [x] **实现通过且时序满足**：WNS 0.246 ns，WHS 0.012 ns；no_clock、未约束端点均为 0
  - 依据：`RF-clock-ai:~/build/rf_clock_impl_run2/timing_summary.rpt`
  - CDC：8 条 critical 全部在 RFDC IP 内部（AXI 配置寄存器到 tile 控制端口；fabric 复位
    和 SYSREF 进入 IP 自带的 `xpm_cdc` 同步器），由 IP 约束处理，与 RFSoC-MTS 参考
    设计一致；`p_top` 的跨时钟域路径均为安全（`cdc_critical_details.rpt`）
  - 约束中有 3 条 `set_max_delay` 没有匹配到对象（critical warning），已从
    `PL/constraints/rf_clock_4x2.xdc` 删除；fabric 与 DMA 时钟同出一个 MMCM，这些路径
    按同步路径分析并已满足时序。下次构建应无 critical warning，待验证
- [x] **导出 `rf_clock.bit`、`rf_clock.hwh`、`rf_clock.xsa`**
  - 依据：`RF-clock-ai:~/build/rf_clock_impl_run2/`（tile 0 + tile 2、DAC 9.6 GS/s 配置）
- [x] **关闭构建机睡眠**：已 mask `sleep/suspend/hibernate/hybrid-sleep/
  suspend-then-hibernate.target`，GNOME 空闲挂起设为 `nothing`
  - 依据：`systemctl is-enabled` 均为 masked（2026-10-13）
- [ ] **远程构建机网络稳定**
  - 日志显示本次开机后**没有发生过睡眠**。13:16 的重启是按了电源键关机；14:01
    前后的断线是 USB Wi-Fi 网卡（`wlx38a38ca06de0`，YaleSecure）掉线，
    `reason=4 locally_generated=1`，并且 Wi-Fi 省电模式开着（`wifi.powersave = 3`）
  - 已将 `/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf` 改为
    `wifi.powersave = 2`（用户同意，2026-10-13）；NetworkManager 不支持在线
    reapply，下次 Wi-Fi 重新连接后生效。观察之后是否还掉线，再决定这一项是否打勾
- [ ] **Syncthing**：新建的 `FPGA/bd/`、`FPGA/constraints/` 没有同步到远程（目前
  用 scp 手动拷贝），需查明原因
- [x] **`include "src/..."` 在 Vivado 里可用**：module reference 的 HDL 解析器不看
  `include_dirs`，按工作目录解析 include；脚本在 `create_bd_cell -reference` 之前
  `cd` 到 `PL/`（探针 `RF-clock-ai:~/build/probe/inc_probe.tcl`，变体 d 通过）
- [x] **run3 构建**（ABI 1.1、PPS 上拉、第二路 PPS）：`RF-clock-ai:~/build/rf_clock_impl_run3`
  - WNS 0.372 ns，WHS 0.012 ns，无 critical warning；AJ13/AH13 为 LVCMOS18 + PULLUP
    （`*_io_placed.rpt`）；已部署到板上 `~/RF-clock/overlay/`，run2 留在 `overlay/run2/`
  - 上板：MMCM 不锁（见上面 PL_CLK 一项）；未用管脚上拉把 PL RGB 灯和 LED 点亮
- [x] **run4 构建**（PL_CLK/PL_SYSREF 差分接收、`UNUSEDPIN PULLNONE`）：
  `RF-clock-ai:~/build/rf_clock_impl_run4`
  - WNS 0.365 ns，WHS 0.012 ns，无 critical warning；四个时钟管脚 LVDS、100 Ω 差分终端、
    DQS_BIAS（`*_io_placed.rpt`）；已部署，run2/run3 留在板上 `overlay/run2|run3/`
- [x] **run6 构建**（ABI 1.2、`BUILD_VERSION`，rf-clock 1.2.0.0）：`RF-clock-ai:~/build/rf_clock_impl_run6`
  - WNS 0.335 ns，WHS 0.011 ns，无 critical warning；BRAM36 74.5/1080、URAM 0/80；管脚与 run4 相同；
    `BUILD_VERSION` = 0x01020000。已打包到 `src/rf_clock/overlay/` 并部署（run5 按旧编码构建，中途停掉）
  - 上板（2026-10-14）：`PL.open()` 版本检查通过；例子 03、M1（12 s）、M2（5 次）、M3（延时 10 次、
    压力 3 × 6.4 MB）全部 PASS
- [x] **run8 构建**（TX buffer 69632 beat，rf-clock 1.2.1.0）：`RF-clock-ai:~/build/rf_clock_impl_run8`
  - WNS 0.345 ns，WHS 0.011 ns，无 critical warning；URAM 68/80，BRAM36 10.5/1080；`BUILD_VERSION`
    = 0x01020100；`tx_bram_ctrl` 在 0xA040_0000；管脚与 run4/run6 相同。已打包并部署（2026-10-14）
  - 上板：例子 03、M1（12 s）、M2（5 次）、M3（延时 10 次：226.12 个样本、标准差 125 ps；压力
    3 × 6.4 MB）全部 PASS；MTS 落在固定目标上（DAC 1896、ADC 1296）
  - 满深度：TX buffer 写入后经 AXI 读回全部正确；17 个 bank 各放一段不同的随机 BPSK（4086 码片
    = 4096 beat），整段 69632 beat 播放后按段相关，17 个延时在 225.93–226.38 个样本（差 0.45），
    PSL 16.3–18.4 dB；65535 码片的整段突发延时 226.0 ± 0.3 个样本。单样本冲激在串扰下 SNR 太低，
    不能用来做这个检查
  - 待查：65535 码片整段突发的 PSL 只有 12 dB，比 4096 码片的 16–18 dB 低（延时不受影响）；
    串扰通道里 3.8 GHz 和 5.8 GHz 两路都折叠到 ADC 的 1.0 GHz，接线加滤波后再看
  - run7（同一版本）未打包：单块 69632 深的存储没被推断成 URAM（写使能带地址判断、深度不是 2 的幂），
    退回 548 个 BRAM36；改成 17 个 4096 beat 的 bank 后由 run8 解决

- [x] **板子卡死时远程恢复**：`RF-clock-ai` 上 `xsdb tools/jtag_reset_board.tcl 887329260072A`
  （JTAG `rst -system`，再让停在 reset catch 的 A53 #0 继续），约 40 s 后 ssh 恢复
  - 依据：2026-10-13 两次实测；单独 `rst -system` 会让板子停在 FSBL 入口不启动
- [ ] **断电级恢复**：JTAG 救不回来时（例如 JTAG/PMU 也挂住）只能断电；需要智能插座或
  可远程控制的电源

## 4. RFDC 与上电（板上）

- [x] **PYNQ 版本**：`RFSoC-4x2-ai` 上的 PYNQ 版本，`xrfdc` 是否支持 MTS 接口
  - PYNQ 3.1.1；`xrfdc` 的 Python 层没有 MTS，但 `libxrfdc.so`（embeddedsw xilinx-v2020.2）导出
    `XRFdc_MultiConverter_Init/Sync`；`src/rf_clock/pl/rfdc.py` 按 2020.2 的 `xrfdc_mts.h` 用 cffi 调用
  - 开机约 1 分钟后 `bootpy.service` 会加载 base overlay，覆盖已加载的设计；加载前先等启动完成
    （`bringup.wait_for_boot`，2026-10-14 冷启动实测撞上过一次）
- [x] **上电顺序可行**：按 `docs/ABI.md` §7.1，先加载 overlay，再写 LMK/LMX，拉一下
  `pl_resetn0`，等时钟状态 GPIO，再访问 P2
  - 旧顺序（先时钟后 overlay）的实测（2026-10-13）：第一次写完时钟约 4 s 内整机卡死
    （base overlay 仍在 PL 中，原因未定位）；第二次先停 20 s 再加载，不卡死但 MMCM
    未锁；第三次重新加载后 MMCM 直接锁定，P2 读写正常
  - 新顺序（run4，2026-10-13）：两次完整 bring-up 均成功，无卡死，`pps_locked` 约 5 s 后置位
- [x] **RFDC tile 启动**：ADC/DAC 的 tile 0 和 tile 2 都启动完成，tile PLL 锁定
  （tile 0 只为 MTS，但它也必须有时钟并启动）
  - 依据：`tests/pl/m2_tx_timing.py`，`pl_resetn0` 之后 IP 自行启动到 TileState 0xF，不需要调用
    StartUp（2026-10-14，run4）
- [ ] **PL_SYSREF 采样稳定**：约束中 PL_SYSREF 的输入延时（2.000/2.031 ns）照抄自
  RFSoC-MTS，那里 PL_CLK 是 500 MHz；现在 PL_CLK 100 MHz、SYSREF 分频也变了，两者的
  相对相位可能不同。上板用示波器看 PL_CLK 与 PL_SYSREF 的边沿关系，确认在 PL_CLK
  采样沿附近有足够余量；必要时修改约束或 LMK 的 SYSREF 延时
- [x] **MTS 同步成功**：ADC/DAC MTS 返回成功，记录延时；多次重启后延时一致
  - 依据：DAC tile 0/2 延时 1848、调整 0；ADC tile 0/2 延时 1236、调整 0（ref tile 0，目标 -1）。
    3 次 PL 重载（其中 2 次重写时钟）和 2 次 JTAG 冷启动结果完全相同（板上
    `~/RF-clock/m2_runs.jsonl`，2026-10-14）。尚未把它们固定为标定目标值
  - **延时随 bitstream 变**：run6 上 ADC 延时 1248（tile 0 调整 2），DAC 仍为 1848；两次上电一致。
    环回延时随之从 212.07 变为 214.08 个样本（+2.0 样本 = ADC 延时 +12 ÷ 抽取 6）
  - [x] **MTS 目标延时固定**（用户定，2026-10-14）：DAC 1896、ADC 1296（`board.py`，
    `PL.open()` 默认）；DAC、ADC 都按 6 一步调整。run4 和 run6 上串扰环回延时为 226.05 和
    226.02 个样本，差 0.03，在标准差内；`MTSResult.ok` 另外要求每个 tile 都落在目标上
  - 混频器读回：ADC NCO −1000 MHz；DAC NCO 也读成 −1000 MHz，按 IMR 高通（NCO 在 fs/2 = 4.8 GS/s
    上，3.8 GHz 折叠为 −1.0 GHz）理解，需在频谱仪上确认 5.8 GHz 输出
- [ ] **DAC 输出 5.8 GHz**：频谱仪上看到 5.8 GHz（以及 3.8 GHz 分量，由 BPF 抑制）；
  记录输出功率，与 mix-mode 在 0.604 fs 的预期比较
- [ ] **ADC 能收 5.8 GHz**：注入 5.8 GHz 单音，确认在基带出现在预期频率；记录 SNR
- [x] **DAC beat 内布局**：8 个样本的 lane 顺序和 I/Q 位置
  - 完成标准：每条 lane 不同的 ramp + 单 lane 冲激，有线环回逐位核对，写入
    `docs/ABI.md` §4.5.8
  - 结论：DAC 按 I/Q 逐字交错读取，第 n 个样本 = `{Q[32n+31:32n+16], I[32n+15:32n]}`
    （`TX_LAYOUT`）。依据：`tests/pl/m3_loopback.py --lane-check`（2026-10-14，靠板上 DAC→ADC
    串扰，未接环回线）：按平面格式打包时 8 条 lane 的延迟为 D − ⌈k/2⌉（211.83 … 208.05），
    改用交错格式后 8 条 lane 一致（极差 0.19–0.25 个样本）；软件模型
    `tests/test_loopback_model.py` 复现了同一规律。冲激只放在 I 上，一对 16 位中 I/Q 的归属
    未单独验证
  - 极性：DAC 混频器旁路时 I = +满幅在 DAC_A 口输出负电压（示波器，2026-10-14），即板上输出相对 I
    反相。做相位/共轭分析时计入
  - 影响：此前 TX 按平面格式打包，M2 发出的是被打乱的波形；M2 只验证时刻，结论不受影响。
    `WaveformGenerator.generate_beats` 现在默认用 `TX_LAYOUT`
- [x] **ADC I/Q 合并顺序**：combiner 输出是否为 {Q(m21), I(m20)}；写入 `docs/ABI.md` §4.6.9
  - lane 顺序：平面格式（低 128 位 8 个 I、高 128 位 8 个 Q，lane 0 在低位）与 lane check 一致
    （`RX_LAYOUT`）。I/Q 归属同上，未单独验证
- [ ] **频谱翻转**：确认接收基带是发射基带的共轭（计划 §3）；BPSK 实信号不受影响
- [x] **RX DMA 持续带宽**：3.2 GB/s 写入，ARM 同时访问 DDR 时窗口不溢出（计划 M3）
  - 依据：`tests/pl/m3_loopback.py --stress`：5 个 2 ms（6.4 MB）窗口和 3 个 20 ms（64 MB）
    窗口，ARM 后台拷贝内存约 4.2 GB/s，全部有效（2026-10-14）

## 5. PL 功能（板上）

- [x] **raw_tick**：每秒正好 100,000,000 tick（以 PPS 为参照）
  - 依据：run4 M1，60 个 PPS 周期全部 100000000（2026-10-13）。ARM 原始时钟测得
    +108 ppm，是 ARM 晶振的误差
- [x] **PPS 捕获**：`PPS_LAST_PERIOD` 恒为 100,000,000；`PPS_JITTER_CNT` 不增长
  （若增长，需用 IDELAY 调整 PPS 输入）
  - 依据：run4 连续 3 小时（10801 个 PPS 沿），`PPS_JITTER_CNT` 增量 0、故障增量 0、
    `pps_locked` 始终为 1（板上 `~/RF-clock/long_run.log`，2026-10-13/14）。P1 在硬件里检查
    每一个周期，所以这覆盖全部周期，不只是软件每秒采到的那些
  - 此前一次 10 分钟测试在约 23:43:40 UTC 出现 3 个偏离周期（+1590、+80、+130 tick），
    随后自行恢复、无需重写时钟；像是 10 MHz 参考被短暂扰动，原因未确认
    （`RF-clock-ai:~/build/m1_run4_600s.log`）
- [x] **TX 定时**：示波器以 PPS 触发，突发相对 PPS 的延迟固定，重启后不变（计划 M2）
  - 数字部分已通过：每个突发 `TX_ACTUAL` = target、= PPS_TICK + 偏移，包括冷启动后
    （`tests/pl/m2_tx_timing.py`，2026-10-14）
  - 模拟部分第一次（2026-10-14，rf-clock 1.2.1.0，RIGOL DHO924 250 MHz）：CH1 = FS725 1PPS，
    CH2 = DAC_A。`m2_tx_timing.py --scope-nco 100 --amplitude 16000 --offset-ticks 1000`（DAC 临时改成
    IMR 低通、1 区、NCO 100 MHz）；多次突发都在 PPS 上升沿后 **10.2 µs**（设定 10 µs + 约 200 ns 固定
    延时），没有抖动和跳变。突发可见长度 40.8 µs（按格读；理论 40.97 µs，>10% 包络）。50 ns/div 下
    PPS **下降沿**到突发起点稳定在约 212 ns，即 FS725 1PPS 脉宽约 10.0 µs；P1 定时用的是上升沿，下降沿
    不作基准。
  - `--offset-ticks 0`（突发排在 PPS 上升沿那一拍），50 ns/div 光标测 PPS 上升沿 50% → 突发起点：
    222.5、226.5、226.5、225.5、222.5 ns，平均 **224.7 ns**，峰峰值 4 ns（手动读数误差量级；没有
    10 ns 的 tick 跳变）。包含 PPS 输入链路、DAC 数据通路（scope 模式）、RRC 爬升约 35 ns。
  - **重启一致性（M2 验收）**：`m2_tx_timing.py --waveform rect --scope-nco 100 --amplitude 32000
    --offset-ticks 0`（矩形波形，scope 模式细混频器缩放 1.0），不带 `--no-clocks`（每次重新加载
    bitstream、重写 LMK/LMX、pl_resetn0、重做 MTS）重复 3 次；示波器 Measure → Delay（CH1 PPS 上升沿
    → CH2 突发第二个峰；第一个峰只有半幅），每次 **192.5–193 ns**，统计标准差 < 1 ns（2026-10-14，
    rf-clock 1.2.1.0，MTS 目标 DAC 1896 / ADC 1296）。与 PRN 目测值 224.7 ns 相差约 32 ns，即 RRC 爬升段
  - 冷启动后（2026-10-14）：之后三次运行（各自重写时钟）MTS 结果与之前完全相同（DAC 1896/调整 8，
    ADC 1296/调整 10）。示波器读数：第三个峰 203.5–204 ns（干扰导致改用第三峰，= 第二峰 + 10 ns
    + 约 1 ns），第二个峰 194.00 ns，比冷启动前的 192.5–193 ns 多 1–1.5 ns。原因待分：测量阈值变化，
    或下面这一项（每 0.42 ns 一步）
  - **LMK 零延时模式未开**：`LMK04828_100.0.txt` 中 0x13F = 0x02（FB_MUX = DCLKout8 但 FB_MUX_EN = 0，
    PLL1 N 取 OSCin）。VCO 2400 MHz ÷ 24 = 100 MHz，分频器起点不固定时 tick 相对 FS725 10 MHz/PPS
    可落在 24 个相位之一（间隔 0.42 ns），即"PPS 亚 tick 相位"可能随每次写时钟而变。实测约 6 次写时钟
    都在 1.5 ns 内，不像均匀随机。示波器设置不动、热重启 5 次：197.00、194.50、186.50、196.50、
    197.00 ns，跨约 10.5 ns ≈ 1 tick，MTS 每次相同 → 确认是 tick 相位随写时钟而变
  - [x] **打开 LMK 嵌套零延时**（2026-10-14）：`LMK04828_100.0.txt` 0x13F = 0x0B、PLL1_N = 120（R = 12，
    鉴相 833.3 kHz 不变）；`make_100mhz_config(mode="ext10m")` 生成；`PL.open()` 改为把包里的寄存器
    填进 xrfclk 的 `_Config` 再写（`board.program_clocks`），板上不再依赖 xrfclk 目录里的拷贝。
    板上：M1 30 s（周期全为 1e8、抖动 0）、M2 5 次 PASS，MTS 不变
    发布为 **rf-clock 1.2.1.1**（只改 Python/包数据，配 1.2.1.0 bitstream；用户定）。板上 `~/RF-clock`
    即 1.2.1.1；本地工作区已含 1.3 的改动（`TX_REPEAT`，`pyproject.toml` = 1.3.0.0），1.3 会包含这些改动
  - 零延时后用同样方法热重启 5 次：197.5、194.5、193.0、197.5、201.0 ns，且第一个峰前出现更小的峰。
    **测量方法有缺陷**：`--scope-nco 100` 每次运行重设 NCO，突发起点的载波相位每次不同（0–360° = 0–10 ns），
    按"第 N 个峰"/阈值读数会在一个载波周期内跳。零延时前的 186.5–197 ns 也可能主要是这个原因，
    所以"tick 相位随写时钟而变"未被证实（零延时按数据手册保留，无害）
  - 改用与相位无关的方法：`--scope-nco 0`（粗混频器旁路，DAC 直接输出 I）+ `--waveform square`
    （I 为 25 MHz 方波，第一个边沿即突发起点）。板上冒烟测试 PASS
  - [x] **示波器复测（方波、基带）**（2026-10-14，rf-clock 1.2.1.1，LMK 零延时，MTS 目标 DAC 1896 / ADC 1296）：
    `m2_tx_timing.py 120 --waveform square --scope-nco 0 --amplitude 32000 --offset-ticks 0`，热重启 5 次
    （每次重载 bitstream、重写时钟、重做 MTS），示波器 Delay（CH1 PPS↑ → CH2 第一个边沿）：
    **180.00、180.00、180.00、180.00、180.00 ns**。第一个边沿是下降沿：DAC_A 输出相对 I 反相
    截图 `images/tests/pl_m2_tx_timing.png`（CH2 = PPS，CH1 = DAC_A，1 MΩ；两路同开时采样率 625 MSa/s，
    1.6 ns/点，所以重启间差异在该分辨率以内）
  - 待做（可选）：冷启动一次确认 180.00 ns；用旧（非零延时）LMK 镜像加同样方法测几次，看零延时是否必需
  - 待做：正式 5.8 GHz 配置下的绝对延时（scope 模式数据通路不同，要用带宽足够的仪器或 M3 接线环回标定）
    要用 ≥ 6 GHz 示波器或检波器确认（scope 模式的数据通路不同）
- [ ] **有线环回**：本板 DAC → 衰减器 → ADC，相关得到的延时稳定（计划 M3）
  - 未接线时，板上 DAC tile 2 → ADC tile 2 的串扰已能测出相关峰：延时 212.07 个样本
    （265.1 ns），10 次标准差 156 ps，PSL ≥ 16 dB（`tests/pl/m3_loopback.py`，2026-10-14）。
    待接线后测：标准差、多次重启（含冷启动）的一致性

## 6. FS725

- [ ] **RS-232 修正命令**：频率偏移命令的量程、分辨率、单位（FS725/PRS10 手册）
- [ ] **PPS 粗对齐能力**：能否一次性把 PPS 相位跳到目标附近（计划 §10 第 4 项）
- [ ] **ARM 能控制 FS725**：串口连接方式（USB 转串口等）和驱动

## 7. 系统设计中尚未决定的事（计划 §10）

- [x] **双向链路**：A、B 互相发送
  - 依据：用户确认
- [ ] **天线**：TX/RX 分开，还是共用加 T/R 开关（若共用，PL 需要定时输出开关控制）
- [ ] **节点间数据通道**：默认以太网，需确认两台 ARM 之间可达
- [ ] **目标精度**：ns 级（码相关即可）还是 100 ps 以下（需要载波相位）
- [ ] **法规**：5.8 GHz 发射功率和占用带宽限制

## 8. 仓库整理

- [x] **删除旧 H 架构的文件**（2026-10-13，用户要求）
  - 已删除：H 系列 RTL/testbench、`FPGA/ip/`（旧 RFDC 配置和 BD 脚本）、
    `FPGA/src/rf_clock_bd_wrapper.v`、`FPGA/compile_rtl.tcl`、
    `FPGA/sim/run_testbench.ps1`/`.sh`、`FPGA/data/H09_rrc_coeffs.vh`、
    `utils/compare_h09_rtl.py`、`handler/RFDC_config.md`
  - 修改：旧的 `utils/gen_h09_rrc_coeffs.py` 已删除，新生成器为
    `tools/gen_rrc_coeffs.py`；`.vscode/tasks.json` 改用 `run_pl_tests.ps1`；
    `project.sigasi` 增加 `PL/src` 为 include 目录；`README.md` 重写
  - 保留：`config/RRC_config.db`（`RRC_config`/`RRC_coeff` 浮点 RRC 系数）；
    `utils/sim_h09_rrc_fir.py` 仅提供基于该数据库的可选 Plotly 诊断，不再维护旧数据库
    或旧 RTL/CSV 比对格式
- [x] **`FPGA/` 改名为 `PL/`**（2026-10-14，用户要求）：脚本、配置（`.vscode/tasks.json`、
  `project.sigasi`、`.gitignore`、`tools/generate_abi.py`）和文档中的路径已改；上面记录已删除
  文件的条目保留原路径；RF-clock-ai 上 Syncthing 已同步改名
- [x] **overlay 随包发布、版本绑定**（2026-10-14，用户要求；`docs/ABI.md` §14.1）：
  `src/rf_clock/overlay/`（`.bit`/`.hwh`/`.xsa` 用 Git LFS）+ `manifest.json`，由
  `tools/package_overlay.py` 生成；构建脚本把 `pyproject.toml` 版本写进 P2 `BUILD_VERSION`
  （ABI 1.2，0x94），`PL.open()` 加载前查 manifest、加载后查 `BUILD_VERSION`。版本号四段
  `A.B.C.D`（用户定）：A.B = `VERSION`，C = 该协议下第几个 bitstream，D = 只改 Python；
  A.B.C 必须与安装的包一致。从 1.2.0.0 开始（run6）
- [x] **PL 部件按地址空间划分**（2026-10-14，用户要求）：`pl.ctrl`（原 `pl.p2`，P2 寄存器：
  全部命令、时间戳、状态）、`pl.tx_buffer`（TX BRAM）、`pl.rx_dma`（`RXDMA`，只管 S2MM）。
  `ctrl.wait_tx()` 返回 `TX_ACTUAL`，`ctrl.wait_rx()` 返回 `RXWindow` 并做 §7.3 的校验；
  `rxcap.py` 改为 `rxdma.py`
  - [x] 上板复测 M2、M3（拆分后的 RX 顺序：`rx_dma.prepare` → `ctrl.arm_rx` → `ctrl.wait_rx`
    → `rx_dma.wait`）：run6 上全部 PASS（2026-10-14）
- [x] **PYNQ 3.1 不能在 `.bit` 旁边放同名 `.xsa`**：它会改走 XSA 分支并报
  `UnknownInputFileExtension`；随包的 XSA 改名为 `rf_clock_hw.xsa`（`tools/package_overlay.py`）
- [x] **TX buffer 加深到 69632 beat**（用户要求，2026-10-14）：17 × 4096，放得下 65535 码片的
  突发（65545 beat）；用 UltraRAM，分成 17 个 4096 beat 的 bank（单块 69632 深的存储 Vivado 不肯推断成
  URAM，run7 退回了 548 个 BRAM36），AXI 窗口 0xA040_0000、4 MiB；
  P3/`p_top` 增加 `DEPTH`/`TX_DEPTH` 参数（不必是 2 的幂），窗口内超出部分的写入丢弃。
  rf-clock 1.2.1.0（run8）
- [x] **Python 类型标注清理**（2026-10-14，用户反馈 Pylance 报很多 `"X" is not a known attribute of "None"`）：
  `PL` 的部件、`RFDC.mts`、`PLClient.info` 不再是可空类型（未就绪时访问抛异常）；`ClockStatusIO`
  与实现对齐；`pyproject.toml` 加 `[tool.pyright]`（`pythonPlatform = "Linux"`、`extraPaths`）。
  `uvx pyright --pythonpath .venv/Scripts/python.exe src tests examples tools`：0 错误
- [x] **TX 重复播放**（用户要求，2026-10-14；ABI 1.3，rf-clock 1.3.0.0）：新寄存器 `TX_REPEAT`
  （0x5C，shadow，1 … 65535，复位 1）；一条命令把 `TX_LENGTH` 个 beat 首尾相接播放 `TX_REPEAT`
  遍，遍与遍之间没有空拍，`TX_ACTUAL` 为第一遍起点，全部播完才 DONE。Python：
  `arm_tx(target, beats, repeat=n)`（1.3 之前的 bitstream 上不写这个地址）、
  `WaveformGenerator(periodic=True)`（循环 RRC 卷积，无拖尾，65535 码片 = 65535 beat）
  - [x] RTL 和仿真：P3 testbench 测 3 遍 × 5 beat、2 遍 × 满深度（从最后一个 bank 绕回第一个）、
    65535 遍 × 1 beat，以及 0 和 65536 被拒；反向检查（新一遍从第 1 个 beat 开始）会失败
  - [x] 构建 run9（`RF-clock-ai:~/build/rf_clock_impl_run9`）：WNS 0.322 ns，WHS 0.012 ns，无 critical
    warning；URAM 68/80，BRAM36 10.5/1080
  - [x] 打包、部署（板上 1.3.0.0，2026-10-14）；例子 03、M1（12 s）、M2（5 次）、M3 压力（3 × 6.4 MB）PASS
  - [x] 上板：周期 65535 码片 × 10 遍由一条命令连续发出（6.55 ms），一个窗口收下；每遍单独相关，
    10 个延时在 231.990–232.061 个样本（差 0.071），PSL 22–23 dB；`TX_ACTUAL` = target，
    `TX_SEQ` 只加 1。遍间多一个空拍会让后面每遍延时累加 8 个样本，所以遍间无缝
  - M3 延时这次 FAIL（231.95 个样本、标准差 684 ps、最小 PSL 5.1 dB），run8 在同样条件下结果相同：
    原因是接线变了（示波器测试时改成 DAC → 示波器 CH1 三通 → 用户所说的 "ADC_1"），不是 run9。TX 幅度
    加大 31 倍 |rx| 仍在噪声水平，说明线缆没接到接收口 ADC_B（推断接的是 ADC_A），只改变了串扰通道。正式的
    M3 要 DAC_A → SMA 线 → ≥ 20 dB 衰减器 → ADC_B，不经示波器三通
- [x] **提交**：rf-clock 1.2.1.1（36cc575）、换行统一为 LF（f591358）、rf-clock 1.3.0.0（其后一个提交）
