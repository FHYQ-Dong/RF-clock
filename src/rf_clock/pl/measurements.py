"""The measurement queue (docs/ABI.md 4.12): this node's measurements and the peer's packets.

The PL writes one ``OwnMeasurement`` for every window (a measurement of the
peer's burst when locked) and one ``PeerPacket`` for every packet it decoded
with a good CRC, into a ring the ARM reads from QUEUE_TAIL up to QUEUE_HEAD.
Since 2.5, while P13 steers through FC, also one ``SteerStep`` for every step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import abi
from .client import PLClient, PLClientError
from .mmio import RegisterIO

QE = abi.ABI.QE


def _s32(value: int) -> int:
    value = int(value) & 0xFFFF_FFFF
    return value - (1 << 32) if value & 0x8000_0000 else value


def _s64(lo: int, hi: int) -> int:
    value = ((int(hi) & 0xFFFF_FFFF) << 32) | (int(lo) & 0xFFFF_FFFF)
    return value - (1 << 64) if value >> 63 else value


@dataclass(frozen=True)
class OwnMeasurement:
    """QE_TYPE_OWN: this node's tracker on one window of the peer's burst."""

    seq: int  # entry sequence, mod 2^16
    pps_count: int  # this node's second of the window
    slot: int
    tick: int  # the window's start tick
    locked: bool  # the fields below are a measurement
    window_bad: bool  # the window had RX flags
    searched: bool  # a search ran on this window
    lag: int
    fraction: int  # 2^-32 sample
    phase: int  # 2^-32 turn
    magnitude: int
    t_rx_fs: int  # the peak's arrival, fs after this node's PPS
    quality: int  # 0.25 dB
    window_power: int  # sum of I^2 + Q^2 over the window

    @property
    def quality_db(self) -> float:
        return self.quality / 4


@dataclass(frozen=True)
class PeerPacket:
    """QE_TYPE_PEER: the fields of a peer packet decoded with a good CRC (plan 5.1)."""

    seq: int
    pps_count: int  # this node's second of the window it came in
    slot: int
    tick: int
    version: int
    time_epoch: int  # the peer's, low 8 bits
    quality: int  # the peer's measurement of this node's burst, 0.25 dB
    status: int  # PKST_* bits
    config_id: int
    peer_pps_count: int
    peer_slot: int
    t_tx_fs: int  # the burst's start, fs after the peer's PPS
    t_rx_fs: int  # this node's previous burst at the peer, fs after the peer's PPS
    phase: int  # that measurement's carrier phase, 2^-32 turn

    def _bit(self, bit: int) -> bool:
        return bool(self.status & (1 << bit))

    @property
    def peer_locked(self) -> bool:
        return self._bit(abi.ABI.PKST.LOCKED)

    @property
    def measurement(self) -> bool:
        """``t_rx_fs``, ``phase`` and ``quality`` are a measurement."""
        return self._bit(abi.ABI.PKST.MEASUREMENT)

    @property
    def window_valid(self) -> bool:
        return self._bit(abi.ABI.PKST.WINDOW_VALID)

    @property
    def overflow(self) -> bool:
        return self._bit(abi.ABI.PKST.OVERFLOW)

    @property
    def pps_locked(self) -> bool:
        return self._bit(abi.ABI.PKST.PPS_LOCKED)

    @property
    def clocks_locked(self) -> bool:
        return self._bit(abi.ABI.PKST.CLOCKS_LOCKED)


@dataclass(frozen=True)
class SteerStep:
    """QE_TYPE_STEER (since 2.5): one step of P13, the steering through FC in the PL (docs/ABI.md
    4.16).  ``pps_count`` and ``step`` name the step, ``index`` counts from the first with theta."""

    seq: int
    pps_count: int
    step: int
    pairs: int
    command_tick: int  # the tick its command was to start
    index: int
    theta_q16: int  # 2^-16 fs; the prediction in a step without pairs
    dF_q16: int  # theta less B's own corrections, its change from the step before, 2^-16 fs
    S_q24: int  # 2^-24 fine steps of FC
    low: int  # FC's fine DAC value after the step
    change: int
    remainder_q24: int
    old_changes: int  # the changes older than STEER_L steps, summed
    missing: bool
    settling: bool
    saturated: bool
    stopped: bool
    first: bool
    late: bool  # the command before this one started after its tick
    steps: bool  # FC steps, not the algorithm

    @property
    def theta_ps(self) -> float:
        return self.theta_q16 / 65536 / 1000

    @property
    def dF_ps(self) -> float:
        return self.dF_q16 / 65536 / 1000


class QueueError(PLClientError):
    pass


def decode_entry(words: Sequence[int]) -> OwnMeasurement | PeerPacket | SteerStep:
    """One 16-word entry (docs/ABI.md 4.12)."""
    if len(words) != abi.ABI.QUEUE_ENTRY_WORDS:
        raise ValueError(f"an entry has {abi.ABI.QUEUE_ENTRY_WORDS} words, got {len(words)}")
    w = [int(x) & 0xFFFF_FFFF for x in words]
    head = w[QE.HEADER]
    kind, flags, seq = head & 0xFF, (head >> 8) & 0xFF, head >> 16
    common = dict(seq=seq, pps_count=w[QE.PPS_COUNT], slot=w[QE.SLOT],
                  tick=(w[QE.TICK_HI] << 32) | w[QE.TICK_LO])
    if kind == QE.TYPE_OWN:
        return OwnMeasurement(
            **common,
            locked=bool(flags & (1 << abi.ABI.QEF.LOCKED)),
            window_bad=bool(flags & (1 << abi.ABI.QEF.WINDOW_BAD)),
            searched=bool(flags & (1 << abi.ABI.QEF.SEARCHED)),
            lag=w[QE.LAG],
            fraction=_s32(w[QE.FRACTION]),
            phase=_s32(w[QE.PHASE]),
            magnitude=(w[QE.MAG_HI] << 32) | w[QE.MAG_LO],
            t_rx_fs=_s64(w[QE.TRX_LO], w[QE.TRX_HI]),
            quality=w[QE.QUALITY] & 0xFF,
            window_power=(w[QE.POWER_HI] << 32) | w[QE.POWER_LO],
        )
    if kind == QE.TYPE_PEER:
        p = w[QE.P_HEAD]
        return PeerPacket(
            **common,
            version=p >> 24, time_epoch=(p >> 16) & 0xFF, quality=(p >> 8) & 0xFF, status=p & 0xFF,
            config_id=w[QE.P_CONFIG_ID],
            peer_pps_count=w[QE.P_PPS_COUNT],
            peer_slot=w[QE.P_SLOT],
            t_tx_fs=_s64(w[QE.P_TTX_LO], w[QE.P_TTX_HI]),
            t_rx_fs=_s64(w[QE.P_TRX_LO], w[QE.P_TRX_HI]),
            phase=_s32(w[QE.P_PHASE]),
        )
    if kind == QE.TYPE_STEER:
        f = abi.ABI.QESF
        fc = w[QE.S_FC]
        change = fc >> 16
        return SteerStep(
            seq=seq, pps_count=w[QE.PPS_COUNT], step=w[QE.S_STEP] & 0x1FF, pairs=(w[QE.S_STEP] >> 16) & 0x3FF,
            command_tick=(w[QE.S_CMD_HI] << 32) | w[QE.S_CMD_LO], index=w[QE.S_INDEX],
            theta_q16=_s64(w[QE.S_THETA_LO], w[QE.S_THETA_HI]), dF_q16=_s64(w[QE.S_DF_LO], w[QE.S_DF_HI]),
            S_q24=_s64(w[QE.S_S_LO], w[QE.S_S_HI]), low=fc & 0xFFF,
            change=change - 0x10000 if change & 0x8000 else change,
            remainder_q24=_s32(w[QE.S_REM]), old_changes=_s32(w[QE.S_OLD]),
            missing=bool(flags >> f.MISSING & 1), settling=bool(flags >> f.SETTLING & 1),
            saturated=bool(flags >> f.SATURATED & 1), stopped=bool(flags >> f.STOPPED & 1),
            first=bool(flags >> f.FIRST & 1), late=bool(flags >> f.LATE & 1), steps=bool(flags >> f.STEPS & 1),
        )
    raise QueueError(f"unknown entry type {kind}")


class MeasurementQueue:
    """``pl.queue``: read the ring and acknowledge what was read."""

    def __init__(self, memory: RegisterIO, client: PLClient) -> None:
        self.memory = memory
        self.client = client

    @property
    def entries(self) -> int:
        return self.client.read32(abi.ABI.REG.QUEUE_ENTRIES)

    def dropped(self) -> int:
        """Entries lost to a full ring (QUEUE_DROPPED_CNT)."""
        return self.client.read32(abi.ABI.REG.QUEUE_DROPPED_CNT)

    def pending(self) -> int:
        head = self.client.read32(abi.ABI.REG.QUEUE_HEAD)
        tail = self.client.read32(abi.ABI.REG.QUEUE_TAIL)
        return (head - tail) % self.entries

    def _entry_words(self, index: int) -> list[int]:
        base = index * 4 * abi.ABI.QUEUE_ENTRY_WORDS
        return [int(self.memory.read(base + 4 * k)) for k in range(abi.ABI.QUEUE_ENTRY_WORDS)]

    def read(self, max_entries: int | None = None) -> list[OwnMeasurement | PeerPacket | SteerStep]:
        """The entries from QUEUE_TAIL up to QUEUE_HEAD (at most ``max_entries``),
        then QUEUE_TAIL past them."""
        size = self.entries
        head = self.client.read32(abi.ABI.REG.QUEUE_HEAD)
        tail = self.client.read32(abi.ABI.REG.QUEUE_TAIL)
        count = (head - tail) % size
        if max_entries is not None:
            count = min(count, int(max_entries))
        out: list[OwnMeasurement | PeerPacket | SteerStep] = []
        self.client.require_fabric_clock()
        for k in range(count):
            out.append(decode_entry(self._entry_words((tail + k) % size)))
        if count:
            self.client.write32(abi.ABI.REG.QUEUE_TAIL, (tail + count) % size)
        return out

    def clear(self) -> int:
        """Drop the unread entries; returns how many."""
        size = self.entries
        head = self.client.read32(abi.ABI.REG.QUEUE_HEAD)
        tail = self.client.read32(abi.ABI.REG.QUEUE_TAIL)
        self.client.write32(abi.ABI.REG.QUEUE_TAIL, head)
        return (head - tail) % size
