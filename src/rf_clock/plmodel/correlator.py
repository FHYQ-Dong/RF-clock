"""P8: integer correlation and the estimator, as the PL computes them (plan section 6, P8).

Correlation of a window (16-bit I and Q) with the reference (P3's 16-bit samples of
the same chips, real) is exact in integers: each sum of 16 x 16-bit products over a
burst fits 47 bits, the DSP48E2's accumulator.  The estimator then works on
integers too and is defined to the last bit here:

- magnitude: floor(sqrt(I^2 + Q^2)), from the exact 94-bit power;
- integer peak: the lag of the largest power (the first one on a tie);
- sub-sample position: 0.5 (l - r) / (l - 2c + r) through the magnitudes at the
  peak and its neighbours, as ``rf_clock.correlator.find_peak`` does, in 2^-32
  sample rounded half away from zero (0 at the window's edge or without a
  downward curvature) and kept in i32: +0.5 sample is 2^31 - 1;
- carrier phase: atan2(Q, I) at the integer peak by CORDIC in 2^-32 turn, after
  the larger of |I| and |Q| is normalized to 46 bits (one leading-zero count and a
  shift), 30 vectoring iterations with arithmetic shifts.

These are to match the ARM's float64 ``estimate_delay`` on the same window and
reference within 0.01 ps of envelope and 0.001 ps of phase (plan section 6, P8).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FRACTION_BITS = 32  # the sub-sample position, in 2^-32 sample
PHASE_BITS = 32  # the carrier phase, in 2^-32 turn
CORDIC_ITERATIONS = 30
CORDIC_NORM_BITS = 46  # the larger of |I|, |Q| is shifted to this many bits before CORDIC
CORDIC_ATAN = [round(math.atan(2.0 ** -k) / (2 * math.pi) * 2 ** PHASE_BITS) for k in range(CORDIC_ITERATIONS)]
SAMPLE_PS = 1250.0  # one sample at 800 MS/s
GUARD = 16  # lags each side of a peak that belong to its main lobe (two chips)


@dataclass(frozen=True)
class Peak:
    """One correlation peak as the PL reports it."""

    lag: int  # integer lag: the reference starts at window sample ``lag``
    fraction: int  # sub-sample position, 2^-32 sample, -2^31 .. 2^31 - 1
    phase: int  # carrier phase at ``lag``, 2^-32 turn, -2^31 .. 2^31 - 1
    magnitude: int  # floor(sqrt(I^2 + Q^2)) at ``lag``
    c_i: int  # the correlation at ``lag``: the carrier phase reference of P9's despreader
    c_q: int

    @property
    def index(self) -> float:
        """The fractional lag in samples, as ``rf_clock.correlator.Peak.index``."""
        return self.lag + self.fraction / 2 ** FRACTION_BITS

    @property
    def phase_rad(self) -> float:
        return self.phase / 2 ** PHASE_BITS * 2 * math.pi


@dataclass(frozen=True)
class Search:
    """A whole-window search's report (without the window's tick, slot and status)."""

    strongest: Peak
    earliest: Peak  # the first local maximum within the threshold of the strongest
    sidelobe: int  # the largest magnitude more than GUARD lags from the strongest

    @property
    def peak_to_sidelobe_db(self) -> float:
        return 20 * math.log10(self.strongest.magnitude / self.sidelobe) if self.sidelobe else math.inf


def correlate(rx_i: np.ndarray, rx_q: np.ndarray, ref: np.ndarray, first: int = 0,
              count: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """I and Q of sum_n rx[k + n] * ref[n] for lags k = first .. first + count - 1
    (default: every lag where the reference fits), exact in int64."""
    rx_i, rx_q, ref = (np.asarray(a, dtype=np.int64) for a in (rx_i, rx_q, ref))
    last = rx_i.size - ref.size if count is None else first + count - 1
    if first < 0 or last > rx_i.size - ref.size or last < first:
        raise ValueError(f"lags {first} .. {last} do not fit a window of {rx_i.size} and a reference of {ref.size}")
    span = slice(first, last + ref.size)
    return (np.correlate(rx_i[span], ref, mode="valid"), np.correlate(rx_q[span], ref, mode="valid"))


def power(i: int, q: int) -> int:
    return int(i) * int(i) + int(q) * int(q)


def magnitude(i: int, q: int) -> int:
    return math.isqrt(power(i, q))


def subsample(left: int, centre: int, right: int) -> int:
    """0.5 (left - right) / (left - 2 centre + right) in 2^-32 sample, rounded half away
    from zero; 0 unless curved down.  With centre the largest, |left - right| <=
    |curvature|, so the quotient is at most 2^31: one 32-step division of
    |left - right| * 2^31 by |curvature|, rounded up when twice the remainder reaches
    the divisor, then signed (a larger left neighbour puts the peak before ``centre``).
    +2^31 (half a sample after ``centre``, when the right neighbour's magnitude equals
    it) is kept in i32 as 2^31 - 1."""
    curvature = left - 2 * centre + right
    if curvature >= 0:
        return 0
    difference = left - right
    q, remainder = divmod(abs(difference) << (FRACTION_BITS - 1), -curvature)
    if 2 * remainder >= -curvature:
        q += 1
    return -q if difference > 0 else min(q, 2 ** (FRACTION_BITS - 1) - 1)


def phase(i: int, q: int) -> int:
    """atan2(q, i) in 2^-32 turn, by the CORDIC the PL runs."""
    i, q = int(i), int(q)
    if i == 0 and q == 0:
        return 0
    shift = CORDIC_NORM_BITS - max(abs(i), abs(q)).bit_length()
    x, y = (i << shift, q << shift) if shift >= 0 else (i >> -shift, q >> -shift)
    z = 0
    if x < 0:  # into the right half-plane by -90 or +90 degrees
        x, y, z = (y, -x, 2 ** (PHASE_BITS - 2)) if y >= 0 else (-y, x, -2 ** (PHASE_BITS - 2))
    for k, step in enumerate(CORDIC_ATAN):
        if y > 0:
            x, y, z = x + (y >> k), y - (x >> k), z + step
        else:
            x, y, z = x - (y >> k), y + (x >> k), z - step
    return (z + 2 ** (PHASE_BITS - 1)) % 2 ** PHASE_BITS - 2 ** (PHASE_BITS - 1)


def peak_at(i: np.ndarray, q: np.ndarray, k: int, first: int = 0) -> Peak:
    """The estimate at array index ``k`` of correlations that start at lag ``first``."""
    mags = [magnitude(i[j], q[j]) for j in (k - 1, k, k + 1) if 0 <= j < i.size]
    fraction = subsample(*mags) if 0 < k < i.size - 1 else 0
    return Peak(lag=first + k, fraction=fraction, phase=phase(i[k], q[k]), magnitude=magnitude(i[k], q[k]),
                c_i=int(i[k]), c_q=int(q[k]))


def track(rx_i: np.ndarray, rx_q: np.ndarray, ref: np.ndarray, predicted: int, half: int = 4,
          min_magnitude: int = 0) -> Peak | None:
    """The tracker: lags predicted - half .. predicted + half, with zeros for samples
    beyond the window; None (no lock) when the largest power is at either end of that
    range, so the peak may lie outside it, or its magnitude is below ``min_magnitude``.
    An interior maximum alone is not enough: centred a few chips off the peak, the
    range holds a local maximum of a correlation sidelobe, far weaker than the peak,
    so the lock also needs a magnitude, for example a fraction of the last lock's."""
    i, q = track_correlations(rx_i, rx_q, ref, predicted, half)
    powers = [power(a, b) for a, b in zip(i, q)]
    k = powers.index(max(powers))
    if k in (0, len(powers) - 1):
        return None
    peak = peak_at(i, q, k, predicted - half)
    return peak if peak.magnitude >= min_magnitude else None


