# 从 rf-clock 1.x 升级到 2.x

写给用过 1.x 包（最后一版 1.3.0.12）、要换到 2.x（目前 2.2.0.9）的人。细节见 [docs/ABI_zh.md](ABI_zh.md) 和 [docs/board_setup_zh.md](board_setup_zh.md)。下面链接的报告在 `results/` 里，它不在 Git 中；实验室 NAS 上有一份：`smb://kinabalu2.stdusr.yale.internal/kinabalu/Haoyu/RF-clock/results`（Windows 下为 `\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results`）。

## 改了什么

| | 1.x | 2.x |
| --- | --- | --- |
| 测量 | 每秒一次：PL 把一个 RX 窗口采进 DDR，ARM 做相关 | 每个 2 ms 时隙一次（每秒 500 个，A、B 轮流）：PL 在每个窗口里跟踪对方的 burst（峰值、亚采样位置、载波相位） |
| 两节点之间的交换 | 走以太网 TCP，按系统时间配对记录，所以两块板都要 NTP | 在 burst 里：每个 burst 带着本节点上一次的测量（测量包），按 `PPS_COUNT`（第几秒：每个 PL 自己数 PPS 边沿）和时隙（这一秒里的第几个 2 ms，0-499）配对；不需要网络，不需要 NTP |
| 两个时钟时的启动 | 不支持（M4 两块板共用一台 FS725） | 初始引导（`--acquire`）：时隙开始之前，B 采一个 11 ms 的窗口进 DDR，在里面找到 A 的 burst 并解出它们的测量包，包里带着 A 的发送时刻；由此知道 A 的 PPS 在 B 的时间轴上的位置（误差是假设的单向延时的误差），B 再用 FS725 的 PP 把自己的 1PPS 按整 tick 移过去，这样两个节点的 burst 都落在对方的 RX 窗口里 |
| 修正 B 的 FS725 | `rf_clock.twtt.steer` 在节点旁边运行，Kalman 控制律，每 10 s 修正一次 | 在链路检查（`m4c_link`）里面运行，两种控制律都可以用。Kalman 控制律：Kalman 滤波器估计 θ 和两台钟的频率差，SF 设成让 θ 的误差以时间常数 τ 衰减。预测控制律：用 FS725 锁频环的模型从 θ 里去掉自己的修正，根据最近的历史预测自由运行时钟的频率，再用最小二乘规划接下来的 SF 改动；每秒最多修正五次 |
| 节点输出的 PPS | FS725 自己的 1PPS | 输出 PPS：PL 按自己的 tick 在 DAC_B（J20）上每秒出一个脉冲，外加一根有效线（Pmod J1 第 1 脚，高 = 无效；LED PL_LED0），链路锁定且修正到位后变低 |
| FS725 驱动 | 状态、SF、PP | 另有其环路的 FC、增益 GA、快速写 SF，以及按板级配置核对设备 |

1.x 节点和 2.x 节点不能互相测量：两块板都要装 2.x。1.x 的寄存器地址不变，但时隙引擎运行时 PL 会拒绝 1.x 的 TX/RX 命令。1.x 每秒一次的交换（`rf_clock.twtt.node`、`link`）已弃用。板级配置（`/etc/rf-clock/board.json`）保留 1.3 写进去的内容，在 1.3 下配置好的板子沿用原配置；新增一项 `fs725`：给这块板提供时钟的 FS725（见下面第 3 步）。

## 怎么用

1. 两块板都装 2.x 的 wheel，方法和以前一样（[docs/ABI_zh.md §9.1](ABI_zh.md)）：`sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index rf_clock-2.2.0.9-py3-none-any.whl`。
2. 接线：每块板接自己的 FS725（10 MHz 和 1PPS）；每块板的 DAC_A 经带通滤波器和衰减器接到对方的 ADC_B；每台 FS725 的 RS-232 口接它所在板的 USB。输出 PPS 在 DAC_B 上。
3. 把每块板的 FS725 记进它的板级配置，每台设备做一次：`$run -m rf_clock.checks.fs725_loop --port /dev/serial/by-id/... --gain GA --write`（`run` 同 README），B 用 GA 8，A 用 7；端口也会记下，之后运行不用再给。它把 SF 阶跃 200 再回来，共四次，只用 FC 拟合出设备的锁频环，约 3 分钟；不需要另一块板，也不需要链路。此后每次 `m4c_link --fs725` 启动时都会核对设备是不是记录的那台，并设定它的 GA，改了的话发 warning 说明从多少改成多少；配置里没有的设备会让节点停下，直到把它记进去。
4. 运行链路，可以在 RF-clock-PC 上（两块板空闲时）：

   ```bash
   .venv/bin/python experiments/m4c_v2_link.py --scenario steer --seconds 3600 "--node-args=--pps-out 20" \
       "--a-args=--pps-trig --fs725" "--b-args=--steer-mode predictive --steer-step 0.2 --steer-late-slots 50"
   ```

   也可以在每块板上以 root 手动运行，两边用相同的 `--start-at` 和 `--stop-at`（Unix 时间），θ₀ 取 `links/` 里这套接线的值（台架的是 −0.0215 ns）：

   ```bash
   $run -m rf_clock.checks.m4c_link --role A --start-at T0 --stop-at T1 --no-entries --pps-out 20 --pps-trig --fs725
   $run -m rf_clock.checks.m4c_link --role B --start-at T0 --stop-at T1 --no-entries --pps-out 20 \
       --acquire --fs725 --steer-target-ns -0.0215 --steer-mode predictive --steer-step 0.2 --steer-late-slots 50
   ```

