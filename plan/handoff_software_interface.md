# Handoff：ARM ↔ PL 软件接口（Python 实现）

写于 2026-10-13，交给下一个 session。目标读者：接手实现的 agent 和用户。
PL 部分（RTL、BD、约束、比特流）由另一个 session 负责；需要改 PL 时提出需求，不要
自己改 RTL 或 ABI。

开始前先读：`AGENTS.md`、`docs/ABI.md`（接口契约，最重要）、`plan/RF_clock_modules_zh.md`
§3–§7、`plan/checklist.md` §4–§5。时钟芯片配置是另一份 handoff：
`plan/handoff_clock_config.md`。

## 1. 目标

在 RFSoC 4x2 的 ARM（PYNQ）上，用 Python 实现软件这一侧的接口，让 PL 能被上电、
配置、发射一次 TX 突发、采集一次 RX 窗口，并取回时间戳和样本。先做到能在板上完成
计划里程碑 M1–M3（`plan/RF_clock_modules_zh.md` §9）：

- M1：每秒正好 100,000,000 tick，PPS 捕获正常；
- M2：TX 突发相对 PPS 的延迟固定，重启后不变；
- M3：本板 DAC → 衰减器 → ADC 有线环回，相关得到稳定延时。

相关、TWTT、Kalman、FS725 驱动、节点间通信不在这次范围内（计划 §7 的其余模块）。

## 2. 硬件现状（PL 已完成的部分）

| 项目 | 状态 |
| --- | --- |
| RTL（P0–P4、P6、`p_top`） | iverilog 仿真 5 个 testbench 全部通过（`./PL/sim/run_pl_tests.ps1`） |
| Block Design | `PL/bd/create_rf_clock_bd.tcl`，Vivado 2024.1 校验通过 |
| 实现 | run6：时序满足，WNS 0.335 ns，WHS 0.011 ns |
| 产物 | 随包发布：`src/rf_clock/overlay/`（rf-clock 1.2.0.0，ABI 1.2；来自 `RF-clock-ai:~/build/rf_clock_impl_run6/`） |
| 板上验证 | M1、M2、M3（串扰环回）通过（`plan/checklist.md` §4–§5） |

产物是在 include 路径改为 `src/...` 之前构建的（这只是写法变化，不影响功能）。下一次
构建以仓库为准：

```bash
source /tools/Xilinx/Vivado/2024.1/settings64.sh
cd ~/Program/RF-clock
vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl \
    -tclargs ~/build/<新目录> ~/ref/RFSoC4x2-BSP/board_files impl
```

PYNQ 加载 overlay 需要 `.bit` 和 `.hwh` 同名放在一起。

### 2.1 地址和接口（详见 `docs/ABI.md`）

| 地址空间 | 基地址 | 说明 |
| --- | --- | --- |
| P2 寄存器 | 0xA000_0000 | 命令、状态、时间戳；**fabric 时钟域** |
| AXI DMA（S2MM） | 0xA001_0000 | RX 采集写 DDR |
| 时钟状态 GPIO | 0xA002_0000 | bit0 = fabric MMCM 锁定；PS 时钟域 |
| RFDC | 0xA004_0000 | 由 PYNQ `xrfdc` 驱动访问 |
| TX 波形 buffer | 0xA040_0000 | 4 MiB 窗口，69632 beat 有存储（URAM）；**fabric 时钟域** |

**最重要的一条规则：** 访问 P2 或 TX buffer 之前，必须先读时钟状态 GPIO 并确认 bit0 = 1。
fabric 时钟不存在时访问这两块地址，总线会卡死，ARM 只能重启（`docs/ABI.md` §1.2）。

## 3. 要实现的模块

建议放在 `src/rf_clock/pl/`。板上和开发机都要能导入。

