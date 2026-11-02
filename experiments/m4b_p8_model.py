"""M4b, first step: does the PL correlator's integer estimator equal the ARM's float64?

Problem.  P8 (plan section 6) is to correlate every window in the PL, in integers,
and its estimate must equal the ARM's ``rf_clock.correlator.estimate_delay`` on the
same window and reference within 0.01 ps of envelope (8e-6 sample) and 0.001 ps of
carrier phase (3.6e-5 rad at 5.8 GHz), so that calibrated constants carry over.
``rf_clock.plmodel.correlator`` defines that estimator to the last bit (exact
correlation, integer square root, the sub-sample position rounded to 2^-32 sample,
a 30-iteration CORDIC); this runs it and the float64 estimator on the RX windows the
M4 nodes kept (``--save-every``), with P3's integer reference
(``rf_clock.plmodel.fir``), and reports the largest differences.  The tracker,
centred on the float64 integer peak, must give the same peak as the search.

    .venv/bin/python experiments/m4b_p8_model.py results/m4-twtt-pair-20261026_210437 --limit 50

Results go to results/p8-model-<time>/: windows.jsonl (each window's two estimates)
and summary.json.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from rf_clock.correlator import estimate_delay
from rf_clock.plmodel import correlator as p8
from rf_clock.plmodel import fir
from rf_clock.pl.waveform import WaveformGenerator

ROOT = Path(__file__).resolve().parents[1]
AMPLITUDE = 16000.0  # the M4 nodes' PRN amplitude
CARRIER_HZ = 5.8e9
PS_PER_RAD = 1e12 / (2 * math.pi * CARRIER_HZ)  # 27.4 ps
MIN_PSL_DB = 10.0  # rf_clock.twtt.timing: weaker is no burst


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("runs", nargs="+", type=Path, help="m4_twtt.py results with rx_<node>/")
    parser.add_argument("--limit", type=int, default=0, help="windows per run and node (0: all)")
    parser.add_argument("--threshold", type=float, default=0.5, help="earliest-peak threshold, of the strongest")
    args = parser.parse_args()

    ref = fir.shape(WaveformGenerator().chips(), fir.coefficients(AMPLITUDE)).astype(np.int64)
    out = ROOT / "results" / f"p8-model-{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True)
    rows = []
    with open(out / "windows.jsonl", "w", newline="\n") as f:
        for run in args.runs:
            for node in ("A", "B"):
                files = sorted((run / f"rx_{node}").glob("*.npy"))
                for path in files[: args.limit or None]:
                    w = np.load(path)
                    rx_i, rx_q = w.real.astype(np.int64), w.imag.astype(np.int64)
                    if not (np.array_equal(rx_i, w.real) and np.array_equal(rx_q, w.imag)):
                        sys.exit(f"{path}: not integer samples")
                    t0 = time.perf_counter()
                    ref_float = estimate_delay(w.astype(np.complex128), ref)
                    if ref_float.peak_to_sidelobe_db < MIN_PSL_DB:
                        continue  # no burst in this window
                    model = p8.search(rx_i, rx_q, ref, round(args.threshold * 2 ** 16))
                    tracked = p8.track(rx_i, rx_q, ref, ref_float.integer_index)
                    row = {"window": f"{run.name}/rx_{node}/{path.name}",
                           "float_lag": ref_float.integer_index, "float_index": ref_float.index,
                           "float_phase": ref_float.phase,
                           "float_psl_db": ref_float.peak_to_sidelobe_db,
                           "model_lag": model.strongest.lag, "model_fraction": model.strongest.fraction,
                           "model_phase": model.strongest.phase, "model_psl_db": model.peak_to_sidelobe_db,
                           "earliest_lag": model.earliest.lag,
                           "tracker_equal": tracked == model.strongest,
                           "envelope_ps": (model.strongest.index - ref_float.index) * p8.SAMPLE_PS,
                           "phase_ps": math.remainder(model.strongest.phase_rad - ref_float.phase, 2 * math.pi)
                           * PS_PER_RAD,
                           "seconds": time.perf_counter() - t0}
                    rows.append(row)
                    f.write(json.dumps(row) + "\n")
    if not rows:
        sys.exit("no windows with a burst")
    env = [abs(r["envelope_ps"]) for r in rows]
    pha = [abs(r["phase_ps"]) for r in rows]
    summary = {"windows": len(rows), "same_integer_peak": sum(r["model_lag"] == r["float_lag"] for r in rows),
               "tracker_equal": sum(r["tracker_equal"] for r in rows),
               "earliest_is_strongest": sum(r["earliest_lag"] == r["model_lag"] for r in rows),
               "envelope_ps_max": max(env), "envelope_ps_rms": float(np.sqrt(np.mean(np.square(env)))),
               "phase_ps_max": max(pha), "phase_ps_rms": float(np.sqrt(np.mean(np.square(pha)))),
               "psl_db_max_difference": max(abs(r["model_psl_db"] - r["float_psl_db"]) for r in rows),
               "seconds_per_window": float(np.median([r["seconds"] for r in rows]))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
    print(f"results in {out}")
    for key, value in summary.items():
        print(f"{key:24s} {value}")
    ok = summary["envelope_ps_max"] <= 0.01 and summary["phase_ps_max"] <= 0.001
    print("acceptance (envelope <= 0.01 ps, phase <= 0.001 ps):", "met" if ok else "NOT met")


if __name__ == "__main__":
    main()
