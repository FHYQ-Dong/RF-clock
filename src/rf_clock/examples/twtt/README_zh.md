# 双向时间传递示例

`rf_clock.twtt`（docs/ABI_zh.md §11.12）的带注释示例，在开发机上运行，不需要硬件：

```text
python -m rf_clock.examples.twtt.01_fake_two_way
python -m rf_clock.examples.twtt.02_fake_steer
```

`01_fake_two_way` 从一个节点的测量队列求出 θ 和单向延时：每个窗口里本节点的测量，加上那个 burst 里对端的测量包，构成一对（`rf_clock.twtt.pairs`）。`02_fake_steer` 用预测控制律修正一台模拟的 FS725，锁频环参数取自板级配置的 `fs725` 项，格式和 `rf_clock.checks.fs725_loop` 写入的一样。在板上，`../pl/04_link_node.py` 从 PL 取队列，`rf_clock.checks.m4c_link` 运行完整的节点，包括修正。

英文原文（以其为准）：[README.md](README.md)。
