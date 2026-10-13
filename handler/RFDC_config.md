# RFDC 配置指南

本文说明本项目 RFSoC 中 RF Data Converter（RFDC）的配置边界、Vivado/Tcl
配置方法、运行期 C/C++/Python 配置方法，以及如何检查
`FPGA/src/H09_pulse_rfdc_bridge.v` 与生成的 RFDC IP 是否一致。

## 0. 先区分三类配置

RFDC 不是“只写一段 RTL 就配置完成”的外设。需要同时处理三类内容：

| 层次 | 负责内容 | 主要产物 |
|---|---|---|
| Vivado IP（构建期） | tile/block、采样率、PLL、插值/抽取、数据宽度、AXI-Stream 端口 | RFDC `.xci`、生成 HDL、BD、HWH/XSA |
| PL 数据路径 | H09 样本如何打包为 RFDC 的 AXI4-Stream beat | `H09_pulse_rfdc_bridge.v` |
| PS 运行期 | RFDC 初始化、运行期 mixer/NCO/QMC 等可写设置、状态读取和故障处理 | Xilinx `XRFdc` 驱动、PYNQ `xrfdc` |

Tcl 可以自动化 Vivado 的构建期配置，但不能替代 ARM 上电后的 RFDC 驱动初始化；
Python/PYNQ 也不能改变已经由 IP 固化的所有参数。某些字段是否可在运行期修改，
必须以当前 Vivado/Vitis 版本的 `xrfdc.h`、`xrfdc.py` 和生成的 IP 为准。

本文中的“当前候选基线”来自参考工程的静态 IP 检查，不代表本项目已经完成真实
RFDC、时钟、DAC 输出或射频链路验收。

## 1. 当前项目使用的候选 RFDC profile

### 1.1 已有参考工程中核对过的值

参考工程的 `configure_rrc_rfdc.tcl` 在 Vivado 2024.1、器件
`xczu48dr-ffvg1517-2-e` 上创建 `usp_rf_data_converter` v2.6，并检查了以下值：

| 参数 | DAC 候选值 | 说明 |
|---|---:|---|
| tile/block | DAC tile 2，block 0（逻辑名 `DAC2`/`DAC20`） | 生成端口为 `s20_axis`、模拟端口 `vout20` |
| RFDC 参考时钟 | 480.000 MHz | 板上 LMX/LMK 是否真的产生并锁定该频率，尚未由本项目证明 |
| DAC 采样率 | 9.6 GS/s | 片内 PLL 参考为 480 MHz，典型分频/倍频为 1/20/1 |
| RFDC 输出时钟 | 300 MHz | 连接后级 Clocking Wizard，再得到 100 MHz AXI-Stream 域 |
| fabric/interface 时钟 | 100 MHz | H09 bridge 与 `s20_axis_aclk` 应处于同一时钟域 |
| 数据宽度 | 256 bit | 每拍 16 个 16-bit 字；I/Q→Real 模式下解释为 8 对复样本 |
| 插值 | `DAC_Interpolation_Mode20 = 6` | DUC 前级为 ×6；启用 IMR 后 mixer 后另有 ×2，总倍率为 ×12 |
| mixer mode | `DAC_Mixer_Mode20 = 0` | 这是安装组件 GUI 枚举，不要直接当作运行期 API 枚举 |
| mixer type | `DAC_Mixer_Type20 = 2` | 细混频器候选 |
| Nyquist zone | `DAC_Nyquist20 = 1` | 这是 GUI/IP 参数枚举 |
| RF-DAC NCO | `DAC_NCO_Freq20 = 3.800` | Vivado IP 字段的单位是 GHz；不是直接可套用的运行期 MHz 值 |
| 实时信号端口 | `DAC_RTS = true` | 是否实际连接并监测 overflow/fast-shutdown 仍需在 BD 中检查 |

上述参数意味着输入复样本率候选为 `9.6 GS/s / 12 = 800 MS/s`，即 100 MHz
接口时钟每拍 8 对 I/Q。它正是当前 bridge 的 256 bit 打包假设。

### 1.2 `3.800 GHz` NCO、`5.800 GHz` 射频和 `9.6 GS/s` 不是同一个量

这里必须把三个容易混淆的频率分开：

- `9.6 GS/s` 是 DAC 的采样率（每秒 9.6 G 个 DAC sample），不是模拟载波；
- `DAC_NCO_Freq20 = 3.800` 是 RFDC 数字 mixer/NCO 的频率候选。在 Vivado IP
  配置字段中单位是 GHz；运行期 `XRFdc_Mixer_Settings.Freq` 常见单位则是 MHz，
  不能把这两个字段的数值和单位混用；
