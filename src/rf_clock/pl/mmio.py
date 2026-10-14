"""Small MMIO protocols and PYNQ/fake implementations.

All accesses are 32-bit little-endian words.  The protocol is intentionally
smaller than PYNQ's MMIO class so tests can provide a deterministic model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class RegisterIO(Protocol):
    def read(self, offset: int) -> int: ...

    def write(self, offset: int, value: int) -> None: ...


class ClockStatusIO(Protocol):
    def read(self, offset: int) -> int: ...


class MemoryMMIO:
    """In-memory 32-bit MMIO used by tests and a fake PL model.

    ``on_write`` can implement side effects such as doorbells.  It receives
    the aligned offset and masked value after the value has been stored.
    """

    def __init__(
        self,
        *,
        initial: dict[int, int] | None = None,
        on_write: Callable[[int, int], None] | None = None,
    ) -> None:
        self.values = {int(k): int(v) & 0xFFFF_FFFF for k, v in (initial or {}).items()}
        self.on_write = on_write
        self.read_log: list[int] = []
        self.write_log: list[tuple[int, int]] = []

    def read(self, offset: int) -> int:
        self._check_offset(offset)
        self.read_log.append(offset)
        return self.values.get(offset, 0)

    def write(self, offset: int, value: int) -> None:
        self._check_offset(offset)
        value = int(value)
        if not 0 <= value <= 0xFFFF_FFFF:
            raise ValueError("MMIO writes must contain one 32-bit word")
        value &= 0xFFFF_FFFF
        self.values[offset] = value
        self.write_log.append((offset, value))
        if self.on_write is not None:
            self.on_write(offset, value)

    def write_block(self, offset: int, words) -> None:
        """Same contract as ``PynqMMIO.write_block``; logged as single writes."""
        self.block_writes = getattr(self, "block_writes", 0) + 1
        for index, word in enumerate(words):
            self.write(int(offset) + 4 * index, int(word))

    @staticmethod
    def _check_offset(offset: int) -> None:
        if int(offset) < 0 or int(offset) % 4:
            raise ValueError(f"MMIO offset must be a non-negative 32-bit word: {offset!r}")


class PynqMMIO:
    """Lazy wrapper around ``pynq.MMIO``.

    Keeping the import in ``__init__`` makes the package importable on a
    development machine without PYNQ installed.
    """

    def __init__(self, base: int, length: int = 0x1000, *, mmio=None) -> None:
        if mmio is None:
            try:
                from pynq import MMIO
            except ImportError as exc:  # pragma: no cover - exercised on board
                raise RuntimeError("PynqMMIO requires the PYNQ runtime") from exc
            mmio = MMIO(int(base), int(length))
        self._mmio = mmio

    def read(self, offset: int) -> int:
        return int(self._mmio.read(int(offset))) & 0xFFFF_FFFF

    def write(self, offset: int, value: int) -> None:
        self._mmio.write(int(offset), int(value) & 0xFFFF_FFFF)

    def write_block(self, offset: int, words) -> None:
        """Copy consecutive 32-bit words with one aligned array store.

        For memory regions (the TX buffer) only: registers need one access at
        a time.  About 0.5 ms for 128 KiB on the board versus 0.3 s word by word.
        """
        if int(offset) % 4:
            raise ValueError("block writes must start on a 32-bit boundary")
        start = int(offset) // 4
        self._mmio.array[start: start + len(words)] = words
