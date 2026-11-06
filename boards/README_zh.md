# 各板的配置文件

英文原文（以其为准）：[README.md](README.md)。

每个目录以板子的 S/N 命名，存放这块板 `/etc/rf-clock/board.json` 的副本，即 `PL.open()` 读取的板级配置（docs/ABI_zh.md §11.1）。它由 docs/board_setup_zh.md 第 4 节的工具在板上写出（`rf_clock.checks.vco_bands`、`rf_clock.checks.mts_dtc`），第 5.1 节再记入这块板的 FS725（`rf_clock.checks.fs725_loop`）；这里的副本用来参考格式，也是 SD 卡重写后的备份。它们不属于包。

| 板子 | 地址 | 强制的 VCO 档位（ADC、DAC） | MTS 捕获码（DAC T1；ADC T1；ADC PLL） | FS725（S/N、GA、已测环路） |
| --- | --- | --- | --- | --- |
| [887329260072A](887329260072A/board.json) | 192.168.2.99 | 118、122 | 62/61；62/65；71/76 | 41175、7、GA 7 |
| [8873292600AB](8873292600AB/board.json) | 192.168.2.100 | 124、126 | 58/57；52/56；62/66 | 107129、8、GA 7、8 和 9 |
| [887329260101](887329260101/board.json) | 192.168.2.101 | 125、128 | 55/54；59/62；68/74 | 无 |

887329260072A 是台架上的节点 A，8873292600AB 是节点 B（2026-11-03 起）。8873292600AB 和 887329260101 的档位表里还没有各档延时：要等它们接上各自的回环线（docs/board_setup_zh.md 第 4.7 步）。

**FS725 本身的设置**（[887329260072A/fs725-41175-20261106.json](887329260072A/fs725-41175-20261106.json)、[8873292600AB/fs725-107129-20261106.json](8873292600AB/fs725-107129-20261106.json)）：每块板的 FS725 里那台 PRS10 能回答的所有查询（`docs/PRS10m.pdf` 第 6 节 "Abridged Command List"：型号和序列号、锁频环、磁场、1PPS 设置、DAC SD0-SD7、模拟通道 AD0-AD19），每项的运行值（`XX?`）与存在这台的 EEPROM 里的值（`XX!?`）并列，2026-11-06 只用查询命令读出，以备这两台以后另作他用。rf-clock 不发送任何写 EEPROM 的命令（不带 `!`），所以断电重启后每台都回到它的 `!?` 值。当天运行值与 EEPROM 值不同的只有链路设置的那几项，都在 B 的那台上：GA 8（EEPROM 为 7），1 pps 锁相关闭（EEPROM 为开），1 pps 延时 DAC SD1 为 68（EEPROM 为 128，由 `--acquire` 的 PP 移动）；FC，即晶振的调谐量，属于环路，本来就在游动。两台都不回答 `RS?`。要重新读取，用 `rf_clock.fs725.FS725.query` 发送同样的查询。

**放回副本**（SD 卡重写之后）：文件里的 `serial` 必须是这块板的，`PL.open()` 反正也会检查：

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -c "from rf_clock.pl.board_config import read_board_serial; print(read_board_serial())"
sudo install -D -m 644 board.json /etc/rf-clock/board.json
```

然后像任何改动之后一样检查这块板（docs/board_setup_zh.md 第 6 节）：`rf_clock.checks.mts_dtc check` 和 M2。

**更新副本**：docs/board_setup_zh.md 第 4 节里任何写配置的步骤做完后，把板上的文件拷到这里并提交，例如在 RF-clock-PC 上：`ssh xilinx@192.168.2.99 cat /etc/rf-clock/board.json > boards/887329260072A/board.json`。要改值就在板上改，不要改这里：用工具改，或手工选定档位后重做 docs/board_setup_zh.md 第 6 节为此列出的步骤；然后再拷过来。
