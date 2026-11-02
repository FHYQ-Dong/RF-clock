"""P3: the chip stream through the RRC FIR (plan sections 3.1 and 6, P3).

The ARM rounds the float64 RRC coefficients times the amplitude to integers once;
the PL adds or subtracts them as the chips are +1, -1 or 0 (the guard), with no
multiplier and no rounding after the sum, so its output equals :func:`shape`
exactly.  The rounded coefficients put each sample within a few LSB of the first
version's waveform (``rf_clock.pl.waveform``, which rounds the float sum).
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from ..pl.waveform import DEFAULT_BETA, DEFAULT_SAMPLES_PER_SYMBOL, DEFAULT_SPAN_CHIPS, rrc_config

SPS = DEFAULT_SAMPLES_PER_SYMBOL  # samples per chip: one chip per tick, 8 samples per beat
INT16_MAX = 32767


def coefficients(amplitude: float, *, beta: float = DEFAULT_BETA, span_chips: int = DEFAULT_SPAN_CHIPS) -> np.ndarray:
    """The integer taps the ARM loads into P3: round(h[k] * amplitude).

    ValueError if a sample could leave int16: each output sample sums one tap of
    every phase k = p (mod 8) with a sign, so the largest is the sum of |taps|
    of one phase."""
    _, h = rrc_config(beta, span_chips, SPS)
    q = np.rint(np.asarray(h, dtype=np.float64) * float(amplitude)).astype(np.int64)
    worst = max(int(np.abs(q[p::SPS]).sum()) for p in range(SPS))
    if worst > INT16_MAX:
        raise ValueError(f"amplitude {amplitude}: a sample can reach {worst}, beyond int16")
    return q


def shape(chips: Sequence[int] | np.ndarray, taps: np.ndarray) -> np.ndarray:
    """The burst P3 plays for ``chips`` (each +1, -1 or 0): every chip spreads its
    taps over the samples from chip index * 8 on, and the output holds
    (len(chips) - 1) * 8 + len(taps) samples, the RRC tail included."""
    chips = np.asarray(chips, dtype=np.int64)
    if chips.ndim != 1 or not chips.size or not np.isin(chips, (-1, 0, 1)).all():
        raise ValueError("chips must be a non-empty sequence of -1, 0 and +1")
    up = np.zeros((chips.size - 1) * SPS + 1, dtype=np.int64)
    up[::SPS] = chips
    return np.convolve(up, np.asarray(taps, dtype=np.int64)).astype(np.int16)


def burst_chips(prn: Sequence[int] | np.ndarray, packet: Sequence[int] | np.ndarray = (),
                guard: int = 10) -> np.ndarray:
    """A burst's chip stream (plan section 5): the PRN, ``guard`` zero chips (at
    least the RRC span), then the measurement packet's chips, if any."""
    if packet is None or len(packet) == 0:
        return np.asarray(prn, dtype=np.int64)
    return np.concatenate([np.asarray(prn, dtype=np.int64), np.zeros(guard, dtype=np.int64),
                           np.asarray(packet, dtype=np.int64)])
