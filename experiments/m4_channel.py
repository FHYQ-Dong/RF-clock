"""M4: the channel each burst went through, from the RX windows the nodes kept.

Problem.  The envelope timing of the correlation peak departs from its carrier
phase: by tens of picoseconds over a night, and after a power cycle by +60 to +86 ps
on A's DAC -> B's ADC and by -19 ps on B's DAC -> A's ADC
(results/m4-twtt-20261021 section 5.6, results/m4-twtt-pair-20261022_105942).  The
two move together as long as the received burst is a delayed copy of the
transmitted one, so the waveform itself changed.  How: a tilt of the amplitude
across the band, a ripple (an echo), or the group delay moving against the phase
delay?  The PRN (100 Mchip/s, RRC 0.25) fills 5.8 GHz +- 62.5 MHz, close to the
edges of the 5725-5875 MHz band-pass filters, where their group delay rises.

From the windows that ``m4_twtt.py --save-every N`` keeps (``rx_<node>/<slot>_<count>.npy``,
800 MS/s complex baseband), each direction's response is estimated from the
correlation c of the received window with the transmitted reference s, averaged over
the windows of a run, as

    H(f) = C(f) / A(f),  over the band where |A(f)| > 5 % of its peak,

with C the spectrum of c, moved by the run's mean envelope peak to lag 0, within
+-128 samples (+-160 ns), and A that of the reference's own autocorrelation over the
same lags, both under one Hann window.  Cutting both alike smooths H to about 6 MHz
(dividing R(f) by S(f) bin by bin would carry the PRN's spectral fine structure as
noise) and keeps echoes up to about 100 ns; moving the peak to lag 0 first keeps the
cut from distorting a delay that falls between samples.  The group delay of H is
then the time against the envelope peak.
It covers everything from the DAC's
samples to the ADC's: both converters' analog parts, the filter, the attenuator,
the cables, and the digital interpolation and decimation, which are the same at
every power-up.  Baseband f is 5.8 GHz + f or - f; which (the spectral inversion,
plan/checklist.md section 4) does not matter here.

    .venv/bin/python experiments/m4_channel.py results/m4-twtt-pair-<time> [more runs]

For each run and direction it prints the envelope peak of the node's records and the
group delay of H (weighted by |A|), both against the same integer
sample, the group delay's spread over the band, and the amplitude's tilt and ripple;
channel.png, next to the first run, shows |H| and the group delay of every run.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

SAMPLE_RATE = 800e6
SAMPLE_PS = 1250.0
HALF = 128  # lags each side of the peak that H is taken from (160 ns)
GRID = 4096  # frequency points of H
BAND = 0.05  # of the peak of |A(f)|
DIRECTIONS = {"AB": "B", "BA": "A"}  # the node that receives each slot
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#8f57d1", "#d64f8a"]  # categorical, in order


def records(run: Path, node: str) -> tuple[float, dict[str, dict]]:
    """The node's PRN amplitude, and its own records of the kept windows by file name."""
    amplitude, kept = 16000.0, {}
    for line in (run / f"twtt_{node}.jsonl").read_text().splitlines():
        e = json.loads(line) if line.strip() else {}
        if e.get("type") == "info":
            amplitude = e.get("amplitude") or amplitude
        if e.get("type") == "local":
            for entry in e["slots"].values():
                if "saved" in entry.get("rx", {}):
                    kept[entry["rx"]["saved"]] = entry["rx"]
    return amplitude, kept


