"""Software model of the M3 loopback data path (rf_clock.checks.m3_loopback).

TX beats -> samples -> channel (fractional delay, phase, noise) -> int16 ->
DMA-buffer bytes -> unpack_complex -> correlate.  The model's DAC reads beats
as TX_LAYOUT and the ADC side delivers RX_LAYOUT, as measured on the board.
"""

from __future__ import annotations

import numpy as np

from rf_clock.correlator import estimate_delay
from rf_clock.pl.beats import RX_LAYOUT, TX_LAYOUT, BeatLayout, BeatPacker
from rf_clock.pl.waveform import WaveformGenerator

from rf_clock.checks.m3_loopback import impulse_pattern, locate_impulses


def channel(tx: np.ndarray, length: int, delay: float, phase: float, noise: float, seed: int = 0):
    """Delay (fractional, via FFT phase ramp), rotate, add noise, quantise."""
    padded = np.zeros(length, dtype=np.complex128)
    padded[: tx.size] = tx
    freqs = np.fft.fftfreq(length)
    rx = np.fft.ifft(np.fft.fft(padded) * np.exp(-2j * np.pi * freqs * delay)) * np.exp(1j * phase)
    rng = np.random.default_rng(seed)
    rx += noise * (rng.normal(size=length) + 1j * rng.normal(size=length))
    return np.clip(np.round(rx.real), -32768, 32767), np.clip(np.round(rx.imag), -32768, 32767)


def dma_bytes(i: np.ndarray, q: np.ndarray, packer: BeatPacker) -> bytes:
    return packer.pack_bytes([(int(a), int(b)) for a, b in zip(i, q)])


def test_delay_survives_packing_and_quantisation():
    gen = WaveformGenerator(amplitude=4096)
    tx_beats = gen.generate_beats()
    tx = BeatPacker(TX_LAYOUT).unpack_complex(b"".join(b.to_bytes(32, "little") for b in tx_beats))
    rx_len = (len(tx_beats) + 1250) * 8
    i, q = channel(tx, rx_len, delay=361.37, phase=-1.1, noise=30.0)
    rx = BeatPacker(RX_LAYOUT).unpack_complex(dma_bytes(i, q, BeatPacker(RX_LAYOUT)))
    peak = estimate_delay(rx, gen.generate().astype(np.float64))
    assert abs(peak.index - 361.37) < 0.05
    assert peak.peak_to_sidelobe_db > 15


def run_lane_check(packed_with: BeatLayout, delay: float = 412.6):
    """Impulses packed with ``packed_with``; the DAC plays them as TX_LAYOUT."""
    samples, positions = impulse_pattern(16384)
    data = BeatPacker(packed_with).pack_bytes([(int(v), 0) for v in samples])
    sent = BeatPacker(TX_LAYOUT).unpack_complex(data)  # what the DAC actually plays
    i, q = channel(sent, sent.size + 10_000, delay=delay, phase=0.4, noise=20.0)
    rx = BeatPacker(RX_LAYOUT).unpack_complex(dma_bytes(i, q, BeatPacker(RX_LAYOUT)))
    return [rx_pos - tx for rx_pos, tx in zip(locate_impulses(np.abs(rx), positions), positions)]


def test_lane_check_consistent_with_tx_layout():
    delays = run_lane_check(TX_LAYOUT)
    assert max(delays) - min(delays) < 0.5
    assert abs(np.mean(delays) - 412.6) < 0.5


def test_lane_check_reproduces_planar_packing_on_the_board():
    # Packing TX planar while the DAC reads interleaved moves lane k to sample
    # floor(k/2): delays D, D-1, D-1, D-2, ... as seen on the board.
    delays = np.array(run_lane_check(BeatLayout()))
    expected = 412.6 - np.ceil(np.arange(8) / 2)
    assert np.all(np.abs(delays - expected) < 0.3)
