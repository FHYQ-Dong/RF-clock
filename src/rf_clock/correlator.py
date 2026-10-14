"""Delay estimation by cross-correlation (plan section 7, ``correlator``).

Non-coherent: correlate the complex RX samples with the known TX reference,
take the magnitude, find the peak and refine it to a fraction of a sample.
Between two FS725-disciplined nodes the carrier offset is about 0.06 Hz at
5.8 GHz (plan section 4.3), so no carrier recovery is needed within a burst.

The fractional part comes from a parabola through the three largest
correlation magnitudes.  For the default RRC waveform its bias is at most
0.002 sample (2.5 ps) over the whole fraction range (simulated, noiseless);
noise and the analog channel dominate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Peak:
    index: float  # fractional lag (samples): the reference starts at rx[index]
    integer_index: int
    magnitude: float  # |correlation| at the integer peak
    peak_to_sidelobe: float  # peak / largest |correlation| outside +-guard samples
    phase: float  # angle of the correlation at the integer peak, radians

    @property
    def peak_to_sidelobe_db(self) -> float:
        return float(20 * np.log10(self.peak_to_sidelobe))


def correlate(rx, ref) -> np.ndarray:
    """Complex cross-correlation for every lag where ``ref`` fits in ``rx``.

    ``c[k] = sum_n rx[k + n] * conj(ref[n])`` for ``k = 0 .. len(rx) - len(ref)``,
    computed with zero-padded FFTs.
    """
    rx = np.asarray(rx, dtype=np.complex128)
    ref = np.asarray(ref, dtype=np.complex128)
    if ref.ndim != 1 or rx.ndim != 1:
        raise ValueError("rx and ref must be one-dimensional")
    if not 0 < ref.size <= rx.size:
        raise ValueError("ref must be non-empty and no longer than rx")
    n = 1 << int(np.ceil(np.log2(rx.size + ref.size - 1)))
    spectrum = np.fft.fft(rx, n) * np.conj(np.fft.fft(ref, n))
    return np.fft.ifft(spectrum)[: rx.size - ref.size + 1]


def find_peak(corr, *, guard: int = 16) -> Peak:
    """Largest |corr|, parabola-refined, with its peak-to-sidelobe ratio.

    ``guard`` samples on each side of the peak belong to the main lobe and
    are excluded from the sidelobe search (16 = two chips at 8 samples/chip).
    """
    corr = np.asarray(corr)
    magnitude = np.abs(corr)
    k = int(np.argmax(magnitude))
    fraction = 0.0
    if 0 < k < magnitude.size - 1:
        left, centre, right = magnitude[k - 1], magnitude[k], magnitude[k + 1]
        curvature = left - 2 * centre + right
        if curvature < 0:
            fraction = float(0.5 * (left - right) / curvature)
    sidelobes = magnitude.copy()
    sidelobes[max(0, k - guard): k + guard + 1] = 0
    largest_sidelobe = float(sidelobes.max()) if sidelobes.size else 0.0
    return Peak(
        index=k + fraction,
        integer_index=k,
        magnitude=float(magnitude[k]),
        peak_to_sidelobe=float(magnitude[k] / largest_sidelobe) if largest_sidelobe > 0 else float("inf"),
        phase=float(np.angle(corr[k])),
    )


def estimate_delay(rx, ref, *, guard: int = 16) -> Peak:
    """Where ``ref`` starts in ``rx``, in samples (fractional)."""
    return find_peak(correlate(rx, ref), guard=guard)
