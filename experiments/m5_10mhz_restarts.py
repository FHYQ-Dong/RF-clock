"""M5: is it the boards' clock chain or the scope that jumps when a run starts again?

Problem.  Over the night of 2026-11-07 (results/m5-overnight-20261107_011058, boundaries.py)
B's 10 MHz against A's on the scope, less theta - theta0, jumped by 1-11 ps (rms 6.2 ps,
±0.8 ps each) between one hourly run and the next, and drifted back over the next 20-40
minutes, while the two output PPS jumped by no more than their noise.  That quantity holds the
relation between each FS725's 10 MHz and its board's converter clocks (the LMK04828, both
LMX2594 and the RFDC tile PLLs, all locked again at every PL.open), and the scope's C1 and C2.
But each run also restarted experiments/m5_hdo_phase.py, which sets the scope up again: the
two causes came together.  LMX2594 bands are forced and their DACISET moved by at most 1 between
runs, MTS gave the same latencies and offsets every time, and the die temperatures do not
follow the quantity, so the LMX bands, MTS and the boards' heat are not it.

Procedure, with the wiring of experiments/m4c_v2_link.py --scope --scope-out-pps:

1. ``links``: the scope records for the whole part (one m5_hdo_phase.py, set up once), while
   ``--runs`` steering runs of ``--seconds`` each start one after another (each a new PL.open);
2. ``scopes``: one steering run of ``--runs`` x ``--seconds``, while the scope script is started
   anew ``--runs`` times (each a new setup), the link running on.

For each part, the 10 MHz quantity and out_B - out_A + (theta - theta0) on either side of each
restart (the last and the first ``--span`` s, a line each, carried to the restart), as
results/m5-overnight-20261107_011058/boundaries.py does them.  The quantity jumps in the part
whose restarts cause it.  ``--node-args "--pps-out 20 --no-clocks"`` then splits the boards'
restarts: without the clocks written again, a jump left lies in the RFDC (its tile PLLs, MTS) or
the bitstream, not in the LMK04828 or LMX2594.

Since rf-clock 2.4 each node also logs P11's phase of its 10 MHz against its PL's 100 MHz clock
(tau_ref, docs/ABI.md 4.14; ``ref_phase`` lines), with the 10 MHz on ADC_D through a tee beside
the scope.  B's tau_ref less A's then sees, from inside, what the scope's 10 MHz quantity sees:
each boundary gives its jump too (modulo 10 ns: B's acquisition moves its PPS, and with it the
tick that tau_ref is counted from, by whole ticks), and the summary the least-squares slope of
the scope's jumps on P11's and what is left about it, and the same within the runs (each run's
mean taken out), on the records and on 60 s means (the scope scatters by about 5 ps a record, more
than P11 drifts in minutes); the 60 s means go to result.json for plots.  Results in
results/m5-10mhz-restarts-<time>/.

    .venv/bin/python experiments/m5_10mhz_restarts.py [--runs 6] [--seconds 540] [--parts links,scopes]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from m3_common import results_dir, ssh
from rf_clock.twtt import logs

HERE = Path(__file__).parent
RESULTS = HERE.parent / "results"
A, B = "192.168.2.99", "192.168.2.100"
LINK_ARGS = ["--scenario", "steer", "--a-args=--pps-trig"]
NODE_ARGS = "--pps-out 20"  # --node-args: "--pps-out 20 --no-clocks" restarts without writing the clocks again
WHOLE = 100_000_000  # beats of a whole second in P11's lines


def scope(out: Path, seconds: float) -> subprocess.Popen:
    return subprocess.Popen(["sudo", "-n", sys.executable, str(HERE / "m5_hdo_phase.py"), "--seconds", str(seconds),
                             "--out", str(out), "--out-pps2"], stdout=open(out.with_suffix(".out"), "w"),
                            stderr=subprocess.STDOUT)


def start_link(seconds: int) -> subprocess.Popen:
    cmd = [sys.executable, str(HERE / "m4c_v2_link.py"), *LINK_ARGS, f"--node-args={NODE_ARGS}", "--seconds", str(seconds)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def link(seconds: int) -> str:
    p = start_link(seconds)
    text = p.communicate()[0]
    found = re.search(r"results in (\S+)", text)
    if p.returncode or found is None:
        sys.exit(f"link run failed:\n{text[-1500:]}")
    return Path(found.group(1)).name


def ref_phase_ps(run: str, when: np.ndarray) -> np.ndarray:
    """B's tau_ref less A's (ps) at the Unix times ``when``, from the nodes' ``ref_phase``
    lines (whole seconds); each line comes just after the second it summed, whose middle is
    taken half a second earlier.  NaN where a node has none (before rf-clock 2.4)."""
    out = []
    for node in ("B", "A"):
        rows = [e for e in logs.read(RESULTS / run / f"m4c_{node}.jsonl", ("ref_phase",)) if e["beats"] == WHOLE]
        if len(rows) < 2:
            return np.full(when.shape, np.nan)
        t = np.array([e["time"] for e in rows]) - 0.5
        turns = np.unwrap(np.array([e["delay_ns"] for e in rows]) * (2 * np.pi / 100.0)) / (2 * np.pi)
        out.append(np.interp(when, t, turns * 100e3))
    return out[0] - out[1]


def quantities(run: str, rec: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Unix time, 10 MHz less (theta - theta0), out_B - out_A + (theta - theta0) and B's
    tau_ref less A's of the records within one link run, from 120 s after B's first steering
    step."""
    b = logs.read(RESULTS / run / "m4c_B.jsonl", ("config", "steer"))
    target = logs.config(b)["tests"]["steer_target_ns"] * 1e3
    steps = [e for e in b if e["type"] == "steer" and "step" in e and e.get("theta_ps") is not None]
    offset = float(np.median([e["time"] - (e["count"] + (e["step"] + 1) * 0.1) for e in steps])) - 0.03
    st = np.array([e["count"] + (e["step"] + 0.75) * 0.1 for e in steps])
    err = np.array([e["theta_ps"] for e in steps]) - target
    rec = [r for r in rec if r.get("out_b_minus_a_ps") is not None and r.get("lag_ps") is not None]
    edge = np.ceil(np.array([r["asked"] for r in rec]) - offset)
    keep = (edge >= st[0] + 120) & (edge <= st[-1])
    e_at = np.interp(edge[keep], st, err)
    when = edge[keep] + offset
    return (when, np.array([r["lag_ps"] for r in rec])[keep] - e_at,
            np.array([r["out_b_minus_a_ps"] for r in rec])[keep] + e_at, ref_phase_ps(run, when))


