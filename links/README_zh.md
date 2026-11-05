# 链路标定

英文原文（以其为准）：[README.md](README.md)。

θ₀ 是节点 B 的修正目标：两块板共用一个参考、真实钟差为零时测到的 θ（plan 第 8 节的零基线标定）。它等于两个方向的非互易性 Δ/2，加上那个共用参考里两块板 PPS 路径之差。Δ 来自射频路径，即两块板之间的线缆、衰减器、滤波器，以及 RFDC 配置，所以 θ₀ 属于一对板加一套接线，不属于某一块板：它存在这里，不放进板级配置（`boards/`），也不放进包里。

每对板一个文件，`<A 的 S/N>_<B 的 S/N>.json`，`wirings` 下每套接线一条，`current` 指出台架上现在接的是哪套：

| 键 | 内容 |
| --- | --- |
| `rf` | 每个方向实际接的射频路径 |
| `theta0_ns` | θ₀，单位 ns：下面各次运行 θ 的均值 |
| `measured` | 测量日期（`date`）、所用的共用参考（`reference`）、rf-clock 版本、各次运行及其 θ（`runs`）、它们的离散度（`spread_ps`，各次上电之间的 sd），以及报告 |

`experiments/m4c_v2_link.py` 按 `current` 那套接线的 θ₀ 修正（`--wiring NAME` 选别的接线，`--target-ns` 直接给值），并把用到的那条记进 `settings.json`；手动运行 `rf_clock.checks.m4c_link --steer-target-ns` 时从这里取值。

**换了接线。** 软件看不出接的是哪些线，所以改了射频路径（线缆、衰减器、滤波器、天线）或 RFDC 配置的人，要新加一条并把它设为 `current`，旧的保留。先测 θ₀：两块板共用一个参考（docs/board_setup_zh.md 第 5.1 节，`experiments/m4c_v2_link.py --seconds 60`，多次上电），从每次的 `summary.json` 取 θ，均值记为 `theta0_ns`。

| 板对 | 现在的接线 | θ₀ |
| --- | --- | --- |
| [887329260072A（A）、8873292600AB（B）](887329260072A_8873292600AB.json) | `cabled-20261103` | −21.5 ps（4 次上电，sd 1.95 ps） |
