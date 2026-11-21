"""The FS725's serial line in the PL, P12 (docs/ABI.md 4.15), since 2.5.

The PL drives the FS725's RS-232 line itself, through a TTL-to-RS-232 module on PMOD1
(MAX3232, DB-9 male, straight into the FS725's port), so that P13 can send each step's FC
command at its tick without the ARM or USB in the way.  The ARM shares the line: it fills a
line of up to 32 bytes and asks for it to be sent, and P12 sends it in a gap where it ends
before P13's next command.  Bytes the FS725 sends back wait in a FIFO of 256 the ARM reads.

``PLPort`` puts this behind the ``rf_clock.fs725.Port`` protocol, so that every ``FS725``
method works through the PL: ``FS725(PLPort(pl.serial))``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import abi
from .client import PLClient

REG = abi.ABI.REG
LINE_BYTES = 32
BAUD_9600_TICKS = 10417  # 100 MHz / 9600 baud, 0.003 % fast


@dataclass(frozen=True)
class SerialStatus:
    line_queued: bool  # a line waits or is being sent
    tx_active: bool
    cmd_pending: bool  # a P13 command waits for its tick
    line_len: int
    rxd_a: bool  # the level on receive pin A (PMOD1_7)
    rxd_b: bool  # on receive pin B (PMOD1_3)
    rx_level: int  # bytes waiting
    lines: int  # the ARM's lines sent
    commands: int  # P13's commands sent
    received: int  # bytes received
    framing_errors: int
    overruns: int
    rxd_a_edges: int  # falling edges on pin A, mod 256: the pin the module is on counts
    rxd_b_edges: int


class SerialLine:
    """``pl.serial``: the line to the FS725."""

    def __init__(self, client: PLClient) -> None:
        self.client = client

    def configure(self, *, enable: bool = True, bit_ticks: int = BAUD_9600_TICKS) -> None:
        if not abi.ABI.SERIAL_BIT_TICKS_MIN <= int(bit_ticks) <= 0xFFFF:
            raise ValueError(f"bit_ticks must be {abi.ABI.SERIAL_BIT_TICKS_MIN} .. 65535")
        self.client.write32(REG.SERIAL_CONFIG, int(bit_ticks) | (int(bool(enable)) << abi.ABI.SERCFG.ENABLE))

    @property
    def enabled(self) -> bool:
        return bool(self.client.read32(REG.SERIAL_CONFIG) >> abi.ABI.SERCFG.ENABLE & 1)

    def status(self) -> SerialStatus:
        s = self.client.read32(REG.SERIAL_STATUS)
        err = self.client.read32(REG.SERIAL_ERR_CNT)
        edges = self.client.read32(REG.SERIAL_RX_EDGES)
        b = abi.ABI.SERST
        return SerialStatus(
            line_queued=bool(s >> b.LINE_QUEUED & 1), tx_active=bool(s >> b.TX_ACTIVE & 1),
            cmd_pending=bool(s >> b.CMD_PENDING & 1), line_len=(s >> b.LINE_LEN_LSB) & 0x3F,
            rxd_a=bool(s >> b.RXD_A & 1), rxd_b=bool(s >> b.RXD_B & 1), rx_level=(s >> b.RX_LEVEL_LSB) & 0x1FF,
            lines=self.client.read32(REG.SERIAL_LINES_CNT), commands=self.client.read32(REG.SERIAL_CMDS_CNT),
            received=self.client.read32(REG.SERIAL_RX_CNT), framing_errors=err & 0xFFFF, overruns=err >> 16,
            rxd_a_edges=edges & 0xFF, rxd_b_edges=(edges >> 8) & 0xFF)

    def busy(self) -> bool:
        return bool(self.client.read32(REG.SERIAL_STATUS) >> abi.ABI.SERST.LINE_QUEUED & 1)

    def send(self, data: bytes, timeout: float = 2.0) -> None:
        """Send ``data`` as lines of at most 32 bytes, each after the one before has left."""
        data = bytes(data)
        for start in range(0, len(data), LINE_BYTES):
            deadline = time.monotonic() + timeout
            while self.busy():
                if time.monotonic() > deadline:
                    raise TimeoutError("the serial line did not take the line before it")
                time.sleep(0.001)
            for byte in data[start:start + LINE_BYTES]:
                self.client.write32(REG.SERIAL_LINE, byte)
            self.client.write32(REG.SERIAL_CONTROL, 1 << abi.ABI.SERCTL.SEND)

    def receive(self) -> bytes:
        """The bytes waiting in the FIFO (reading takes each one out)."""
        out = bytearray()
        while True:
            v = self.client.read32(REG.SERIAL_RX)
            if not v >> abi.ABI.SERRX.VALID & 1:
                return bytes(out)
            out.append(v & 0xFF)

    def flush(self) -> None:
        self.client.write32(REG.SERIAL_CONTROL, 1 << abi.ABI.SERCTL.RX_FLUSH)

    def last_line_tick(self) -> int:
        lo = self.client.read32(REG.SERIAL_LINE_TICK_LO)
        return (self.client.read32(REG.SERIAL_LINE_TICK_HI) << 32) | lo


class PLPort:
    """``rf_clock.fs725.Port`` over P12: ``FS725(PLPort(pl.serial))``."""

    def __init__(self, line: SerialLine) -> None:
        self.line = line
        self.rest = b""
        if not line.enabled:
            line.configure(enable=True)
        line.flush()

    def write(self, data: bytes) -> None:
        self.line.send(data)

    def read_line(self, timeout: float) -> bytes | None:
        """Up to the next carriage return, without it; None if none came in ``timeout``."""
        deadline = time.monotonic() + timeout
        while True:
            self.rest += self.line.receive()
            if b"\r" in self.rest:
                break
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.002)
        line, self.rest = self.rest.split(b"\r", 1)
        return line.strip(b"\n")

    def flush_input(self) -> None:
        self.line.flush()
        self.rest = b""

    def close(self) -> None:
        pass
