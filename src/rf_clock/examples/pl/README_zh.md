# ARM <-> PL 示例

`docs/ABI.md`（中文版 `docs/ABI_zh.md`）所述软件接口的带注释示例。开发机示例使用内存中的假 PL，不访问硬件：

```text
python -m rf_clock.examples.pl.01_fake_tx
python -m rf_clock.examples.pl.02_fake_rx
```

板上示例需要 RFSoC 4x2（PYNQ）、接好 FS725 的时钟和 PPS，并以 root 运行：

```text
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.03_pynq_bringup
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.04_link_node A
```

`03_pynq_bringup` 在一个 PPS 之后发一个 burst，并从同一个 tick 起采一个窗口（rf-clock 1.x 的 TX、RX 命令）。`04_link_node` 是 rf-clock 2 链路的一个节点，一块板上以 `A` 运行、另一块以 `B` 运行：由码片生成 burst、跟踪器、测量队列，以及每秒的 θ；完整的节点是 `rf_clock.checks.m4c_link`。两个示例都用随 rf-clock 打包的 overlay。开发机上的双向时间传递和修正示例在 `../twtt/`；不用 `rf_clock.pl` 的底层访问（PYNQ 原生和 C++）在 `../native/` 和 `docs/ABI.md` 附录 A；板上检查在 `rf_clock.checks`。
