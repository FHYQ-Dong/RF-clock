from __future__ import annotations

import numpy as np
import pytest

from rf_clock.correlator import correlate, estimate_delay
from rf_clock.pl.waveform import WaveformGenerator


def delayed(signal: np.ndarray, length: int, delay: float, phase: float) -> np.ndarray:
    """``signal`` delayed by a (fractional) number of samples inside ``length``,
    using a frequency-domain phase ramp; the RRC waveform is band-limited."""
    padded = np.zeros(length, dtype=np.complex128)
    padded[: signal.size] = signal
    freqs = np.fft.fftfreq(length)
    shifted = np.fft.ifft(np.fft.fft(padded) * np.exp(-2j * np.pi * freqs * delay))
    return shifted * np.exp(1j * phase)


@pytest.fixture(scope="module")
def reference() -> np.ndarray:
    return WaveformGenerator().generate().astype(np.float64)


def test_correlate_matches_direct_sum():
    rng = np.random.default_rng(1)
    rx = rng.normal(size=40) + 1j * rng.normal(size=40)
    ref = rng.normal(size=7) + 1j * rng.normal(size=7)
    direct = [np.sum(rx[k : k + 7] * np.conj(ref)) for k in range(34)]
    assert np.allclose(correlate(rx, ref), direct)


@pytest.mark.parametrize("delay", [1234.0, 1234.3, 2001.75])
def test_estimate_delay_recovers_fractional_delay(reference, delay):
    rng = np.random.default_rng(2)
    rx = delayed(reference, reference.size + 4096, delay, phase=0.7)
    rx += 0.05 * np.abs(reference).max() * (rng.normal(size=rx.size) + 1j * rng.normal(size=rx.size))
    peak = estimate_delay(rx, reference)
    assert abs(peak.index - delay) < 0.1
    assert peak.peak_to_sidelobe > 10
    assert abs(np.angle(np.exp(1j * (peak.phase - 0.7)))) < 0.2


def test_ref_longer_than_rx_is_rejected(reference):
    with pytest.raises(ValueError):
        correlate(reference[:10], reference[:20])