| 模块 | 功能 | 要点 |
| --- | --- | --- |
| `abi.py` | 寄存器偏移、位域、状态、返回码、标志位 | **由脚本从 `PL/src/p_regs.vh` 和 `p_defs.vh` 生成**，并加一个测试：两边不一致就失败。不要手抄 |
| `mmio.py` | 32 位读写的抽象接口 | 两个实现：板上用 `pynq.MMIO`；开发机用假的 PL 模型（见第 5 节） |
| `client.py` | `PLClient` | 检查 ID/VERSION（major = 1）；读 64 位值时先 LO 后 HI；解析 STATUS；提交 TX/RX 命令（先写 shadow 再写 doorbell，再读 RESULT 确认 ARMED）；等待状态；CLEAR_FAULT；记录 `TIME_EPOCH`，变化时丢弃数据。所有 fabric 域访问前检查时钟状态 GPIO |
| `waveform.py` | TX 波形 | 提供 `load_config`、`generate_test_chips`、`simulate_full_convolution`、`quantize_output` 和 `WaveformGenerator`；系数固定读自 `config/RRC_config.db` 的 `RRC_config`/`RRC_coeff` 表（`COEFFICIENT` 为浮点数）。旧的 `PL/data/H09_config.db` 格式、旧定点兼容路径和旧 RTL/CSV 比对入口均已删除；`utils/sim_h09_rrc_fir.py` 仅复用该接口生成可选诊断图 |
| `beats.py` | 样本 ↔ 256-bit beat | TX：8 个复样本打包成一个 beat；RX：beat 拆成 I/Q。beat 内布局**尚未确定**，写成可配置，默认按 ADC 合并顺序 `{Q[255:128], I[127:0]}` |
| `txbuf.py` | 写 TX buffer | 第 i 个 beat 在偏移 i × 32；按 32 位字小端写入；TX 处于 ARMED/RUNNING 时禁止写（否则 PL 进入 FAULT） |
| `rxdma.py` | RX 采集（DMA 一侧；命令和窗口校验在 `client.py`） | 分配 DMA buffer（`pynq.allocate`）；按 `docs/ABI.md` §7.3 的顺序：**先启动 DMA，再按 RX doorbell**；等 DONE；校验 `RX_RESULT`、`RX_ACTUAL == target`、DMA 实际字节数 == `RX_BEATS × 32`；`invalidate()` 后再读 |
| `bringup.py` | 上电顺序 | 按 `docs/ABI.md` §7.1：加载 overlay → 写时钟芯片 → 拉 `pl_resetn0` → 等 GPIO 锁定 → RFDC tile 启动和 MTS 同步 → PL 检查（含写 `PPS_CONFIG`） |
| `experiments/` | M1–M3 的脚本 | 见第 6 节 |

## 4. 已知约束和数字

- 时间：1 tick = 10 ns = 1 beat = 8 个复样本，Fs = 800 MS/s；样本 n 的数字时刻为
  `target × 10 ns + n × 1.25 ns`（`docs/ABI.md` §6）。
- RRC：每码片 8 样本，81 抽头，突发长度 = 码片数 × 8 + 73 个样本。
- **TX buffer 为 69632 beat**（rf-clock 1.2.1.0 起；之前 8192），65535 码片的突发（65545 beat）
  放得下。
- RX 窗口最多 2097151 beat（受 DMA 26 位长度寄存器限制）。
- `MIN_LEAD_TICKS = 64`：target 至少比当前 tick 晚 64 tick。实际上要留出 Linux 调度和
  Python 的延迟，建议至少提前几毫秒。
- 发射的 5.8 GHz 是第二 Nyquist 区镜像，**接收基带是发射基带的共轭**。BPSK 实信号
  不受影响（计划 §3）。
- DAC 对应板上 `vout20`（`DAC_VOUT0_230`，**板上 SMA 口 DAC_A**；原理图为准，4x2 手册框图把它写成 DAC B 是错的）；ADC 对应 `vin2_01`。ADC/DAC 的 tile 0 只为满足 MTS 而启用，
  它们的数据不用。
- DMA 地址宽度 64 位，需要同时写 `S2MM_DA` 和 `S2MM_DA_MSB`（PYNQ 的 DMA 驱动会处理）。

## 5. 开发机上的测试

板子可用之前，用一个假的 PL 模型测试 `client`、`txbuf`、`rxdma` 的逻辑：

- 按 `docs/ABI.md` 实现寄存器语义：doorbell 校验顺序和拒绝码、状态转换、快照（读 LO
  锁存 HI）、SLVERR 情况（部分写、写只读、未映射地址）、时钟状态 GPIO。
- raw_tick 由测试控制推进。
- 可以拿 `PL/sim/tb_p_top.v` 当行为参考：它用 AXI-Lite 走完了一次 TX + RX 环回。
- pytest；测试不得依赖 `pynq`（开发机上没有）。

## 6. 板上实验（M1–M3）

