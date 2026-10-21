# ARM <-> PL 示例

`docs/ABI.md`（中文版 `docs/ABI_zh.md`）所述软件接口的带注释示例。开发机示例使用内存中的假 PL，不访问硬件：

```text
python -m rf_clock.examples.pl.01_fake_tx
python -m rf_clock.examples.pl.02_fake_rx
```

板上示例需要 RFSoC 4x2（PYNQ）、接好 FS725 的时钟和 PPS，并以 root 运行：

```text
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.03_pynq_bringup
```

它使用随 rf-clock 打包的 overlay。不用 `rf_clock.pl` 的底层访问（PYNQ 原生和 C++）在 `../native/` 和 `docs/ABI.md` 附录 A；M1–M3 的板上检查在 `rf_clock.checks`。
