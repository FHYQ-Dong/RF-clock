from __future__ import annotations

import math

import numpy as np
import pytest

from rf_clock.correlator import estimate_delay
from rf_clock.pl.waveform import WaveformGenerator
from rf_clock.plmodel import correlator as p8
from rf_clock.plmodel import fir


def test_the_integer_fir_is_the_first_waveform_to_a_few_lsb():
    taps = fir.coefficients(16000)
    chips = WaveformGenerator().chips()
    burst = fir.shape(chips, taps)
    first = WaveformGenerator(amplitude=16000).generate()
    assert burst.size == first.size == 4096 * 8 + 73
    assert np.abs(burst.astype(int) - first.astype(int)).max() <= 3


def test_an_amplitude_that_could_overflow_int16_is_refused():
    with pytest.raises(ValueError, match="beyond int16"):
        fir.coefficients(40000)  # the worst phase sums to 39200


def test_a_burst_is_the_prn_a_guard_and_the_packet():
    chips = fir.burst_chips([1, -1, 1], [-1, -1], guard=10)
    assert chips.tolist() == [1, -1, 1] + [0] * 10 + [-1, -1]
    assert fir.burst_chips([1, -1]).tolist() == [1, -1]


@pytest.mark.parametrize("angle", np.linspace(-math.pi, math.pi, 37))
@pytest.mark.parametrize("scale", [3, 1 << 20, 1 << 46])
def test_cordic_phase_is_atan2(angle, scale):
    i, q = round(scale * math.cos(angle)), round(scale * math.sin(angle))
    got = p8.phase(i, q) / 2 ** p8.PHASE_BITS * 2 * math.pi
    assert math.remainder(got - math.atan2(q, i), 2 * math.pi) == pytest.approx(0, abs=1e-8 if scale > 3 else 1e-6)


def test_the_subsample_position_is_the_float_parabola_rounded():
    left, centre, right = 970_001, 1_000_000, 980_007
    exact = 0.5 * (left - right) / (left - 2 * centre + right)
    assert p8.subsample(left, centre, right) == round(exact * 2 ** 32)
    assert p8.subsample(5, 5, 5) == 0  # no downward curvature


def window(lag: int, angle: float, *, copies=(), noise: float = 20.0, size: int = 4000, seed: int = 0):
    """A window with the 256-chip burst at ``lag``, rotated by ``angle``, in integer noise."""
    rng = np.random.default_rng(seed)
    ref = fir.shape(WaveformGenerator().chips(count=256), fir.coefficients(16000)).astype(np.int64)
    x = rng.normal(0, noise, size) + 1j * rng.normal(0, noise, size)
    for at, gain in [(lag, 1.0), *copies]:
        x[at:at + ref.size] += gain * 0.01 * ref * np.exp(1j * angle)
    return np.rint(x.real).astype(np.int64), np.rint(x.imag).astype(np.int64), ref


def test_the_search_finds_the_burst_as_the_float64_estimator_does():
    rx_i, rx_q, ref = window(700, 1.234)
    found = p8.search(rx_i, rx_q, ref, threshold=2 ** 15)
    float64 = estimate_delay(rx_i + 1j * rx_q, ref)
    assert found.strongest.lag == float64.integer_index == 700
    assert (found.strongest.index - float64.index) * p8.SAMPLE_PS == pytest.approx(0, abs=1e-4)
    assert math.remainder(found.strongest.phase_rad - float64.phase, 2 * math.pi) == pytest.approx(0, abs=1e-7)
    assert found.peak_to_sidelobe_db == pytest.approx(float64.peak_to_sidelobe_db, abs=1e-6)
    assert found.earliest == found.strongest


def test_the_earliest_peak_within_the_threshold_comes_first():
    rx_i, rx_q, ref = window(900, 0.3, copies=[(500, 0.7)])  # a weaker copy 400 samples early
    found = p8.search(rx_i, rx_q, ref, threshold=round(0.5 * 2 ** 16))
    assert found.strongest.lag == 900 and abs(found.earliest.index - 500) < 1  # each copy shifts the other a little
    assert p8.search(rx_i, rx_q, ref, threshold=round(0.8 * 2 ** 16)).earliest == found.strongest


def test_the_tracker_gives_the_search_peak_or_no_lock():
    rx_i, rx_q, ref = window(700, -2.0)
    found = p8.search(rx_i, rx_q, ref, threshold=2 ** 15)
    assert p8.track(rx_i, rx_q, ref, predicted=702) == found.strongest
    assert p8.track(rx_i, rx_q, ref, predicted=706) is None  # still rising at 702: the peak lies before the range
    sidelobe = p8.track(rx_i, rx_q, ref, predicted=720)  # 716 .. 724: a sidelobe's local maximum
    assert sidelobe is not None and sidelobe.magnitude < found.strongest.magnitude / 2
    assert p8.track(rx_i, rx_q, ref, predicted=720, min_magnitude=found.strongest.magnitude // 2) is None
