"""M5 preparation: the frequency of a free-running White Rabbit node against the FS725.

Problem.  M5 steers the follower's FS725 to the master's reference through SF, which
reaches only +-2e-9 (``rf_clock.fs725``).  With a free-running White Rabbit node as
the master, its frequency against the FS725 has to lie well inside that range, and
how much it wanders sets the steering's time constant (``experiments/m5_steer_sim.py``).

The HDO9404-MS on RF-clock-PC's USB compares the two PPS: C1 is EPG1 output 4 (the
FS725's PPS, retimed by the EPG1, which is locked to the FS725's 10 MHz; the
trigger), C2 the White Rabbit node's PPS.  One acquisition 2 s wide finds the White
Rabbit edge after the trigger to 1 us; then a window at 2 GS/s, delayed onto that
edge, gives its time after the trigger at every acquisition and follows it as it
drifts.  The scope delays a window by at most 10 000 divisions, so the window is at
least 1/1000 of the delay wide: 200 us (20 us/div) up to a delay of 200 ms.  The edge time goes as -y t, with y the White Rabbit node's frequency
against the FS725 (1e-9 = 1 ns/s).  The scope's timebase only scales the 0.1-1 s
delay, which does not change the slope beyond its own ppm.

Run on RF-clock-PC as root (pyusb), with nothing else using the scope::

    sudo .venv/bin/python experiments/m5_wr_offset.py --minutes 10

Results go to results/m5-wr-offset-<time>/: samples.jsonl (wall time, edge time after
the trigger) and summary.json (y from a straight line, its drift from a parabola, the
residual, and y over each minute).  The scope keeps what this sets: four active
channels, C1 and C2 at 1 V/div, math traces F1 and F2 hidden, the fine timebase with the delay back at 0;
recall a saved setup to go back.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
from tanglab_instruments import HDO9404

from m3_common import results_dir

SCOPE = "USB0::0x05FF::0x1023::4404N30414::INSTR"  # HDO9404-MS on RF-clock-PC's USB
TRIGGER_V = 1.0  # C1: the EPG1's 2.5 V pulse into 50 ohm, as in experiments/m2_restarts.py
FINE_RATE = 2e9  # samples/s in the fine window
MIN_DIV_S = 20e-6  # the fine window's smallest timebase
MAX_DELAY_DIV = 9000  # the scope delays a window by at most 10 000 divisions
# The scope keeps the delay in divisions when the timebase changes: a window delayed by
# 124 ms at 20 us/div (6200 divisions) became 1243 s at 0.2 s/div, and the scope stopped
# answering until its remote interface was switched off and on (2026-10-27).  So the
# delay goes to 0 before the timebase changes, and at the end.


def acquire(scope: HDO9404, timeout_s: int = 5) -> bool:
    """One triggered acquisition; False if no trigger came in ``timeout_s``."""
    return scope.vbs_query(f"app.Acquisition.Acquire({timeout_s}, False)").strip() not in ("0", "False")


def rising_edges(t: np.ndarray, v: np.ndarray, level: float) -> np.ndarray:
    """Times where ``v`` crosses ``level`` upwards, interpolated between samples."""
    i = np.flatnonzero((v[:-1] < level) & (v[1:] >= level))
    return t[i] + (level - v[i]) / (v[i + 1] - v[i]) * (t[i + 1] - t[i])


def set_window(scope: HDO9404, centre_s: float, tolerance_s: float) -> float:
    """Centre the window ``centre_s`` after the trigger; returns where the scope put it
    (it rounds the delay), SystemExit if further than ``tolerance_s`` from that."""
    scope.vbs(f"app.Acquisition.Horizontal.HorOffset = {-centre_s:.12g}")
    got = -float(scope.vbs_query("app.Acquisition.Horizontal.HorOffset"))
    if abs(got - centre_s) > tolerance_s:
        raise SystemExit(f"the scope put the window at {got:+.9f} s, not {centre_s:+.9f} s")
    return got


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("--minutes", type=float, default=10.0)
    args = parser.parse_args()

    scope = HDO9404(SCOPE, backend="pyusb")
    for statement in ('app.Acquisition.Horizontal.ActiveChannels = "4"',  # C1 is not acquired with 2
                      "app.Math.F1.View = False", "app.Math.F2.View = False",  # their filters slow it down
                      'app.Acquisition.Trigger.Source = "C1"', 'app.Acquisition.Trigger.Type = "Edge"',
                      f"app.Acquisition.Trigger.C1.Level = {TRIGGER_V}",
                      'app.Acquisition.Trigger.C1.Slope = "Positive"',
                      "app.Acquisition.C1.VerScale = 1.0", "app.Acquisition.C1.VerOffset = 0",
                      "app.Acquisition.C2.VerScale = 1.0", "app.Acquisition.C2.VerOffset = -1.0",
                      'app.Acquisition.TriggerMode = "Stopped"'):
        scope.vbs(statement)

    # Coarse: 2 s around the trigger at 1 MS/s.
    scope.vbs("app.Acquisition.Horizontal.HorOffset = 0")
    scope.vbs("app.Acquisition.Horizontal.MaxSamples = 2000000")
    scope.vbs("app.Acquisition.Horizontal.HorScale = 0.2")
    if not acquire(scope):
        raise SystemExit("no trigger on C1 (EPG1 output 4)")
    t, v = scope.get_waveform("C2")
    level = (float(v.min()) + float(v.max())) / 2
    after = rising_edges(t, v, level)
    after = after[after > 0]
    if not len(after) or v.max() - v.min() < 0.5:
        raise SystemExit(f"no PPS edge on C2 (C2 from {v.min():+.2f} to {v.max():+.2f} V)")
    centre = float(after[0])
    print(f"C2 edge {centre * 1e3:.4f} ms after the trigger (coarse); level {level:.2f} V", flush=True)

    # Fine: a window at 2 GS/s on that edge, wide enough to be delayed that far.
    scope.vbs(f"app.Acquisition.Horizontal.HorScale = {max(MIN_DIV_S, (centre + 1e-3) / MAX_DELAY_DIV):.6g}")
    div = float(scope.vbs_query("app.Acquisition.Horizontal.HorScale"))
    scope.vbs(f"app.Acquisition.Horizontal.MaxSamples = {int(10 * div * FINE_RATE)}")
    recentre = 2.5 * div  # move the window once the edge is this far from its centre
    centre = set_window(scope, centre, div / 2)
    print(f"fine window {10 * div * 1e6:.0f} us at "
          f"{float(scope.vbs_query('app.Acquisition.Horizontal.SampleRate')) / 1e9:.1f} GS/s", flush=True)

    out = results_dir("m5-wr-offset")
    samples = []
    deadline = time.time() + args.minutes * 60
    try:
        with open(out / "samples.jsonl", "w", newline="\n") as f:
            while time.time() < deadline:
                if not acquire(scope):
                    print("no trigger", flush=True)
                    continue
                now = time.time()
                t, v = scope.get_waveform("C2")
                edges = rising_edges(t, v, level)
                if not len(edges):
                    print(f"no edge in the window around {centre * 1e3:.6f} ms; coarse search again needed",
                          flush=True)
                    break
                edge = float(edges[0])
                samples.append((now, edge))
                f.write(json.dumps({"time": now, "edge_s": edge}) + "\n")
                f.flush()
                if abs(edge - centre) > recentre:
                    centre = set_window(scope, edge, div / 2)
                if len(samples) % 30 == 0:
                    x = np.array(samples)
                    y = -np.polyfit(np.round(x[:, 0] - x[0, 0]), x[:, 1], 1)[0]
                    print(f"{len(samples)} samples over {x[-1, 0] - x[0, 0]:.0f} s: edge {edge * 1e3:.6f} ms, "
                          f"y {y:+.3e}", flush=True)
    finally:
        scope.vbs("app.Acquisition.Horizontal.HorOffset = 0")

    if len(samples) < 10:
        raise SystemExit(f"only {len(samples)} samples in {out}")
    x = np.array(samples)
    # Each acquisition is one PPS of the FS725: whole seconds apart.  The wall time,
    # taken when the readout ends, jitters by tens of ms, which at 1e-7 is ns.
    s = np.round(x[:, 0] - x[0, 0])
    line = np.polyfit(s, x[:, 1], 1)
    parabola = np.polyfit(s, x[:, 1], 2)
    minutes = [-np.polyfit(s[m], x[m, 1], 1)[0] for k in range(int(s[-1] // 60))
               if (m := (s >= 60 * k) & (s < 60 * (k + 1))).sum() >= 10]
    summary = {"samples": len(x), "seconds": float(s[-1]), "edge_first_s": float(x[0, 1]),
               "edge_last_s": float(x[-1, 1]), "y": float(-line[0]),
               "y_drift_per_s": float(-2 * parabola[0]),
               "residual_ns": float(np.std(x[:, 1] - np.polyval(line, s)) * 1e9),
               "y_per_minute": [float(m) for m in minutes]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
    print(f"results in {out}")
    print(f"y (White Rabbit against FS725) {summary['y']:+.4e}, drift {summary['y_drift_per_s']:+.2e} /s, "
          f"residual {summary['residual_ns']:.3f} ns rms over {summary['seconds']:.0f} s")
    print("y per minute: " + ", ".join(f"{m:+.3e}" for m in minutes))


if __name__ == "__main__":
    main()