def jumps(segments: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]], span: float) -> list[dict]:
    out = []
    for (t0, l0, p0, r0), (t1, l1, p1, r1) in zip(segments, segments[1:]):
        mid = (t0[-1] + t1[0]) / 2
        row: dict = {"gap_s": round(float(t1[0] - t0[-1]), 1)}
        for name, a, b in (("ten_mhz", l0, l1), ("output_pps", p0, p1), ("p11", r0, r1)):
            ea, eb = (t0 >= t0[-1] - span) & np.isfinite(a), (t1 <= t1[0] + span) & np.isfinite(b)
            if ea.sum() < 2 or eb.sum() < 2:
                row[name] = None
                continue
            fa, fb = np.polyfit(t0[ea] - mid, a[ea], 1), np.polyfit(t1[eb] - mid, b[eb], 1)
            jump = float(fb[1] - fa[1])
            row[name] = round((jump + 5000.0) % 10000.0 - 5000.0 if name == "p11" else jump, 2)
        out.append(row)
    return out


def means(t: np.ndarray, *series: np.ndarray, width: float = 60.0) -> np.ndarray:
    """Rows of (seconds from the first record, each series' mean) over ``width`` s bins at
    least half full."""
    k = np.floor((t - t[0]) / width)
    rows = [[(kk + 0.5) * width, *(float(np.mean(s[k == kk])) for s in series)]
            for kk in np.unique(k) if (k == kk).sum() >= width / 2]
    return np.array(rows).reshape(-1, 1 + len(series))


def within(pairs: list[tuple[np.ndarray, np.ndarray]]) -> dict | None:
    """The slope of the scope's series on P11's, each run's mean taken out, and the scatter
    left about it."""
    if not pairs:
        return None
    wy, wx = (np.concatenate([v - np.mean(v) for v in vs]) for vs in zip(*pairs))
    ws = float(np.polyfit(wx, wy, 1)[0])
    return {"slope": round(ws, 3), "scope_sd_ps": round(float(np.std(wy)), 2), "p11_sd_ps": round(float(np.std(wx)), 2),
            "left_sd_ps": round(float(np.std(wy - ws * wx)), 2)}


