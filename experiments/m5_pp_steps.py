"""M5: how exactly does the FS725's PP move its 1PPS?  B's PPS against B's own 10 MHz on the scope.

Problem.  Steered to a calibrated target the two PPS came together to 0.15 ns, but the
constant they were calibrated with moved by -145 ps between two runs
(results/m5-v2-20261103/REPORT.md section 9.5).  Between them B's FS725 had made a
different PP (1120 ns earlier, then 40 ns later).  PPS_TICK moves by exactly the ticks
asked for; the 1PPS moves by what the PP realizes, and any error of that goes into the
constant one for one.  This measures the PP's error directly.

Mechanism measured.  B's 1PPS and B's 10 MHz both come from B's FS725, so the place of
the PPS edge against the 10 MHz, x_B = (PPS edge) - (10 MHz phase), does not move with
the unit's frequency; only PP moves it, by -n for "n ns earlier".  Each step's change
of x_B (modulo the 100 ns period) less the -n asked for is that PP's error.  Neither the
link nor node A takes part, and nothing is transmitted.

Procedure.  The scope logs (experiments/m5_hdo_phase.py --pps --pps-trigger C3) one
record a second, triggered on B's PPS: C1 B's 10 MHz, C3 B's PPS (also C2 / C4, A's).
After a baseline the script sends B's FS725 a list of PP commands over ssh (B's USB
serial port; no rf_clock process may run on B), each held ``--hold`` seconds: 1, 5, 10,
40, 100, 1000 and 1120 ns earlier and back, and three 10 ns steps in a row.  A move
later by m ns is PP 1 000 000 000 - m.  For each step, from 3 s after the command to the
next, it takes the mean of x_B and its standard error; the PP's error is the change
less the move asked for, wrapped into +-50 ns.

Run on RF-clock-PC as the normal user (the scope part through sudo)::

    .venv/bin/python experiments/m5_pp_steps.py

Results go to results/pp-steps-<time>/: hdo.jsonl (the scope), steps.jsonl (the
commands and their times) and summary.json (per step: x_B, its error and standard
error, and the same for A's PPS against A's 10 MHz, which no command moves).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from m3_common import results_dir, ssh

B = "192.168.2.100"  # S/N 8873292600AB
FS725_B = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A9NRKYDL-if00-port0"
PYNQ_PYTHON = "/usr/local/share/pynq-venv/bin/python"
PERIOD_NS = 100.0
# ns earlier (> 0) or later (< 0) for each step
STEPS = [1, -1, 5, -5, 10, -10, 10, 10, 10, -30, 40, -40, -40, 40, 100, -100, 1000, -1000, 1120, -1120]


def wrap(x: float) -> float:
    return (x + PERIOD_NS / 2) % PERIOD_NS - PERIOD_NS / 2


def place_pulse(move_ns: int) -> float:
    """Send PP to B's FS725; returns the time the command was done."""
    argument = move_ns if move_ns > 0 else 1_000_000_000 + move_ns
    code = (f"from rf_clock.fs725 import FS725; u = FS725.open('{FS725_B}'); "
            f"u.place_pulse({argument}); u.close()")
    r = ssh(B, f'sudo -n {PYNQ_PYTHON} -c "{code}"')
    if r.returncode:
        sys.exit(f"PP {argument} failed: {r.stderr}")
    return time.time()


def x_of(records: list[dict], pps: str, phase: str) -> tuple[np.ndarray, np.ndarray]:
    """Times and the PPS edge against its 10 MHz (ns, wrapped), from records that have both."""
    rows = [(r["time"], wrap(r[pps] - r[phase])) for r in records if r.get(pps) is not None]
    return np.array([t for t, _ in rows]), np.array([x for _, x in rows])


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--hold", type=float, default=25.0, help="seconds per step")
    args = parser.parse_args()

    if ssh(B, f"pgrep -fc '^{PYNQ_PYTHON} -m rf_clock'").stdout.strip() not in ("", "0"):
        sys.exit("an rf_clock program runs on B; stop it first")
    out = results_dir("pp-steps")
    seconds = args.hold * (len(STEPS) + 1) + 30
    scope = subprocess.Popen(["sudo", "-n", sys.executable, str(Path(__file__).with_name("m5_hdo_phase.py")),
                              "--pps", "--pps-trigger", "C3", "--seconds", str(seconds), "--out", str(out / "hdo.jsonl")],
                             stdout=open(out / "hdo.out", "w"), stderr=subprocess.STDOUT)
    time.sleep(10 + args.hold)  # the scope set up, then the baseline
    steps = [{"move_ns": 0, "time": time.time() - args.hold}]
    for move in STEPS:
        done = place_pulse(move)
        steps.append({"move_ns": move, "time": done})
        print(f"PP {'earlier' if move > 0 else 'later'} {abs(move)} ns", flush=True)
        time.sleep(max(0.0, done + args.hold - time.time()))
    end = time.time()
    scope.wait(timeout=seconds + 60)
    (out / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8", newline="\n")

    records = [json.loads(x) for x in (out / "hdo.jsonl").read_text(encoding="utf-8").splitlines()
               if x.strip() and json.loads(x)["type"] == "acq"]
    tb, xb = x_of(records, "pps_b_ns", "d1_ns")
    ta, xa = x_of(records, "pps_a_ns", "d2_ns")
    bounds = [s["time"] for s in steps] + [end]
    rows, previous = [], None
    for i, s in enumerate(steps):
        k = (tb >= bounds[i] + 3) & (tb < bounds[i + 1] - 0.5)
        ka = (ta >= bounds[i] + 3) & (ta < bounds[i + 1] - 0.5)
        mean = float(np.mean(xb[k])) if k.any() else float("nan")
        row = {"move_ns": s["move_ns"], "edges": int(k.sum()), "x_b_ns": mean,
               "x_b_sd_ps": float(np.std(xb[k]) * 1e3) if k.any() else None,
               "x_a_ns": float(np.mean(xa[ka])) if ka.any() else None,
               "x_a_sd_ps": float(np.std(xa[ka]) * 1e3) if ka.any() else None}
        if previous is not None:
            row["error_ps"] = wrap(mean - previous["x_b_ns"] + s["move_ns"]) * 1e3
            row["error_se_ps"] = float(np.hypot(row["x_b_sd_ps"] / np.sqrt(max(row["edges"], 1)),
                                                previous["x_b_sd_ps"] / np.sqrt(max(previous["edges"], 1))))
        rows.append(row)
        previous = row
    summary = {"steps": rows, "x_b_sd_ps_median": float(np.median([r["x_b_sd_ps"] for r in rows if r["x_b_sd_ps"]])),
               "x_a_sd_ps_median": float(np.median([r["x_a_sd_ps"] for r in rows if r["x_a_sd_ps"]])) if any(
                   r["x_a_sd_ps"] for r in rows) else None}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{'move (ns)':>10} {'edges':>6} {'x_B (ns)':>10} {'sd (ps)':>8} {'error (ps)':>11} {'+- (ps)':>8}")
    for r in rows:
        err = f"{r['error_ps']:11.1f} {r['error_se_ps']:8.1f}" if "error_ps" in r else ""
        print(f"{r['move_ns']:>10} {r['edges']:>6} {r['x_b_ns']:10.4f} {r['x_b_sd_ps'] or 0:8.1f} {err}")
    print(f"x_B scatter per edge: {summary['x_b_sd_ps_median']:.1f} ps (median), x_A: {summary['x_a_sd_ps_median']}")
    print(f"results in {out}")


if __name__ == "__main__":
    main()
