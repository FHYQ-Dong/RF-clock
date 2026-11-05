"""Initial acquisition (rf_clock.pl.acquire) on a synthetic long window."""

from __future__ import annotations

import numpy as np

from rf_clock.pl import acquire as acq
from rf_clock.pl.waveform import generate_test_chips
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9

TICK_FS = 10_000_000


def burst_samples(prn, taps, t_tx_ticks: int, pps_count: int) -> np.ndarray:
    data = p9.pack(p9.Measurement(config_id=7, pps_count=pps_count, slot=0, time_epoch=1, t_tx_fs=t_tx_ticks * TICK_FS,
                                  t_rx_fs=0, phase=0, quality=0, status=0))
    return fir.shape(fir.burst_chips(prn, p9.spread(data), p9.GUARD_CHIPS), taps).astype(np.float64)


def test_two_bursts_give_the_peer_pps():
    rng = np.random.default_rng(4)
    prn = generate_test_chips(4096, 0xACE1)
    taps = fir.coefficients(16000)
    peer_pps, delay, window_tick, own_pps = 12_345, 29, 13_000, 0
    size = 450_000
    x = np.zeros(size)
    for t_tx in (1000, 26_000):  # two bursts, 25 000 ticks apart, as their T_tx say
        start = (peer_pps + t_tx + delay - window_tick) * 8 + 0.3  # samples into the window
        b = burst_samples(prn, taps, t_tx, 41)
        x += np.interp(np.arange(size) - start, np.arange(b.size), b, left=0.0, right=0.0) * 0.5
    phase = np.exp(1j * 0.8)
    rx = x * phase + (rng.normal(0, 200, size) + 1j * rng.normal(0, 200, size))
    rx_i, rx_q = np.rint(rx.real).astype(np.int16), np.rint(rx.imag).astype(np.int16)

    ref = fir.shape(prn, taps).astype(np.int64)
    power = acq.correlation_power(rx_i, rx_q, ref, chunk=1 << 17)
    lags = acq.find_peaks(power, ref.size)
    assert len(lags) == 2
    bursts = [acq.decode_burst(rx_i, rx_q, k, ref, p9.code_reference(taps), 4096) for k in lags]
    assert all(b.packet is not None and b.packet.pps_count == 41 for b in bursts)
    found = acq.locate(window_tick, own_pps, 5, bursts, arrival_ticks=delay)
    assert found.offset_ticks is not None and abs(found.offset_ticks - peer_pps) < 0.05
    assert found.spread_ticks is not None and found.spread_ticks < 0.05
    assert found.peer_pps_count == 41


def test_folding_and_place_pulse():
    b = acq.Burst(lag=0, fraction=0, magnitude=1, c_i=1, c_q=0,
                  packet=p9.Measurement(1, 2, 0, 0, 1000 * TICK_FS, 0, 0, 0, 0))
    # the peer's PPS 0.3 s before ours: folded to -30 000 000 ticks, not +70 000 000
    found = acq.locate(window_tick=70_000_000 + 1000, own_pps_tick=100_000_000, own_pps_count=3, bursts=[b],
                       arrival_ticks=0)
    assert found.offset_ticks == -30_000_000
    assert acq.place_pulse_ns(250.4) == 1_000_000_000 - 250  # later by 250 ns: earlier by 1 s - 250 ns
    assert acq.place_pulse_ns(-12.0) == 10  # whole ticks only
    assert acq.place_pulse_ns(-7234.1) == 7230
    assert acq.place_pulse_ns(4.9) is None
    assert acq.place_pulse_ns(-5.1) == 10