| 实验 | 步骤 | 通过标准 |
| --- | --- | --- |
| M1 tick 与 PPS | `tests/pl/m1_check.py`：读 `PPS_LAST_PERIOD`、`PPS_COUNT`、`PPS_JITTER_CNT`，持续几分钟 | 周期恒为 100,000,000；`PPS_JITTER_CNT` 不增长；`pps_locked = 1`（2026-10-13 通过，3 小时） |
| M2 TX 定时 | `tests/pl/m2_tx_timing.py`：RFDC 启动和 MTS，以 `pps_tick + 偏移` 为 target 发突发；示波器以 PPS 触发看 DAC 输出 | 突发相对 PPS 的延迟固定；重启（含重新 MTS）后不变（2026-10-14 数字部分通过，模拟待测） |
| M3 有线环回 | `tests/pl/m3_loopback.py`：`vout20` → 衰减器 → `vin2_01`；同一 target 发射并采集；`rf_clock.correlator` 求峰 | 峰位置稳定；`RX_ACTUAL == target`；多次重启后延时一致（2026-10-14：RX 通路和 DDR 压力已通过；靠串扰测得 265.1 ns、标准差 156 ps；接线后待测） |

M3 的 `--lane-check` 已确定 beat 布局：DAC 是 I/Q 逐字交错（`TX_LAYOUT`），ADC 合并后是平面
格式（`RX_LAYOUT`），见 `docs/ABI.md` §4.5.8、§4.6.9。

## 7. 依赖和阻塞项

| 项目 | 影响 | 状态 |
| --- | --- | --- |
| LMK04828/LMX2594 配置文件 | 没有 PL_CLK 100 MHz，MMCM 不锁定，P2 和 TX buffer 都不能访问 | 见 `plan/handoff_clock_config.md`；可先用其中的 `onboard` 配置调通 PL |
| 板子 ssh 访问 | 从 `RF-clock-ai` 登录 `xilinx@192.168.2.99` 被拒绝（publickey） | 需用户配置密钥 |
| 板上 PYNQ / Python 版本 | 决定能用的语法；`xrfdc` 是否提供 MTS 接口 | 未查。`pyproject.toml` 要求 Python ≥ 3.14，只适用于开发机；板上代码要按板上的 Python 版本写 |
| PRN 选择 | 现在的模型用 16 位 LFSR（种子 0xACE1，抽头 0/2/3/5），计划里写的是 4095 码片 m 序列 | **需要用户决定**码长和序列；码长受 TX buffer 限制（≤ 约 8182 码片） |
| DAC 幅度 | 模型默认幅度 512（2⁹）；DAC 满量程是 16 位 | 需要在 M2/M3 时按输出功率和削波情况确定 |
| 1PPS 输入 | 4x2 的 1PPS 比较器阈值和 FS725 PPS 电平是否匹配 | run2 上板 `PPS_COUNT` = 0（比较器开漏、未上拉）；run3 加上拉和第二路，用 `PPS_INPUTS` 判断（清单 §1） |

## 8. 不要做的事

- 不要改 RTL、BD、约束或 `p_regs.vh` / `p_defs.vh`；需要 PL 改动时提给 PL 负责人。
  ABI 有任何变化，都要同时更新 `p_regs.vh`、`p_defs.vh`、`docs/ABI.md`，并按 §14 升级
  `VERSION`。
- 不要在软件里写死地址：从 HWH（`overlay.ip_dict`）读取，再与 `docs/ABI.md` 核对。
- 不要把软件读到的 `TICK` 当作测量时间戳；测量只用 `PPS_TICK`、`TX_ACTUAL`、
  `RX_ACTUAL`（`docs/ABI.md` §2）。
- 不要使用作废窗口（`RX_RESULT = 0x30`）的样本，也不要使用跨 `TIME_EPOCH` 的数据。

## 9. 本次实现状态与示例

软件接口已放在 `src/rf_clock/pl/`：

- **入口是 `board.py` 的 `PL`**（2026-10-14）：`PL.open()` 按 `docs/ABI.md` §7.1 完成全部上电，
  返回的对象持有各硬件部件（`ctrl` 寄存器、`tx_buffer`、`rx_dma`、`rfdc`、`clock_gpio`），操作通过
  部件自己的方法完成。说明见 `docs/ABI.md` §10、§11.1；不用本库时的底层访问（PYNQ 原生、C++）
  见附录 A。
- `abi.py` 由 `tools/generate_abi.py` 从 `PL/src/p_regs.vh` 和
  `PL/src/p_defs.vh` 生成。寄存器偏移、CONTROL 位和 STATUS 位分别使用
  `ABI.REG.*`、`ABI.CTRL.*`、`ABI.STAT.*`；不要再新增散落的数字偏移。
