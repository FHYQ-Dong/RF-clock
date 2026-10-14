# Handoff：用 Python 生成 LMK04828 / LMX2594 寄存器文件

写于 2026-10-13，交给下一个 session。目标读者：接手实现的 agent 和用户。
开始前先读 `AGENTS.md`、`plan/RF_clock_modules_zh.md` §2–§3、`plan/checklist.md` §2。

## 1. 目标

在 Ubuntu/Python 上生成 RFSoC 4x2 板上时钟芯片的寄存器文件，不依赖 TI TICS Pro
（只有 Windows 版）。生成的文件由板上 PYNQ 的 `xrfclk` 通过 SPI 写入芯片。

完成后，`plan/checklist.md` §2 中以下几项应能打勾：生成 LMK04828 寄存器文件、
确定 SYSREF 频率、生成 LMX2594 寄存器文件；板上验证项（PLL1 真正锁定到 FS725、
所有时钟同源）需要上板完成。

## 2. 需要的时钟

```text
FS725 10 MHz ──► LMK04828 CLKin0 ──PLL1──► VCXO ──PLL2──► VCO（候选 2400 MHz）
                                                    ├─► PL_CLK 100 MHz   → PL 管脚 AN11（LVCMOS18，单端）
                                                    ├─► PL_SYSREF        → PL 管脚 AP18（LVCMOS18，单端）
                                                    ├─► RFDC SYSREF      → RFDC sysref_in（专用差分管脚）
                                                    └─► LMX2594 参考     → LMX2594 → 480 MHz → ADC tile 0/2、DAC tile 0/2
```

| 输出 | 频率 | 依据 / 约束 |
| --- | --- | --- |
| PL_CLK | 100 MHz | BD 中 MMCM 输入固定为 100 MHz（`PL/bd/create_rf_clock_bd.tcl`）；频率不对 MMCM 不会锁定，PL 寄存器会访问不了 |
| RFDC 参考时钟（LMX 输出） | 480 MHz | RFDC tile PLL：480 MHz → DAC 9.6 GS/s、ADC 4.8 GS/s；ADC/DAC tile 0 和 tile 2 都要 |
| SYSREF（RFDC 和 PL 两路） | 候选 5 MHz | 必须是 100 MHz、480 MHz、4.8 GHz、9.6 GHz 的整数分之一，并满足 PG269 对 MTS SYSREF 的要求（上限等）；10 MHz 也满足整除，需查 PG269 后定 |
| LMK PLL2 VCO | 候选 2400 MHz | 2400/24 = 100，2400/5 = 480，2400/480 = 5；必须落在 LMK04828 VCO0 或 VCO1 的范围内，按数据手册核对 |

两个 tile 的参考时钟必须同源、SYSREF 共用，MTS 才有意义（`AGENTS.md`）。

## 3. 已有资料

都在远程 `RF-clock-ai:~/ref/`（不在仓库里）：

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `mts4x2/LMK04828_500.0.txt`、`mts4x2/LMX2594_500.0.txt` | Xilinx RFSoC-MTS 的 4x2 配置：PL_CLK 500 MHz、LMX 500 MHz，做过 MTS | 已知可用的 4x2 配置；用来反推 LMK 各路输出接到哪里 |
| `mts4x2/mts.xdc`、`mts4x2/mts.tcl` | 同一参考设计的约束和 BD | PL_CLK/PL_SYSREF 管脚，PL_CLK 500 MHz 的时钟结构 |
| `PYNQ/sdbuild/packages/xrfclk/package/xrfclk/xrfclk.py` | PYNQ 的写入程序 | 文件格式、文件名规则、写入过程（LMX 写前需复位） |
| `PYNQ/.../xrfclk/LMX2594_*.txt`、`LMK04832_122.88.txt` | 其他板的配置 | 只能当格式参考，芯片型号不同（LMK04832 ≠ LMK04828） |
| `pynq4x2/base.tcl` | PYNQ 4x2 base overlay | 证明 tile 0 和 tile 2 都有参考时钟 |

**还没拿到，需要找的：**

- CASPER 的 `rfsoc4x2_lmk_CLKin0_extref_10M_PL_122M88_LMXREF_245M76.txt`：LMK04828 从
  CLKin0 锁到外部 10 MHz，PYNQ 论坛有多人确认在 4x2 上可用。PLL1 锁定外部参考的设置
  应以它为准。出处：
  <https://discuss.pynq.io/t/xrfclk-configuration-lmk04828-using-external-10mhz-reference-clock/5562>，
  文件本身可能在 casper-astro 的 GitHub 仓库里。
- 数据手册（在远程用 `curl` 下载，`pdftotext -layout` 提取寄存器表）：
  - LMK04828：<https://www.ti.com/lit/ds/symlink/lmk04828.pdf>
  - LMX2594：<https://www.ti.com/lit/ds/symlink/lmx2594.pdf>
