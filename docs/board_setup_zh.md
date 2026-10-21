# 把一块新的 RFSoC 4x2 接入 RF-clock

英文原文（以其为准）：[board_setup.md](board_setup.md)。

本文是从一块新的 Real Digital RFSoC 4x2 到它能参与双向时间比对的完整步骤：系统、安装、接线，然后按依赖顺序做各项检查和每块板的标定，每一步给出命令和必须看到的结果。用到的接口都以 [ABI_zh.md](ABI_zh.md) 为准；各项标定的来由在 `results/` 里（每一步注明对应的报告）。

## 0. 需要准备的

- 板子、一张至少 16 GB 的 micro-SD 卡、它的 USB 线（JTAG 和串口控制台）和网线。
- 实验台主机 RF-clock-PC：以太网 `eno1` = 192.168.2.1/24，板子都接在这个网上；为这个网段提供 NTP（chrony，`/etc/chrony/conf.d/rf-clock.conf`）；仓库在 `~/Program/RF-clock`，带 `.venv`；JTAG 复位需要 Vivado 2024.1。
- 参考信号：FS725 的 10 MHz 经 Thorlabs EPG1（REF IN → REF OUT）和电阻分配器送到每块板；FS725 的 1PPS 经 EPG1（TRIG IN → 每块板一路输出）。
- 射频电缆、20 dB 衰减器和 5725–5875 MHz 带通滤波器：板子自环（DAC_A → ADC_B）一套，两块板之间每个方向一套。
- 发布的 wheel `rf_clock-<版本>-py3-none-any.whl`，来自 GitHub 或组里 Gitea 的 release。

