"""Minimal use on the board: power up, send one burst after a PPS and capture
a window from the same tick.

Run on the RFSoC 4x2 as root:

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.03_pynq_bringup

PL.open() runs every power-up step of docs/ABI.md section 7.1 (overlay,
LMK/LMX, pl_resetn0, RFDC and MTS, PPS lock).  The object it returns holds one
block per address space: ctrl (P2 registers: every command and status),
tx_buffer (TX waveform memory), rx_dma (S2MM DMA), rfdc and clock_gpio.  All
addresses come from the HWH.
"""

from __future__ import annotations

from rf_clock.pl import PL, WaveformGenerator

PPS_PERIOD = 100_000_000  # ticks


def main() -> None:
    pl = PL.open(progress=print)                   # the overlay packaged with rf-clock
    print(f"PL ready: ABI 0x{pl.ctrl.info.version:08x}; {pl.rfdc.mts.dac.describe()}")

    beats = WaveformGenerator().generate_beats()   # TX_LAYOUT
    rx_beats = len(beats) + 1250
    pl.tx_buffer.write_beats(beats)                # on-chip TX buffer
    pl.rx_dma.prepare(rx_beats)                    # S2MM address and length, before the RX doorbell

    _, pps = pl.ctrl.wait_next_pps()               # just after a PPS
    target = pps + PPS_PERIOD + 100_000            # 1 ms after the next PPS
    pl.ctrl.arm_tx(target, len(beats))             # TX_TARGET, TX_LENGTH, doorbell
    pl.ctrl.arm_rx(target, rx_beats)               # RX_TARGET, RX_LENGTH, doorbell
    t_tx = pl.ctrl.wait_tx(timeout=2.5)            # TX_ACTUAL, checked == target
    window = pl.ctrl.wait_rx(timeout=2.5)          # RX_ACTUAL (checked), RX_BEATS, window flags

    x = pl.rx_dma.wait(window.beats).samples       # complex64, 800 MS/s, from window.actual
    print(f"burst at tick {t_tx}; RX window {window.beats} beats, peak |x| = {abs(x).max():.0f}")


if __name__ == "__main__":
    main()
