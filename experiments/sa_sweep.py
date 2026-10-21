"""Sweep a band on the Signal Hound SA124B and summarize it (levels, channel power, peaks).

Bench tool, not an experiment: it measured the DAC output spectrum (checklist
"DAC outputs 5.8 GHz"; results/dac-spectrum-20261017) and serves any later
spectrum check.  Run on RF-clock-PC, where the analyzer is plugged in, as
root, with the project's ``instruments`` dependency group; the driver is
``tanglab_instruments.SA124B`` over the obsolete Linux SA API 3.0.11 (see
results/dac-spectrum-20261017/REPORT.md for the setup and the udev rule)::

    sudo .venv/bin/python experiments/sa_sweep.py 3.7995e9 3.8005e9 --rbw 1e3 --sweeps 3
    sudo .venv/bin/python experiments/sa_sweep.py 3.5e9 4.1e9 --rbw 1e5 --ref -30 --average --sweeps 5 \\
        --channel 3.8e9 125e6 --out prn.npz

Each sweep can lose a few ~1 MHz segments over USB (they read as the floor), so
several sweeps are combined per bin (median by default) and the lost segments of
each are counted.  The analyzer has no hardware image rejection: a signal wider
than ~43 MHz leaves an image (the API's software rejection only works for
continuous signals).
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import numpy as np
from tanglab_instruments import SA124B

# Under sudo, the home of the user who ran it, not /root.
HOME = Path(os.path.expanduser("~" + os.environ.get("SUDO_USER", "")))
LIBRARY = HOME / "ref/signalhound/sa_linux/sdk3011/sa_linux_sdk/lib/libsa_api.so.3.0.11"
SERIAL = 17320349


def lost_segments(stack: np.ndarray, combined: np.ndarray, bin_hz: float) -> list[int]:
    """Per sweep, runs of >= 0.5 MHz more than 10 dB under the combined trace."""
    min_run = max(int(0.5e6 / bin_hz), 3)
    counts = []
    for s in stack:
        low = np.concatenate(([0], (s < combined - 10).astype(np.int8), [0]))
        edges = np.flatnonzero(np.diff(low))
        counts.append(int(np.sum(edges[1::2] - edges[0::2] >= min_run)))
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("start", type=float, help="Hz")
    p.add_argument("stop", type=float, help="Hz")
    p.add_argument("--rbw", type=float, default=1e6)
    p.add_argument("--ref", type=float, default=-10.0, help="reference level, dBm")
    p.add_argument("--sweeps", type=int, default=1, help="combine this many sweeps")
    p.add_argument("--combine", choices=("median", "max"), default="median",
                   help="median drops segments lost over USB; max = max-hold")
    p.add_argument("--average", action="store_true", help="average detector in power units (else min/max)")
    p.add_argument("--no-reject", action="store_true", help="turn off the API's software image rejection")
    p.add_argument("--channel", type=float, nargs=2, metavar=("CENTER", "WIDTH"),
                   help="also print the power integrated over this band, Hz (assumes ENBW = RBW)")
    p.add_argument("--peaks", type=int, default=15)
    p.add_argument("--above", type=float, default=15.0, help="peak threshold above the median, dB")
    p.add_argument("--out", type=Path, help="save f, p (combined), sweeps, combine, rbw, ref as .npz")
    p.add_argument("--library", type=Path, default=LIBRARY)
    a = p.parse_args()

    t0 = time.monotonic()
    with SA124B(SERIAL, library=str(a.library)) as sa:
        sa.configure((a.start + a.stop) / 2, a.stop - a.start, a.rbw, ref_dbm=a.ref,
                     detector="average" if a.average else "minmax", image_reject=not a.no_reject)
        traces = [sa.sweep() for _ in range(a.sweeps)]
    f = traces[0][0]
    stack = np.array([t[2] for t in traces])  # the max row (equal to min with the average detector)
    combined = np.median(stack, axis=0) if a.combine == "median" else stack.max(axis=0)
    bin_hz = float(f[1] - f[0])
    print(f"{len(f)} points, bin {bin_hz / 1e3:.2f} kHz, {a.sweeps} sweep(s) in {time.monotonic() - t0:.1f} s, "
          f"ref {a.ref} dBm, rbw asked {a.rbw / 1e3:g} kHz, combine {a.combine}")
    if a.sweeps > 1:
        print(f"lost segments per sweep: {lost_segments(stack, combined, bin_hz)}")
    if a.out:
        np.savez(a.out, f=f, p=combined, sweeps=stack, combine=a.combine, rbw=a.rbw, ref=a.ref)
    if a.channel:
        c, w = a.channel
        sel = np.abs(f - c) <= w / 2
        mw = 10 ** (combined[sel] / 10)
        total = 10 * np.log10(mw.sum() * bin_hz / a.rbw)
        cum = np.cumsum(mw) / mw.sum()
        obw = f[sel][np.searchsorted(cum, 0.995)] - f[sel][np.searchsorted(cum, 0.005)]
        print(f"channel {c / 1e6:.1f} MHz +- {w / 2e6:.1f} MHz: {total:.1f} dBm; "
              f"99 % occupied bandwidth {obw / 1e6:.1f} MHz")
    floor = float(np.median(combined))
    print(f"median {floor:.1f} dBm; max {combined.max():.1f} dBm at {f[combined.argmax()] / 1e6:.3f} MHz")
    picked: list[int] = []
    for i in np.argsort(combined)[::-1]:
        if combined[i] < floor + a.above or len(picked) >= a.peaks:
            break
        if all(abs(f[i] - f[j]) > max(20 * bin_hz, 5e6) for j in picked):
            picked.append(int(i))
    for i in sorted(picked, key=lambda k: f[k]):
        print(f"  {f[i] / 1e6:11.3f} MHz  {combined[i]:6.1f} dBm")


if __name__ == "__main__":
    main()
