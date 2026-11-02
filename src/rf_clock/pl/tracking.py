"""The correlator, P8 (docs/ABI.md 4.10): the tracker's lock and the whole-window search.

The tracker runs in every window while locked; a search runs without a lock or
on request.  Not locked, the PL re-locks by itself only on a strongest peak
within one lag of the last lock; any other peak waits (``waiting``) until the
ARM, having checked the one-way delay, accepts it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from . import abi
from .client import PLClient, WaitTimeout
from ..plmodel import correlator as model


def _s32(value: int) -> int:
    value = int(value) & 0xFFFF_FFFF
    return value - (1 << 32) if value & 0x8000_0000 else value


def fraction16(value: float, name: str) -> int:
    """A fraction 0 .. 1 in the PL's 2^-16 units (P8_CONFIG)."""
    raw = round(float(value) * 65536)
    if not 0 <= raw <= 0xFFFF:
        raise ValueError(f"{name} must lie in 0 .. 1 - 2^-16, got {value}")
    return raw


@dataclass(frozen=True)
class Peak:
    """A reported peak, in the units of ``rf_clock.plmodel.correlator.Peak``."""

    lag: int
    fraction: int  # 2^-32 sample
    phase: int  # 2^-32 turn
    magnitude: int

    @property
    def index(self) -> float:
        return self.lag + self.fraction / 2 ** model.FRACTION_BITS

    @property
    def phase_rad(self) -> float:
        return self.phase / 2 ** model.PHASE_BITS * 2 * math.pi


@dataclass(frozen=True)
class SearchReport:
    seq: int
    pps_count: int  # the window's second
    slot: int
    flags: abi.RxFlag
    tick: int  # the window's start tick
    strongest: Peak
    earliest: Peak
    sidelobe: int

    @property
    def peak_to_sidelobe_db(self) -> float:
        return 20 * math.log10(self.strongest.magnitude / self.sidelobe) if self.sidelobe else math.inf


@dataclass(frozen=True)
class CorrelatorStatus:
    locked: bool
    search_busy: bool
    waiting: bool  # a search found a peak the PL may not lock on by itself
    lock_lag: int
    lock_magnitude: int
    lost: int
    relocks: int
    searches: int
    refused: int


class Correlator:
    """``pl.correlator``: configure P8, command it, read its search report."""

    def __init__(self, client: PLClient) -> None:
        self.client = client

    @property
    def lags(self) -> int:
        """K, the lags of a whole window for the current PRN (P8_SEARCH_LAGS)."""
        return self.client.read32(abi.ABI.REG.P8_SEARCH_LAGS)

    def configure(self, *, threshold: float = 0.5, lock_min: float = 0.5, quality_offset: int = 0) -> None:
        """``threshold``: the earliest peak's magnitude relative to the strongest;
        ``lock_min``: a tracked peak's magnitude relative to the last lock's;
        ``quality_offset``: see :func:`quality_offset`."""
        raw = (fraction16(threshold, "threshold") << abi.ABI.P8CFG.THRESHOLD_LSB) | (
            fraction16(lock_min, "lock_min") << abi.ABI.P8CFG.LOCK_MIN_LSB)
        if not -(1 << 31) <= int(quality_offset) < (1 << 31):
            raise ValueError("quality_offset must fit i32")
        self.client.write32(abi.ABI.REG.P8_CONFIG, raw)
        self.client.write32(abi.ABI.REG.QUALITY_OFFSET, int(quality_offset) & 0xFFFF_FFFF)

    def status(self) -> CorrelatorStatus:
        raw = self.client.read32(abi.ABI.REG.P8_STATUS)
        read = self.client.read32
        return CorrelatorStatus(
            locked=bool(raw & (1 << abi.ABI.P8ST.LOCKED)),
            search_busy=bool(raw & (1 << abi.ABI.P8ST.SEARCH_BUSY)),
            waiting=bool(raw & (1 << abi.ABI.P8ST.WAITING)),
            lock_lag=read(abi.ABI.REG.P8_LOCK_LAG),
            lock_magnitude=self.client.read64(abi.ABI.REG.P8_LOCK_MAG_LO),
            lost=read(abi.ABI.REG.P8_LOST_CNT),
            relocks=read(abi.ABI.REG.P8_RELOCK_CNT),
            searches=read(abi.ABI.REG.P8_SEARCH_CNT),
            refused=read(abi.ABI.REG.P8_REFUSED_CNT),
        )

    def _control(self, bit: int) -> None:
        self.client.write32(abi.ABI.REG.P8_CONTROL, 1 << bit)

    def search(self) -> None:
        """Search the next window (refused and counted while a search runs)."""
        self._control(abi.ABI.P8CTL.SEARCH)

    def accept(self, *, earliest: bool = False) -> None:
        """Lock on the reported strongest (or earliest) peak."""
        self._control(abi.ABI.P8CTL.ACCEPT_EARLIEST if earliest else abi.ABI.P8CTL.ACCEPT_STRONGEST)

    def unlock(self) -> None:
        self._control(abi.ABI.P8CTL.UNLOCK)

    def _peak(self, lag: int, frac: int, phase: int, mag_lo: int) -> Peak:
        read = self.client.read32
        return Peak(lag=read(lag), fraction=_s32(read(frac)), phase=_s32(read(phase)),
                    magnitude=self.client.read64(mag_lo))

    def report(self, *, attempts: int = 5) -> SearchReport | None:
        """The latest search report, or None before the first.  SR_SEQ is read
        first and last; a report that changed in between is read again."""
        reg = abi.ABI.REG
        for _ in range(attempts):
            seq = self.client.read32(reg.SR_SEQ)
            if seq == 0:
                return None
            found = SearchReport(
                seq=seq,
                pps_count=self.client.read32(reg.SR_PPS_COUNT),
                slot=self.client.read32(reg.SR_SLOT),
                flags=abi.RxFlag(self.client.read32(reg.SR_FLAGS) & 0xF),
                tick=self.client.read64(reg.SR_TICK_LO),
                strongest=self._peak(reg.SR_STRONG_LAG, reg.SR_STRONG_FRAC, reg.SR_STRONG_PHASE, reg.SR_STRONG_MAG_LO),
                earliest=self._peak(reg.SR_EARLY_LAG, reg.SR_EARLY_FRAC, reg.SR_EARLY_PHASE, reg.SR_EARLY_MAG_LO),
                sidelobe=self.client.read64(reg.SR_SIDELOBE_LO),
            )
            if self.client.read32(reg.SR_SEQ) == seq:
                return found
        raise WaitTimeout(f"the search report kept changing over {attempts} reads")

    def wait_report(self, after: int = 0, *, timeout: float = 1.0, poll_interval: float = 0.005) -> SearchReport:
        """The first report with SR_SEQ beyond ``after``."""
        deadline = time.monotonic() + timeout
        while True:
            found = self.report()
            if found is not None and found.seq > after:
                return found
            if time.monotonic() >= deadline:
                raise WaitTimeout(f"no search report after {after} within {timeout} s")
            time.sleep(poll_interval)


def quality_offset(window_samples: int, reference) -> int:
    """QUALITY_OFFSET for a reference (``rf_clock.plmodel.correlator.quality_offset``)."""
    return model.quality_offset(window_samples, reference)
