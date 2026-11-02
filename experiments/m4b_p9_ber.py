"""M4b, first step: does P9's coherent despreading reach the bit error rate BPSK should?

Problem.  P9 (plan section 6) despreads the peer's measurement packet with the arrival
time and carrier phase of a burst it correlated before, and the spreading factor L is
to come from the bit error rate at the link's chip SNR (plan section 5.1: about 10 dB
Eb/N0 for 1e-5 before the CRC).  That holds only if the despreader loses little
against ideal coherent BPSK.  This simulates bursts (PRN, guard, a packet of random
fields at L = 64) through P3's FIR (``rf_clock.plmodel.fir``), delays each by a random
fraction of a sample, turns it by a random carrier phase, adds noise for a set Eb/N0
and rounds to integers as the ADC does; P8's tracker (``rf_clock.plmodel.correlator``)
finds the peak on the PRN, and P9 (``rf_clock.plmodel.packet``) despreads the packet
from that peak.  Eb is the energy the despreader correlates per bit (the shaped code
over its 8 L samples), N0 twice the noise variance per component.  The bit and packet
error rates are compared with BPSK's 0.5 erfc(sqrt(Eb/N0)); the gap is the
despreader's loss (the sub-sample delay it ignores, the pulse tails outside each
bit's samples, the rounding).

    .venv/bin/python experiments/m4b_p9_ber.py --packets 200

Results go to results/p9-ber-<time>/summary.json.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from rf_clock.pl.waveform import WaveformGenerator
from rf_clock.plmodel import correlator as p8
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9

ROOT = Path(__file__).resolve().parents[1]
AMPLITUDE = 16000.0  # P3's taps, as the M4 nodes' PRN
GAIN = 0.05  # received amplitude against the transmitted samples: peaks of about 800 LSB
LAG = 300  # where the PRN starts in the window


def random_packet(rng: np.random.Generator) -> bytes:
    m = p9.Measurement(config_id=int(rng.integers(2**32)), pps_count=int(rng.integers(2**32)),
                       slot=int(rng.integers(500)), time_epoch=int(rng.integers(256)),
                       t_tx_fs=int(rng.integers(-2**62, 2**62)), t_rx_fs=int(rng.integers(-2**62, 2**62)),
                       phase=int(rng.integers(-2**31, 2**31)), quality=int(rng.integers(256)),
                       status=int(rng.integers(256)))
    return p9.pack(m)


def received(burst: np.ndarray, delay: float, angle: float, sigma: float, rng: np.random.Generator):
    """The burst at window sample LAG + ``delay`` (fraction by a phase ramp), turned by
    ``angle``, with noise of ``sigma`` per component, rounded to integers."""
    size = LAG + burst.size + 400
    n = 1 << int(np.ceil(np.log2(size)))
    padded = np.zeros(n)
    padded[LAG:LAG + burst.size] = burst
    shifted = np.fft.ifft(np.fft.fft(padded) * np.exp(-2j * np.pi * np.fft.fftfreq(n) * delay)).real[:size]
    x = GAIN * shifted * np.exp(1j * angle) + rng.normal(0, sigma, size) + 1j * rng.normal(0, sigma, size)
    return np.rint(x.real).astype(np.int64), np.rint(x.imag).astype(np.int64)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("--packets", type=int, default=200, help="packets per Eb/N0")
    parser.add_argument("--ebn0", type=float, nargs="+", default=[0, 2, 4, 6, 8, 10], help="dB")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    taps = fir.coefficients(AMPLITUDE)
    prn = WaveformGenerator().chips()
    ref = fir.shape(prn, taps).astype(np.int64)
    code_ref = p9.code_reference(taps)
    span = fir.SPS * p9.SPREAD
    first = p9.PULSE_PEAK - fir.SPS // 2
    e_ref = np.mean([float(np.dot(code_ref[first + span * b:first + span * (b + 1)].astype(float),
                                  code_ref[first + span * b:first + span * (b + 1)].astype(float)))
                     for b in range(p9.PACKET_BITS)])
    eb = GAIN ** 2 * e_ref
    out = ROOT / "results" / f"p9-ber-{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True)
    rows = []
    print(f"{'Eb/N0 dB':>8} {'bits':>8} {'bit err':>8} {'BER':>10} {'BPSK':>10} {'packets':>8} {'bad':>5} {'no lock':>7}")
    for ebn0_db in args.ebn0:
        sigma = math.sqrt(eb / (2 * 10 ** (ebn0_db / 10)))
        bit_errors = bad_packets = no_lock = 0
        for _ in range(args.packets):
            data = random_packet(rng)
            burst = fir.shape(fir.burst_chips(prn, p9.spread(data), guard=p9.GUARD_CHIPS), taps).astype(float)
            delay = float(rng.uniform(-0.5, 0.5))
            rx_i, rx_q = received(burst, delay, float(rng.uniform(-math.pi, math.pi)), sigma, rng)
            peak = p8.track(rx_i, rx_q, ref, predicted=LAG)
            if peak is None:
                no_lock += 1
                bad_packets += 1
                continue
            bits = p9.despread(rx_i, rx_q, p9.packet_start(peak.lag), peak.c_i, peak.c_q, code_ref)
            errors = int(np.count_nonzero(bits != p9.to_bits(data)))
            bit_errors += errors
            bad_packets += p9.unpack(p9.from_bits(bits)) is None
        n_bits = args.packets * p9.PACKET_BITS
        theory = 0.5 * math.erfc(math.sqrt(10 ** (ebn0_db / 10)))
        row = {"ebn0_db": ebn0_db, "bits": n_bits, "bit_errors": bit_errors, "ber": bit_errors / n_bits,
               "ber_bpsk": theory, "packets": args.packets, "bad_packets": bad_packets, "no_lock": no_lock}
        rows.append(row)
        print(f"{ebn0_db:8.1f} {n_bits:8d} {bit_errors:8d} {row['ber']:10.2e} {theory:10.2e} {args.packets:8d} "
              f"{bad_packets:5d} {no_lock:7d}", flush=True)
    summary = {"spreading": p9.SPREAD, "packet_bits": p9.PACKET_BITS, "gain": GAIN, "eb": eb, "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
    print(f"results in {out}")


if __name__ == "__main__":
    main()
