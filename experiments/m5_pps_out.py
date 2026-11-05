"""M5: does B's output PPS (P10, DAC_B) hold against B's own 10 MHz where its FS725's 1PPS does not?

Problem.  The FS725's 1PPS moves by what a PP realizes, up to 0.23 ns off the move asked
for, and jitters by 34 ps against the unit's 10 MHz (results/m5-v2-20261103/REPORT.md
sections 9.5, 9.6), so a PPS difference calibrated at one theta holds only until the next
PP.  The output PPS is made from the node's time scale: its pulse of second k leaves at
PPS_TICK(k) + OFFSET ticks, and the tick and the DAC's sample clock both come from the
FS725's 10 MHz.  So against that 10 MHz the output edge should hold to the scope's
resolution from second to second, over power-ups (MTS fixes the DAC's latency, the LMK's
zero-delay mode the tick's phase), and through a PP move by exactly the whole ticks that
PPS_TICK moved, while the 1PPS moves by the PP's own error (plan/pps_output.md section 5).

Procedure.  The scope (experiments/m5_hdo_phase.py --out-pps) records once a second,
triggered on B's 1PPS: C1 B's 10 MHz, C3 B's 1PPS (tee beside J7), C4 B's DAC_B (J20, 50
ohm).  out_vs_10mhz is the output edge against C1's phase, pps_vs_10mhz the 1PPS edge
against it, both wrapped into +-50 ns.  Modes:

- ``steady``: B runs ``rf_clock.checks.pps_out`` (PL.open, then one line a second with the
  die temperature) while the scope records ``--seconds``; the summary gives the scatter of
  both edges and their slopes against the die temperature.
- ``pp``: with the output already on and no rf_clock process on B, B's FS725 makes the PP
  moves of STEPS, each held ``--hold`` s (experiments/m5_pp_steps.py).  A move of 2 ns
  stays within the tick (B's edge may come about 5 ns later or 4.7 ns earlier before its
  latch tick changes, results/m5-v2-20261103/REPORT.md section 3), so the output must not
  move; one of 10 ns moves PPS_TICK, and with it the output, by exactly
  one tick.  P1 takes a period one tick off as jitter and keeps its lock; a larger move
  drops the lock, which comes back after four good periods, the output meanwhile on the
  flywheel, so each step is taken from 8 s after its command.
- ``opens``: ``--runs`` times PL.open (``rf_clock.checks.pps_out`` for 10 s, the output
  left on), each followed by ``--hold`` s on the scope.

Run on RF-clock-PC as the normal user (the scope part through sudo)::

    .venv/bin/python experiments/m5_pps_out.py steady --seconds 600
    .venv/bin/python experiments/m5_pps_out.py pp
    .venv/bin/python experiments/m5_pps_out.py opens --runs 5

Results go to results/pps-out-<mode>-<time>/: hdo.jsonl (the scope), board.out (B's lines,
each with the time it arrived), steps.jsonl and summary.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from m3_common import results_dir, ssh
from m5_pp_steps import B, PYNQ_PYTHON, place_pulse, wrap

STEPS = [2, -2, 10, -10, 10, 10, 10, -30, 100, -100]  # ns earlier (> 0) or later (< 0)
SETTLE_S = 8.0  # after a PP: P1's new lock, then the output follows the new edge


def check_cmd(seconds: int, args) -> str:
    return (f"cd /tmp && sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr {PYNQ_PYTHON} -m rf_clock.checks.pps_out "
            f"{seconds} --offset {args.offset} --amplitude {args.amplitude} --synced --keep")


def board_lines(proc: subprocess.Popen, sink: list[tuple[float, str]], path: Path) -> threading.Thread:
    """Collect B's output lines with their arrival times (also into ``path``)."""
    def run() -> None:
        with path.open("a", encoding="utf-8", newline="\n") as f:
            for line in proc.stdout:  # type: ignore[union-attr]
                now = time.time()
                sink.append((now, line.rstrip()))
                f.write(f"{now:.3f} {line}")
                f.flush()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


LOGGER = [sys.executable, str(Path(__file__).resolve().with_name("m5_hdo_phase.py")), "--out-pps"]


def start_scope(out: Path, seconds: float) -> subprocess.Popen:
    return subprocess.Popen(["sudo", "-n", *LOGGER, "--seconds", str(seconds), "--out", str(out / "hdo.jsonl")],
                            stdout=open(out / "hdo.out", "w"), stderr=subprocess.STDOUT)


def stop_scope(scope: subprocess.Popen) -> None:
    """SIGTERM to the root logger (a signal to its sudo does not reach it); it stops after
    its acquisition."""
    subprocess.run(["sudo", "-n", "pkill", "-TERM", "-f", "^" + " ".join(LOGGER)])
    scope.wait(timeout=60)