def response(run: Path, slot: str) -> dict | None:
    """The run's mean H(f) of one direction and the envelope peaks of its windows."""
    from rf_clock.pl.waveform import WaveformGenerator

    node = DIRECTIONS[slot]
    amplitude, kept = records(run, node)
    files = sorted((run / f"rx_{node}").glob(f"{slot}_*.npy"))
    if not files:
        return None
    ref = WaveformGenerator(amplitude=amplitude).generate().astype(np.float64)
    peaks = [kept[f.name]["peak"] for f in files if f.name in kept]
    k0 = int(round(statistics.median(peaks)))
    peak = statistics.fmean(peaks)  # the run's envelope peak, samples
    n = 1 << int(np.ceil(np.log2(len(np.load(files[0])) + len(ref))))
    s = np.fft.fft(ref, n)
    to_zero = np.exp(2j * np.pi * np.fft.fftfreq(n) * peak)  # moves that peak to lag 0
    lags = np.arange(-HALF, HALF + 1)
    taper = 0.5 * (1 + np.cos(np.pi * lags / (HALF + 1)))
    c = taper * np.mean([np.fft.ifft(np.fft.fft(np.load(p), n) * np.conj(s) * to_zero)[lags % n] for p in files], axis=0)
    a = taper * np.fft.ifft(np.abs(s) ** 2)[lags % n]  # the reference's autocorrelation
    # Lag 0 first, so that the spectra carry no linear phase of their own.
    big_c, big_a = (np.fft.fft(np.roll(np.pad(x, (0, GRID - x.size)), -HALF)) for x in (c, a))
    f = np.fft.fftfreq(GRID, 1 / SAMPLE_RATE)
    band = np.abs(big_a) > BAND * np.max(np.abs(big_a))
    order = np.argsort(f[band])
    f, h, w = f[band][order], (big_c / big_a)[band][order], np.abs(big_a[band][order])
    phase = np.unwrap(np.angle(h))
    group = -np.gradient(phase, f) / (2 * np.pi) * 1e12 + (peak - k0) * SAMPLE_PS  # ps, from sample k0
    db = 20 * np.log10(np.abs(h) / np.abs(h[np.argmin(np.abs(f))]))
    line = np.polyfit(f, db, 1, w=w)
    return {"f": f, "db": db, "group_ps": group, "windows": len(files),
            "envelope_ps": (peak - k0) * SAMPLE_PS,
            "group_mean_ps": float(np.average(group, weights=w)),
            "group_center_ps": float(np.interp(0.0, f, group)),
            "group_spread_ps": float(np.ptp(group[np.abs(f) < 50e6])),
            "tilt_db_per_100mhz": float(line[0] * 100e6),
            "ripple_db": float(np.ptp((db - np.polyval(line, f))[np.abs(f) < 50e6]))}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("runs", nargs="+", type=Path, help="m4_twtt.py results, each with rx_<node>/")
    args = parser.parse_args()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(10, 6), sharex=True)
    found = False
    for i, run in enumerate(args.runs):
        for col, slot in enumerate(DIRECTIONS):
            r = response(run, slot)
            if r is None:
                print(f"{run.name} {slot}: no kept windows")
                continue
            found = True
            print(f"{run.name} {slot}: {r['windows']} windows; envelope {r['envelope_ps']:+.1f} ps, "
                  f"group delay {r['group_mean_ps']:+.1f} ps (weighted by |A|; at the carrier {r['group_center_ps']:+.1f}, "
                  f"spread {r['group_spread_ps']:.1f} ps over +-50 MHz); tilt {r['tilt_db_per_100mhz']:+.2f} dB/100 MHz, "
                  f"ripple {r['ripple_db']:.2f} dB")
            mhz = r["f"] / 1e6
            axes[0, col].plot(mhz, r["db"], color=COLORS[i % len(COLORS)], lw=1.2, label=run.name)
            axes[1, col].plot(mhz, r["group_ps"] - r["group_center_ps"], color=COLORS[i % len(COLORS)], lw=1.2)
            axes[0, col].set_title(f"{slot}: received by {DIRECTIONS[slot]}", loc="left", fontsize=10)
    if not found:
        sys.exit("no kept windows (m4_twtt.py --save-every N)")
    axes[0, 0].set_ylabel("|H| (dB, 0 at the carrier)")
    axes[1, 0].set_ylabel("group delay − at the carrier (ps)")
    for ax in axes[1]:
        ax.set_xlabel("baseband frequency (MHz)")
    for ax in axes.flat:
        ax.grid(True, color="#e1e0d9", lw=0.6)
    axes[0, 1].legend(frameon=False, fontsize=7)
    out = args.runs[0] / "channel.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"figure: {out}")


if __name__ == "__main__":
    main()
