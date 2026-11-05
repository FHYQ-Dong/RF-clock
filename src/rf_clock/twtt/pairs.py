"""The two-way time transfer of rf-clock 2, from one node's measurement queue (plan section
4.2, docs/ABI.md 4.11-4.12 and 11.12).

Each node's slot engine sends its burst ``tx_offset`` ticks into each of its own slots (A the
even ones, B the odd ones) and opens an RX window for the peer's burst in the peer's slots.
For the peer's burst in slot k the node's queue holds two entries:

- its OWN entry of that window: T_rx, when the burst arrived, on this node's time;
- the burst's measurement packet (a PEER entry): the peer's T_tx of that burst, slot k on the
  peer's time, and the peer's own last measurement, its T_rx of this node's burst in slot
  k - 1, which left at slot k - 1's start plus ``tx_offset`` on this node's time.

So one window gives one pair, its two directions 2 ms apart:

    m_in  = T_rx(this node, slot k)     - T_tx(peer, slot k)           the peer to this node
    m_out = T_rx(peer, slot k - 1)      - T_tx(this node, slot k - 1)  this node to the peer

with m_AB, m_BA = m_out, m_in on node A and m_in, m_out on node B, and

    theta = (m_AB - m_BA) / 2   B's clock reading minus A's, plus the non-reciprocity Delta / 2
    delay = (m_AB + m_BA) / 2   the one-way delay

Each node forms them from its own queue alone, and both see the same pairs.  ``Pairer``
matches the two entries of a window, ``SecondTheta`` averages the pairs of each second (the
node's ``second`` lines), and ``StepTheta`` the end of each step of a law that corrects more
often than once a second; ``rf_clock.twtt.logs.pairs`` forms the same pairs from a log.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable

from rf_clock.pl.abi import ABI
from rf_clock.pl.measurements import OwnMeasurement, PeerPacket

FS_PER_TICK = 10_000_000  # one 10 ns tick


@dataclass(frozen=True)
class SlotTiming:
    """The slot engine's timing: as ``pl.slots`` reads it, or the log's ``config`` line."""

    slots_per_second: int
    slot_ticks: int
    tx_offset: int

    def tx_fs(self, slot: int) -> int:
        """When a node's burst of ``slot`` leaves, fs after the node's PPS edge."""
        return (slot * self.slot_ticks + self.tx_offset) * FS_PER_TICK


@dataclass(frozen=True)
class Pair:
    """One window's two directions, ns: ``count`` and ``slot`` are the window's (this node's
    PPS_COUNT, the peer's slot)."""

    count: int
    slot: int
    m_ab_ns: float
    m_ba_ns: float

    @property
    def theta_ns(self) -> float:
        return (self.m_ab_ns - self.m_ba_ns) / 2

    @property
    def delay_ns(self) -> float:
        return (self.m_ab_ns + self.m_ba_ns) / 2


def pair(timing: SlotTiming, role_a: bool, count: int, slot: int, t_rx_fs: int, peer_t_tx_fs: int,
         peer_t_rx_fs: int) -> Pair:
    """The pair of one window: this node's T_rx of the peer's burst in ``slot``, and from that
    burst's packet the peer's T_tx of it and the peer's T_rx of this node's burst one slot
    earlier."""
    m_in = (t_rx_fs - peer_t_tx_fs) / 1e6
    m_out = (peer_t_rx_fs - timing.tx_fs((slot - 1) % timing.slots_per_second)) / 1e6
    m_ab, m_ba = (m_out, m_in) if role_a else (m_in, m_out)
    return Pair(count, slot, m_ab, m_ba)


class Pairer:
    """Matches this node's OWN entries with the peer packets of the same window (PPS_COUNT
    and slot): a locked OWN entry waits for its packet, and a packet that carries a
    measurement makes the pair.  ``add`` takes queue entries, ``add_record`` the same as
    log lines."""

    def __init__(self, timing: SlotTiming, role_a: bool) -> None:
        self.timing, self.role_a = timing, role_a
        self.waiting: dict[tuple[int, int], int] = {}  # (count, slot) -> T_rx, fs

    def _peer(self, count: int, slot: int, measured: bool, t_tx_fs: int, t_rx_fs: int) -> Pair | None:
        t_rx = self.waiting.pop((count, slot), None)
        if t_rx is None or not measured:
            return None
        return pair(self.timing, self.role_a, count, slot, t_rx, t_tx_fs, t_rx_fs)

    def add(self, entry: OwnMeasurement | PeerPacket) -> Pair | None:
        if isinstance(entry, OwnMeasurement):
            if entry.locked:
                self.waiting[(entry.pps_count, entry.slot)] = entry.t_rx_fs
            return None
        return self._peer(entry.pps_count, entry.slot, entry.measurement, entry.t_tx_fs, entry.t_rx_fs)

    def add_record(self, record: dict) -> Pair | None:
        """A log line of type ``own`` or ``peer`` (``asdict`` of the entry)."""
        if record["type"] == "own":
            if record["locked"]:
                self.waiting[(record["pps_count"], record["slot"])] = record["t_rx_fs"]
            return None
        return self._peer(record["pps_count"], record["slot"], bool(record["status"] >> ABI.PKST.MEASUREMENT & 1),
                          record["t_tx_fs"], record["t_rx_fs"])

    def forget(self, before_count: int) -> None:
        """Drop the OWN entries of seconds before ``before_count`` whose packet never came."""
        self.waiting = {k: v for k, v in self.waiting.items() if k[0] >= before_count}


class SecondTheta:
    """theta and the one-way delay averaged over the pairs whose window lies in one second
    of this node's PPS_COUNT, from this node's queue alone (the node's ``second`` lines)."""

    def __init__(self, role: str, slots_per_second: int, slot_ticks: int, tx_offset: int, late_slots: int = 0,
                 pair_sink: Callable[[int, int, float], None] | None = None) -> None:
        self.slots = slots_per_second
        self.pairer = Pairer(SlotTiming(slots_per_second, slot_ticks, tx_offset), role == "A")
        self.pair_sink = pair_sink  # also gets (count, slot, theta_ns) of every pair
        self.late_slots = late_slots  # also theta over the last this many slots of each second
        self.count: int | None = None
        self.theta: list[float] = []
        self.late: list[float] = []
        self.delay: list[float] = []

    def add(self, entry: OwnMeasurement | PeerPacket) -> dict | None:
        """Take one queue entry; when it is the first pair of a new second, return the
        record of the second before."""
        p = self.pairer.add(entry)
        if p is None:
            return None
        done = self.flush() if self.count is not None and p.count != self.count else None
        self.count = p.count
        self.theta.append(p.theta_ns)
        if self.pair_sink is not None:
            self.pair_sink(p.count, p.slot, p.theta_ns)
        if self.late_slots and p.slot >= self.slots - self.late_slots:
            self.late.append(p.theta_ns)
        self.delay.append(p.delay_ns)
        return done

    def flush(self) -> dict | None:
        """The record of the second gathered so far (None if it has no pair)."""
        if not self.theta:
            return None
        n = len(self.theta)
        steps = [b - a for a, b in zip(self.theta, self.theta[1:])]
        record = dict(count=self.count, pairs=n, theta_ns=statistics.fmean(self.theta),
                      theta_sd_ps=statistics.stdev(self.theta) * 1000 if n > 1 else None,
                      # from pair to pair, the clocks' drift within the second left out
                      theta_pair_sd_ps=statistics.stdev(steps) / 2 ** 0.5 * 1000 if n > 2 else None,
                      delay_ns=statistics.fmean(self.delay),
                      delay_sd_ps=statistics.stdev(self.delay) * 1000 if n > 1 else None)
        if self.late_slots:
            record["theta_late_ns"] = statistics.fmean(self.late) if self.late else None
        self.theta, self.late, self.delay = [], [], []
        assert self.count is not None
        self.pairer.forget(self.count)
        return record


class StepTheta:
    """theta over the last ``late_slots`` slots of each step of ``step_slots`` slots, for a
    law that corrects more often than once a second: fed every pair by ``SecondTheta``, it
    finishes a step's record when the first pair of a later step comes in (``done``)."""

    def __init__(self, step_slots: int, late_slots: int, slots_per_second: int) -> None:
        self.step_slots, self.late_slots = step_slots, late_slots
        self.steps_per_second = slots_per_second // step_slots
        self.key: tuple[int, int] | None = None
        self.pairs = 0
        self.late: list[float] = []
        self.done: list[dict] = []

    def add(self, count: int, slot: int, theta_ns: float) -> None:
        key = (count, slot // self.step_slots)
        if self.key is not None and key != self.key:
            self.flush()
        self.key = key
        self.pairs += 1
        if slot % self.step_slots >= self.step_slots - self.late_slots:
            self.late.append(theta_ns)

    def flush(self) -> None:
        if self.key is not None and self.pairs:
            count, step = self.key
            self.done.append(dict(count=count, step=step, index=count * self.steps_per_second + step, pairs=self.pairs,
                                  theta_late_ns=statistics.fmean(self.late) if self.late else None))
        self.pairs, self.late = 0, []
