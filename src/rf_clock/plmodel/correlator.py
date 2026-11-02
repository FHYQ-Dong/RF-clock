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
  downward curvature);
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
    fraction: int  # sub-sample position, 2^-32 sample, |fraction| <= 2^31
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
    the divisor, then signed (a larger left neighbour puts the peak before ``centre``)."""
    curvature = left - 2 * centre + right
    if curvature >= 0:
        return 0
    difference = left - right
    q, remainder = divmod(abs(difference) << (FRACTION_BITS - 1), -curvature)
    if 2 * remainder >= -curvature:
        q += 1
    return -q if difference > 0 else q


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
    """The tracker: lags predicted - half .. predicted + half; None (no lock) when the
    largest power is at either end of that range, so the peak may lie outside it, or
    its magnitude is below ``min_magnitude``.  An interior maximum alone is not enough:
    centred a few chips off the peak, the range holds a local maximum of a correlation
    sidelobe, far weaker than the peak, so the lock also needs a magnitude, for
    example a fraction of the last lock's."""
    i, q = correlate(rx_i, rx_q, ref, predicted - half, 2 * half + 1)
    powers = [power(a, b) for a, b in zip(i, q)]
    k = powers.index(max(powers))
    if k in (0, len(powers) - 1):
        return None
    peak = peak_at(i, q, k, predicted - half)
    return peak if peak.magnitude >= min_magnitude else None


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
