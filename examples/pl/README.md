# ARM ↔ PL 示例

这些示例对应 `plan/handoff_software_interface.md` 和 `docs/ABI.md`。它们只演示
软件接口的顺序；开发机示例使用内存中的假 PL，不会访问真实硬件。

```text
uv run python examples/pl/01_fake_tx.py
uv run python examples/pl/02_fake_rx.py
```

板上示例需要 PYNQ、`xrfclk`、RFDC 驱动和实际的 `.bit/.hwh`，因此默认不会执行：

```text
python examples/pl/03_pynq_bringup.py /home/xilinx/rf_clock.bit
```

板上的 Python 版本以 `python --version` 为准。当前开发机 `pyproject.toml` 的
`requires-python >=3.14` 不代表 PYNQ 镜像也提供 3.14；驱动核心只用标准库，板上
可以用系统 Python 或另建兼容的 venv。