def compare(rows: list[dict], segments: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]) -> dict | None:
    """The scope's 10 MHz quantity against B's tau_ref less A's: the least-squares slope of
    its jumps on P11's (through 0) and their rms about it; the same within the runs, on the
    records and on 60 s means, and the 60 s means of each run."""
    runs = [(t, lv, rv) for t, lv, _, rv in segments if np.isfinite(rv).all() and t.size]
    if not runs:
        return None
    binned = [means(t, lv, rv) for t, lv, rv in runs]
    out: dict = {"within": within([(lv, rv) for _, lv, rv in runs]),
                 "within_60s": within([(b[:, 1], b[:, 2]) for b in binned if len(b) > 2]),
                 "means_60s": [[[round(float(v), 2) for v in row] for row in b] for b in binned]}
    pairs = [(r["ten_mhz"], r["p11"]) for r in rows if r.get("p11") is not None]
    if pairs:
        y, x = np.array(pairs).T
        slope = float(x @ y / (x @ x))
        out |= {"jumps_slope": round(slope, 3), "jumps_left_rms_ps": round(float(np.sqrt(np.mean((y - slope * x) ** 2))), 2)}
    return out


def summary(rows: list[dict]) -> dict:
    out: dict = {}
    for k in ("ten_mhz", "output_pps", "p11"):
        v = [r[k] for r in rows if r.get(k) is not None]
        out[k] = {"jumps_ps": v, "rms_ps": round(float(np.sqrt(np.mean(np.square(v)))), 2) if v else None}
    return out


def read(path: Path) -> list[dict]:
    return [r for r in logs.read(path, ("acq",))]


def part_links(out: Path, runs: int, seconds: int, span: float) -> dict:
    hdo = out / "links_hdo.jsonl"
    sc = scope(hdo, runs * (seconds + 90) + 120)
    time.sleep(15)
    names = [link(seconds) for _ in range(runs)]
    sc.wait()
    rec = read(hdo)
    segs = [quantities(n, rec) for n in names]
    rows = jumps(segs, span)
    return {"runs": names, "boundaries": rows, "summary": summary(rows), "p11_vs_scope": compare(rows, segs)}


def part_scopes(out: Path, runs: int, seconds: int, span: float) -> dict:
    total = runs * (seconds + 20) + 300
    p = start_link(total)
    time.sleep(15)  # until the runner has removed the last run's node logs
    deadline = time.time() + 400
    while ssh(B, "grep -c '^{\"type\": \"steer\"' /dev/shm/m4c_B.jsonl 2>/dev/null").stdout.strip() in ("", "0"):
        if time.time() > deadline:
            sys.exit("B is not steering")
        time.sleep(5)
    time.sleep(150)  # past the start, as in the other part
    files = []
    for i in range(runs):
        f = out / f"scopes_hdo_{i}.jsonl"
        scope(f, seconds).wait()
        files.append(f)
    text = p.communicate()[0]
    found = re.search(r"results in (\S+)", text)
    if p.returncode or found is None:
        sys.exit(f"link run failed:\n{text[-1500:]}")
    name = Path(found.group(1)).name
    segs = [quantities(name, read(f)) for f in files]
    rows = jumps(segs, span)
    return {"run": name, "boundaries": rows, "summary": summary(rows), "p11_vs_scope": compare(rows, segs)}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--seconds", type=int, default=540)
    parser.add_argument("--span", type=float, default=240.0)
    parser.add_argument("--parts", default="links,scopes")
    parser.add_argument("--node-args", default=NODE_ARGS, help="to both nodes; with --no-clocks a restart leaves "
                        "the LMK04828 and LMX2594 as they are (PL.open still loads the bitstream, starts the RFDC, MTS)")
    args = parser.parse_args()
    globals()["NODE_ARGS"] = args.node_args
    out = results_dir("m5-10mhz-restarts")
    result = {}
    for part in args.parts.split(","):
        result[part] = (part_links if part == "links" else part_scopes)(out, args.runs, args.seconds, args.span)
        brief = {k: v for k, v in (result[part]["p11_vs_scope"] or {}).items() if k != "means_60s"}
        print(part, json.dumps(result[part]["summary"]), json.dumps(brief), flush=True)
        (out / "result.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"results in {out}")


if __name__ == "__main__":
    main()