def records(out: Path) -> list[dict]:
    rows = [json.loads(x) for x in (out / "hdo.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if r["type"] == "acq" and r.get("out_ns") is not None and r.get("pps_b_ns") is not None]


def edges(rows: list[dict], t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
    """out_vs_10mhz and pps_vs_10mhz (ns) of the records in [t0, t1)."""
    k = [r for r in rows if t0 <= r["time"] < t1]
    return (np.array([r["out_vs_10mhz_ns"] for r in k]),
            np.array([wrap(r["pps_b_ns"] - r["d1_ns"]) for r in k]))


def out_minus_pps(rows: list[dict], t0: float, t1: float) -> float | None:
    """Mean output edge less 1PPS edge (ns, not wrapped) of the records in [t0, t1)."""
    x = [r["out_minus_pps_ns"] for r in rows if t0 <= r["time"] < t1]
    return float(np.mean(x)) if x else None


def stats(x: np.ndarray, ref: float) -> dict:
    """Mean (ns) and sd (ps) of x, wrapped about ref so that a period boundary does not split it."""
    if not x.size:
        return {"n": 0}
    y = np.array([wrap(v - ref) for v in x]) + ref
    return {"n": int(y.size), "mean_ns": float(np.mean(y)), "sd_ps": float(np.std(y) * 1e3),
            "se_ps": float(np.std(y) * 1e3 / np.sqrt(y.size))}


def die_temps(lines: list[tuple[float, str]]) -> list[tuple[float, float]]:
    """(arrival time, die_c) of B's per-second lines."""
    out = []
    for when, line in lines:
        parts = line.split()
        if len(parts) == 7 and parts[0].isdigit():
            try:
                out.append((when, float(parts[6])))
            except ValueError:
                pass
    return out


def steady(args, out: Path) -> dict:
    lines: list[tuple[float, str]] = []
    board = subprocess.Popen(["ssh", "-o", "BatchMode=yes", f"xilinx@{B}", check_cmd(int(args.seconds) + 60, args)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    board_lines(board, lines, out / "board.out")
    while not any(line.strip().startswith("count") for _, line in lines):  # PL.open done
        if board.poll() is not None:
            sys.exit("rf_clock.checks.pps_out ended before its first line; see board.out")
        time.sleep(1)
    time.sleep(3)
    start = time.time()
    scope = start_scope(out, args.seconds)
    scope.wait(timeout=args.seconds + 120)
    board.wait(timeout=180)
    rows = records(out)
    o, p = edges(rows, start, time.time())
    temps = die_temps(lines)
    summary = {"out_vs_10mhz": stats(o, float(np.median(o))) if o.size else {},
               "pps_vs_10mhz": stats(p, float(np.median(p))) if p.size else {}}
    if temps and o.size > 10:
        tt = np.array([w for w, _ in temps])
        dc = np.array([c for _, c in temps])
        rt = np.array([r["time"] for r in rows if start <= r["time"]])
        at = np.interp(rt, tt, dc)
        for name, x in (("out_vs_10mhz", o), ("pps_vs_10mhz", p)):
            y = np.array([wrap(v - np.median(x)) for v in x])
            if np.ptp(at) > 0.3:
                slope = float(np.polyfit(at, y, 1)[0] * 1e3)
                summary[name]["ps_per_k"] = slope
        summary["die_c"] = {"min": float(at.min()), "max": float(at.max())}
    return summary


def pp(args, out: Path) -> dict:
    if ssh(B, f"pgrep -fc '^{PYNQ_PYTHON} -m rf_clock'").stdout.strip() not in ("", "0"):
        sys.exit("an rf_clock program runs on B; stop it first")
    seconds = args.hold * (len(STEPS) + 1) + 30
    scope = start_scope(out, seconds)
    time.sleep(10 + args.hold)
    steps = [{"move_ns": 0, "time": time.time() - args.hold}]
    for move in STEPS:
        steps.append({"move_ns": move, "time": place_pulse(move)})
        print(f"PP {'earlier' if move > 0 else 'later'} {abs(move)} ns", flush=True)
        time.sleep(max(0.0, steps[-1]["time"] + args.hold - time.time()))
    steps.append({"move_ns": None, "time": time.time()})  # the end of the last step
    scope.wait(timeout=seconds + 60)
    (out / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8", newline="\n")
    return pp_summary(out)


def pp_summary(out: Path) -> dict:
    """Per PP step: both edges against the 10 MHz, their changes less the move asked for (the
    output moves by the whole ticks, the 1PPS by the nanoseconds), and the output less the
    1PPS unwrapped, which shows a whole-period move (100 ns) that the 10 MHz cannot."""
    steps = [json.loads(x) for x in (out / "steps.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = records(out)
    if steps[-1]["move_ns"] is not None:  # a run from before the end line was written
        steps.append({"move_ns": None, "time": rows[-1]["time"] + 1})
    result, previous = [], None
    for i, s in enumerate(steps[:-1]):
        t0, t1 = s["time"] + (SETTLE_S if i else 3.0), steps[i + 1]["time"] - 0.5
        o, p = edges(rows, t0, t1)
        ref_o = previous["out"]["mean_ns"] if previous else (float(np.median(o)) if o.size else 0.0)
        ref_p = previous["pps"]["mean_ns"] if previous else (float(np.median(p)) if p.size else 0.0)
        row = {"move_ns": s["move_ns"], "out": stats(o, ref_o), "pps": stats(p, ref_p),
               "out_minus_pps_ns": out_minus_pps(rows, t0, t1)}
        if previous is not None and row["out"]["n"] and row["pps"]["n"]:
            ticks = round(s["move_ns"] / 10)
            row["out_change_ns"] = wrap(row["out"]["mean_ns"] - previous["out"]["mean_ns"])
            row["out_error_ps"] = wrap(row["out_change_ns"] + 10 * ticks) * 1e3  # whole ticks earlier
            row["out_error_se_ps"] = float(np.hypot(row["out"]["se_ps"], previous["out"]["se_ps"]))
            row["pps_error_ps"] = wrap(row["pps"]["mean_ns"] - previous["pps"]["mean_ns"] + s["move_ns"]) * 1e3
            row["pps_error_se_ps"] = float(np.hypot(row["pps"]["se_ps"], previous["pps"]["se_ps"]))
        result.append(row)
        if row["out"]["n"]:
            previous = row
    return {"steps": result}


def opens(args, out: Path) -> dict:
    seconds = args.runs * (args.hold + 120) + 60
    scope = start_scope(out, seconds)
    runs = []
    for i in range(args.runs):
        r = ssh(B, check_cmd(10, args), timeout=300)
        (out / "board.out").open("a", encoding="utf-8", newline="\n").write(r.stdout + r.stderr)
        start = time.time()
        print(f"PL.open {i + 1}: {'PASS' if r.returncode == 0 else 'FAIL'}", flush=True)
        time.sleep(args.hold)
        runs.append({"run": i + 1, "time": start, "end": time.time(), "check": r.returncode})
        (out / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in runs), encoding="utf-8", newline="\n")
    stop_scope(scope)
    return opens_summary(out)


def opens_summary(out: Path) -> dict:
    """Per PL.open: both edges against the 10 MHz, the runs referred to the first.  Without
    steps.jsonl the runs are the stretches of records between the gaps PL.open leaves (no
    output pulse while it reloads the bitstream and writes the clocks); the first stretch is
    then the PL.open before the run."""
    rows = records(out)
    path = out / "steps.jsonl"
    if path.exists():
        runs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    else:
        times = [r["time"] for r in rows]
        cuts = [0] + [i + 1 for i in range(len(times) - 1) if times[i + 1] - times[i] > 5] + [len(times)]
        runs = [{"run": k, "time": times[a] - 2, "end": times[b - 1] + 0.5} for k, (a, b) in enumerate(zip(cuts, cuts[1:]))]
    result, ref_o, ref_p = [], None, None
    for run in runs:
        o, p = edges(rows, run["time"] + 2, run["end"])
        ref_o = ref_o if ref_o is not None else (float(np.median(o)) if o.size else 0.0)
        ref_p = ref_p if ref_p is not None else (float(np.median(p)) if p.size else 0.0)
        result.append({"run": run["run"], "out": stats(o, ref_o), "pps": stats(p, ref_p)})
    means = [r["out"]["mean_ns"] for r in result if r["out"]["n"]]
    return {"runs": result, "out_spread_ps": float(np.ptp(means) * 1e3) if means else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("mode", choices=("steady", "pp", "opens"))
    parser.add_argument("--summary", metavar="DIR", help="pp, opens: summarize an earlier run's DIR again")
    parser.add_argument("--seconds", type=float, default=600, help="steady: seconds on the scope")
    parser.add_argument("--hold", type=float, default=25.0, help="pp, opens: seconds per step or run")
    parser.add_argument("--runs", type=int, default=5, help="opens: PL.open count")
    parser.add_argument("--offset", type=int, default=20, help="output PPS offset, ticks")
    parser.add_argument("--amplitude", type=int, default=-24000,
                        help="output PPS DAC code (negative: DAC_B is inverted, so the leading edge rises)")
    args = parser.parse_args()
    if args.summary:
        out = Path(args.summary)
        summary = {"mode": args.mode, **{"pp": pp_summary, "opens": opens_summary}[args.mode](out)}
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(summary, indent=2))
        return
    out = results_dir(f"pps-out-{args.mode}")
    summary = {"mode": args.mode, "args": vars(args), **{"steady": steady, "pp": pp, "opens": opens}[args.mode](args, out)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, indent=2))
    print(f"results in {out}")


if __name__ == "__main__":
    main()
