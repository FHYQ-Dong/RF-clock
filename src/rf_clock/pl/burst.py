"""The burst memory, P3 (docs/ABI.md 4.9): the PRN, the packet code and the RRC taps.

The PL makes every burst, and the correlator's and the packet decoder's
references, from these memories through the same FIR (``rf_clock.plmodel.fir``).
They can only be written while the slot engine is off.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from . import abi
from .client import PLClient, PLClientError
from .mmio import RegisterIO
from ..plmodel import fir
from ..plmodel import packet as p9

INT16_MAX = 32767


class BurstMemoryError(PLClientError):
    pass


def pack_chips(chips: Sequence[int] | np.ndarray) -> np.ndarray:
    """Chips (+1 / -1) as burst memory words: chip n is bit n % 32 of word n / 32, 1 for +1."""
    chips = np.asarray(chips)
    if chips.ndim != 1 or not np.isin(chips, (-1, 1)).all():
        raise ValueError("chips must be a sequence of +1 and -1")
    bits = np.zeros(-(-chips.size // 32) * 32, dtype=np.uint8)
    bits[:chips.size] = chips > 0
    packed = np.asarray(np.packbits(bits.reshape(-1, 32)[:, ::-1], axis=1))
    return packed.view(">u4").ravel().astype(np.uint32)


def unpack_chips(words: Sequence[int] | np.ndarray, count: int) -> np.ndarray:
    bytes_ = np.asarray(words, dtype=">u4").view(np.uint8).reshape(-1, 4)
    bits = np.asarray(np.unpackbits(bytes_, axis=1))[:, ::-1]
    return np.where(bits.ravel()[:count] > 0, 1, -1).astype(np.int64)


class BurstMemory:
    """``pl.burst``: load the chip memories and the taps."""

    def __init__(self, memory: RegisterIO, client: PLClient) -> None:
        self.memory = memory
        self.client = client

    @property
    def capacity(self) -> int:
        """Chips each code memory holds (CHIP_MEM_CHIPS)."""
        return self.client.read32(abi.ABI.REG.CHIP_MEM_CHIPS)

    def _engine_on(self) -> bool:
        control = self.client.read32(abi.ABI.REG.SLOT_CONTROL)
        status = self.client.read32(abi.ABI.REG.SLOT_STATUS)
        return bool(control & (1 << abi.ABI.SLOTCTL.ENABLE) or status & (1 << abi.ABI.SLOTST.RUNNING))

    def _write_words(self, offset: int, words: np.ndarray) -> None:
        self.client.require_fabric_clock()
        write_block = getattr(self.memory, "write_block", None)
        if write_block is not None:
            write_block(offset, np.asarray(words, dtype="<u4"))
        else:
            for index, word in enumerate(words):
                self.memory.write(offset + 4 * index, int(word))

    def load(self, prn: Sequence[int] | np.ndarray, taps: Sequence[int] | np.ndarray, *,
             packet_code: Sequence[int] | np.ndarray | None = None, guard: int = p9.GUARD_CHIPS) -> None:
        """Write the PRN (or acquisition code), the packet code (default: the 288 x L
        chips of ``rf_clock.plmodel.packet.code_chips``) and the taps
        (``rf_clock.plmodel.fir.coefficients``), then BURST_PRN_CHIPS and
        BURST_GUARD_CHIPS.  BurstMemoryError while the slot engine is on."""
        prn = np.asarray(prn)
        code = (p9.code_chips(p9.PACKET_BITS * self.client.read32(abi.ABI.REG.PACKET_SPREAD))
                if packet_code is None else np.asarray(packet_code))
        taps = np.asarray(taps, dtype=np.int64)
        capacity = self.capacity
        if not 1 <= prn.size <= capacity or code.size > capacity:
            raise ValueError(f"a code holds 1 .. {capacity} chips; got {prn.size} and {code.size}")
        if taps.shape != (abi.ABI.BMEM.TAP_COUNT,):
            raise ValueError(f"{abi.ABI.BMEM.TAP_COUNT} taps expected, got {taps.shape}")
        worst = max(int(np.abs(taps[p::fir.SPS]).sum()) for p in range(fir.SPS))
        if worst > INT16_MAX:
            raise ValueError(f"a sample can reach {worst}: the PL does not saturate (docs/ABI.md 4.9)")
        if not 0 <= int(guard) <= 0xFFFF:
            raise ValueError("guard must fit 16 bits")
        if self._engine_on():
            raise BurstMemoryError("the slot engine is enabled: burst memory writes would be refused")
        refused = self.client.read32(abi.ABI.REG.BURST_MEM_REJ_CNT)
        self._write_words(abi.ABI.BMEM.PRN_OFFSET, pack_chips(prn))
        self._write_words(abi.ABI.BMEM.PACKET_OFFSET, pack_chips(code))
        self._write_words(abi.ABI.BMEM.TAPS_OFFSET, (taps & 0xFFFF).astype(np.uint32))
        if self.client.read32(abi.ABI.REG.BURST_MEM_REJ_CNT) != refused:
            raise BurstMemoryError("burst memory writes were refused (the slot engine came on)")
        self.client.write32(abi.ABI.REG.BURST_PRN_CHIPS, int(prn.size))
        self.client.write32(abi.ABI.REG.BURST_GUARD_CHIPS, int(guard))
        if self.read_taps() != [int(t) for t in taps]:
            raise BurstMemoryError("the taps read back differ from those written")

    def read_taps(self) -> list[int]:
        self.client.require_fabric_clock()
        out = []
        for k in range(abi.ABI.BMEM.TAP_COUNT):
            value = int(self.memory.read(abi.ABI.BMEM.TAPS_OFFSET + 4 * k)) & 0xFFFF
            out.append(value - 0x10000 if value & 0x8000 else value)
        return out

    def read_prn(self, count: int | None = None) -> np.ndarray:
        """The PRN memory's first ``count`` chips (default BURST_PRN_CHIPS)."""
        count = self.client.read32(abi.ABI.REG.BURST_PRN_CHIPS) if count is None else int(count)
        self.client.require_fabric_clock()
        words = [int(self.memory.read(abi.ABI.BMEM.PRN_OFFSET + 4 * w)) for w in range(-(-count // 32))]
        return unpack_chips(words, count)
