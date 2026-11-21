"""The phase of the node's 10 MHz reference, P11 (docs/ABI.md 4.14, since 2.4).

A copy of the 10 MHz the node's FS725 sends out (to the scope, to White Rabbit) goes
into ADC_D (ADC tile 0 block 0).  ``PL.open()`` sets that block's NCO to 0 Hz with
phase 0 (``rf_clock.pl.rfdc.baseband_adc``), so its I stream carries the samples, and
P11 sums them over each second against a 10 MHz cosine and sine on the tick grid
(one period is 80 samples, 10 ticks).  For ``x = A cos(2 pi f (t - tau))`` the sums
give ``atan2(Q, I) / (2 pi f)`` modulo 100 ns, the delay of the reference's crest after
a tick that is a multiple of 10; raw_tick starts from 0 at reset, at no fixed place
against the 10 MHz, so that grid moves by whole ticks at every start.  tau_ref is taken
against the second's own start instead, its PPS_TICK:
``tau_ref = atan2(Q, I) / (2 pi f) - 10 ns x (PPS_TICK mod 10)`` modulo 100 ns, the
reference's delay against the PL's 100 MHz clock plus the fixed latency of ADC_D's path
(MTS fixes it at every start); and ``A = 2 |I + jQ| / (32767 x 8 x beats)``.  The
clock chain from the FS725 to the converters (LMK04828, LMX2594, the tile PLLs) moves
that delay at each start and over hours; the tick-based output PPS does not move with it.

At each PPS event P11 keeps the second just ended (``REF_PHASE_*``) until the next
event; REF_PHASE_SEQ counts the seconds kept, and is read before and after the rest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import abi
from .client import PLClient, WaitTimeout

REG = abi.ABI.REG
REF_HZ = 10e6  # the reference
TICK_NS = 10.0
SCALE = 32767  # P11's cosine table: cos x 32767
SAMPLES_PER_BEAT = 8


def _signed64(raw: int) -> int:
    return raw - (1 << 64) if raw >> 63 else raw


@dataclass(frozen=True)
class RefPhaseSecond:
    seq: int  # REF_PHASE_SEQ: the seconds kept so far
    pps_count: int  # the second summed
    beats: int  # beats summed (1e8 a whole second)
    peak: int  # the second's largest |sample|; near 32767 the input clips
    i: int
    q: int
    pps_tick: int  # PPS_TICK of the edge that started the second

    @property
    def delay_ns(self) -> float:
        """tau_ref: the reference's delay after the second's PPS_TICK, in [0, 100) ns."""
        grid = math.atan2(self.q, self.i) / (2 * math.pi * REF_HZ) * 1e9
        return (grid - TICK_NS * (self.pps_tick % 10)) % (1e9 / REF_HZ)

    @property
    def amplitude(self) -> float:
        """The reference's amplitude in ADC codes (full scale 32767)."""
        samples = SAMPLES_PER_BEAT * self.beats
        return 2 * math.hypot(self.i, self.q) / (SCALE * samples) if samples else 0.0


class RefPhase:
    """``pl.ref_phase``: the sums of the last second P11 kept."""

    def __init__(self, client: PLClient) -> None:
        self.client = client

    def seq(self) -> int:
        return self.client.read32(REG.REF_PHASE_SEQ)

    def read(self, *, attempts: int = 5) -> RefPhaseSecond | None:
        """The last second kept, or None before the first.  Its PPS_TICK is the latest
        edge's less PPS_LAST_PERIOD, that edge being the one that closed the second.
        REF_PHASE_SEQ is read first and last; a second kept in between, or an edge, is
        read again."""
        for _ in range(attempts):
            seq = self.seq()
            if seq == 0:
                return None
            pps_count = self.client.read32(REG.REF_PHASE_COUNT)
            beats = self.client.read32(REG.REF_PHASE_BEATS)
            peak = self.client.read32(REG.REF_PHASE_PEAK) & 0xFFFF
            i = _signed64(self.client.read64(REG.REF_PHASE_I_LO))
            q = _signed64(self.client.read64(REG.REF_PHASE_Q_LO))
            count, tick = self.client.latest_pps()
            period = self.client.read32(REG.PPS_LAST_PERIOD)
            if count == pps_count + 1 and self.seq() == seq:
                return RefPhaseSecond(seq=seq, pps_count=pps_count, beats=beats, peak=peak, i=i, q=q,
                                      pps_tick=tick - period)
        raise WaitTimeout(f"P11's second kept changing over {attempts} reads")