- `PLClient` 负责 GPIO 时钟守门、ID/VERSION、状态解码、LO→HI 的 64 位读取、
  shadow→doorbell→RESULT 命令顺序和 `TIME_EPOCH` 检查。
- `TXBuffer`、`RXDMA`、`BeatLayout`/`BeatPacker` 和 `WaveformGenerator`
  分别负责片上波形 buffer、DMA 窗口、256-bit beat 布局和 ARM 端波形生成。
  这些模块不在导入时依赖 PYNQ，开发机可以使用 `MemoryMMIO` 和假 DMA 测试。
- `bringup.py` 通过 HWH 的 `overlay.ip_dict` 获取 MMIO 地址，并按 overlay→时钟→
  `pl_resetn0`→GPIO→RFDC/MTS→PL 检查的顺序组织板上启动；RFDC 具体 API 通过回调注入。

带注释的最小示例在 `examples/pl/`：

```text
uv run python examples/pl/01_fake_tx.py
uv run python examples/pl/02_fake_rx.py
```

`03_pynq_bringup.py` 是板上骨架，执行前要填入当前 PYNQ 镜像的
`xrfclk` 配置和 `xrfdc` MTS 调用。硬件 M1–M3 仍需按第 6 节实测，示例中的假 PL
不能替代板上验证。

## 10. 板上部署（已在板上完成，2026-10-13）

板子（`xilinx@192.168.2.99`，主机名 `pynq`）不能联网；不改动 PYNQ 环境
`/usr/local/share/pynq-venv`（Python 3.10.4，pynq 3.1.1，numpy 1.21.5 来自系统包，
`include-system-site-packages = true`）。登录方式：从 `RF-clock-ai` 用 `ssh xilinx@192.168.2.99`
（`RF-clock-ai` 的 `~/.ssh/id_ed25519` 已授权）。

当前板上状态：

| 项目 | 位置 |
| --- | --- |
| 代码 | `~/RF-clock`（从 `RF-clock-ai:~/Program/RF-clock` rsync，排除 `.git`、`.venv`、`__pycache__`、`.codegraph`） |
| 环境 | `~/RF-clock/.venv`（uv 0.13.0 创建，打开 system site-packages） |
| PYNQ 包 | `.venv/.../site-packages/pynq_venv.pth` 只读引用 PYNQ 环境的 site-packages |
| xrfclk | 私有副本在 `.venv/.../site-packages/xrfclk/`（历史上为放 `LMK04828_100.0.txt`、`LMX2594_480.0.txt`）。2026-10-14 起 `PL.open()` 直接从 `rf_clock/clocks/files/` 读寄存器、填进 xrfclk 的 `_Config` 表，副本里的文件不再使用 |
| rf_clock | `uv pip install --no-deps -e .`（可编辑，指向 `~/RF-clock/src`） |
| 比特流 | 随包的 `~/RF-clock/src/rf_clock/overlay/`（rf-clock 1.2.0.0）；旧的 run2–run4 留在 `~/RF-clock/overlay/`，加载它们要 `check_version=False` |

找包顺序已核实为：`.venv` → PYNQ 环境 → 系统包，与 PYNQ 自己的顺序一致。

重建环境的命令（在板上，`~/RF-clock` 下）：

```bash
export PATH="$HOME/.local/bin:$PATH"
uv venv --offline --python /usr/bin/python3.10 --system-site-packages .venv
NEW_SITE=$(.venv/bin/python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
PYNQ_SITE=/usr/local/share/pynq-venv/lib/python3.10/site-packages
echo "$PYNQ_SITE" > "$NEW_SITE/pynq_venv.pth"
cp -r "$PYNQ_SITE/xrfclk" "$NEW_SITE/"
cp src/rf_clock/clocks/files/LMK04828_100.0.txt src/rf_clock/clocks/files/LMX2594_480.0.txt "$NEW_SITE/xrfclk/"
uv pip install --python .venv/bin/python --no-deps -e .
```

要点：

- 可编辑安装不需要联网：uv 0.13.0 用自带的构建后端，只对 `uv_build<0.13.0` 约束给出警告。
- **`BOARD` 环境变量**：`xrfclk` 在 import 时就读 `BOARD`。它由
  `/etc/profile.d/boardname.sh` 从设备树读出（`RFSoC4x2`），只在登录 shell 里有。用
  `bash -lc` 或交互登录后再运行。PYNQ 运行时还需要 `XILINX_XRT=/usr`。