- `5.800 GHz` 是本项目希望得到的射频中心频率候选。

当前 profile 使用 RF-DAC 的高通 IMR/第二 Nyquist image 路径。对零中频（或以
 `0 Hz` 为中心的复基带）作近似说明时，目标关系是

```text
f_RF ≈ f_DAC - f_NCO = 9.6 GHz - 3.8 GHz = 5.8 GHz
```

 更细地看，输入的 `800 MS/s` 复样本先经过前级 `×6`，进入 mixer/IMR 前的数字
 采样率约为 `4.8 GS/s`；`3.8 GHz` 是这个离散频率域中的 NCO 设置（按 `4.8 GHz`
 周期等效为 `-1.0 GHz`），而不是先和 `5.8 GHz` 做模拟加法。IMR 再以 `×2`
 进入 `9.6 GS/s` DAC 采样域并选取第二 Nyquist image，所以最后的射频位置才表现
 为上述 `9.6 - 3.8` 的折叠关系。对有带宽的 RRC 波形，这个关系只给出中心位置；
 边带方向和实际幅相仍需用 DAC→ADC loopback 的频谱确认。

因此不能写成“`5.8 + 3.8 = 9.6`，所以载波是 9.6 GHz”。`9.6 GHz` 在这里仍然
只是采样率；减法关系来自第二 Nyquist image/高通 IMR 的频谱折叠，且会伴随频谱
方向（边带）反转。最终必须同时核对生成 IP 的 mixer/NCO readback 和实际频谱，不能
仅凭这个静态等式宣称板上已经输出 5.8 GHz。参考：[RF-DAC high-sampling-rate
mode（AMD PG269）](https://docs.amd.com/r/en-US/pg269-rf-data-converter/RF-DAC-High-Sampling-Rates-Mode-Gen-3/DFE)。

### 1.3 为什么候选参考时钟是 `480 MHz`

`480 MHz` 是 RFDC tile 内部 PLL 的参考输入候选，不是 H09 的 100 MHz AXI-Stream
时钟，也不是直接把 FS725 的 10 MHz 接到 DAC。当前静态 profile 的 DAC PLL 派生值为：

```text
reference divider = 1
feedback multiplier = 20
output divider = 1
480 MHz × 20 / 1 = 9.6 GHz
```

这使 PLL 能为 9.6 GS/s DAC 产生所需的高速采样时钟；随后 RFDC 的数据路径再以
 `9.6 GS/s / 12 = 800 MS/s` 接收数据，映射到 100 MHz、每拍 8 对 I/Q 的 AXIS
接口。`480 MHz` 是在当前器件、Vivado 版本和目标采样率下通过 IP 参数合法性检查的
候选值，不应被解释成“FS725 10 MHz 乘 48 已经在本板上实现”。仍需在最终硬件中
确认 LMK/LMX（或板上等效时钟器件）确实产生 480 MHz、PLL lock 有效且抖动满足要求，
并把这些 lock/status 纳入 H01/H10 的准入条件。RFDC 外部时钟输入和 PLL 参数的
定义见 [PG269 Clock Inputs](https://docs.amd.com/r/en-US/pg269-rf-data-converter/Clock-Inputs)
与 [PG269 PLL Parameters](https://docs.amd.com/r/en-US/pg269-rf-data-converter/PLL-Parameters)。

### 1.4 mixer、IMR 和 `×6/×2` 插值分别是什么

- **mixer/NCO**：RFDC 内部的数字数控振荡器和复数乘法器。它把输入复基带频谱搬移
  到所需的数字频率；`3.8 GHz` 是该数字搬移参数，不是一个独立的模拟输出端口。
  具体频率范围、符号和更新方式以 [PG269 RF-DAC NCO and Mixer](https://docs.amd.com/r/en-US/pg269-rf-data-converter/RF-DAC-Numerical-Controlled-Oscillator-and-Mixer)
  及运行期驱动 readback 为准。
- **IMR（Image Rejection）**：图像抑制滤波/数据路径模式。在当前高采样率、高通配置
  中，它选择第二 Nyquist image，并在 mixer 后带来额外的 2 倍插值；该路径会产生
  频谱折叠和边带方向变化，故 NCO 的符号、频率和 Nyquist/IMR 模式必须成组验证。
- **前级 `×6`**：`DAC_Interpolation_Mode20 = 6` 表示 DUC 前级的总插值倍率为 6。
  它通常由 RF-DAC FIR 级联组合得到，并不是某一个固定的“6 倍单级滤波器”。
- **IMR 后 `×2`**：高采样率 IMR 路径隐含的额外 2 倍插值。

所以当前候选链路是：

```text
800 MS/s complex input × 6 (DUC/front interpolation)
    × 2 (IMR high-pass image path)
    = 9.6 GS/s DAC sample rate
```

不要把总倍率 `12` 直接填入 `DAC_Interpolation_Mode20`；该字段仍应填前级的
 `6`。AMD 对 RF-DAC 数字数据路径和 Gen3/DFE 插值级的说明见：
[RF-DAC digital datapath](https://docs.amd.com/r/en-US/pg269-rf-data-converter/RF-DAC-Digital-Datapath)
和 [RF-DAC interpolation filters](https://docs.amd.com/r/en-US/pg269-rf-data-converter/RF-DAC-Interpolation-Filters-Gen-3/DFE)。

### 1.5 不要把候选值当作已验证事实

以下内容仍需在实际目标板卡和最终 Vivado 工程中核对：

- 目标板上的参考时钟产生、电平、抖动、锁定和启动顺序；
- RFDC PLL 实际锁定，以及 RFDC 输出时钟的相位/重启重复性；
- RFDC 运行期 mixer/NCO 的频率单位、频谱方向和符号折叠；
- `s20_axis` 的实际 `TDATA` 位序和 I/Q 解释；
- `TREADY` 在目标 RFDC 配置下是否持续为高；
- overflow、fast-shutdown、IRQ 等实时状态是否已接出、同步、锁存并纳入 H10；
- 经过 RFDC、模拟输出、线缆、接收端后的真实延迟和幅相。

## 2. H09 bridge 与 RFDC 的接口契约

当前 [H09_pulse_rfdc_bridge.v](../FPGA/src/H09_pulse_rfdc_bridge.v) 假设 RFDC
输入接口如下：

```text
M_AXIS_TDATA  : 256 bit
M_AXIS_TVALID : 1 bit
M_AXIS_TREADY : 1 bit
TKEEP/TLAST   : 不存在（当前 profile）
```

每个 32-bit lane 的布局为：

```text
lane 0: TDATA[ 15:  0] = I0, TDATA[ 31: 16] = Q0
lane 1: TDATA[ 47: 32] = I1, TDATA[ 63: 48] = Q1
...
lane 7: TDATA[239:224] = I7, TDATA[255:240] = Q7
```

因此，bridge 与 RFDC 一致的必要条件是：

1. RFDC `s20_axis_tdata` 确实为 256 bit；
2. RFDC 配置确实使用每拍 8 对复样本的 I/Q→Real 输入格式；
3. bridge 与 RFDC 使用同一 100 MHz `s2_axis_aclk` 域；
4. `sample_valid` 直接对应 `TVALID`，`m_axis_tready` 直接反馈到 H09 的
   `sample_ready`；
5. 当前 RFDC 接口没有 `TKEEP/TLAST` 时，最终不足 8 lane 的 beat 必须由 bridge
   置零，不能假设 RFDC 会忽略无效 lane；
6. 活动 PRN frame 内不能依靠 `TREADY` 暂停。开始 frame 前先由 H10 汇总
   `tx_admission_ready`；活动期间如果 RFDC `TREADY` 下降，应该进入 FAULT，
   而不是插入 bubble。

bridge 本身没有 FIFO 或寄存器，因此不会增加数据路径周期延迟。这也意味着它不能
   用来吸收 RFDC 的临时停顿；RFDC 若不能持续接收，应由 H10/H09 报告故障。

## 3. Vivado GUI 配置步骤

下面是一次可重复的配置流程。菜单名称会随 Vivado 版本略有变化，最终以生成的
`.xci`、IP 输出和 HWH 为准。

### 3.1 建立工程和选择器件

1. 创建新的 RTL 或 Block Design 工程。
2. 选择实际 RFSoC 器件。参考候选使用
   `xczu48dr-ffvg1517-2-e`，不能把这个 part 名称直接套到其他板卡。
3. 确认 Vivado 版本与目标 IP 版本。参考候选使用 Vivado 2024.1 的
   `usp_rf_data_converter` v2.6。

### 3.2 添加 RF Data Converter IP

在 IP Catalog 添加 `RF Data Converter`，先配置 DAC tile 2，再按需要配置 ADC tile。
建议只打开实际使用的 block，关闭未使用的 tile/block，避免把未连接的输出误当作
健康状态。

DAC 候选配置：

1. 使能 DAC tile 2 / slice 0（IP 参数名通常表现为 `DAC2`、`DAC20`）。
2. 参考时钟填 `480.000 MHz`，采样率填 `9.6 GHz`。
3. 选择对应的 IMR/高通模式，并将 **前级** 插值填为 `6`；不要把总倍率 `12`
   直接填入 `DAC_Interpolation_Mode20`。
4. 选择 I/Q→Real、16-bit 数据宽度，使 AXI-Stream 成为 256 bit、8 对 I/Q。
5. 设置 DAC NCO 为 profile 中的 `3.800 GHz` 候选。
6. 打开 DAC 实时信号端口（如果最终要将 overflow/fast-shutdown 纳入 H10）。
7. 保留 RFDC 产生的 `dac2_clk`/`s2_axis_aclk`、`s2_axis_aresetn`、`s20_axis_*`
   以及必要的 IRQ/fast-shutdown/overflow 端口。

### 3.3 连接 BD，以及把 RFDC 当作 RTL 模块使用

建议的连接关系：

```text
H09_pulse_rfdc_bridge.M_AXIS
        └── RFDC.s20_axis

RFDC.s2_axis_aclk ──> H09 bridge/H09 sample clock domain
RFDC.s2_axis_aresetn ──> H09 bridge/H09 sample reset
RFDC.s20_axis_tready ──> bridge ──> H09 sample_ready
```

可以。RFDC IP 生成后可以在 `H00_top` 的 RTL 层级中像普通 module 一样实例化，
但它不是可移植的手写 Verilog 模块：端口、参数和行为由当前 Vivado、器件和 IP
版本生成，必须以生成的实例模板和 wrapper 为准。推荐流程是：

1. 在 Vivado GUI 或 Tcl 中 `create_ip -name usp_rf_data_converter ...`，设置参数；
2. 对 RFDC 执行 `Generate Output Products`，保留 `.xci`、生成的 HDL/仿真模型及
   必需的约束/辅助文件；
3. 从生成的 `.veo`/`.vho` 或 wrapper 复制**实例模板**，不要自己猜端口名和位宽；
4. 在 `H00_top` 实例化生成的 RFDC wrapper，并将 H09 bridge、时钟、复位、AXI-Lite
   和状态线连接起来；
5. 在同一个 Vivado 工程中加入 XCI/IP repository 和 output products，再做
   elaboration、synthesis、implementation。不能只把某个生成的 `.v` 文件复制到
   `FPGA/src` 后用 Icarus 当作 RFDC 行为模型。

两种组织方式都可以：

```text
方案 A：H00_top 直接实例化生成的 RFDC wrapper
H00_top ── H09_pulse_rfdc_bridge ── generated_rfdc_wrapper ── DAC pins

方案 B：先在 Block Design 中连接 RFDC、PS AXI、Clocking Wizard，
        生成 HDL wrapper，再由 H00_top 实例化这个 wrapper
```

方案 B 通常更适合 PS AXI 地址、HWH/XSA 和时钟连接管理；方案 A 便于明确 RTL
端口，但 AXI interconnect、时钟、reset 和 RFDC 专用端口需要自己完整连接。两种
方案最终都应满足上面的 `s20_axis`/100 MHz/256-bit 契约。AMD 的 IP 实例化说明见
[UG895: Instantiating IP](https://docs.amd.com/r/en-US/ug895-vivado-system-level-design-entry/Instantiating-IP)
和 [UG896: Instantiating an IP](https://docs.amd.com/r/2024.2-English/ug896-vivado-ip/Instantiating-an-IP)。

RFDC 的 AXI-Lite 控制口接到 PS 的 AXI master（或一个明确的 AXI interconnect），
并为其分配地址。地址必须从最终导出的 HWH/XSA 读取，不能在 RTL 或 Python 中猜测。

对于 300 MHz converter clock 到 100 MHz fabric clock 的关系，使用 Clocking Wizard
或 RFDC 生成的时钟链；两个频率相同的 100 MHz 时钟不能自动视为同相，若来自不同
链路仍需按独立时钟域处理。

当前 `H00_top.v` 还只是项目 shell，尚未包含某个具体 RFDC generated wrapper；因此
现在不能仅凭 H00 的空壳端口声称 RFDC 已经接通。等最终 IP 生成后，应把生成 wrapper
的真实端口表、AXI 地址、时钟/复位极性和 DAC 模拟引脚一起纳入 H00 的端口审计。

### 3.4 生成和导出

依次执行：

1. `Validate Design`；
2. `Generate Output Products`；
3. 综合/实现前检查 clock、reset、AXI-Stream 宽度和接口方向；
4. 生成 bitstream 后导出与 bitstream 配对的 HWH/XSA；
5. 保存 `.xci`、生成 HDL、HWH/XSA、Vivado 版本和 Git commit。

仅看到 IP 在 BD 中出现，不能证明 RFDC 已经在板上锁定，也不能证明 H09 波形已经
到达 DAC。

## 4. Tcl 自动化构建期配置

### 4.1 现有参考脚本

参考工程中的 `RFSoC/utils/configure_rrc_rfdc.tcl` 会：

- 强制要求 Vivado 2024.1；
- 创建全新的临时工程，拒绝覆盖已存在输出目录；
- 创建 `usp_rf_data_converter` v2.6；
- 设置 `CONFIG.*` 参数并保存 requested/readback TSV；
- 生成 IP 输出并检查生成 HDL 中的端口和 PLL 参数；
- 生成 300→100 MHz 的 Clocking Wizard；
- 输出哈希、状态和静态检查结果。

它是静态 IP 配置探针，不是板卡下载器，不会编程 RFDC、LMK/LMX、DAC 或 ADC。

### 4.2 最小 Tcl 模板

本项目已经提供可重复执行的完整脚本：
[`FPGA/ip/generate_rfdc_ip.tcl`](../FPGA/ip/generate_rfdc_ip.tcl)。它使用本节的
480 MHz / 9.6 GS/s / DAC tile 2 profile，同时生成 `rrc_adc_clk` 和 `rrc_dac_clk`
两个 300 MHz→100 MHz Clocking Wizard，并在生成后检查关键 RFDC 端口。脚本拒绝覆盖
已有输出目录；例如在 `RF-clock-ai` 上运行：

```bash
source /tools/Xilinx/Vivado/2024.1/settings64.sh
cd ~/Program/RF-clock
vivado -mode batch -source FPGA/ip/generate_rfdc_ip.tcl \
    -tclargs FPGA/ip/rfdc_profile_480mhz
```

下面的片段是脚本内部使用的最小 RFDC 参数示例：

以下模板只适合在已经确认 Vivado/IP/part 的环境使用。参数名称和合法取值必须先
用当前安装的组件 metadata 验证：

```tcl
create_project -force rfdc_tx ./build/rfdc_tx \
    -part xczu48dr-ffvg1517-2-e

create_ip \
    -name usp_rf_data_converter \
    -vendor xilinx.com \
    -library ip \
    -version 2.6 \
    -module_name rfdc_tx

set rfdc [get_ips rfdc_tx]

set_property -dict [list \
    CONFIG.DAC2_PLL_Enable {true} \
    CONFIG.DAC2_Refclk_Freq {480.000} \
    CONFIG.DAC2_Sampling_Rate {9.6} \
    CONFIG.DAC2_Outclk_Freq {300.000} \
    CONFIG.DAC_Slice20_Enable {true} \
    CONFIG.DAC_Data_Type20 {0} \
    CONFIG.DAC_Data_Width20 {16} \
    CONFIG.DAC_Interpolation_Mode20 {6} \
    CONFIG.DAC_Mixer_Mode20 {0} \
    CONFIG.DAC_Mixer_Type20 {2} \
    CONFIG.DAC_Mode20 {2} \
    CONFIG.DAC_NCO_Freq20 {3.800} \
    CONFIG.DAC_Nyquist20 {1} \
    CONFIG.DAC_RTS {true} \
] $rfdc

generate_target all $rfdc
report_ip_status -file ./build/rfdc_tx/ip_status.rpt
```

### 4.3 Tcl 必须做的回读检查

不要只检查 `set_property` 是否成功。至少把请求值和回读值写入文件，并对以下内容
做失败即停止的检查：

```tcl
foreach {key expected} {
    CONFIG.DAC2_Refclk_Freq       480.000
    CONFIG.DAC2_Sampling_Rate      9.6
    CONFIG.DAC2_Outclk_Freq      300.000
    CONFIG.DAC_Slice20_Enable       true
    CONFIG.DAC_Data_Width20          16
    CONFIG.DAC_Interpolation_Mode20  6
    CONFIG.DAC_NCO_Freq20          3.800
} {
    set actual [get_property $key $rfdc]
    if {$actual ne $expected} {
        error "$key mismatch: actual=$actual expected=$expected"
    }
}
```

还应检查派生值（例如 `DAC2_Enable`、`DAC2_Fabric_Freq`、PLL feedback/output
divider）以及生成 HDL 中的端口宽度。参考脚本检查了类似：

```text
input  [255:0] s20_axis_tdata
output [127:0] m22_axis_tdata
output [127:0] m23_axis_tdata
output         dac20_datapath_overflow
input  [2:0]   dac20_fast_shutdown
```

Tcl 参数是 IP 构建期接口，不是运行期 C 驱动的枚举。特别是 GUI 的 `NCO_Freq20`
使用 GHz，而运行期 `MixerSettings.Freq` 通常使用 MHz；两者必须分别查阅当前版本
驱动和实际读回值。

## 5. 运行时 C 配置（Vitis/standalone 或 Linux 驱动）

### 5.1 推荐的运行顺序

运行期不要在 RFDC 正在输出活动同步帧时修改 tile/block 配置。建议顺序为：

1. 确认板级参考时钟和 RFDC tile 时钟已锁定；
2. 保持 DAC 输出关闭或数据为零，H10 不给出 `tx_admission_ready`；
3. 通过 `XRFdc_CfgInitialize` 初始化驱动对象；
4. 读取 tile/block 状态和实际配置；
5. 设置允许运行期修改的 mixer/QMC/增益参数；
6. 调用相应 `UpdateEvent` 使设置生效；
7. 再次读回并比对；
8. 只有 RFDC、H03、H08、H09 和 bridge 全部 ready 时，才允许 H05 接受发送命令。

### 5.2 C 代码骨架

下面是驱动调用的结构示例。实际 `XRFdc_Config` 查找方式、tile/block 编号、枚举
名称和可写字段，以目标 Vitis/embeddedsw 中的 `xrfdc.h` 为准：

```c
#include "xrfdc.h"

static XRFdc rfdc;

int rfdc_init(void)
{
    XRFdc_Config *cfg;
    int status;

    cfg = XRFdc_LookupConfig(XPAR_XRFDC_0_DEVICE_ID);
    if (cfg == NULL) {
        return XST_FAILURE;
    }

    status = XRFdc_CfgInitialize(&rfdc, cfg);
    if (status != XRFDC_SUCCESS) {
        return status;
    }

    /* First read/qualify status. Do not enable TX merely because init passed. */
    XRFdc_IPStatus ip_status;
    XRFdc_GetIPStatus(&rfdc, &ip_status);

    return XST_SUCCESS;
}

int rfdc_set_dac_mixer(void)
{
    XRFdc_Mixer_Settings mixer;
    int status;

    status = XRFdc_GetMixerSettings(
        &rfdc, XRFDC_DAC_TILE, 2, 0, &mixer);
    if (status != XRFDC_SUCCESS) {
        return status;
    }

    /* Units and enum values must be checked in this SDK version. */
    mixer.Freq = 3800.0;       /* commonly MHz in the runtime API */
    mixer.PhaseOffset = 0.0;

    status = XRFdc_SetMixerSettings(
        &rfdc, XRFDC_DAC_TILE, 2, 0, &mixer);
    if (status != XRFDC_SUCCESS) {
        return status;
    }

    return XRFdc_UpdateEvent(
        &rfdc, XRFDC_DAC_TILE, 2, 0, XRFDC_EVENT_MIXER);
}
```

不要把 `mixer.Freq = 3800.0` 当成无需验证的事实。参考 profile 的 `3.800` 是
Vivado IP 字段值（GHz）；许多运行期 API 用 MHz，且高通 IMR 可能引入频谱折叠/反向。
必须用本机头文件、驱动实现和 `GetMixerSettings` 回读确认。

插值、采样率、PLL 和 tile enable 通常是构建期参数，是否支持运行期修改不能假设。
如果 SDK 没有明确的 setter/getter 或要求 tile 停止/复位，应重新生成 IP，而不是在
活动发送期间写寄存器。

### 5.3 C++ 封装建议

C++ 不会提供另一套 RFDC 硬件接口，通常只是对 C 驱动做错误处理和生命周期封装：

```cpp
class RfdcManager {
public:
    int initialize();
    int read_profile(Profile &out) const;
    int configure_mixer(double freq_mhz);
    int qualify_tx() const;

private:
    XRFdc handle_{};
};
```

`qualify_tx()` 应同时检查 RFDC 状态、H10 的 `tx_admission_ready` 和 H05 的命令状态，
不能只检查 `XRFdc_CfgInitialize()` 返回成功。

## 6. 运行时 Python/PYNQ 配置

### 6.1 只读检查

PYNQ 中对象层级由 overlay/HWH 决定，不能硬编码成所有板卡都相同。常见形式是：

```python
from pynq import Overlay

overlay = Overlay("rf_clock.bit", download=False)
rfdc = overlay.radio.rfdc       # 实际层级以 overlay.ip_dict/HWH 为准
print(rfdc.IPStatus)
```

访问 `overlay.radio` 或构造 radio 对象在部分 PYNQ base 中会触发 RFDC 初始化；这不应
被称为“纯只读”。在生产软件中应先明确硬件所有权、DAC 默认关闭和恢复策略。

### 6.2 PYNQ `xrfdc` 运行期设置骨架

不同 PYNQ/xrfdc 版本的 tile/block 层级和字段略有不同。下面展示的是常见结构，运行
前必须打印对象和字段确认：

```python
import xrfdc
from pynq import Overlay

overlay = Overlay("rf_clock.bit", download=False)
radio = overlay.radio
rfdc = radio.rfdc

dac_tile = rfdc.dac_tiles[2]
dac_block = dac_tile.blocks[0]

print("IPStatus:", rfdc.IPStatus)
print("DAC block before:", dict(dac_block.BlockStatus))
print("Mixer before:", dict(dac_block.MixerSettings))

settings = dict(dac_block.MixerSettings)
settings["Freq"] = 3800.0       # 常见 API 单位为 MHz，必须实测回读确认
settings["PhaseOffset"] = 0.0
dac_block.MixerSettings = settings
dac_block.UpdateEvent(xrfdc.EVENT_MIXER)

print("Mixer after:", dict(dac_block.MixerSettings))
```

只要修改失败、回读不一致、tile 未锁定、出现 overflow/fast-shutdown 或 H10 未给出
admission，就必须保持 DAC 静默并报告故障；不要自动重试一个状态不确定的 RFDC 写入。

### 6.3 运行时 Python 的安全边界

- 使用与 bit/HWH 同一构建产物的 Python 代码；
- 先读 `IPStatus`、tile/block 状态和当前 mixer，再决定是否写入；
- 写入前停止或禁止 H05 新命令，确认 H09/RFDC 数据流已排空；
- 写入后读取并记录配置快照；
- 退出路径显式关闭 DAC 或置零数据；
- 不用 Python 脚本自动猜地址、自动下载未知 overlay 或恢复未知故障。

仓库参考工程的 `utils/pynq_rfdc_status.py` 和 `utils/pynq_amplitude_steps.py`
可作为状态读取和受控 mixer 更新的参考，但其中的板卡、overlay、版本和地址不能
直接移植到本项目。

## 7. 如何检查 bridge 与 Vivado RFDC 一致

建议按静态、RTL、运行时三层检查，而不是只看一张 block diagram。

### 7.1 静态 IP/HWH 检查

从最终导出的 HWH/XSA 或 `.xci` 读取并保存：

```text
RFDC IP 类型和版本
DAC tile/block 与 s20_axis 的映射
s20_axis_tdata 宽度
s20_axis_tvalid/tready 是否存在
TKEEP/TLAST/TUSER 是否存在
s2_axis_aclk 的频率和连接
s2_axis_aresetn 的极性和连接
DAC2 参考频率、采样率、fabric 频率
插值、mixer、Nyquist、NCO、RTS 选项
```

静态检查应拒绝以下任何不一致：

| 检查项 | bridge 当前假设 | RFDC 必须满足 |
|---|---|---|
| 数据宽度 | 256 bit | `s20_axis_tdata` 为 256 bit |
| lane | 8 × 32 bit | IP 数据格式为 8 对 16-bit I/Q |
| sideband | 无 `TKEEP/TLAST` | profile 不能要求 bridge 提供这些旁带 |
| 时钟 | 100 MHz sample/fabric domain | `s2_axis_aclk` 和 H09 sample clock 一致 |
| ready | 直接反馈给 H09 | `s20_axis_tready` 有明确健康/停顿语义 |
| reset | `rst` 期间不发 valid/ready | AXIS reset 极性和释放顺序已核对 |

### 7.2 生成 HDL 检查

在 Vivado 生成的 RFDC wrapper 中检查端口，而不是只查看 GUI 标签：

```powershell
rg -n "s20_axis_tdata|s20_axis_tvalid|s20_axis_tready|s2_axis_aclk|s2_axis_aresetn" \
    .\path\to\generated\rfdc*.v
```

应看到类似：

```verilog
input  [255:0] s20_axis_tdata;
input          s20_axis_tvalid;
output         s20_axis_tready;
input          s2_axis_aclk;
input          s2_axis_aresetn;
```

实际名称可能被 BD 包装器改变，最终应以顶层 elaborated design 和 HWH 为准。

### 7.3 RTL 编译检查

对 H09 bridge 做独立的 Verilog 编译，并在 testbench 中注入：

1. 8 个 lane 的不同 I/Q 递增值，检查字序；
2. `sample_mask = 8'b0000_0111`，检查 lane 3--7 被置零；
3. `sample_valid=1` 时 `m_axis_tvalid=1`；
4. reset 时 `m_axis_tvalid=0`、`sample_ready=0`；
5. `m_axis_tready=0` 且 `sample_valid=1` 时产生 bridge fault；
6. `sample_last` 被接受时产生 `frame_done`。

RTL 通过只能证明 bridge 的位宽和握手逻辑，不能证明 RFDC IP 的采样率和模拟配置。

### 7.4 运行时读回检查

在板上、DAC 静默且取得所有权后，记录：

- RFDC IP/tile/block 状态；
- PLL/clock lock；
- mixer/NCO/QMC 当前设置；
- DAC 数据路径 overflow/fast-shutdown；
- H10 的 RFDC ready/fault；
- H09 bridge 的 `bridge_active`、`bridge_fault`、`frame_done`。

只有静态配置、生成端口、RTL 字序和运行时读回全部一致，才能把该配置作为本次
发送实验的有效 profile。即便如此，仍不能把它等同于模拟波形或跨节点同步验收。

## 8. 推荐的文件和产物管理

建议每次 RFDC 配置都保存以下信息：

```text
Vivado version
target part/board
RFDC IP version
Tcl script SHA-256
XCI/BD/HWH/XSA/bit SHA-256
requested parameters
Vivado readback parameters
generated-port audit result
runtime readback result
board serial and reference-clock setup
```

构建期配置、运行期配置和 H10 状态应使用同一个 `config_id` 或 profile ID 关联。
H05/H02 看到的 TX 故障码只能说明数字控制路径观察到的原因；它不能替代 RFDC
运行期快照、时钟锁定记录和模拟测试记录。

## 9. 参考脚本和当前限制

- 本项目 bridge：[`FPGA/src/H09_pulse_rfdc_bridge.v`](../FPGA/src/H09_pulse_rfdc_bridge.v)
- 本项目 RTL 编译脚本：[`FPGA/compile_rtl.tcl`](../FPGA/compile_rtl.tcl)
- RFDC IP 生成脚本：[`FPGA/ip/generate_rfdc_ip.tcl`](../FPGA/ip/generate_rfdc_ip.tcl)
- RFDC Block Design 与 wrapper 生成脚本：[`FPGA/ip/create_rfdc_block_design.tcl`](../FPGA/ip/create_rfdc_block_design.tcl)
- 当前生成的可复用 wrapper：[`FPGA/src/rf_clock_bd_wrapper.v`](../FPGA/src/rf_clock_bd_wrapper.v)
- 参考 RFDC 静态配置脚本：`keysight-bb84-testbed/RFSoC/utils/configure_rrc_rfdc.tcl`
- 参考静态 profile：`keysight-bb84-testbed/docs/RF_clock/rrc_rfdc_profile.md`
- 参考 DAC 接口核验：`keysight-bb84-testbed/docs/RF_clock/tx_rfdc_interface_v1.md`

当前证据仍是静态 IP/源码检查和已有参考工程记录；本项目尚未因本文档自动生成
板级 RFDC 工程、bitstream、ARM 驱动或真实 DAC/ADC 波形验收。任何“已锁定”、
“已发射”或“时延已确定”的结论，都必须附带对应板卡运行时证据。

### 9.1 一键生成 RFDC Block Design wrapper

在 `RF-clock-ai` 上执行（Vivado 2024.1）：

```bash
source /tools/Xilinx/Vivado/2024.1/settings64.sh
cd ~/Program/RF-clock
vivado -mode batch \
  -source FPGA/ip/create_rfdc_block_design.tcl \
  -tclargs FPGA/ip/rf_clock_bd_480mhz
```

脚本会创建 RFDC、ADC/DAC 两个 300 MHz→100 MHz Clocking Wizard、复位反相器，
并生成 `FPGA/src/rf_clock_bd_wrapper.v`。wrapper 的 `S20_AXIS_*` 可直接连接
`H09_pulse_rfdc_bridge`，`tx_clk_100` 可作为 H03/H05/H08/H09/H10 的 TX 时钟；
`S_AXI_*` 留给 H00 中的 PS/AXI interconnect。板级差分时钟、SYSREF、ADC 输入和
DAC 输出也都保留在 wrapper 端口上，管脚约束仍需按实际板卡补充。

该可复用 BD 没有 AXI master，因此 Vivado 对 RFDC `S_AXI/Reg` 未分配地址段的
`BD 41-1356` 提示是预期的；把 wrapper 放进包含 PS/SmartConnect 的板级 BD 后，
再在 Address Editor 中分配 RFDC 基地址。