- 4x2 原理图或板卡手册：确认外部参考 SMA 接 CLKin0、LMK 各输出接到哪里、有几个
  LMX2594、各接哪个 tile。拿不到时，用第 5 步的反推结果代替，并在清单里写明依据。

## 4. 文件格式

TICS Pro 导出格式，`xrfclk` 直接读取：

```text
R0 (INIT)	0x000090
R0	0x000010
R2	0x000200
...
```

- 每行：寄存器名，制表符，十六进制值。LMK04828 的值为 24 位：高位是寄存器地址，
  低 8 位是数据（例如 `0x000090` 表示地址 0x000、数据 0x90）。LMX2594 的值是
  地址加 16 位数据，按数据手册核对位宽。
- 行的顺序就是写入顺序，必须保留（例如复位、`R0 (INIT)`、最后的校准写入）。
- 文件名 `<芯片>_<频率>.txt`，`xrfclk.set_ref_clks(lmk_freq, lmx_freq)` 按频率选文件
  （见 `xrfclk.py` 的 `_read_tics_output`）。我们的文件要另取不冲突的频率键，并在
  软件里记录"哪个键对应哪份配置"。

## 5. 做法

1. **提取寄存器定义**：从数据手册提取 LMK04828、LMX2594 的寄存器表，写成 Python 的
   字段表（字段名、地址、位范围、含义）。所有位定义以数据手册为准，不凭记忆。
2. **解码器**：把 `.txt` 解析成结构化配置：
   - LMK04828：CLKin 选择和 R 分频、PLL1 R/N、PLL2 R/N/预分频、VCO 选择、每路输出的
     分频值、格式（LVDS/LVPECL/LVCMOS 等）、是否上电、SYSREF 分频和模式。
   - LMX2594：参考路径（倍频、预分频、R）、N 分频（整数/小数）、VCO、输出分频、
     输出 A/B 的使能和功率。
   - 由此算出每路输出的频率。
3. **验证解码器**：解码 `mts4x2/LMK04828_500.0.txt` 和 `LMX2594_500.0.txt`（以及找到的
   CASPER 文件），算出的频率必须和文件名及已知用途一致（MTS：PL_CLK 500 MHz、LMX
   500 MHz；CASPER：PL 122.88 MHz、LMX 参考 245.76 MHz）。不一致就说明字段表有错，
   先修好再往下走。
4. **反推 4x2 的时钟连线**：对比几份已知文件，确定哪一路 DCLKout/SDCLKout 是 PL_CLK、
   PL_SYSREF、RFDC SYSREF、LMX 参考，各自的输出格式是什么。结果写进本文件第 2 节和
   清单，注明"反推，未经原理图确认"。
5. **编码器**：以 CASPER 外部 10 MHz 文件为基础（保留 PLL1 和 CLKin0 的全部设置），只改：
   PLL2 参数（VCO 2400 MHz）、PL_CLK 分频（100 MHz）、LMX 参考那一路的分频、SYSREF
   分频；不用的输出保持关闭。LMX2594 由已知文件改出 480 MHz 输出。改完用解码器重新
   解码，确认每路频率和锁定相关字段都正确。
6. **两套 LMK 配置**：
   - `ext10m`：CLKin0 外部 10 MHz（正式使用）。
   - `onboard`：用板载参考，输出频率相同，用来在没接 FS725 时先调通 PL 和 RFDC。
     必须在文件名和文档里写明它**不跟随 FS725**，不能用于测量。
7. **测试**：pytest 覆盖解码已知文件的结果、编码后再解码的一致性、频率整除关系
   （SYSREF 能整除 PL_CLK、参考时钟、采样时钟）。

## 6. 交付物

| 路径 | 内容 |
| --- | --- |
| `src/rf_clock/clocks/lmk04828.py`、`lmx2594.py` | 字段表、解码器、编码器 |
| `src/rf_clock/clocks/files/` | 生成的 `.txt`（`ext10m` 和 `onboard` 两套 LMK，加 LMX 480 MHz），以及作为基础的已知文件副本（先确认许可：RFSoC-MTS 为 MIT，CASPER 文件的许可需查） |
| `tests/test_clocks.py` | 上面第 7 步的测试 |
| 本文件、`plan/checklist.md` §2 | 回填结论和依据 |

注意：本 handoff 不再约束 Python 版本或依赖包版本；这些版本以当前 `pyproject.toml` 为准。
生成器在开发机上运行，板上只需要 `.txt` 文件，不需要在板上跑生成器。

## 7. 板上验证（需要用户或上板 session）

1. 把 `.txt` 放到板上 `xrfclk` 的包目录，调用 `xrfclk.set_ref_clks(...)`。
2. 读回 LMK04828 状态寄存器：PLL1、PLL2 锁定；读回 LMX2594 锁定状态。
3. 示波器或频率计：PL_CLK 100 MHz、LMX 输出 480 MHz、SYSREF 频率。
4. 拔掉 FS725 的 10 MHz：PLL1 必须变为失锁（防止 VCXO 自由振荡时误以为正常）；插回
   后重新锁定。
