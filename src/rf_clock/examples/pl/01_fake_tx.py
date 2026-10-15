"""One TX waveform load and timed command, on a development machine.

    python -m rf_clock.examples.pl.01_fake_tx

No PYNQ needed.  FakePL models only the register changes of the ABI that the
software sequence depends on; on the board, PL.open() provides PynqMMIO
registers and the on-chip TX buffer instead.
"""

from __future__ import annotations

from rf_clock.pl import abi
from rf_clock.pl.client import PLClient
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.txbuf import TXBuffer


def status_word(tx: abi.TxState = abi.TxState.IDLE) -> int:
    """A fake STATUS word with time_valid and pps_locked set."""
    return (
        (1 << abi.ABI.STAT.TIME_VALID)
        | (1 << abi.ABI.STAT.CLOCKS_LOCKED)
        | (1 << abi.ABI.STAT.PPS_LOCKED)
        | (int(tx) << abi.ABI.STAT.TX_STATE_LSB)
    )


class FakePL:
    """Models the TX doorbell and completion only, not the RFDC data path."""

    def __init__(self) -> None:
        self.clock = MemoryMMIO(initial={0: 1})  # GPIO bit0: fabric MMCM locked
        self.regs = MemoryMMIO(
            initial={
                abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
                abi.ABI.REG.VERSION: abi.ABI.VERSION,
                abi.ABI.REG.STATUS: status_word(),
                abi.ABI.REG.TIME_EPOCH: 1,
                abi.ABI.REG.TX_DEPTH: 8192,
                abi.ABI.REG.RX_MAX_BEATS: 2097151,
                abi.ABI.REG.MIN_LEAD_TICKS: 64,
            },
            on_write=self._write,
        )
        self.tx_target = 0

    def _write(self, offset: int, value: int) -> None:
        if offset == abi.ABI.REG.TX_TARGET_LO:
            self.tx_target = (self.tx_target & ~0xFFFF_FFFF) | value
        elif offset == abi.ABI.REG.TX_TARGET_HI:
            self.tx_target = (self.tx_target & 0xFFFF_FFFF) | (value << 32)
        elif offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.TX_DOORBELL:
            # The PL answers ARMED right after the doorbell, never DONE at once.
            self.regs.values[abi.ABI.REG.TX_RESULT] = int(abi.ResultCode.ARMED)
            self.regs.values[abi.ABI.REG.STATUS] = status_word(abi.TxState.ARMED)

    def complete_tx(self) -> None:
        """The DONE state after the DAC has taken beat 0."""
        self.regs.values[abi.ABI.REG.TX_RESULT] = int(abi.ResultCode.DONE)
        self.regs.values[abi.ABI.REG.TX_ACTUAL_LO] = self.tx_target & 0xFFFF_FFFF
        self.regs.values[abi.ABI.REG.TX_ACTUAL_HI] = self.tx_target >> 32
        self.regs.values[abi.ABI.REG.STATUS] = status_word(abi.TxState.DONE)


def main() -> None:
    pl = FakePL()
    client = PLClient(pl.regs, pl.clock)
    info = client.validate()  # ID/VERSION first, then the other registers

    # Write the TX buffer only in IDLE/DONE; a beat is 32 bytes, written as
    # little-endian 32-bit words.
    tx_buffer = TXBuffer(MemoryMMIO(), client)
    tx_buffer.write_beat(0, bytes(range(32)))

    # The target must be a future tick; the PL checks it against MIN_LEAD_TICKS.
    target = 100_000_100
    client.arm_tx(target, beats=1)  # shadow → doorbell → RESULT == ARMED
    pl.complete_tx()
    client.wait_tx(timeout=0.0, poll_interval=0.0)
    if client.tx_actual() != target:
        raise RuntimeError("TX_ACTUAL does not match the requested target")
    print(f"ABI 0x{info.version:08x}: TX completed at tick {client.tx_actual()}")


if __name__ == "__main__":
    main()