- **root**：访问 `/dev/mem`、下载比特流、写 SPI 时钟芯片都需要 root，`sudo` 会清掉环境变量：
  `sudo env BOARD=$BOARD XILINX_XRT=/usr .venv/bin/python <脚本>`（在登录 shell 里执行）。
- 板上不要用 `uv sync`：它会按锁文件同步整个环境。`uv pip install --no-deps` 只加装项目。
- **不要拷 `LMK04828_casper_ext10m.txt`** 进 `xrfclk` 目录：`xrfclk` 用 `split('_')` 解析文件名。
- 不要在 `.venv` 里另装一份 numpy：它会盖掉 `pynq` 用的那份（1.21.5，与 `pyproject.toml` 一致）。
- Jupyter（PYNQ 自带，以 root 运行）：
  `sudo .venv/bin/python -m ipykernel install --name rf-clock --display-name "RF-clock (uv)"`。
- PYNQ 镜像升级后，`.pth` 自动跟随，`xrfclk` 私有副本需要重拷。
- **远程恢复**：访问时钟没在跑的 fabric 域会把 AXI 总线卡死，板子 ping 不通。从
  `RF-clock-ai`（JTAG 线接在它上面）复位：`source /tools/Xilinx/Vivado/2024.1/settings64.sh;
  xsdb tools/jtag_reset_board.tcl 887329260072A`，约 40 s 后 ssh 恢复（2026-10-13 实测）。
  重启后 PL 里是 PYNQ 的 base overlay，时钟是 PYNQ 默认值。串口日志在
  `RF-clock-ai:~/build/board99_console.log`。

## 11. PL 侧 review 后的修改（2026-10-13）

PL 负责人按 RTL 行为 review 了 `src/rf_clock/pl/`，以下行为已改（测试见
`tests/test_pl_interface.py`、`tests/test_bringup.py`、`tests/test_waveform*.py`）：

| 位置 | 原行为 | 现行为与原因 |
| --- | --- | --- |
| `Bringup.run` | 加载 overlay 后立即要求 `pps_locked` | 轮询等待：`time_valid` 超时 1 s，`pps_locked` 超时 10 s。P1 要一个起始 PPS 沿加 4 个正确周期（约 5 s）才锁定；FS725 的 1PPS 必须接上 |
| `Bringup.run` | 有故障时直接 CLEAR_FAULT | 先读 `FAULT_BITS`/`FAULT_INFO`，存入 `BringupResult.initial_fault_*`，再清除（ABI §7.1） |
| `PLClient` 提交命令 | 只有 RESULT == ARMED 才算接受 | ARMED/RUNNING 算接受；DONE（RX 还有 RX_INVALID）只有在 `TX_SEQ`/`RX_SEQ` 增加时才算。target 很近时，PL 可能在 ARM 读 RESULT 之前就已开始执行 |
| `RXCapture.capture`（现为 `RXDMA.prepare`） | doorbell 被拒后 S2MM 保持启动 | 调用 `DMAEngine.abort()`：AXI DMA 软复位（PG021 `S2MM_DMACR` bit 2）并重新启动 S2MM 通道；拆分后由下一次 `prepare()` 在通道不空闲时调用。**PYNQ 上的复位行为需上板验证** |
| `waveform.load_config` | 找不到 ID 时改用库中唯一的配置 | 直接报错：收发两端靠 config_id 确认用的是同一组系数 |
| `bringup.overlay_address` | 只查 `ip_dict` | 也查 `mem_dict`。PYNQ 把 AXI BRAM 控制器（TX buffer `tx_bram_ctrl`）列在 `mem_dict` |
| `TXBuffer.write_beats` | 每拍读一次 STATUS、每个字读一次 GPIO | 整批只读一次 STATUS，每拍读一次 GPIO |
| `Bringup.run`（上板后） | 先写时钟再加载 overlay；加载后读一次 GPIO | 先加载 overlay 再写时钟，然后调用 `pl_reset`（`pynq_pl_reset` 拉 `pl_resetn0`），1 s 内轮询 GPIO 锁定；`validate` 后写 `PPS_CONFIG`（`pps_source_trig`/`pps_invert`）。原因见 `docs/ABI.md` §7.1 和清单 §4 |
| `PLClient`（ABI 1.1） | — | 新增 `pps_inputs()`（`PPS_INPUTS`）和 `set_pps_config()`；bitstream 的 minor < 1 时报 `IdentityError` |
