"""M5: B's FS725 with its frequency lock loop slowed to 128 s: how its crystal wanders, how it answers FC.

Problem.  Steering B through SF goes SF -> magnetic field -> FLL integrator -> 1 s RC -> crystal
and leaves 15-17 ps (results/precision-20261105).  The PRS10 lets the crystal's tuning FC be set
directly (docs/PRS10m.pdf p. 24-25), which leaves only the RC (p. 47): in replay that law holds
B to 5-7 ps (results/precision-20261105/fcdirect.py, batch.py), but the replay used theta with
both FLLs on and a guessed plant.  Two things decide whether it holds on the bench: the
crystal's own wander without the FLL (B's FLL puts about half of theta's second-to-second rate
in, docs/fs725_principles.md section 10, but the free crystal is not specified), and the plant:
FC's dead time and the RC's time constant.

The FLL cannot simply be stopped: with ``LO 0`` or ``GA 0`` the unit reports itself unlocked and
its LOCK/1PPS pin stays high (p. 20, "LM"), so B's PL loses its PPS and the slot engine stops
(2026-11-06, results/m4c-v2-acquire-20261106_023402).  At ``GA 1`` the loop runs with a 128 s
time constant and the unit stays locked, its 1PPS on; an FC written there stays, the loop
pulling it back by about 2 units a second (measured on B the same night), so on 0.1-10 s the
crystal is as good as free, and a law writing FC every 0.1 s overrides the loop.
``--node A --low-gain G --cycles 0`` does the same to A's unit at gain G without FC steps (A's
link never opens its FS725), to see what A's loop puts into theta.

Procedure.  The link runs (``m4c_v2_link.py --scenario acquire``: B finds A's PPS, both keep
their queue entries, nothing steers).  Once B is locked, a script on B, started detached so that
it finishes even if the connection drops: ``--on`` s with the FLL running (FC read every 0.1 s),
then ``GA 1`` and ``--off`` s with FC left where the loop had it, then ``--cycles`` cycles of
FC steps of ``--step`` low-DAC units (each level held ``--hold`` s and written again every 0.5 s
against the slow loop: up, back, down, back, or up and back when the DAC has no room below),
then FC back and the gain it had, in a ``finally`` (also on SIGTERM).
Nothing is written to the EEPROM.  Analysis (``--analyze DIR`` again): per-pair theta in each
span; detrended 0.1 s means; their step change, Allan deviation, and the error of an AR
prediction 0.1-2 s ahead (what limits steering); and one dead time and time constant fitted to
all FC steps together (each with its own offset, rate and size).  Results in
results/m4c-v2-acquire-<time>/ (the link's directory), fll_off.json.

    .venv/bin/python experiments/m5_fll_off.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from m3_common import check_idle, ssh
from rf_clock.twtt import logs

A, B = "192.168.2.99", "192.168.2.100"
PYNQ = "sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
BOARD_LOG = "/dev/shm/fll_off.jsonl"
BOARD_CODE = r'''
import json, signal, sys, time
from rf_clock.fs725 import FS725
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))  # so that the finally below runs: FC back, LO 1
signal.signal(signal.SIGINT, signal.default_int_handler)  # a detached process starts with SIGINT ignored
from rf_clock.pl.board_config import load_board_config
on_s, off_s, step, hold, cycles, low_gain = (float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3]),
                                             float(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]))
unit = FS725.open(load_board_config().fs725["port"])


def log(**k):
    print(json.dumps({"time": time.time(), **k}), flush=True)


def write(cmd):
    unit.port.write(cmd.encode() + b"\r")


base = None
gain = unit.gain()
try:
    log(event="start", fc=unit.frequency_control(), lo=unit.query("LO?"), ga=gain)
    end = time.time() + on_s
    while time.time() < end:
        log(event="fc", fc=unit.frequency_control())
        time.sleep(0.1)
    base = unit.frequency_control()
    t = time.time()
    write(f"GA{low_gain}")  # GA 1: the loop at a 128 s time constant; GA 0 and LO 0 stop the 1PPS
    log(event="off", written=t, fc=base, lo=unit.query("LO?"), ga=unit.gain())
    end = time.time() + off_s
    while time.time() < end:
        log(event="fc", fc=unit.frequency_control())
        time.sleep(1.0)
    high, low = base
    if low - step >= 1024 and low + step <= 3072:
        levels = [step, 0, -step, 0]
    else:
        levels = [step, 0] if low + step <= 3072 else [-step, 0]
    for _ in range(cycles):
        for d in levels:
            t = time.time()
            write(f"FC{high},{low + d}")
            log(event="set", written=t, fc=[high, low + d], step=d, read=unit.frequency_control())
            end = t + hold
            while time.time() < end - 0.5:  # the slow loop pulls FC back about 2 units a second: hold it
                time.sleep(0.5)
                write(f"FC{high},{low + d}")
            time.sleep(max(0.0, end - time.time()))
finally:
    if base is not None and cycles > 0:  # after FC steps; without them FC stays the loop's
        write(f"FC{base[0]},{base[1]}")
    write(f"GA{gain}")
    write("LO1")
    time.sleep(0.5)
    log(event="restored", lo=unit.query("LO?"), ga=unit.gain(), fc=unit.frequency_control())
    unit.close()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--on", type=float, default=300.0, help="seconds with the FLL running first")
    parser.add_argument("--off", type=float, default=600.0, help="seconds at --low-gain, FC left alone")
    parser.add_argument("--low-gain", type=int, default=1, help="the FLL's gain in the second span (1: 128 s)")
    parser.add_argument("--node", choices=("A", "B"), default="B", help="whose FS725 (A: no FC steps then)")
    parser.add_argument("--step", type=int, default=200, help="FC steps, low-DAC units (about 1e-12 each)")
    parser.add_argument("--hold", type=float, default=12.0, help="seconds at each FC level")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--analyze", type=Path, metavar="DIR", help="analyze an earlier run again")
    args = parser.parse_args()
    if args.analyze is not None:
        analyze(args.analyze)
        return
    node = A if args.node == "A" else B
    for ip in (A, B):
        check_idle(ip)
    seconds = int(120 + args.on + args.off + args.cycles * 4 * args.hold + 60)
    # a log left by an earlier run holds its own "locked": the FS725 must stay untouched until the
    # link has checked it and B has moved its PPS (acquire_peer), so only this run's lock counts
    ssh(B, "sudo -n rm -f /dev/shm/m4c_B.jsonl /dev/shm/m4c_B.out")
    launched = time.time()
    link = subprocess.Popen([sys.executable, str(Path(__file__).with_name("m4c_v2_link.py")), "--scenario", "acquire",
                             "--seconds", str(seconds)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + seconds
    while True:
        line = ssh(B, "grep -m1 '^{\"type\": \"locked\"' /dev/shm/m4c_B.jsonl 2>/dev/null").stdout.strip()
        if line and json.loads(line)["time"] > launched:
            break
        if time.time() > deadline or link.poll() is not None:
            sys.exit("B did not lock; FS725 untouched")
        time.sleep(5)
    time.sleep(10)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "fll_off_board.py"
        tmp.write_text(BOARD_CODE, encoding="utf-8", newline="\n")
        subprocess.run(["scp", "-q", str(tmp), f"xilinx@{node}:/tmp/fll_off_board.py"], check=True)
    ssh(node, f"cd /tmp && (setsid nohup {PYNQ} /tmp/fll_off_board.py {args.on} {args.off} {args.step} {args.hold} "
              f"{args.cycles} {args.low_gain} > {BOARD_LOG} 2>&1 < /dev/null &)")
    print(f"B locked; sequence started on {args.node} ({args.on:.0f} s, then {args.off:.0f} s at GA {args.low_gain}, "
          f"{args.cycles} cycles of FC steps)", flush=True)
    assert link.stdout is not None
    text = link.stdout.read()
    link.wait()
    print(text[-2000:], flush=True)
    found = re.search(r"results in (\S+)", text)
    if found is None:
        sys.exit("the link printed no results directory")
    out = Path(found.group(1))
    for _ in range(60):  # the sequence ends before the link; wait for its last line anyway
        if '"restored"' in ssh(node, f"tail -1 {BOARD_LOG}").stdout:
            break
        time.sleep(5)
    else:  # the link ended early: stop the sequence now, its finally puts the FLL back
        ssh(node, "sudo -n pkill -TERM -f '^/usr/local/share/pynq-venv/bin/python /tmp/fll_off_board.py'")
        time.sleep(3)
    subprocess.run(["scp", "-q", f"xilinx@{node}:{BOARD_LOG}", str(out / "fll_off.jsonl")], check=True)
    last = (out / "fll_off.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    if '"restored"' not in last or '"lo": "1"' not in last:
        print(f"{args.node}'s FS725 may still be at GA {args.low_gain}: last line {last!r}", flush=True)
    analyze(out)


def ar_fit(f: np.ndarray, p: int) -> np.ndarray:
    x = np.column_stack([f[p - j - 1:f.size - j - 1] for j in range(p)])
    a, *_ = np.linalg.lstsq(x, f[p:], rcond=None)
    return a


def span_stats(t: np.ndarray, th: np.ndarray, t0: float, t1: float, P: float = 0.1) -> dict:
    """theta's 0.1 s means over t0 .. t1 (B's count): step change, Allan deviation, AR prediction error."""
    n = int((t1 - t0) / P)
    k = ((t - t0) / P).astype(int)
    ok = (t >= t0) & (k < n)
    s = np.asarray(np.bincount(k[ok], th[ok], n), dtype=float)
    cnt = np.asarray(np.bincount(k[ok], minlength=n))
    have = cnt > 0
    m = np.interp(np.arange(n), np.flatnonzero(have), s[have] / cnt[have])
    x = np.arange(n) * P
    m = m - np.polyval(np.polyfit(x, m, 1), x)
    adev = {}
    for q in (1, 2, 5, 10, 20, 50, 100, 200):
        if 2 * q < n:
            d = m[2 * q:] - 2 * m[q:-q] + m[:-2 * q]
            adev[round(q * P, 1)] = round(float(np.sqrt(np.mean(d * d) / 2) / (q * P)), 3)  # ps/s = 1e-12
    p, mean_n = 40, 600
    f = np.diff(m) / P
    cs = np.concatenate([[0.0], np.cumsum(f)])
    i = np.arange(f.size)
    lo = np.maximum(i - mean_n + 1, 0)
    a = ar_fit(f - (cs[i + 1] - cs[lo]) / (i + 1 - lo), p)  # about the trailing mean, as the prediction
    errs = {h: [] for h in (1, 2, 5, 10, 20)}
    for i in range(mean_n + p, n - 21):
        mean = f[i - mean_n:i].mean()
        noise = list(f[i - p:i] - mean)
        ph, pred = m[i], {}
        for h in range(1, 21):
            nxt = float(np.dot(a, noise[::-1][:p]))
            noise.append(nxt)
            ph += (mean + nxt) * P
            pred[h] = ph
        for h in errs:
            errs[h].append(pred[h] - m[i + h])
    return {"seconds": round(t1 - t0, 1), "pairs": int(ok.sum()),
            "step_change_ps": round(float(np.std(np.diff(m))), 3),
            "adev_1e-12": adev,
            "prediction_error_ps": {round(h * P, 1): round(float(np.sqrt(np.mean(np.square(v)))), 3)
                                    for h, v in errs.items() if v}}


def lag_phase(s: np.ndarray, dead: float, tau: float) -> np.ndarray:
    x = s - dead
    return np.where(x > 0, x - tau * (1 - np.exp(-np.clip(x, 0, None) / tau)), 0.0)


def fit_steps(t, th, sets, hold):
    """One dead time and RC time constant for all FC steps: theta = a + b s + D lag_phase(s)."""
    spans = []
    for e in sets:
        tw = e["count"]
        sel = (t >= tw - 2.0) & (t < tw + hold - 0.5)
        spans.append((t[sel] - tw, th[sel], e))

    def cost(dead, tau):
        total, sizes = 0.0, []
        for s, y, _ in spans:
            x = np.column_stack([np.ones_like(s), s, lag_phase(s, dead, tau)])
            coef, res, *_ = np.linalg.lstsq(x, y, rcond=None)
            total += float(res[0]) if res.size else float(np.sum((y - x @ coef) ** 2))
            sizes.append(float(coef[2]))
        return total, sizes

    best = (float("inf"), 0.0, 1.0)
    for dead in np.arange(0.0, 1.0, 0.02):
        for tau in np.arange(0.3, 3.0, 0.1):
            c, _ = cost(dead, tau)
            if c < best[0]:
                best = (c, float(dead), float(tau))
    _, d0, t0 = best
    for dead in np.arange(max(0.0, d0 - 0.02), d0 + 0.021, 0.002):
        for tau in np.arange(max(0.1, t0 - 0.1), t0 + 0.101, 0.01):
            c, _ = cost(dead, tau)
            if c < best[0]:
                best = (c, float(dead), float(tau))
    c, dead, tau = best
    _, sizes = cost(dead, tau)
    per_unit = [D / sp[2]["step_change"] for D, sp in zip(sizes, spans) if sp[2]["step_change"]]
    n = sum(s.size for s, _, _ in spans)
    return {"dead_s": round(float(dead), 3), "tau_s": round(float(tau), 3), "steps": len(spans),
            "residual_ps": round(float(np.sqrt(c / n)), 3),
            "ps_per_s_per_unit": [round(v, 4) for v in per_unit],
            "ps_per_s_per_unit_mean": round(float(np.mean(per_unit)), 4) if per_unit else None}


def analyze(out: Path) -> None:
    seq = [json.loads(x) for x in (out / "fll_off.jsonl").read_text(encoding="utf-8").splitlines()
           if x.startswith("{")]
    own = [e for e in logs.read(out / "m4c_B.jsonl", ("own",)) if e["locked"]]
    lat = np.array([e["time"] - (e["pps_count"] + e["slot"] / 500) for e in own])
    offset = float(np.percentile(lat, 1))  # Unix time of B's count 0, to a few ms (the quickest reads)
    pairs = logs.pairs(out / "m4c_B.jsonl")
    t = np.array([p.count + p.slot / 500 for p in pairs])
    th = np.array([p.theta_ns for p in pairs]) * 1e3
    ev = {e["event"]: e for e in seq if e["event"] in ("start", "off", "restored")}
    sets = [e for e in seq if e["event"] == "set"]
    prev = ev["off"]["fc"][1]
    for e in sets:
        e["count"] = e["written"] - offset
        e["step_change"] = e["fc"][1] - prev
        prev = e["fc"][1]
    on0, off0 = ev["start"]["time"] - offset, ev["off"]["written"] - offset
    first_set = sets[0]["count"] if sets else ev["restored"]["time"] - offset
    hold = float(np.median(np.diff([e["count"] for e in sets]))) if len(sets) > 1 else 12.0
    result = {"offset_unix_minus_count": offset, "events": ev,
              "fll_on": span_stats(t, th, on0 + 5, off0),
              "fll_low": span_stats(t, th, off0 + 5, first_set),
              "fc_steps": fit_steps(t, th, sets, hold) if sets else None}
    (out / "fll_off.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    for k in ("fll_on", "fll_low", "fc_steps"):
        print(k, result[k])
    print(f"results in {out}")


if __name__ == "__main__":
    main()
