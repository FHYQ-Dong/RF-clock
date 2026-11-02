from __future__ import annotations

import math

import numpy as np
import pytest

from rf_clock.correlator import estimate_delay
from rf_clock.pl.waveform import WaveformGenerator
from rf_clock.plmodel import correlator as p8
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9


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


MEASUREMENT = p9.Measurement(config_id=2134958710, pps_count=1234567, slot=499, time_epoch=3,
                             t_tx_fs=2 * 10**12 + 5, t_rx_fs=-(2**62) + 7, phase=-123456789, quality=88, status=0x5A)


def test_crc16_is_ccitt_false():
    assert p9.crc16(b"123456789") == 0x29B1


def test_a_packet_round_trips_and_a_flipped_bit_fails_its_crc():
    data = p9.pack(MEASUREMENT)
    assert len(data) == p9.PACKET_BYTES == 36
    assert p9.unpack(data) == MEASUREMENT
    assert p9.from_bits(p9.to_bits(data)) == data
    bad = bytearray(data)
    bad[17] ^= 0x04
    assert p9.unpack(bytes(bad)) is None


def test_the_packet_code_is_maximal_and_unlike_the_prn():
    code = p9.code_chips(65535 + 100)
    assert np.array_equal(code[:100], code[65535:])  # period 65535 ...
    assert not np.array_equal(code[:100], code[65535 - 1:65535 + 99])
    prn = WaveformGenerator().chips().astype(float)
    cross = np.correlate(p9.code_chips(p9.PACKET_BITS * p9.SPREAD).astype(float), prn, mode="valid")
    assert np.abs(cross).max() < 0.06 * prn.size  # no stretch of the code looks like the PRN


def burst_window(data: bytes, lag: int, angle: float, noise: float, seed: int = 1):
    """A window holding a burst (PRN, guard, packet) at ``lag``, rotated, in integer noise."""
    taps = fir.coefficients(16000)
    chips = fir.burst_chips(WaveformGenerator().chips(), p9.spread(data), guard=p9.GUARD_CHIPS)
    burst = fir.shape(chips, taps).astype(float)
    rng = np.random.default_rng(seed)
    size = lag + burst.size + 200
    x = rng.normal(0, noise, size) + 1j * rng.normal(0, noise, size)
    x[lag:lag + burst.size] += 0.01 * burst * np.exp(1j * angle)
    return np.rint(x.real).astype(np.int64), np.rint(x.imag).astype(np.int64), taps


@pytest.mark.parametrize("angle", [0.0, 2.0, -2.9])
def test_the_packet_is_despread_coherently_from_the_peak(angle):
    data = p9.pack(MEASUREMENT)
    rx_i, rx_q, taps = burst_window(data, 300, angle, noise=8.0)
    ref = fir.shape(WaveformGenerator().chips(), taps).astype(np.int64)
    peak = p8.track(rx_i, rx_q, ref, predicted=301)
    assert peak is not None and peak.lag == 300
    bits = p9.despread(rx_i, rx_q, p9.packet_start(peak.lag), peak.c_i, peak.c_q, p9.code_reference(taps))
    assert p9.unpack(p9.from_bits(bits)) == MEASUREMENT


def test_the_fraction_is_kept_in_i32():
    assert p8.subsample(100, 300, 300) == 2 ** 31 - 1  # +0.5 sample
    assert p8.subsample(300, 300, 100) == -2 ** 31


def test_the_tracker_takes_samples_beyond_the_window_as_zero():
    rng = np.random.default_rng(3)
    rx_i, rx_q, ref = (rng.integers(-1000, 1000, n) for n in (300, 300, 100))
    i, q = p8.track_correlations(rx_i, rx_q, ref, 1)
    pad_i, pad_q = (np.concatenate([np.zeros(8, dtype=np.int64), a, np.zeros(8, dtype=np.int64)]) for a in (rx_i, rx_q))
    want_i, want_q = p8.correlate(pad_i, pad_q, ref, 8 + 1 - 4, 9)
    assert list(i) == list(want_i) and list(q) == list(want_q)
    end_i, _ = p8.track_correlations(rx_i, rx_q, ref, 200)  # lags 196 .. 204, the last 3 past the window
    assert end_i[0] == p8.correlate(rx_i, rx_q, ref, 196, 1)[0][0]


def test_mitchell_log_and_quality():
    assert [p8.log2_mitchell(v) for v in (0, 1, 2, 3, 24, 1 << 93)] == [0, 0, 16, 24, 64 + 8, 16 * 93]
    assert p8.quality(1 << 60, 1 << 40, 0) == (16 * 20 * 771) >> 10
    assert p8.quality(1, 1 << 40, 0) == 0  # clamped
    assert p8.quality(1 << 93, 1, 1000) == 255


def test_t_rx_rounds_the_fraction_to_the_nearest_fs():
    assert p8.t_rx_fs(1000, 900, 3, 0) == 100 * 10_000_000 + 3 * 1_250_000
    assert p8.t_rx_fs(0, 0, 0, 2 ** 31) == 625_000          # half a sample
    assert p8.t_rx_fs(0, 0, 0, -2 ** 31) == -625_000
    assert p8.t_rx_fs(0, 0, 0, 1717) == 0 and p8.t_rx_fs(0, 0, 0, 1718) == 1  # 0.4997 / 0.5000 fs
