"""M5: the two FS725s' 10 MHz and 1PPS against each other on the HDO9404-MS, beside the link's theta.

Problem.  With each board on its own FS725, theta drifts at the two clocks' frequency
difference and wanders about that line (results of 2026-11-03: -68 ps/s, 31 ps about
the line over 32 s, sd of a single pair 1.0 ps).  Is that the clocks, or the link?
Each board's tick and converter clocks are made from its FS725's 10 MHz, so the time
offset of the two 10 MHz outputs, as an instrument outside the system sees it, must
move as theta does: theta = B's reading - A's, so B's 10 MHz comes earlier by as much
as theta grows.  This logs that offset, modulo the 100 ns period, while the link runs,
so that theta(t) can be compared with it second by second.

With ``--pps`` it also logs PPS_B - PPS_A, M5's acceptance quantity (plan section 9).
theta aligns the two nodes' tick-anchored time scales, not the PPS edges: at the FS725
outputs PPS_B - PPS_A = (d_A + l_A) - (d_B + l_B) + Delta/2 - theta, where d is each
node's PPS lead on the tick that latches it and l its PPS cable and input path.  Steered
to a target theta_0, the PPS difference is a constant, which this measures so that it
can be put into theta_0 (plan/checklist.md, M5).

Procedure.  C1 and C2 take the second 10 MHz output of the two FS725s (DC 50 ohm,
0.5 V/div: the traces sit 0.4-0.8 V below zero, as read back), C1 B's and C2 A's; the
scope records 2 us at 10 GS/s.  For each acquisition the script fits a 10 MHz sine to
each channel (least squares, with a DC term) and writes the time by which C2's zero
crossing lags C1's (ps, within +-50 ns), the two amplitudes and the times the
acquisition was asked for and read.  Without ``--pps`` the scope triggers on C1 at its
mean, found from a first forced acquisition, about twice a second.

``--pps``: C3 and C4 take each FS725's 1PPS through a tee beside its board's J7 (C3 B's,
C4 A's; DC 1 Mohm, 1 V/div); the scope triggers on A's PPS (C4 rising through 1 V), once
a second, with the 2 us centred on it.  Each PPS edge's time is the 50 % point of a line
fitted to the samples between 20 % and 80 % of its rise (about 2 ns, 20 samples), and
the script also writes pps_b_minus_a_ps; an edge outside the record is written as None
(before B's acquisition its PPS can be microseconds away).  It also writes each 10 MHz's
phase as a delay in the record (d1_ns, d2_ns), so that each PPS can be referred to its
own FS725's 10 MHz (experiments/m5_pp_steps.py); ``--pps-trigger C3`` triggers on B's PPS.

``--out-pps``: C3 takes B's FS725 1PPS as above and C4 B's output PPS from DAC_B (J20, DC
50 ohm, 0.1 V/div; plan/pps_output.md); the scope triggers on C3 and records 1 us at
20 GS/s.  The output pulse is AC-coupled: its leading edge rises in about 1 ns, overshoots
and then droops.  Its time is the 50 % point of a line fitted to the samples between 20 %
and 80 % of its level 1-3 ns after the edge (``pulse_edge``), about 7 ps rms per record
from the scope's noise on the edge.  Each record holds
pps_b_ns and out_ns (both edges in the record), out_minus_pps_ns, and out_vs_10mhz_ns,
the output edge against C1's phase wrapped into +-50 ns (experiments/m5_pps_out.py).

``--out-pps2``: C3 takes A's output PPS and C4 B's (both DAC_B, DC 50 ohm, 0.1 V/div), C1
and C2 the two 10 MHz as always; the scope triggers on B's output (C4 rising through
0.1 V) and records 1 us at 20 GS/s.  Each record holds out_a_ns and out_b_ns
(``pulse_edge``), out_b_minus_a_ps, and each output against its own FS725's 10 MHz
(out_b_vs_10mhz_ns against C1, out_a_vs_10mhz_ns against C2, wrapped into +-50 ns), so
that a change of the difference can be told to come from one board or the other.

Run on RF-clock-PC as root (the scope's USB node is root-only), with the project's
``instruments`` dependency group::

    sudo .venv/bin/python experiments/m5_hdo_phase.py --seconds 600 --out results/x/hdo.jsonl [--pps]

experiments/m4c_v2_link.py --scope [--scope-pps] starts it for the length of a link run.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path

import numpy as np
from tanglab_instruments import HDO9404

SCOPE = "USB0::0x05FF::0x1023::4404N30414::INSTR"  # HDO9404-MS on RF-clock-PC's USB
F_REF = 10e6
SETUP = [  # (VBS statement) for the 10 MHz channels and the timebase
    "app.Acquisition.C1.View = True", "app.Acquisition.C2.View = True",
    'app.Acquisition.C1.Coupling = "DC50"', 'app.Acquisition.C2.Coupling = "DC50"',
    "app.Acquisition.C1.VerScale = 0.5", "app.Acquisition.C2.VerScale = 0.5",
    "app.Acquisition.C1.VerOffset = 0", "app.Acquisition.C2.VerOffset = 0",
    "app.Acquisition.Horizontal.HorScale = 200e-9", "app.Acquisition.Horizontal.HorOffset = 0",
    'app.Acquisition.Horizontal.Maximize = "FixedSampleRate"', "app.Acquisition.Horizontal.SampleRate = 10e9",
    'app.Acquisition.Trigger.Type = "Edge"',
]
SETUP_10MHZ_TRIGGER = ['app.Acquisition.Trigger.Source = "C1"', 'app.Acquisition.Trigger.C1.Slope = "Positive"']
SETUP_PPS = [  # the two 1PPS, and the trigger on A's
    "app.Acquisition.C3.View = True", "app.Acquisition.C4.View = True",
    'app.Acquisition.C3.Coupling = "DC1M"', 'app.Acquisition.C4.Coupling = "DC1M"',
    "app.Acquisition.C3.VerScale = 1", "app.Acquisition.C4.VerScale = 1",
    "app.Acquisition.C3.VerOffset = 0", "app.Acquisition.C4.VerOffset = 0",
]


SETUP_OUT_PPS = [  # B's 1PPS and B's output PPS, 1 us at 20 GS/s
    "app.Acquisition.C3.View = True", "app.Acquisition.C4.View = True",
    'app.Acquisition.C3.Coupling = "DC1M"', 'app.Acquisition.C4.Coupling = "DC50"',
    "app.Acquisition.C3.VerScale = 1", "app.Acquisition.C4.VerScale = 0.1",
    "app.Acquisition.C3.VerOffset = 0", "app.Acquisition.C4.VerOffset = 0",
    "app.Acquisition.Horizontal.HorScale = 100e-9", "app.Acquisition.Horizontal.SampleRate = 20e9",
]


SETUP_OUT_PPS2 = [  # A's and B's output PPS, 1 us at 20 GS/s, trigger on B's
    "app.Acquisition.C3.View = True", "app.Acquisition.C4.View = True",
    'app.Acquisition.C3.Coupling = "DC50"', 'app.Acquisition.C4.Coupling = "DC50"',
    "app.Acquisition.C3.VerScale = 0.1", "app.Acquisition.C4.VerScale = 0.1",
    "app.Acquisition.C3.VerOffset = 0", "app.Acquisition.C4.VerOffset = 0",
    "app.Acquisition.Horizontal.HorScale = 100e-9", "app.Acquisition.Horizontal.SampleRate = 20e9",
    'app.Acquisition.Trigger.Source = "C4"', 'app.Acquisition.Trigger.C4.Slope = "Positive"',
    "app.Acquisition.Trigger.C4.Level = 0.1",
]


def pps_trigger(channel: str) -> list[str]:
    """Trigger on one PPS (C4: A's, C3: B's), rising through 1 V."""
    return [f'app.Acquisition.Trigger.Source = "{channel}"', f'app.Acquisition.Trigger.{channel}.Slope = "Positive"',
            f"app.Acquisition.Trigger.{channel}.Level = 1.0"]


def sine_delay(t: np.ndarray, v: np.ndarray, f: float = F_REF) -> tuple[float, float]:
    """The delay (s, within one period) and amplitude of a sine of frequency f: v = A cos(2 pi f (t - tau))."""
    w = 2 * np.pi * f * t
    m = np.column_stack([np.cos(w), np.sin(w), np.ones_like(t)])
    (a, b, _), *_ = np.linalg.lstsq(m, v, rcond=None)
    return float(np.arctan2(b, a) / (2 * np.pi * f)), float(np.hypot(a, b))


def edge_time(t: np.ndarray, v: np.ndarray, *, min_swing: float = 1.0) -> float | None:
    """The 50 % time (s) of the first rising edge: a line fitted to the samples between 20 %
    and 80 % of the swing, solved for the middle.  None when the record holds no edge."""
    lo, hi = float(np.percentile(v, 2)), float(np.percentile(v, 98))
    if hi - lo < min_swing:
        return None
    mid = (lo + hi) / 2
    above = np.nonzero(v > mid)[0]
    if not above.size or above[0] == 0:
        return None
    a = b = int(above[0])
    while a > 0 and v[a - 1] > lo + 0.2 * (hi - lo):
        a -= 1
    while b < v.size - 1 and v[b + 1] < lo + 0.8 * (hi - lo):
        b += 1
    if b - a < 2:
        return None
    slope, icept = np.polyfit(t[a:b + 1], v[a:b + 1], 1)
    return float((mid - icept) / slope)


def pulse_edge(t: np.ndarray, v: np.ndarray, *, min_peak: float = 0.03) -> tuple[float, float] | None:
    """The 50 % time (s) of a short pulse's leading edge, and its level (V, signed), from the
    record's median.  The level is the mean 1-3 ns after the edge first passes half the
    largest excursion (past the overshoot, before the AC coupling's droop matters); a line
    is fitted to the samples between 20 % and 80 % of it.  (The first peak instead moved
    the 50 % point by up to 0.1 ns where noise ended the climb early: results/pps-out-20261104.)
    None when no sample departs from the median by ``min_peak``."""
    d = v - np.median(v)
    big = np.nonzero(np.abs(d) > max(min_peak, 0.5 * float(np.max(np.abs(d)))))[0]
    if not big.size or big[0] < 2:
        return None
    sign = 1.0 if d[big[0]] > 0 else -1.0
    s = sign * d
    tc = t[big[0]]
    late = (t > tc + 1e-9) & (t < tc + 3e-9)
    if not late.any():
        return None
    level = float(np.mean(s[late]))
    k = int(np.nonzero(s > 0.5 * level)[0][0])
    a = b = k
    while a > 0 and s[a - 1] > 0.2 * level:
        a -= 1
    while b + 1 < s.size and s[b + 1] < 0.8 * level:
        b += 1
    if b - a < 1:
        return None
    slope, icept = np.polyfit(t[a:b + 1], s[a:b + 1], 1)
    return float((0.5 * level - icept) / slope), sign * level


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--out", required=True)
    parser.add_argument("--period", type=float, default=0.5, help="seconds between acquisitions (at least)")
    parser.add_argument("--pps", action="store_true", help="also the two 1PPS on C3 (B) and C4 (A), trigger on C4")
    parser.add_argument("--pps-trigger", choices=("C3", "C4"), default="C4", help="with --pps: the PPS to trigger on")
    parser.add_argument("--out-pps", action="store_true",
                        help="B's 1PPS on C3 and B's output PPS (DAC_B) on C4, trigger on C3")
    parser.add_argument("--out-pps2", action="store_true",
                        help="A's output PPS on C3 and B's on C4, trigger on C4")
    args = parser.parse_args()

    stop = False

    def on_term(signum, frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, on_term)
    scope = HDO9404(SCOPE, backend="pyusb")
    setup = SETUP + (SETUP_OUT_PPS2 if args.out_pps2 else SETUP_OUT_PPS + pps_trigger("C3") if args.out_pps else
                     SETUP_PPS + pps_trigger(args.pps_trigger) if args.pps else SETUP_10MHZ_TRIGGER)
    pps_mode = args.pps or args.out_pps or args.out_pps2
    before = {s.split(" = ")[0]: scope.vbs_query(s.split(" = ")[0]) for s in setup}
    for statement in setup:
        scope.vbs(statement)
    scope.trigger_mode = "Stopped"
    level = None
    if not pps_mode:
        scope.vbs_query("app.Acquisition.Acquire(2, True)")  # forced: C1's mean sets the trigger level
        level = float(np.mean(scope.get_waveform("C1")[1]))
        scope.vbs(f"app.Acquisition.Trigger.C1.Level = {level:.3f}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    period = 1 / F_REF
    end = time.time() + args.seconds
    n = 0
    with out.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"type": "start", "time": time.time(), "settings_before": before, "setup": setup,
                            "trigger_level": level, "pps": args.pps, "out_pps": args.out_pps,
                            "out_pps2": args.out_pps2}) + "\n")
        while time.time() < end and not stop:
            asked = time.time()
            timeout = 2.5 if pps_mode else 2
            if scope.vbs_query(f"app.Acquisition.Acquire({timeout}, False)").strip().lower() not in ("1", "true", "-1"):
                f.write(json.dumps({"type": "no_trigger", "time": asked}) + "\n")
                continue
            got = time.time()
            t1, v1 = scope.get_waveform("C1")
            t2, v2 = scope.get_waveform("C2")
            d1, a1 = sine_delay(t1, v1)
            d2, a2 = sine_delay(t2, v2)
            lag = (d2 - d1 + period / 2) % period - period / 2
            record = {"type": "acq", "time": got, "asked": asked, "lag_ps": lag * 1e12, "amp1": a1, "amp2": a2,
                      "points": len(t1)}
            if args.out_pps2:
                ea = pulse_edge(*scope.get_waveform("C3"))
                eb = pulse_edge(*scope.get_waveform("C4"))
                ta = None if ea is None else ea[0]
                tb = None if eb is None else eb[0]

                def vs(t, d):
                    return None if t is None else ((t - d + period / 2) % period - period / 2) * 1e9

                record.update(d1_ns=d1 * 1e9, d2_ns=d2 * 1e9, out_a_ns=None if ta is None else ta * 1e9,
                              out_b_ns=None if tb is None else tb * 1e9,
                              out_a_level_v=None if ea is None else ea[1], out_b_level_v=None if eb is None else eb[1],
                              out_b_minus_a_ps=None if ta is None or tb is None else (tb - ta) * 1e12,
                              out_b_vs_10mhz_ns=vs(tb, d1), out_a_vs_10mhz_ns=vs(ta, d2))
            elif args.out_pps:
                tb = edge_time(*scope.get_waveform("C3"))
                edge = pulse_edge(*scope.get_waveform("C4"))
                to = None if edge is None else edge[0]
                record.update(d1_ns=d1 * 1e9, d2_ns=d2 * 1e9, pps_b_ns=None if tb is None else tb * 1e9,
                              out_ns=None if to is None else to * 1e9, out_peak_v=None if edge is None else edge[1],
                              out_minus_pps_ns=None if tb is None or to is None else (to - tb) * 1e9,
                              out_vs_10mhz_ns=None if to is None else ((to - d1 + period / 2) % period - period / 2) * 1e9)
            elif args.pps:
                tb = edge_time(*scope.get_waveform("C3"))
                ta = edge_time(*scope.get_waveform("C4"))
                record.update(d1_ns=d1 * 1e9, d2_ns=d2 * 1e9,  # each 10 MHz's phase, as a delay in the record
                              pps_b_ns=None if tb is None else tb * 1e9, pps_a_ns=None if ta is None else ta * 1e9,
                              pps_b_minus_a_ps=None if tb is None or ta is None else (tb - ta) * 1e12)
            f.write(json.dumps(record) + "\n")
            f.flush()
            n += 1
            time.sleep(max(0.0, args.period - (time.time() - asked)))
        f.write(json.dumps({"type": "stop", "time": time.time(), "acquisitions": n}) + "\n")
    scope.trigger_mode = "Auto"
    print(f"{n} acquisitions in {out}")


if __name__ == "__main__":
    main()
