"""``pl.rx_dma``: the AXI DMA S2MM that writes RX windows into DDR.

Only the DMA side of an RX window; the command and its checks are on
``pl.ctrl`` (docs/ABI.md 7.3)::

    pl.rx_dma.prepare(n)               # S2MM: buffer address and length
    pl.ctrl.arm_rx(target, n)          # RX_TARGET, RX_LENGTH, doorbell
    window = pl.ctrl.wait_rx()         # DONE, flags, RX_ACTUAL == target
    x = pl.rx_dma.wait(window.beats).samples
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any, Protocol

from .beats import RX_LAYOUT


class DMAEngine(Protocol):
    def prepare(self, buffer: Any, nbytes: int) -> None: ...

    def wait(self, timeout: float | None = None) -> None: ...

    def abort(self) -> None: ...

    @property
    def received_bytes(self) -> int: ...


class RXDMAError(RuntimeError):
    pass


@dataclass(frozen=True)
class RXData:
    buffer: Any
    beats: int
    received_bytes: int

    @cached_property
    def samples(self):
        """complex64 ``I + jQ`` samples (RX_LAYOUT), decoded on first access.

        The buffer RXDMA allocates is reused by its next window, so take the
        samples (a copy) before preparing the next one.
        """
        return RX_LAYOUT.unpack_complex(self.buffer[: self.beats * 32])


class PynqS2MMDMA:
    """Adapter for a PYNQ ``DMA`` object's receive channel.

    PYNQ's channel performs the physical-address and 64-bit register writes.
    ``received_bytes`` reads the S2MM length register after TLAST.  A custom
    ``length_reader`` is useful with PYNQ versions whose ``register_map``
    representation differs.
    """

    # AXI DMA S2MM_DMACR and its soft-reset bit (AMD PG021).
    S2MM_DMACR = 0x30
    DMACR_RESET = 0x4

    def __init__(self, dma: Any, *, length_reader=None) -> None:
        self.dma = dma
        self.channel = dma.recvchannel
        self._length_reader = length_reader
        self._received_bytes: int | None = None
        self._outstanding = False

    def abort(self) -> None:
        """Drop a started transfer whose TLAST will never arrive.

        Soft-resets the AXI DMA (the reset bit clears itself) and restarts the
        S2MM channel so the next ``transfer()`` sees an idle, running channel.
        Not yet verified on the board.
        """
        self.dma.write(self.S2MM_DMACR, self.DMACR_RESET)
        for _ in range(1000):
            if not int(self.dma.read(self.S2MM_DMACR)) & self.DMACR_RESET:
                break
        else:
            raise RXDMAError("AXI DMA soft reset did not complete")
        self.channel.start()
        self._received_bytes = None
        self._outstanding = False

    def prepare(self, buffer: Any, nbytes: int) -> None:
        if int(getattr(buffer, "nbytes", nbytes)) < nbytes:
            raise ValueError("DMA buffer is smaller than the requested RX window")
        if self._outstanding and not self.channel.idle:
            # The last window never opened (RX doorbell rejected or cancelled),
            # so its transfer still waits for TLAST.
            self.abort()
        self._received_bytes = None
        # Program exactly the window: a buffer may be reused for shorter windows.
        self.channel.transfer(buffer, nbytes=nbytes)
        self._outstanding = True

    def wait(self, timeout: float | None = None) -> None:
        # Most PYNQ releases expose wait() without a timeout.  P4 reports RX
        # DONE only after the DMA took TLAST, so after ctrl.wait_rx() this
        # returns at once; a custom fake can use the optional argument.
        if timeout is None:
            self.channel.wait()
        else:
            try:
                self.channel.wait(timeout=timeout)
            except TypeError:
                self.channel.wait()
        self._outstanding = False
        self._received_bytes = self._read_length()

    def _read_length(self) -> int:
        if self._length_reader is not None:
            return int(self._length_reader())
        register_map = getattr(self.dma, "register_map", None)
        if register_map is None:
            raise RXDMAError("PYNQ DMA adapter needs register_map or length_reader")
        value = getattr(register_map, "S2MM_LENGTH", None)
        if value is None:
            raise RXDMAError("PYNQ DMA register_map has no S2MM_LENGTH")
        return int(value)

    @property
    def received_bytes(self) -> int:
        if self._received_bytes is None:
            raise RXDMAError("DMA has not completed")
        return self._received_bytes


def pynq_rx_dma(overlay: Any, name: str = "rx_dma") -> PynqS2MMDMA:
    """S2MM adapter for the overlay's AXI DMA.

    PYNQ binds ``pynq.lib.dma.DMA`` only if that module was imported before
    the overlay loaded; otherwise build the driver from the HWH description.
    """
    from pynq.lib.dma import DMA  # board-only

    ip = getattr(overlay, name)
    return PynqS2MMDMA(ip if isinstance(ip, DMA) else DMA(overlay.ip_dict[name]))


class RXDMA:
    """The S2MM side of RX windows (see the module docstring).

    ``prepare()`` must come before ``ctrl.arm_rx()``: the PL rejects the RX
    doorbell while S2MM is not ready (REJ 0x14).  Without an explicit
    ``buffer`` one from ``buffer_factory`` is used and reused while it is
    large enough.
    """

    BEAT_BYTES = 32

    def __init__(self, dma: DMAEngine, *, buffer_factory=None) -> None:
        self.dma = dma
        self.buffer_factory = buffer_factory
        self._buffer = None
        self._prepared = None  # buffer of the window in flight

    def _allocate(self, nbytes: int) -> Any:
        """Reuse the last allocated buffer while it is large enough."""
        buffer = self._buffer
        if buffer is not None and int(getattr(buffer, "nbytes", None) or len(buffer)) >= nbytes:
            return buffer
        if self.buffer_factory is None:
            raise ValueError("buffer_factory is required when no buffer is supplied")
        buffer = self.buffer_factory(nbytes)
        if int(getattr(buffer, "nbytes", nbytes)) < nbytes:
            raise ValueError("buffer_factory returned an undersized buffer")
        self._buffer = buffer
        return buffer

    def prepare(self, beats: int, *, buffer=None) -> Any:
        """Point S2MM at a buffer for a window of ``beats``; return the buffer."""
        if not isinstance(beats, int) or beats <= 0:
            raise ValueError("beats must be positive")
        nbytes = beats * self.BEAT_BYTES
        if buffer is None:
            buffer = self._allocate(nbytes)
        elif int(getattr(buffer, "nbytes", nbytes)) < nbytes:
            raise ValueError("DMA buffer is smaller than requested RX window")
        self.dma.prepare(buffer, nbytes)
        self._prepared = buffer
        return buffer

    def wait(self, beats: int, *, timeout: float = 1.0) -> RXData:
        """Finish the window after ``ctrl.wait_rx()``; ``beats`` is its ``RXWindow.beats``.

        Checks that S2MM wrote exactly ``beats`` beats and invalidates the
        cache over the buffer.
        """
        if self._prepared is None:
            raise RXDMAError("no RX window prepared")
        buffer = self._prepared
        self.dma.wait(timeout=timeout)
        self._prepared = None
        received = int(self.dma.received_bytes)
        if received != beats * self.BEAT_BYTES:
            raise RXDMAError(f"DMA received {received} bytes, PL reports {beats} beats")
        invalidate = getattr(buffer, "invalidate", None)
        if invalidate is not None:
            invalidate()
        return RXData(buffer=buffer, beats=beats, received_bytes=received)

    def abort(self) -> None:
        """Drop the prepared transfer (its window was rejected or cancelled)."""
        self.dma.abort()
        self._prepared = None
