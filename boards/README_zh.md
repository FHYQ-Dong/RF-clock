# 各板的配置文件

英文原文（以其为准）：[README.md](README.md)。

每个目录以板子的 S/N 命名，存放这块板 `/etc/rf-clock/board.json` 的副本，即 `PL.open()` 读取的板级配置（docs/ABI_zh.md §11.1）。它由 docs/board_setup_zh.md 第 4 节的工具在板上写出（`rf_clock.checks.vco_bands`、`rf_clock.checks.mts_dtc`）；这里的副本用来参考格式，也是 SD 卡重写后的备份。它们不属于包。

| 板子 | 地址 | 强制的 VCO 档位（ADC、DAC） | MTS 捕获码（DAC T1；ADC T1；ADC PLL） |
| --- | --- | --- | --- |
| [887329260072A](887329260072A/board.json) | 192.168.2.99 | 118、122 | 62/61；62/65；72/76 |
| [887329260101](887329260101/board.json) | 192.168.2.101 | 125、128 | 54/53；59/62；68/73 |

887329260101 的档位表里还没有各档延时：要等它接上自己的回环线（docs/board_setup_zh.md 第 4.7 步）。

**放回副本**（SD 卡重写之后）：文件里的 `serial` 必须是这块板的，`PL.open()` 反正也会检查：

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -c "from rf_clock.pl.board_config import read_board_serial; print(read_board_serial())"
sudo install -D -m 644 board.json /etc/rf-clock/board.json
```

然后像任何改动之后一样检查这块板（docs/board_setup_zh.md 第 6 节）：`rf_clock.checks.mts_dtc check` 和 M2。

**更新副本**：docs/board_setup_zh.md 第 4 节里任何写配置的步骤做完后，把板上的文件拷到这里并提交，例如在 RF-clock-PC 上：`ssh xilinx@192.168.2.99 cat /etc/rf-clock/board.json > boards/887329260072A/board.json`。要改值就在板上改，不要改这里：用工具改，或手工选定档位后重做 docs/board_setup_zh.md 第 6 节为此列出的步骤；然后再拷过来。
