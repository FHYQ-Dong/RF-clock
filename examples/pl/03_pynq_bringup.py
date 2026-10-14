"""板上的最小用法：上电，按 PPS 发一个突发，再同拍收一个窗口。

在板上 ~/RF-clock 下以 root 运行：

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python examples/pl/03_pynq_bringup.py

PL.open() 按 docs/ABI.md §7.1 完成全部上电步骤（overlay、LMK/LMX、pl_resetn0、
RFDC 和 MTS、PPS 锁定），返回的对象按地址空间持有各个硬件部件：ctrl（P2 寄存器，
所有命令和状态）、tx_buffer（TX 波形 BRAM）、rx_dma（S2MM DMA）、rfdc、clock_gpio。
地址都从 HWH 取得，不写死。
"""

from __future__ import annotations

from rf_clock.pl import PL, WaveformGenerator

PPS_PERIOD = 100_000_000  # ticks


def main() -> None:
    pl = PL.open(progress=print)                   # 默认用随 rf-clock 打包的 overlay
    print(f"PL ready: ABI 0x{pl.ctrl.info.version:08x}; {pl.rfdc.mts.dac.describe()}")

    beats = WaveformGenerator().generate_beats()   # TX_LAYOUT
    rx_beats = len(beats) + 1250
    pl.tx_buffer.write_beats(beats)                # TX buffer（片上 BRAM）
    pl.rx_dma.prepare(rx_beats)                    # S2MM：buffer 地址和长度，先于 RX doorbell

    _, pps = pl.ctrl.wait_next_pps()               # 刚过一个 PPS
    target = pps + PPS_PERIOD + 100_000            # 下一个 PPS 之后 1 ms
    pl.ctrl.arm_tx(target, len(beats))             # TX_TARGET、TX_LENGTH、doorbell
    pl.ctrl.arm_rx(target, rx_beats)               # RX_TARGET、RX_LENGTH、doorbell
    t_tx = pl.ctrl.wait_tx(timeout=2.5)            # TX_ACTUAL（已核对 == target）
    window = pl.ctrl.wait_rx(timeout=2.5)          # RX_ACTUAL（已核对）、RX_BEATS、窗口标志

    x = pl.rx_dma.wait(window.beats).samples       # complex64，800 MS/s，从 window.actual 开始
    print(f"burst at tick {t_tx}; RX window {window.beats} beats, peak |x| = {abs(x).max():.0f}")


if __name__ == "__main__":
    main()
