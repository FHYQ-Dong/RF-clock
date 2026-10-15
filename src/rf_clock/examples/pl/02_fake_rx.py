"""One RX window on a development machine: rx_dma sets up the S2MM, then ctrl
rings the RX doorbell, waits and checks the window.

    python -m rf_clock.examples.pl.02_fake_rx
"""

from __future__ import annotations

from rf_clock.pl import abi
from rf_clock.pl.client import PLClient
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.rxdma import RXDMA


class Buffer:
    def __init__(self, nbytes: int):
        self.nbytes = nbytes
        self.invalidated = False

    def invalidate(self) -> None:
        # A PYNQ allocate() buffer is invalidated before the CPU reads it.
        self.invalidated = True


class FakeDMA:
    def __init__(self, regs: MemoryMMIO):
        self.regs = regs
        self.expected = 0
        self.received = 0

    def prepare(self, buffer: Buffer, nbytes: int) -> None:
        self.expected = nbytes
        # The real adapter calls dma.recvchannel.transfer(buffer) here.

    def wait(self, timeout=None) -> None:
        self.received = self.expected

    def abort(self) -> None:
        pass

    @property
    def received_bytes(self) -> int:
        return self.received


def status_word(rx: abi.RxState = abi.RxState.IDLE) -> int:
    return (
        (1 << abi.ABI.STAT.TIME_VALID)
        | (1 << abi.ABI.STAT.CLOCKS_LOCKED)
        | (1 << abi.ABI.STAT.PPS_LOCKED)
        | (1 << abi.ABI.STAT.DMA_READY)
        | (int(rx) << abi.ABI.STAT.RX_STATE_LSB)
    )


def main() -> None:
    clock = MemoryMMIO(initial={0: 1})
    regs: MemoryMMIO

    def on_write(offset: int, value: int) -> None:
        if offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.RX_DOORBELL:
            # Read right after the doorbell: ARMED; DONE only once the DMA finished.
            regs.values[abi.ABI.REG.RX_RESULT] = int(abi.ResultCode.ARMED)
            regs.values[abi.ABI.REG.STATUS] = status_word(abi.RxState.ARMED)

    regs = MemoryMMIO(
        initial={
            abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
            abi.ABI.REG.VERSION: abi.ABI.VERSION,
            abi.ABI.REG.STATUS: status_word(),
            abi.ABI.REG.TIME_EPOCH: 1,
            abi.ABI.REG.TX_DEPTH: 8192,
            abi.ABI.REG.RX_MAX_BEATS: 2097151,
            abi.ABI.REG.MIN_LEAD_TICKS: 64,
            abi.ABI.REG.RX_BEATS: 4,
            abi.ABI.REG.RX_ACTUAL_LO: 2000,
            abi.ABI.REG.RX_ACTUAL_HI: 0,
        },
        on_write=on_write,
    )
    client = PLClient(regs, clock)
    client.validate()

    rx_dma = RXDMA(FakeDMA(regs))
    buffer = Buffer(4 * 32)
    rx_dma.prepare(4, buffer=buffer)  # S2MM first, or the PL rejects the doorbell (0x14)
    client.arm_rx(2000, beats=4)
    # The real PL reports RX DONE once the DMA has taken the last beat (TLAST).
    regs.values[abi.ABI.REG.RX_RESULT] = int(abi.ResultCode.DONE)
    regs.values[abi.ABI.REG.STATUS] = status_word(abi.RxState.DONE)
    window = client.wait_rx(timeout=0.0, poll_interval=0.0)  # result, flags, RX_ACTUAL == target
    data = rx_dma.wait(window.beats)                         # bytes == RX_BEATS * 32, invalidate
    print(
        f"RX valid: target={window.target}, actual={window.actual}, "
        f"bytes={data.received_bytes}, invalidated={buffer.invalidated}"
    )


if __name__ == "__main__":
    main()
