"""Safe writes to the 256-bit on-chip TX wave buffer."""

from __future__ import annotations

from collections.abc import Iterable

from . import abi
from .client import PLClient, PLClientError
from .mmio import RegisterIO


class TXBufferError(PLClientError):
    pass


class TXBuffer:
    BEAT_BYTES = 32
    WORD_BYTES = 4
    WORDS_PER_BEAT = BEAT_BYTES // WORD_BYTES

    def __init__(self, memory: RegisterIO, client: PLClient) -> None:
        self.memory = memory
        self.client = client

    @staticmethod
    def _as_bytes(beat: int | bytes | bytearray | memoryview) -> bytes:
        if isinstance(beat, int):
            if not 0 <= beat < (1 << 256):
                raise ValueError("integer beat must fit in 256 bits")
            return beat.to_bytes(32, "little")
        value = bytes(beat)
        if len(value) != 32:
            raise ValueError("each TX beat must contain exactly 32 bytes")
        return value

    def _depth(self) -> int:
        return self.client.info.tx_depth

    def _require_writable(self) -> None:
        if self.client.status().tx_state in (abi.TxState.ARMED, abi.TxState.RUNNING):
            raise TXBufferError("TX buffer is immutable while TX is ARMED/RUNNING")

    def _write_words(self, index: int, beat: int | bytes | bytearray | memoryview) -> None:
        data = self._as_bytes(beat)
        base = index * self.BEAT_BYTES
        # One clock check per beat: a lost fabric clock would hang the bus.
        self.client.require_fabric_clock()
        for word in range(self.WORDS_PER_BEAT):
            value = int.from_bytes(data[word * 4 : word * 4 + 4], "little")
            self.memory.write(base + word * 4, value)

    def write_beat(self, index: int, beat: int | bytes | bytearray | memoryview) -> None:
        if not isinstance(index, int) or index < 0:
            raise ValueError("beat index must be non-negative")
        if index >= self._depth():
            raise TXBufferError(f"beat index {index} exceeds TX_DEPTH")
        self._require_writable()
        self._write_words(index, beat)

    def write_beats(self, beats: Iterable[int | bytes | bytearray | memoryview]) -> int:
        """Load beats 0..n-1.  One block copy when the memory supports it."""
        values = list(beats)
        depth = self._depth()
        if len(values) > depth:
            raise TXBufferError(f"{len(values)} beats exceed TX_DEPTH={depth}")
        # Depth and TX state are checked once for the whole load.
        self._require_writable()
        write_block = getattr(self.memory, "write_block", None)
        if write_block is None:
            for index, beat in enumerate(values):
                self._write_words(index, beat)
            return len(values)
        import numpy as np

        data = b"".join(self._as_bytes(beat) for beat in values)
        # The block copy takes about 0.5 ms; one clock check covers it.
        self.client.require_fabric_clock()
        write_block(0, np.frombuffer(data, dtype="<u4"))
        return len(values)