5. 结果看每个节点的日志：每秒一行 `second`（θ、单向延时、它们的离散度、修正），0.2 s 步长另有 `step` 行；`rf_clock.twtt.logs` 可以读回日志。

要在自己的代码里用这个库，可以看示例：`rf_clock.examples.pl.04_link_node` 是板上链路的一个节点，`rf_clock.examples.twtt` 在开发机上演示双向方程和预测控制律（`rf_clock.twtt.pairs`、`control`、`steer`、`logs`；[docs/ABI_zh.md §11.12](ABI_zh.md)）。

有三个设置属于具体的搭建，不属于包：

- θ₀（`--steer-target-ns`）：这套接线下两块板共用一个参考时的 θ（线缆、滤波器和板子的非互易性，加上那个参考里两路 PPS 之差）。它按接线存在 `links/<A 的 S/N>_<B 的 S/N>.json`，`m4c_v2_link.py` 自动读取（[links/README_zh.md](../links/README_zh.md)）；换了线缆、衰减器或滤波器，要在两块板共用参考的条件下重测，新加一条。
- `--arrival-ticks`：预期的单向延时，单位 10 ns tick（默认 29，台架的线缆）。换链路时要改。
- A 上的 `--pps-trig`：887329260072A 这块板用 PPS 的施密特触发器副本计时，因为它的比较器副本的边沿正好落在 tick 的采样沿上，会在相邻 tick 之间跳。新板子用 `rf_clock.checks.pps_pp_scan` 检查。

## 指标提升了多少

在台架上测得（887329260072A 作 A，8873292600AB 作 B，线缆加衰减器连接，除注明外各接自己的 FS725）。

| 量 | 1.x | 2.x |
| --- | --- | --- |
| θ 测量次数 | 每秒 1 次 | 每秒约 250 次 |
| θ 噪声，两块板共用一个参考 | 每秒 sd 2.95 ps | 每对 0.81-0.87 ps，每秒平均远低于 0.1 ps |
| 一次测量送到对方节点的时间 | 约 1 s，走以太网 | 不到 2 ms，在下一个 burst 里 |
| 修正误差（B 被修正时的 θ − θ₀，rms） | 66 ps（1.x 的 Kalman 控制律：τ 50 s，每 10 s 修正一次；在 2.x 链路上运行） | 27-29 ps（预测控制律，1 s 步长）；约 15 ps（0.2 s 步长，2.2.0.7：两次 20 分钟的运行分别为 15.3 和 15.7 ps；更长的运行还要做） |
| 节点 PPS 相对自己的 10 MHz，秒与秒之间 | FS725 1PPS：43.9 ps rms | 输出 PPS：7.7 ps rms（示波器的分辨率） |
| PP 命令移动 PPS | 1PPS 的移动量偏差 40-260 ps | 输出 PPS 正好移动整 tick，偏差 3.1 ps rms |
| 重新启动并重新引导后两板 PPS 的常数 | 1PPS：变了 145 ps | 输出 PPS：0.9 ps |

## 怎么提升的

- **测量噪声和测量率。** PL 用发出去时同样的码片、经同样的 RRC 滤波器做成参考，以整数运算对每个窗口做相关；2 ms 的乒乓每秒给出约 250 对双向测量，而不是一对（[results/v2-link-20261103/REPORT_zh.md](../results/v2-link-20261103/REPORT_zh.md)）。θ 的噪声现在远低于时钟自身的游走，所以修正受限于时钟，而不是测量。
- **修正，一步一步：**
  1. 更快的 Kalman 控制律（τ 5 s，每 2 s 修正一次）能跟上更快的游走：66 → 约 40 ps。它需要在模型里加入 FS725 的延迟：SF 命令要 1.1-1.6 s 后才作用到频率上。
  2. 这个延迟来自 FS725 的锁频环（FLL）：一个积分器对铷原子的误差积分，经 1 s 的滤波器去调晶振。GA 8 把积分器增益从每秒 0.95 提到 2.2，延迟减半：33-35 ps（[results/fs725-fll-20261105/REPORT_zh.md](../results/fs725-fll-20261105/REPORT_zh.md)，[docs/fs725_principles_zh.md](fs725_principles_zh.md)）。
  3. 预测控制律对这个环路建模，从 θ 里去掉自己的修正，根据最近的历史预测两台时钟的频率，用最小二乘规划接下来的 SF 改动；θ 取每秒最后几个时隙，离下一条命令最近：27-29 ps（[results/predictive-steering-20261105/REPORT_zh.md](../results/predictive-steering-20261105/REPORT_zh.md)）。
  4. 同样的控制律用 0.2 s 步长，每秒修正五次：约 15 ps（[results/two-loops-20261105/REPORT_zh.md](../results/two-loops-20261105/REPORT_zh.md)）。剩下的大部分来自 A 的 FS725，B 只能隔着自己环路的延迟去跟。
- **PPS。** FS725 的 1PPS 电路秒与秒之间离散 22-44 ps，PP 移动也不精确；输出 PPS 是固定 tick 上的一个 DAC 采样，所以继承了 10 MHz 的稳定度，只随 tick 移动（[results/pps-out-20261104/REPORT_zh.md](../results/pps-out-20261104/REPORT_zh.md)）。