下面板上的命令都用：

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
```

## 1. 系统

1. 把 RFSoC 4x2 的 PYNQ 3.1.1 镜像写进 micro-SD 卡，从卡启动板子。
2. 在 RF-clock-PC 上打开它的串口控制台：`/dev/serial/by-id/usb-Xilinx_JTAG+Serial_<JTAG 序列号>-if01-port0`，115200 波特，用户名和密码都是 `xilinx`。用 `if01`；`if00` 是 JTAG 通道，绝不要往里写。
3. 接入共用网络之前先改地址：每个 PYNQ 镜像的静态地址都是 192.168.2.99，而它已被占用。改 `/etc/network/interfaces.d/eth0` 后重启。已占用：192.168.2.99 = 887329260072A，192.168.2.101 = 887329260101，192.168.2.100 = 别的组的板子。
4. 在 RF-clock-PC 上：`ssh-copy-id xilinx@<地址>`，它把 RF-clock-PC 的 SSH 公钥加进板子的 `~/.ssh/authorized_keys`（第一次要输一次板子的密码）。此后 ssh 不再需要密码，`experiments/` 里的脚本都依赖这一点。
5. 从 RF-clock-PC 对时，两块板的秒配对要用（ABI_zh.md §9.1）：

   ```bash
   sudo mkdir -p /etc/systemd/timesyncd.conf.d
   printf '[Time]\nNTP=192.168.2.1\n' | sudo tee /etc/systemd/timesyncd.conf.d/rf-clock.conf
   sudo systemctl restart systemd-timesyncd
   timedatectl show -p NTPSynchronized --value     # 必须输出 yes
   ```

通过标准：`ssh xilinx@<地址>` 无需密码即可登录，NTPSynchronized 为 yes。

## 2. 安装

rf-clock 装在 PYNQ 自己的环境里，PYNQ 的 Jupyter 和板上其他人都直接用它；它唯一的依赖 numpy 也用这个环境里的（ABI_zh.md §9.1）：

```bash
sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index rf_clock-<版本>-py3-none-any.whl
$run -c "import rf_clock, importlib.metadata as m; print(m.version('rf-clock'))"
```

通过标准：输出装上的版本号。新的 wheel 也这样装；绝不要在那里做可编辑安装。

## 3. 接线

| 信号 | 来自 | 接到板子的 |
| --- | --- | --- |
| 10 MHz 参考 | EPG1 REF OUT（方波，锁定在 FS725 上）→ 电阻分配器 | 外部参考 SMA（LMK04828 CLKin0） |
| 1PPS | EPG1 的一路输出：10 µs 正脉冲，接 50 Ω 时 2.5 V | J7 |
| 射频输出 | DAC_A（`vout20`） | → 20 dB → 带通 → 接收板的 ADC_B |
| 射频输入 | ADC_B（`vin2_01`） | 来自发射板的 DAC_A，接法同上 |

FS725 自己的 10 MHz 正弦经分配器后太弱（LMK04828 曾因此进入 holdover），要用 EPG1 的方波。第 4.6、4.7 步时板子自己的 DAC_A 接自己的 ADC_B；第 5 节时每块板的 DAC_A 接另一块板的 ADC_B。

## 4. 单块板的检查和标定

按顺序做：每一步都依赖前面的步骤。板级配置 `/etc/rf-clock/board.json` 收集属于这块板的一切（ABI_zh.md §11.1）；`PL.open()` 拒绝为另一块板写的配置，所以 SD 卡换到另一块板上会被拒绝，直到在那块板上做完第 4 节。配置只存在 SD 卡上：做完第 4 节后把它拷进仓库，存为 `boards/<S/N>/board.json` 并提交（[boards/README_zh.md](../boards/README_zh.md)）。

1. **时间基准（M1）。** `$run -m rf_clock.checks.m1_check 60`。通过标准：每个 PPS 周期都是 100 000 000 个 tick，抖动计数 0，结束时 `pps_locked`，M1 PASS。周期恒定为别的值，说明板子没有 10 MHz（99 992 670 个 tick、慢 73 ppm 的那次就是参考断开）；没有 PPS 沿，说明 J7 上没有信号。
2. **PPS 捕获余量。** 在 RF-clock-PC 上：`.venv/bin/python experiments/m1_pps_phase_scan.py --pair <地址>:<EPG1 输出>`（它步进那一路输出的延时）。通过标准：在使用的延时下，PPS 沿离 10 ns 采样沿超过约 1 ns。否则在 `experiments/epg1.py pps` 里给那一路设延时（887329260072A 的输出 2 是 6.6 ns）；新延时在 EPG1 下次重启 RUN 时生效，重启会打断它的所有输出。报告：`results/pps-phase-scan-20261018_203329`。
3. **VCO 档位。** `$run -m rf_clock.checks.vco_bands scan --write --label <S/N>`（不需要接线）。它写 20 次时钟，取每片 LMX2594 校准最常选的档位，找出能锁定的档位，连同本板序列号写进板级配置。通过标准：建议的两个档位都锁定。否则每次写时钟芯片都可能换档，让对应的转换器移动 8–10 ps（`results/lmx-sync-loopback-20261019` 第 5 节）。
4. **MTS 捕获窗口。** `$run -m rf_clock.checks.mts_dtc record --write`，然后 `$run -m rf_clock.checks.mts_dtc check`。它上电 6 次，取每个 tile 最常选中的 SYSREF 捕获窗口，存下它的码。通过标准：check 报告的最大距离不超过 6 个码。没有这些码时，两个窗口离码 64 差不多远的 tile 在上电之间可能整整移动一个采样（887329260101 的 DAC：104 ps；ABI_zh.md §7.1 第 7 步）。
5. **定时发射（M2）。** `$run -m rf_clock.checks.m2_tx_timing 10`。通过标准：M2 PASS，每个 TX_ACTUAL 都等于目标，两个 MTS 都 OK，latency 为 DAC 1896、ADC 1296。
6. **回环（M3）**，接上板子自己的回环线。`$run -m rf_clock.checks.m3_loopback 20`。通过标准：M3 PASS（PSL 至少 10 dB；20 dB 衰减加带通时约 21 dB），同一套线缆下延时和以前相同（887329260072A 上为 288.06 ns），标准差几 ps。
7. **档位表**，接着回环线。`$run -m rf_clock.checks.vco_bands delays --write`（约 20 分钟）。它测出配置档位两边各两档以内每个档位的回环延时，存进档位表：万一强制的档位锁不住了，换档的代价是多少。通过标准：每个档位都测到，3 次上电相差约 0.5 ps 以内。

配置没有设定档位或捕获窗口、选到的捕获码离记录超过 6 个码、或记码时的档位与现在不同时，`PL.open()` 会发出警告（Python `UserWarning`）。每条警告的意思都是：重做它点名的那一步。

## 5. 两块板（M4）

两块板都做完第 4 节、共用一台 FS725、A 的 DAC_A → B 的 ADC_B 且 B 的 DAC_A → A 的 ADC_B 之后，在 RF-clock-PC 上：

```bash
.venv/bin/python experiments/m4_twtt.py pair --seconds 600
```

它在 192.168.2.99 上启动 `rf_clock.twtt.node A`、在 192.168.2.101 上启动 `node B`（换板子时改脚本里的 `BOARDS`），把两边的日志收集到 `results/m4-twtt-pair-<时间>/`，并汇总 theta 和单程延时（ABI_zh.md §11.12）。通过标准：每一秒都配对成功且有效，theta 的标准差几 ps，两块板重新上电后 theta 不变。共用一台 FS725 时真实的时钟差为零，所以这个 theta 就是零基线常数：非互易项 Delta/2 加上两块板 PPS 路径的差别（某一路 EPG1 延时把 PPS 沿推进下一个 tick 时，会多出 10 ns）。Delta 存在哪里还没定（plan/RF_clock_modules_zh.md 第 8 节）。

## 6. 什么改动后要重做什么

| 改动 | 重做 |
| --- | --- |
| 新的 rf-clock wheel，A.B.C 不变 | 不用（装上即可，第 2 节） |
| 新的 bitstream（C 或 A.B 变了） | 第 4.4–4.7 步和第 5 节 |
| LMK04828 或 LMX2594 的寄存器文件 | 第 4.3–4.7 步和第 5 节 |
| 手工改了强制的 VCO 档位 | 第 4.4、4.7 步和第 5 节 |
| PPS 电缆或 EPG1 延时 | 第 4.1、4.2 步和第 5 节 |
| 射频电缆、衰减器或滤波器 | 第 4.6 步和第 5 节（改的是回环线则还有 4.7） |
| `PL.open()` 关于档位或捕获码的警告 | 它点名的那一步 |
| 重写了 SD 卡（新的 PYNQ 镜像） | 第 1、2 节，然后放回 `boards/<S/N>/board.json`（[boards/README_zh.md](../boards/README_zh.md)），或重做第 4 节 |

## 7. 出问题时

- **板子不响应**（时钟没在跑时访问 PL 会卡死 AXI 总线）：在 RF-clock-PC 上 `source /tools/Xilinx/Vivado/2024.1/settings64.sh; xsdb tools/jtag_reset_board.tcl <JTAG 序列号>`；约 40 s 后 ssh 恢复，PL 里是 PYNQ 的 base overlay（ABI_zh.md §9.1）。
- **`BoardConfigError`**：配置属于另一块板（序列号）、格式不对，或指定的文件不存在；在本板上重做第 4.3 步。
- **`BringupError: LMX2594 not locked`**：强制的档位锁不住，可能是温度变了；重做第 4.3、4.4、4.7 步。
- **`BringupError: PL pps_locked not set`**：J7 上没有 PPS，或没有 10 MHz（M1 能看出是哪个，第 4.1 步）。
- **M3 或 M4 相关峰太弱**（PSL 低于 10 dB，结果为 null）：检查电缆、衰减器或滤波器；M4 看是哪个方向弱，就是哪条线。