def track_correlations(rx_i: np.ndarray, rx_q: np.ndarray, ref: np.ndarray, predicted: int,
                       half: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """I and Q of the tracker's lags predicted - half .. predicted + half, with zeros for
    window samples before 0 or after the window's end."""
    pad = np.zeros(2 * half + ref.size, dtype=np.int64)
    rx_i, rx_q = (np.concatenate([pad, np.asarray(a, dtype=np.int64), pad]) for a in (rx_i, rx_q))
    return correlate(rx_i, rx_q, ref, predicted - half + pad.size, 2 * half + 1)


def search(rx_i: np.ndarray, rx_q: np.ndarray, ref: np.ndarray, threshold: int, guard: int = GUARD) -> Search:
    """The whole-window search, on powers, so that only the reported peaks need a square
    root.  ``threshold`` is in 2^-16 of the strongest magnitude: the earliest peak is the
    first lag whose power is a local maximum (not below either neighbour) with
    power * 2^32 >= threshold^2 * the strongest's power; the sidelobe is the square
    root of the largest power more than ``guard`` lags from the strongest."""
    i, q = correlate(rx_i, rx_q, ref)
    powers = [power(a, b) for a, b in zip(i, q)]
    k = powers.index(max(powers))
    strongest = peak_at(i, q, k)
    level = threshold * threshold * powers[k]
    earliest = strongest
    for j in range(k + 1):
        if (powers[j] * 2 ** 32 >= level
                and (j == 0 or powers[j] >= powers[j - 1]) and (j == len(powers) - 1 or powers[j] >= powers[j + 1])):
            earliest = peak_at(i, q, j)
            break
    outside = [p for j, p in enumerate(powers) if abs(j - k) > guard]
    return Search(strongest=strongest, earliest=earliest, sidelobe=math.isqrt(max(outside)) if outside else 0)


LOG_FRACTION_BITS = 4  # Mitchell's log2: the bits after the leading one
QUALITY_SCALE = 771  # / 1024: from 1/16 log2 unit to 0.25 dB, 40 lg 2 / 16 = 0.7526
SAMPLE_FS = 1_250_000  # one sample in fs
TICK_FS = 10_000_000  # one tick in fs


def log2_mitchell(value: int) -> int:
    """16 floor(log2 v) plus the 4 bits after v's leading one: log2 in 1/16 units,
    at most 0.09 of a unit low (0 for v <= 0)."""
    value = int(value)
    if value <= 0:
        return 0
    n = value.bit_length() - 1
    bits = value >> (n - LOG_FRACTION_BITS) if n >= LOG_FRACTION_BITS else value << (LOG_FRACTION_BITS - n)
    return (n << LOG_FRACTION_BITS) + (bits & ((1 << LOG_FRACTION_BITS) - 1))


def quality(peak_power: int, window_power: int, offset: int) -> int:
    """A measurement's quality in 0.25 dB (docs/ABI.md section 4.10): floor((L(P) - L(S)
    + offset) x 771 / 1024) clamped to 0 .. 255, P = I^2 + Q^2 at the peak, S the
    window's sum of I^2 + Q^2, L the Mitchell log2 in 1/16 units."""
    q = ((log2_mitchell(peak_power) - log2_mitchell(window_power) + int(offset)) * QUALITY_SCALE) >> 10
    return max(0, min(255, q))


def quality_offset(window_samples: int, ref: np.ndarray) -> int:
    """QUALITY_OFFSET for a reference: 16 log2(window samples / sum ref^2), rounded, so
    that the quality is about 4 x 10 lg(peak power / (reference energy x mean sample
    power))."""
    ref = np.asarray(ref, dtype=np.int64)
    return round(16 * math.log2(window_samples / int(np.dot(ref, ref))))


def t_rx_fs(window_tick: int, pps_tick: int, lag: int, fraction: int) -> int:
    """The arrival of a peak in fs after the PPS of the window's second:
    (window tick - PPS tick) x 10^7 + lag x 1 250 000 + floor((fraction x 1 250 000
    + 2^31) / 2^32), the fraction rounded to the nearest fs."""
    return ((int(window_tick) - int(pps_tick)) * TICK_FS + int(lag) * SAMPLE_FS
            + ((int(fraction) * SAMPLE_FS + (1 << (FRACTION_BITS - 1))) >> FRACTION_BITS))
