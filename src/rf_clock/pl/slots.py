"""The slot engine, P7 (docs/ABI.md 4.8): the 2 ms ping-pong from each PPS edge.

Slot k = 0 .. SLOTS_PER_SECOND - 1 starts at PPS_TICK + k x SLOT_TICKS.  Node A
sends a burst in the even slots, node B in the odd ones, ``tx_offset`` ticks after
the slot start; in the peer's slots the node opens a window ``rx_offset`` ticks
after the slot start.  Everything written here is taken at the next PPS edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import abi
from .client import PLClient

WINDOW_LEAD_TICKS = 125  # a window opens 1.25 us before the expected arrival


def rx_offset_for(peer_tx_offset: int, arrival_ticks: int) -> int:
    """SLOT_RX_OFFSET for a peer whose bursts start ``peer_tx_offset`` ticks into its
    slots and arrive ``arrival_ticks`` later: the window opens WINDOW_LEAD_TICKS early."""
    offset = int(peer_tx_offset) + int(arrival_ticks) - WINDOW_LEAD_TICKS
    if offset < 0:
        raise ValueError(f"the window would open {-offset} ticks before the slot")
    return offset


@dataclass(frozen=True)
class SlotStatus:
    running: bool  # from the PPS edge after ENABLE
    slot: int  # the slot the engine works on
    skipped: int  # slots not started (SLOT_SKIPPED_CNT)
    copy_pending: bool  # SLOT_COPY: the next window still goes to DDR


class Slots:
    """``pl.slots``: configure, enable and watch the slot engine."""

    def __init__(self, client: PLClient) -> None:
        self.client = client

    # ---- build parameters ----
    @property
    def slot_ticks(self) -> int:
        return self.client.read32(abi.ABI.REG.SLOT_TICKS)

    @property
    def slots_per_second(self) -> int:
        return self.client.read32(abi.ABI.REG.SLOTS_PER_SECOND)

    @property
    def window_beats(self) -> int:
        return self.client.read32(abi.ABI.REG.RX_WINDOW_BEATS)

    @property
    def span_beats(self) -> int:
        """The window and the latest packet it can hold (RX_SPAN_BEATS)."""
        return self.client.read32(abi.ABI.REG.RX_SPAN_BEATS)

    # ---- configuration ----
    def configure(self, *, role_b: bool, rx_offset: int, tx_offset: int = 1000, packets: bool = True,
                  enable: bool = True) -> None:
        """Write the engine's configuration; it takes effect at the next PPS edge.

        ValueError when the burst would run into the next slot or the window would
        still be open at this node's next burst: the engine would skip those slots."""
        for name, value in (("tx_offset", tx_offset), ("rx_offset", rx_offset)):
            if not 0 <= int(value) <= 0xFFFF_FFFF:
                raise ValueError(f"{name} must fit 32 bits")
        control = (int(bool(role_b)) << abi.ABI.SLOTCTL.ROLE_B) | (int(bool(packets)) << abi.ABI.SLOTCTL.PACKETS)
        # Off first, so that BURST_BEATS reflects PACKETS before the engine may take it.
        self.client.write32(abi.ABI.REG.SLOT_CONTROL, control)
        self.client.write32(abi.ABI.REG.SLOT_TX_OFFSET, int(tx_offset))
        self.client.write32(abi.ABI.REG.SLOT_RX_OFFSET, int(rx_offset))
        ticks = self.slot_ticks
        beats = self.client.read32(abi.ABI.REG.BURST_BEATS)
        if int(tx_offset) + beats > ticks:
            raise ValueError(f"tx_offset {tx_offset} + {beats} burst beats runs past the slot ({ticks} ticks)")
        span = self.span_beats
        if int(rx_offset) + span > ticks + int(tx_offset):
            raise ValueError(f"rx_offset {rx_offset} + {span} span beats is still open at the next burst "
                             f"({ticks} + {tx_offset} ticks)")
        if enable:
            self.client.write32(abi.ABI.REG.SLOT_CONTROL, control | (1 << abi.ABI.SLOTCTL.ENABLE))

    def enable(self) -> None:
        control = self.client.read32(abi.ABI.REG.SLOT_CONTROL)
        self.client.write32(abi.ABI.REG.SLOT_CONTROL, control | (1 << abi.ABI.SLOTCTL.ENABLE))

    def disable(self) -> None:
        """Clear ENABLE: the engine stops at the next PPS edge."""
        control = self.client.read32(abi.ABI.REG.SLOT_CONTROL)
        self.client.write32(abi.ABI.REG.SLOT_CONTROL, control & ~(1 << abi.ABI.SLOTCTL.ENABLE))

    def copy_next_window(self) -> None:
        """Copy the next window's whole span to DDR through the S2MM: set up
        ``pl.rx_dma`` for ``span_beats`` beats first (docs/ABI.md 4.6.9)."""
        self.client.write32(abi.ABI.REG.SLOT_COPY, 1)

    def status(self) -> SlotStatus:
        raw = self.client.read32(abi.ABI.REG.SLOT_STATUS)
        return SlotStatus(
            running=bool(raw & (1 << abi.ABI.SLOTST.RUNNING)),
            slot=(raw >> abi.ABI.SLOTST.SLOT_LSB) & 0x1FF,
            skipped=self.client.read32(abi.ABI.REG.SLOT_SKIPPED_CNT),
            copy_pending=bool(self.client.read32(abi.ABI.REG.SLOT_COPY) & 1),
        )