5. 加载 overlay，读时钟状态 GPIO（0xA002_0000，见 `docs/ABI.md` §4.7）bit0 = 1。

目前从 `RF-clock-ai` 用 ssh 登录板子（`xilinx@192.168.2.99`）被拒绝（publickey），需要
用户先配置密钥。

## 7.1 本次开发机交付和已核对的结论（2026-10-13）

已实现：

- `src/rf_clock/clocks/lmk04828.py` 按 LMK04828 数据手册 SNAS605AS 的寄存器位定义解析
  TICS 文件，计算 PLL2/VCO、DCLK/SDCLK 输出和 SYSREF，并保留原始写入顺序；
- `src/rf_clock/clocks/lmx2594.py` 按 LMX2594 数据手册 SNAS696C 解析参考路径、整数/小数
  N 分频、VCO、通道分频和 OUTA/OUTB；
- `src/rf_clock/clocks/files/LMK04828_100.0.txt` 是正式 `ext10m` 配置，文件名中的数字
  是 `xrfclk` 的频率键；`LMK04828_100.1.txt` 是 `onboard` 配置，保留 RFSoC-MTS 的板载
  参考选择，**不跟随 FS725，禁止用于测量**；`LMX2594_480.0.txt` 在 480 MHz LMK
  参考下输出 480 MHz。`100.0/100.1` 的键到模式映射记录在本节，不能仅凭键名推断。
- `ext10m`（`LMK04828_100.0.txt`）用**嵌套零延时模式**（2026-10-14）：0x13F = 0x0B（PLL1_NCLK_MUX =
  反馈 mux，FB_MUX = DCLKout8，FB_MUX_EN = 1），PLL1 R = 12、N = 120，鉴相 833.3 kHz 与环路增益不变。
  不开时 PL_CLK（VCO 2400 MHz ÷ 24）相对 FS725 10 MHz 有 24 种相位（间隔 0.42 ns），示波器测得
  PPS → 突发延时在重写时钟后于 186.5–197 ns 间变化。`PL.open()` 从包里的文件写时钟（`program_clocks`）
- 目标 LMK PLL2 选择 VCO0，为 `160 MHz / 2 × 15 × 2 = 2400 MHz`（2400 MHz 落在
  LMK04828 VCO0 范围内；不能沿用 MTS 的 VCO1 选择），DCLKout8 为 100 MHz，LMX
  参考支路为 480 MHz，SYSREF 分频 480 得 5 MHz。LMK 输出格式和未使用输出沿用已知
  RFSoC 文件；[RFSoC 4x2 Reference Manual 图 5](https://www.realdigital.org/downloads/15456c97a4fe5a1ee66208c5aa3894bb.pdf)
  反推出 OUT0/OUT4 是两个 LMX 参考、OUT8 是 PL 参考、OUT3/OUT9/OUT11 是 SYSREF
  支路，仍需以正式原理图和示波器确认。
- AMD [PG269 v2.6 SYSREF Signal Requirements](https://docs.amd.com/r/en-US/pg269-rf-data-converter/SYSREF-Signal-Requirements)
  的要求是频率严格小于 10 MHz；本项目的 DAC/16=600 MHz、
  ADC/16=300 MHz 和 PL=100 MHz 的最大公约数约束下，5 MHz 同时整除所有相关时钟，
  因此排除候选 10 MHz。该结论来自 AMD 的 SYSREF Signal Requirements 页面，不能替代
  MTS 连续运行和板上相位验证。

已知基础文件：`LMK04828_500.0.txt`、`LMX2594_500.0.txt` 来自 [Xilinx RFSoC-MTS](https://github.com/Xilinx/RFSoC-MTS)，
该仓库为 MIT；CASPER 外部 10 MHz 文件来自 PYNQ 讨论帖附件，附件本身的再发布许可
尚未核实，因此仓库中保留来源副本并在发布前再次确认许可（来源：[PYNQ discussion](https://discuss.pynq.io/t/xrfclk-configuration-lmk04828-using-external-10mhz-reference-clock/5562)）。

自动测试 `tests/test_clocks.py` 已覆盖两个已知 LMK 文件、LMX 500/480 解码、编码文件
的写入顺序和 5 MHz 整除关系。PLL 锁定、输出频率、拔掉 FS725 后 PLL1 失锁以及 MTS
相位一致性仍是第 7 节的上板验证，不能由这些数学测试代替。

## 8. 不要做的事

- 不要凭记忆写寄存器位定义；每个字段都要能在数据手册里找到出处。
- 不要改 PL 的时钟假设（PL_CLK 100 MHz、RFDC 参考 480 MHz）来迁就时钟配置；如果确实
  做不到，停下来和用户讨论，因为这会牵动 BD、频率规划和 `docs/ABI.md`。
- 不要把 `onboard` 配置用于任何时间测量。
