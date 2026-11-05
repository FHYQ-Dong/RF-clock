"""Initial acquisition (plan section 5.2): the peer's PPS from a long window in DDR.

Before its slot engine runs, a node does not know where the peer's PPS lies: the
two FS725s run free, so their seconds can be up to half a second apart, and the
correlator's window covers 13.8 us.  So the node first captures a long window to
DDR through P4 and the S2MM (its slot engine off), finds the peer's bursts in it by
correlation on the ARM, despreads a burst's measurement packet with
``rf_clock.plmodel``, and from the packet's T_tx (the burst's start after the
peer's PPS) and the burst's arrival in its own ticks puts the peer's PPS on its
own time axis, to within the one-way delay it assumes.  ``place_pulse_ns`` turns
the offset into the FS725's PP argument that moves this node's PPS onto the
peer's; afterwards both nodes' slots line up and each node's window, opened a
fixed lead before the expected arrival, holds the other's bursts.

A long window of 11 ms holds at least two of the peer's bursts (one every 4 ms),
whatever the offset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import abi
from ..plmodel import correlator as p8
from ..plmodel import fir
from ..plmodel import packet as p9

TICKS_PER_SECOND = 100_000_000
SAMPLES_PER_TICK = fir.SPS
LONG_WINDOW_BEATS = 1_100_000  # 11 ms
CHUNK = 1 << 20  # correlation samples per FFT block


@dataclass(frozen=True)
class Burst:
    lag: int  # the burst's start, samples after the window's start
    fraction: int  # 2^-32 sample
    magnitude: int
    c_i: int
    c_q: int
    packet: p9.Measurement | None  # None: not decoded (CRC failed or cut off by the window's end)


@dataclass(frozen=True)
class Acquisition:
    window_tick: int  # RX_ACTUAL of the long window
    own_pps_tick: int  # this node's latest PPS_TICK at the capture
    own_pps_count: int
    bursts: list[Burst] = field(default_factory=list)
    peer_pps_tick: float | None = None  # the peer's PPS in this node's ticks, less the assumed delay
    offset_ticks: float | None = None  # peer PPS - own PPS, folded into -0.5 s .. 0.5 s
    peer_pps_count: int | None = None  # the peer's count of the second of the decoded burst
    spread_ticks: float | None = None  # largest disagreement between the decoded bursts

    @property
    def offset_ns(self) -> float | None:
        return None if self.offset_ticks is None else self.offset_ticks * 10.0


def iq_of(buffer, beats: int) -> tuple[np.ndarray, np.ndarray]:
    """I and Q (int16) of DMA beats in RX_LAYOUT: lane n of I at bits 16 n, of Q at 128 + 16 n."""
    words = np.frombuffer(bytes(buffer[: beats * 32]), dtype="<i2").reshape(beats, 16)
    return words[:, :8].ravel().copy(), words[:, 8:].ravel().copy()


def capture(pl, beats: int = LONG_WINDOW_BEATS, lead_ticks: int = 2_000_000) -> tuple[int, np.ndarray, np.ndarray]:
    """A window of ``beats`` through P4 into DDR, ``lead_ticks`` from now (the slot engine must be off).
    Returns its start tick and the I and Q samples."""
    pl.rx_dma.prepare(beats)
    target = pl.ctrl.read64(abi.ABI.REG.TICK_LO) + lead_ticks
    pl.ctrl.arm_rx(target, beats)
    window = pl.ctrl.wait_rx(timeout=1.0 + (lead_ticks + beats) / TICKS_PER_SECOND)
    data = pl.rx_dma.wait(window.beats, timeout=2.0)
    rx_i, rx_q = iq_of(data.buffer, window.beats)
    return window.actual, rx_i, rx_q


def correlation_power(rx_i: np.ndarray, rx_q: np.ndarray, ref: np.ndarray, chunk: int = CHUNK) -> np.ndarray:
    """|sum_n rx[k + n] ref[n]|^2 for every lag k where the reference fits, by FFT blocks (float32)."""
    x = rx_i.astype(np.float32) + 1j * rx_q.astype(np.float32)
    r = ref.size
    lags = x.size - r + 1
    nfft = 1 << int(np.ceil(np.log2(chunk + r - 1)))
    h = np.conj(np.fft.fft(ref.astype(np.float32), nfft))
    out = np.empty(lags, dtype=np.float32)
    for start in range(0, lags, chunk):
        count = min(chunk, lags - start)
        seg = x[start:start + count + r - 1]
        c = np.fft.ifft(np.fft.fft(seg, nfft) * h)[:count]
        out[start:start + count] = (c.real * c.real + c.imag * c.imag).astype(np.float32)
    return out


def find_peaks(power: np.ndarray, ref_size: int, *, floor_db: float = 10.0, limit: int = 8) -> list[int]:
    """The lags of the strongest peaks, at least a reference apart, down to ``floor_db`` under the strongest."""
    p = power.copy()
    strongest = float(p.max())
    peaks = []
    while len(peaks) < limit:
        k = int(np.argmax(p))
        if p[k] < strongest / 10 ** (floor_db / 10):
            break
        peaks.append(k)
        p[max(0, k - ref_size):k + ref_size] = 0
    return sorted(peaks)


def decode_burst(rx_i: np.ndarray, rx_q: np.ndarray, lag: int, ref: np.ndarray, cref: np.ndarray,
                 prn_chips: int, guard: int = p9.GUARD_CHIPS) -> Burst:
    """The exact estimate near ``lag`` (P8's: the largest exact power within two lags) and the
    packet behind the PRN (P9's despreader, with the burst's own correlation as the carrier
    phase reference)."""
    first = max(lag - 2, 0)
    count = min(lag + 3, rx_i.size - ref.size + 1) - first
    span_i, span_q = rx_i[first:first + count + ref.size - 1], rx_q[first:first + count + ref.size - 1]
    ci, cq = p8.correlate(span_i, span_q, ref, 0, count)
    powers = [p8.power(a, b) for a, b in zip(ci, cq)]
    peak = p8.peak_at(ci, cq, powers.index(max(powers)), first)
    start = lag + fir.SPS * (prn_chips + guard)
    span = fir.SPS * p9.SPREAD * p9.PACKET_BITS + p9.PULSE_PEAK + fir.SPS
    packet = None
    if start + span <= rx_i.size:
        bits = p9.despread(rx_i, rx_q, start, peak.c_i, peak.c_q, cref)
        packet = p9.unpack(p9.from_bits(bits))
    return Burst(lag=peak.lag, fraction=peak.fraction, magnitude=peak.magnitude, c_i=peak.c_i, c_q=peak.c_q,
                 packet=packet)


def locate(window_tick: int, own_pps_tick: int, own_pps_count: int, bursts: list[Burst],
           arrival_ticks: float) -> Acquisition:
    """The peer's PPS from the decoded bursts: each burst arrived at window_tick + lag / 8 ticks,
    T_tx after the peer's PPS and ``arrival_ticks`` (the assumed one-way delay) earlier."""
    estimates = []
    for b in bursts:
        if b.packet is None:
            continue
        arrival = window_tick + (b.lag + b.fraction / 2 ** p8.FRACTION_BITS) / SAMPLES_PER_TICK
        estimates.append((arrival - b.packet.t_tx_fs / 1e7 - arrival_ticks, b.packet.pps_count))
    if not estimates:
        return Acquisition(window_tick, own_pps_tick, own_pps_count, bursts)
    pps, count = estimates[0]
    folded = [((e - own_pps_tick + TICKS_PER_SECOND / 2) % TICKS_PER_SECOND) - TICKS_PER_SECOND / 2
              for e, _ in estimates]
    return Acquisition(window_tick, own_pps_tick, own_pps_count, bursts, peer_pps_tick=pps,
                       offset_ticks=folded[0], peer_pps_count=count, spread_ticks=max(folded) - min(folded))


def acquire(pl, prn: np.ndarray, taps: np.ndarray, *, beats: int = LONG_WINDOW_BEATS,
            arrival_ticks: float = 29.0) -> Acquisition:
    """Capture a long window and locate the peer's PPS in it (see the module docstring)."""
    ref = fir.shape(prn, taps).astype(np.int64)
    cref = p9.code_reference(taps)
    window_tick, rx_i, rx_q = capture(pl, beats)
    own_count, own_tick = pl.ctrl.latest_pps()
    power = correlation_power(rx_i, rx_q, ref)
    bursts = [decode_burst(rx_i, rx_q, k, ref, cref, len(prn)) for k in find_peaks(power, ref.size)]
    if own_tick > window_tick:  # a PPS edge after the window started: keep the one before it
        own_tick -= TICKS_PER_SECOND
        own_count -= 1
    return locate(window_tick, own_tick, own_count, bursts, arrival_ticks)


def place_pulse_ns(offset_ns: float) -> int | None:
    """The FS725 PP argument (move the 1 pps output earlier by that many ns, 1 .. 999 999 999)
    that moves this node's PPS by ``offset_ns`` (positive: later) rounded to whole ticks, or
    None under half a tick.

    Whole ticks, because the PL captures the PPS on its tick: a move by a whole number of
    ticks moves PPS_TICK by exactly that many and leaves the edge where it was within its
    tick, so its capture margin and its sub-tick phase (plan section 8) stay as they were.
    A move of 7234 ns moved PPS_TICK by 723 ticks on the bench (2026-11-03) and left the
    other 4 ns, unseen, in the edge's place within the tick.  The rest of the offset, at
    most half a tick, stays in theta, which the steering takes out."""
    ticks = int(round(offset_ns / 10))
    if ticks == 0:
        return None
    move = 10 * ticks
    return 1_000_000_000 - move if move > 0 else -move
