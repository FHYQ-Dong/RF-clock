"""Test vectors for the 2.0 PL testbenches, from rf_clock.plmodel (the bit-exact models).

Usage: python PL/sim/gen_vectors.py OUT_DIR

Each file holds one hexadecimal value per line, as $readmemh reads it; a testbench
knows how many lines make one vector.  The values come from the same functions the
Python side uses, so a passing testbench means the RTL equals the model to the bit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from rf_clock.plmodel import correlator as p8
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9

W = 47  # correlation width
SEED = 20261102


def hexval(value: int, bits: int) -> str:
    """Two's complement of ``value`` in ``bits`` bits, as hex digits."""
    return format(int(value) & ((1 << bits) - 1), f"0{(bits + 3) // 4}x")


def write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def prn(count: int, rng: np.random.Generator) -> np.ndarray:
    return rng.choice((-1, 1), size=count).astype(np.int64)


def channel(ref: np.ndarray, length: int, delay: float, amplitude: float, noise: float, phase: float,
            rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """A window of ``length`` samples holding ``ref`` delayed by ``delay`` samples (linear
    interpolation is enough for a test), rotated by ``phase``, with Gaussian noise; int16."""
    t = np.arange(length) - delay
    sig = np.interp(t, np.arange(ref.size), ref.astype(np.float64), left=0.0, right=0.0) * amplitude
    i = sig * np.cos(phase) + rng.normal(0, noise, length)
    q = sig * np.sin(phase) + rng.normal(0, noise, length)
    return (np.clip(np.rint(i), -32768, 32767).astype(np.int64), np.clip(np.rint(q), -32768, 32767).astype(np.int64))


# ---------------------------------------------------------------------------
# p_estimator: l, c, r (I, Q), edge -> magnitude, fraction, phase, power
# ---------------------------------------------------------------------------
EST_FIELDS = 11  # l_i l_q c_i c_q r_i r_q edge | magnitude fraction phase power


def estimator_cases(rng: np.random.Generator) -> list[tuple[int, int, int, int, int, int, int]]:
    cases: list[tuple[int, int, int, int, int, int, int]] = []
    # Real peaks: a PRN through the FIR, delayed, correlated.
    taps = fir.coefficients(16000)
    ref = fir.shape(prn(256, rng), taps).astype(np.int64)
    for _ in range(40):
        delay = rng.uniform(5, 50)
        rx_i, rx_q = channel(ref, ref.size + 64, delay, rng.uniform(0.05, 1.0), rng.uniform(10, 2000),
                             rng.uniform(-np.pi, np.pi), rng)
        ci, cq = p8.correlate(rx_i, rx_q, ref)
        k = int(np.argmax(ci.astype(object) ** 2 + cq.astype(object) ** 2))
        if 0 < k < ci.size - 1:
            cases.append((int(ci[k - 1]), int(cq[k - 1]), int(ci[k]), int(cq[k]), int(ci[k + 1]), int(cq[k + 1]), 0))
    # Random values over the whole range, the centre the largest.
    lim = 1 << (W - 1)
    for _ in range(60):
        scale = 1 << int(rng.integers(1, W - 1))
        vals = [int(rng.integers(-scale, scale)) for _ in range(6)]
        mags = [p8.magnitude(vals[0], vals[1]), p8.magnitude(vals[2], vals[3]), p8.magnitude(vals[4], vals[5])]
        j = int(np.argmax(mags))
        pairs = [(vals[0], vals[1]), (vals[2], vals[3]), (vals[4], vals[5])]
        pairs[1], pairs[j] = pairs[j], pairs[1]
        cases.append((*pairs[0], *pairs[1], *pairs[2], 0))
    # Edges: extremes, zero, equal magnitudes (+0.5 sample saturates), flat, an edge peak.
    cases += [
        (0, 0, 0, 0, 0, 0, 0),
        (-lim, 0, -lim, -lim, 0, -lim, 0),
        (lim - 1, lim - 1, -lim, -lim, -lim, lim - 1, 0),
        (100, 0, 300, 0, 300, 0, 0),   # right neighbour as large: +2^31 kept as 2^31 - 1
        (300, 0, 300, 0, 100, 0, 0),   # left as large: -2^31
        (5, 5, 5, 5, 5, 5, 0),         # no curvature
        (1, 0, 2, 0, 1, 0, 0),
        (0, -7, 0, 9, 3, 0, 0),
        (-12345678, 87654321, -2, 99999999, 1, 2, 1),  # edge: fraction 0
        (0, 0, -1, 0, 0, 0, 0),        # phase of -1: -0.5 turn
        (0, 0, 0, -1, 0, 0, 0),
        (0, 0, 1, -1, 0, 0, 0),
    ]
    return cases


def estimator_vectors(rng: np.random.Generator) -> list[str]:
    lines: list[str] = []
    for l_i, l_q, c_i, c_q, r_i, r_q, edge in estimator_cases(rng):
        ml, mc, mr = p8.magnitude(l_i, l_q), p8.magnitude(c_i, c_q), p8.magnitude(r_i, r_q)
        fraction = 0 if edge else p8.subsample(ml, mc, mr)
        lines += [hexval(v, W) for v in (l_i, l_q, c_i, c_q, r_i, r_q)]
        lines += [hexval(edge, 1), hexval(mc, W), hexval(fraction, 32), hexval(p8.phase(c_i, c_q), 32),
                  hexval(p8.power(c_i, c_q), 2 * W)]
    return lines


# ---------------------------------------------------------------------------
# p3_burst_gen: PRN, packet code, taps, packet -> the DAC samples
# ---------------------------------------------------------------------------
BURST_PRN = 300
BURST_GUARD = 10


def packed_words(chips: np.ndarray) -> list[int]:
    """Chip n in bit n % 32 of word n / 32, 1 for +1 (docs/ABI.md 4.9)."""
    bits = (np.asarray(chips) > 0).astype(np.int64)
    bits = np.concatenate([bits, np.zeros((-bits.size) % 32, dtype=np.int64)])
    return [int(sum(int(b) << k for k, b in enumerate(bits[w * 32:(w + 1) * 32]))) for w in range(bits.size // 32)]


def burst_vectors(rng: np.random.Generator, out: Path) -> None:
    chips = prn(BURST_PRN, rng)
    code = p9.code_chips(p9.PACKET_BITS * p9.SPREAD)
    taps = fir.coefficients(16000)
    data = bytes(int(b) for b in rng.integers(0, 256, p9.PACKET_BYTES))
    plain = fir.shape(fir.burst_chips(chips), taps).astype(np.int64)
    full = fir.shape(fir.burst_chips(chips, p9.spread(data), BURST_GUARD), taps).astype(np.int64)
    beats_plain = BURST_PRN + 10
    beats_full = BURST_PRN + BURST_GUARD + p9.PACKET_BITS * p9.SPREAD + 10
    plain = np.concatenate([plain, np.zeros(8 * beats_plain - plain.size, dtype=np.int64)])
    full = np.concatenate([full, np.zeros(8 * beats_full - full.size, dtype=np.int64)])
    packet_bits = int.from_bytes(data, "big")
    write(out / "burst_cfg.hex", [hexval(v, 32) for v in (BURST_PRN, BURST_GUARD, beats_plain, beats_full)])
    write(out / "burst_prn.hex", [hexval(w, 32) for w in packed_words(chips)])
    write(out / "burst_code.hex", [hexval(w, 32) for w in packed_words(code)])
    write(out / "burst_taps.hex", [hexval(t, 16) for t in taps])
    write(out / "burst_packet.hex", [hexval(packet_bits, p9.PACKET_BITS)])
    write(out / "burst_plain.hex", [hexval(v, 16) for v in plain])
    write(out / "burst_full.hex", [hexval(v, 16) for v in full])


# ---------------------------------------------------------------------------
# p8_tracker: a window, lag0 -> the 9 correlations and the estimate
# ---------------------------------------------------------------------------
TRK_PRN = 300
TRK_BEATS = 400  # window beats: 3200 samples, 728 lags
TRK_FIELDS = 1 + 18 + 6


def window_beats_hex(rx_i: np.ndarray, rx_q: np.ndarray) -> list[str]:
    """Beats in the ADC layout: I lane n at bits 16 n, Q lane n at bits 128 + 16 n."""
    lines = []
    for b in range(rx_i.size // 8):
        word = 0
        for n in range(8):
            word |= (int(rx_i[8 * b + n]) & 0xFFFF) << (16 * n)
            word |= (int(rx_q[8 * b + n]) & 0xFFFF) << (128 + 16 * n)
        lines.append(format(word, "064x"))
    return lines


def tracker_vectors(rng: np.random.Generator, out: Path) -> None:
    chips = prn(TRK_PRN, rng)
    taps = fir.coefficients(16000)
    ref = fir.shape(chips, taps).astype(np.int64)
    samples = 8 * TRK_BEATS
    lags = samples - ref.size + 1
    rx_i, rx_q = channel(ref, samples, 100.3, 0.4, 900, 2.0, rng)
    cases = [100, 101, 97, 104, 106, 120, 0, 1, 3, 4, lags - 1, lags + 2, 8, 9, 15]
    lines: list[str] = []
    for lag0 in cases:
        i, q = p8.track_correlations(rx_i, rx_q, ref, lag0)
        powers = [p8.power(a, b) for a, b in zip(i, q)]
        k = powers.index(max(powers))
        peak = p8.peak_at(i, q, k, lag0 - 4)
        lines.append(hexval(lag0, 16))
        for a, b in zip(i, q):
            lines += [hexval(a, W), hexval(b, W)]
        lines += [hexval(peak.lag, 16), hexval(int(0 < k < 8), 1), hexval(peak.magnitude, W),
                  hexval(peak.fraction, 32), hexval(peak.phase, 32), hexval(p8.power(peak.c_i, peak.c_q), 2 * W)]
    write(out / "trk_cfg.hex", [hexval(v, 32) for v in (TRK_PRN, TRK_BEATS, len(cases))])
    write(out / "trk_prn.hex", [hexval(w, 32) for w in packed_words(chips)])
    write(out / "trk_taps.hex", [hexval(t, 16) for t in taps])
    write(out / "trk_window.hex", window_beats_hex(rx_i, rx_q))
    write(out / "trk_cases.hex", lines)


# ---------------------------------------------------------------------------
# p8_search: windows -> the report (strongest, earliest, sidelobe)
# ---------------------------------------------------------------------------
SRC_FIELDS = 1 + 9  # threshold | s_lag s_frac s_phase s_mag e_lag e_frac e_phase e_mag sidelobe


def search_vectors(rng: np.random.Generator, out: Path) -> None:
    chips = prn(TRK_PRN, rng)
    taps = fir.coefficients(16000)
    ref = fir.shape(chips, taps).astype(np.int64)
    samples = 8 * TRK_BEATS
    lags = samples - ref.size + 1
    windows = []
    a_i, a_q = channel(ref, samples, 150.4, 0.45, 700, 1.0, rng)         # direct path
    b_i, b_q = channel(ref, samples, 190.7, 0.6, 1, 2.5, rng)            # stronger delayed copy
    windows.append((a_i + b_i, a_q + b_q))
    windows.append(channel(ref, samples, 0.0, 0.5, 300, -1.0, rng))      # peak at lag 0
    windows.append(channel(ref, samples, lags - 1.0, 0.5, 300, 0.3, rng))  # peak at the last lag
    windows.append(channel(ref, samples, 0.0, 0.0, 3000, 0.0, rng))      # noise only
    plan = [(0, 0x8000), (0, 0xF000), (1, 0x8000), (2, 0x8000), (3, 0x4000)]
    lines: list[str] = []
    for w, threshold in plan:
        found = p8.search(*windows[w], ref, threshold)
        lines.append(hexval(threshold, 16))
        for peak in (found.strongest, found.earliest):
            lines += [hexval(peak.lag, 16), hexval(peak.fraction, 32), hexval(peak.phase, 32), hexval(peak.magnitude, W)]
        lines.append(hexval(found.sidelobe, W))
    write(out / "src_cfg.hex", [hexval(v, 32) for v in (TRK_PRN, TRK_BEATS, len(plan), lags)])
    write(out / "src_prn.hex", [hexval(w, 32) for w in packed_words(chips)])
    write(out / "src_taps.hex", [hexval(t, 16) for t in taps])
    write(out / "src_plan.hex", [hexval(w, 32) for w, _ in plan])
    write(out / "src_windows.hex", [line for wi, wq in windows for line in window_beats_hex(wi, wq)])
    write(out / "src_cases.hex", lines)


# ---------------------------------------------------------------------------
# p9_packet: the assembler against pack(), the despreader against despread()
# ---------------------------------------------------------------------------
P9_WB = TRK_BEATS
P9_GUARD = 10


def p9_vectors(rng: np.random.Generator, out: Path) -> None:
    chips = prn(TRK_PRN, rng)
    taps = fir.coefficients(16000)
    ref = fir.shape(chips, taps).astype(np.int64)
    cref = p9.code_reference(taps)
    span = P9_WB + P9_GUARD + p9.PACKET_BITS * p9.SPREAD + 1
    # assembler: fields -> the packet
    ev_tick, sec_tick = 123_456_789_000, 123_400_000_000
    t_tx = (ev_tick - sec_tick) * p8.TICK_FS
    asm = p9.Measurement(config_id=0x12345678, pps_count=4321, slot=77, time_epoch=0x5A, t_tx_fs=t_tx,
                         t_rx_fs=-98765432101234, phase=-123456789, quality=201,
                         status=(1 << 0) | (1 << 1) | (1 << 4) | (1 << 5))   # locked, measurement, PPS, clocks; window bad
    asm_lines = [hexval(v, 64) for v in (asm.config_id, asm.pps_count, asm.slot, asm.time_epoch, ev_tick, sec_tick,
                                          asm.t_rx_fs, asm.phase, asm.quality)]
    asm_lines.append(hexval(int.from_bytes(p9.pack(asm), "big"), p9.PACKET_BITS))
    # despreader: two spans, one clean and one noisy
    span_lines: list[str] = []
    dsp_lines: list[str] = []
    for amplitude, noise, delay, rot in ((0.5, 300, 123.4, 0.7), (0.05, 9000, 201.8, -2.0)):
        data = p9.pack(p9.Measurement(config_id=int(rng.integers(0, 2 ** 32)), pps_count=int(rng.integers(0, 2 ** 32)),
                                      slot=int(rng.integers(0, 500)), time_epoch=int(rng.integers(0, 256)),
                                      t_tx_fs=int(rng.integers(-2 ** 62, 2 ** 62)),
                                      t_rx_fs=int(rng.integers(-2 ** 62, 2 ** 62)),
                                      phase=int(rng.integers(-2 ** 31, 2 ** 31)), quality=int(rng.integers(0, 256)),
                                      status=int(rng.integers(0, 64))))
        burst = fir.shape(fir.burst_chips(chips, p9.spread(data), P9_GUARD), taps).astype(np.int64)
        rx_i, rx_q = channel(burst, 8 * span, delay, amplitude, noise, rot, rng)
        peak = p8.track(rx_i[:8 * P9_WB], rx_q[:8 * P9_WB], ref, int(round(delay)))
        assert peak is not None
        start = peak.lag + (TRK_PRN + P9_GUARD) * fir.SPS
        bits = p9.despread(rx_i, rx_q, start, peak.c_i, peak.c_q, cref)
        decoded = p9.from_bits(bits)
        good = p9.unpack(decoded) is not None
        dsp_lines += [hexval(peak.lag, 16), hexval(peak.c_i, W), hexval(peak.c_q, W),
                      hexval(int.from_bytes(decoded, "big"), p9.PACKET_BITS), hexval(int(good), 1)]
        span_lines += window_beats_hex(rx_i, rx_q)
    write(out / "p9_cfg.hex", [hexval(v, 32) for v in (TRK_PRN, P9_GUARD, span)])
    write(out / "p9_code.hex", [hexval(w, 32) for w in packed_words(p9.code_chips(p9.PACKET_BITS * p9.SPREAD))])
    write(out / "p9_taps.hex", [hexval(t, 16) for t in taps])
    write(out / "p9_asm.hex", asm_lines)
    write(out / "p9_dsp.hex", dsp_lines)
    write(out / "p9_spans.hex", span_lines)


# ---------------------------------------------------------------------------
# p8_correlator re-lock: a full window, a 40 dB weaker one at the same lag, a full one
# ---------------------------------------------------------------------------
def relock_vectors(rng: np.random.Generator, out: Path) -> None:
    chips = prn(TRK_PRN, rng)
    taps = fir.coefficients(16000)
    ref = fir.shape(chips, taps).astype(np.int64)
    samples = 8 * TRK_BEATS
    windows = [channel(ref, samples, 100.3, 0.4, 300, 0.5, rng),
               channel(ref, samples, 100.3, 0.004, 20, 0.5, rng),   # the burst gone but a weak peak left at its lag
               channel(ref, samples, 100.3, 0.4, 300, 0.6, rng)]
    found = [p8.search(*w, ref, 0x8000) for w in windows]
    assert abs(found[1].strongest.lag - found[0].strongest.lag) <= 1
    assert found[1].strongest.magnitude * 2 < found[0].strongest.magnitude
    write(out / "rl_cfg.hex", [hexval(v, 32) for v in (TRK_PRN, TRK_BEATS, found[0].strongest.lag,
                                                        found[1].strongest.lag, found[2].strongest.lag)])
    write(out / "rl_prn.hex", [hexval(w, 32) for w in packed_words(chips)])
    write(out / "rl_taps.hex", [hexval(t, 16) for t in taps])
    write(out / "rl_windows.hex", [line for wi, wq in windows for line in window_beats_hex(wi, wq)])


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    est = estimator_vectors(rng)
    write(out / "est_vectors.hex", est)
    write(out / "est_count.hex", [hexval(len(est) // EST_FIELDS, 32)])
    burst_vectors(rng, out)
    tracker_vectors(rng, out)
    search_vectors(rng, out)
    p9_vectors(rng, out)
    relock_vectors(rng, out)


if __name__ == "__main__":
    main()
