"""M5: how B's FS725 answers a change of FC, to a few ms: the dead time, the lag and the gain.

Problem.  The steering through FC models the FS725's answer as a first-order lag of 1.12 s with
no dead time beyond the write (results/precision-20261105 section 8, fitted on 12 steps of FC
and theta's 0.1 s means, which could not tell a dead time of 0 from 0.1 s), and its model keeps
0.1 s more dead time as a margin, because in replay the algorithm oscillates once the FS725
answers later than its model by about 0.1 s.  Since rf-clock 2.4.0.1 each write's time is known
to a tick, and in replay a margin of 0.02 s would leave about 15 % less theta - theta0
(results/fc-steering-20261112), but only if the FS725's dead time is known to a few ms.

Procedure.  ``m4c_v2_link.py --scenario fc-steps``: B acquires A's PPS, then its m4c_link steps FC
through the steering's own path (``steer.FCSteps``: GA 1, FC written every 0.1 s step, the PL's
tick read right after each write, the unit watched by ``FCWatch``), ``--size`` fine steps up,
back, down and back, each level held ``--hold`` s, and B keeps its queue entries.  Analysis
(``--analyze DIR``, also run at the end): theta pair by pair, one pair every 4 ms, each timed at
the middle of its two bursts (this node's PPS count plus the peer's slot less half a slot); each
change of FC at the count read right after its write (``fc_set`` ``written_count``).  In a
window from 0.4 s before each change to 0.1 s before the next, theta is taken as a straight line
of the window's own (the free-running clocks) plus the summed answer of the changes of the last
10 s, each a first-order lag ``tau`` after a dead time ``d``, times a gain.  The dead time and
the lag that leave the least residual are found on a grid, the lines and the gain by least
squares, and the fit is repeated on each quarter of the run for its spread.  The mean answer to
one fine step, with the fitted curve, goes to ``fc_response.png``, the numbers to
``fc_response.json``, both in the link's results directory, with the write times after each
step's end.  A's FS725 is left out (``--a-args --pps-trig``), as nothing here needs it.

    .venv/bin/python experiments/m5_fc_response.py [--seconds 1200 --size 400 --hold 2]
    .venv/bin/python experiments/m5_fc_response.py --analyze results/m4c-v2-fc-steps-<time>
    .venv/bin/python experiments/m5_fc_response.py --io-figure results/m4c-v2-fc-steps-<time>   # fc_steps_io.png only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from rf_clock.twtt import logs

BIN_S = 0.01  # theta's pairs averaged over 10 ms bins for the fit
BEFORE_S = 0.4  # each window starts this long before its change
MEMORY_S = 10.0  # changes older than this answer as a straight line within a window


def answer(t: np.ndarray, d: float, tau: float) -> np.ndarray:
    """theta's answer (ps per ps/s of final rate) to a step of FC written at t = 0."""
    x = np.maximum(t - d, 0.0)
    return x - tau * (1.0 - np.exp(-x / tau))


def windows(t: np.ndarray, th: np.ndarray, w: np.ndarray, c: np.ndarray, hold: float) -> list[dict]:
    """theta's 10 ms means in each change's window, with the changes that act on it."""
    out = []
    for i in range(w.size):
        end = w[i + 1] - 0.1 if i + 1 < w.size else w[i] + hold - 0.1
        sel = (t >= w[i] - BEFORE_S) & (t < end)
        if sel.sum() < 100:
            continue
        # at 0, not -1, when a pair's time equals the window's start but for rounding (a write 15 ms after the step's end)
        b = np.maximum(np.floor((t[sel] - w[i] + BEFORE_S) / BIN_S).astype(int), 0)
        n = np.asarray(np.bincount(b), dtype=float)
        keep = n > 0
        tb = (np.asarray(np.bincount(b, t[sel]), dtype=float) / np.maximum(n, 1))[keep]
        yb = (np.asarray(np.bincount(b, th[sel]), dtype=float) / np.maximum(n, 1))[keep]
        near = (w > w[i] - MEMORY_S) & (w <= w[i])
        out.append({"i": i, "w": float(w[i]), "c": float(c[i]), "t": tb, "y": yb, "wj": w[near], "cj": c[near]})
    return out


def fit(win: list[dict], d: float, tau: float) -> tuple[float, float, list[np.ndarray]]:
    """Residual sum of squares and gain (ps/s per fine step) for one dead time and lag; each
    window's own line is projected out."""
    num = den = 0.0
    parts = []
    for wd in win:
        x = wd["t"] - wd["w"]
        a = np.column_stack([np.ones_like(x), x])
        g = answer(wd["t"][:, None] - wd["wj"][None, :], d, tau) @ wd["cj"]
        q, _ = np.linalg.qr(a)
        ry = wd["y"] - q @ (q.T @ wd["y"])
        rg = g - q @ (q.T @ g)
        num += float(ry @ rg)
        den += float(rg @ rg)
        parts.append((ry, rg))
    s = num / den
    rss = sum(float(np.sum((ry - s * rg) ** 2)) for ry, rg in parts)
    return rss, s, [ry - s * rg for ry, rg in parts]


def best(win: list[dict]) -> dict:
    """The dead time and lag on a coarse grid, then a fine one around the best."""
    def search(ds: np.ndarray, taus: np.ndarray) -> tuple[float, float]:
        scores = [(fit(win, d, tau)[0], d, tau) for d in ds for tau in taus]
        _, d, tau = min(scores)
        return float(d), float(tau)

    d, tau = search(np.arange(-0.03, 0.2001, 0.01), np.arange(0.7, 1.6001, 0.05))
    d, tau = search(np.arange(d - 0.012, d + 0.0121, 0.002), np.arange(tau - 0.06, tau + 0.0601, 0.01))
    d, tau = search(np.arange(d - 0.002, d + 0.0021, 0.0005), np.arange(tau - 0.01, tau + 0.0101, 0.002))
    rss, s, res = fit(win, d, tau)
    n = sum(r.size for r in res)
    return {"dead_s": d, "lag_s": tau, "ps_per_s_per_step": s, "rms_ps": float(np.sqrt(rss / n)), "points": n,
            "windows": len(win)}


def analyze(out: Path) -> dict:
    b = out / "m4c_B.jsonl"
    cfg = logs.config(logs.read(b, ("config",)))
    sps = cfg["slots_per_second"]
    hold = float(cfg["tests"]["fc_step_hold"])
    pairs = logs.pairs(b)
    t = np.array([p.count + (p.slot - 0.5) / sps for p in pairs])
    th = np.array([p.theta_ns * 1000 for p in pairs])
    sets = [e for e in logs.read(b, ("fc_set",)) if e.get("written_count") is not None]
    w = np.array([e["written_count"] for e in sets])
    c = np.array([float(e["change"]) for e in sets])
    late = np.array([e["late_s"] for e in sets]) * 1000
    took = (w - np.array([e["at_count"] for e in sets])) * 1000
    first = w[0] + 2 * hold  # the first changes come with the pull-in of the start; steps after them
    keep = w >= first
    win = windows(t, th, w[keep], c[keep], hold)
    res: dict = {"run": out.name, "pairs": int(t.size), "changes": int(keep.sum()), "hold_s": hold,
                 "size": int(cfg["tests"]["fc_step_size"]),
                 "write_after_step_ms": {f"p{q}": round(float(np.percentile(late, q)), 1) for q in (0, 1, 50, 99, 100)},
                 "write_after_tick_read_ms": {f"p{q}": round(float(np.percentile(took, q)), 1) for q in (50, 99, 100)}}
    res["fit"] = best(win)
    quarters = np.array_split(np.arange(len(win)), 4)
    res["quarters"] = [best([win[k] for k in q]) for q in quarters]
    for key in ("dead_s", "lag_s", "ps_per_s_per_step"):
        v = np.array([q[key] for q in res["quarters"]])
        res["fit"][f"{key}_quarter_sd"] = float(v.std(ddof=1))
    # the mean answer to one fine step: each window less its own line and the answer of the earlier
    # changes, by the fitted model, over the change's size
    f = res["fit"]
    grid = np.arange(-BEFORE_S, hold - 0.1, BIN_S)
    acc, cnt = np.zeros(grid.size), np.zeros(grid.size)
    for wd in win:
        x = wd["t"] - wd["w"]
        a = np.column_stack([np.ones_like(x), x])
        older = wd["wj"] < wd["w"]
        g_old = (answer(wd["t"][:, None] - wd["wj"][older][None, :], f["dead_s"], f["lag_s"]) @ wd["cj"][older]
                 if older.any() else np.zeros_like(x))
        g_own = answer(x, f["dead_s"], f["lag_s"]) * wd["c"]
        coef, *_ = np.linalg.lstsq(a, wd["y"] - f["ps_per_s_per_step"] * (g_old + g_own), rcond=None)
        own = (wd["y"] - a @ coef - f["ps_per_s_per_step"] * g_old) / wd["c"]
        k = np.round((x + BEFORE_S) / BIN_S).astype(int)
        ok = (k >= 0) & (k < grid.size)
        np.add.at(acc, k[ok], own[ok])
        np.add.at(cnt, k[ok], 1)
    mean = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
    res["mean_answer"] = {"t_s": grid.round(3).tolist(), "ps_per_step": [None if np.isnan(v) else round(float(v), 5)
                                                                          for v in mean]}
    (out / "fc_response.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: res[k] for k in ("run", "changes", "write_after_step_ms", "write_after_tick_read_ms")}))
    print(json.dumps(res["fit"]))
    try:
        figure(out, res, grid, mean, late)
        io_figure(out)
    except ImportError:
        print("no matplotlib here: fc_response.png not drawn (run --analyze where it is installed)")
    return res


def figure(out: Path, res: dict, grid: np.ndarray, mean: np.ndarray, late: np.ndarray) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    f = res["fit"]
    model = f["ps_per_s_per_step"] * answer(grid, f["dead_s"], f["lag_s"])
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
    label = f"fit, dead time {f['dead_s'] * 1000:.1f} ms, lag {f['lag_s']:.3f} s, {f['ps_per_s_per_step']:.3f} ps/s per fine step"
    for a, lim in ((ax[0], (-BEFORE_S, grid[-1])), (ax[1], (-0.1, 0.4))):
        sel = (grid >= lim[0]) & (grid <= lim[1])
        a.plot(grid[sel], mean[sel], ".", color="C0", ms=4, label="mean of every change, 10 ms bins")
        a.plot(grid[sel], model[sel], "-", color="C3", lw=1.2, label=label)
        a.axvline(0, color="0.6", lw=0.8)
        a.set(xlabel="time after the command's first start bit, on B's tick (s)",
              ylabel="change of θ per fine step of FC (ps)", xlim=lim)
    ax[0].set_title(f"θ's response to one fine step of FC ({res['changes']} changes)")
    ax[1].set_title("The same, its first 0.4 s")
    ax[1].set_ylim(-0.01, float(np.nanmax(model[(grid >= -0.1) & (grid <= 0.4)])) * 1.3 + 0.01)
    ax[0].legend(fontsize=8)
    ax[2].hist(late, bins=np.arange(0, max(80.0, float(late.max()) + 5), 2), color="C2")
    ax[2].set(xlabel="command's first start bit after its step's end (ms)", ylabel="changes",
              title="When each change of FC was sent")
    fig.tight_layout()
    fig.savefig(out / "fc_response.png", dpi=130)


def io_figure(out: Path, after_s: float = 600.0, span_s: float = 8.5) -> None:
    """FC, the input, and theta, the output, over a few seconds ``after_s`` into the steps, starting
    0.5 s before a jump up: theta pair by pair, and the answer the fit of fc_response.json gives,
    each less the straight line that fits it best over the span (the free-running clocks' drift, and
    the slope the earlier changes add), so that what is left is the bending the steps make
    (fc_steps_io.png)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    b = out / "m4c_B.jsonl"
    res = json.loads((out / "fc_response.json").read_text(encoding="utf-8"))
    f = res["fit"]
    sps = logs.config(logs.read(b, ("config",)))["slots_per_second"]
    sets = [e for e in logs.read(b, ("fc_set",)) if e.get("written_count") is not None]
    w = np.array([e["written_count"] for e in sets])
    c = np.array([float(e["change"]) for e in sets])
    level = np.array([e["fc"] for e in sets]) - (sets[0]["fc"] - sets[0]["change"])  # FC less its value before the steps
    ups = w[level == res["size"]]
    later = ups[ups >= w[0] + after_s]
    up = later[0] if later.size else ups[0]  # a short run has no jump up that late: its first
    t0, t1 = up - 0.5, up - 0.5 + span_s
    pairs = logs.pairs(b)
    t = np.array([p.count + (p.slot - 0.5) / sps for p in pairs])
    th = np.array([p.theta_ns * 1000 for p in pairs])
    sel = (t >= t0) & (t < t1)
    t, th = t[sel], th[sel]
    near = (w > t0 - MEMORY_S) & (w < t1)  # older changes add only a straight line here
    model = f["ps_per_s_per_step"] * (answer(t[:, None] - w[near][None, :], f["dead_s"], f["lag_s"]) @ c[near])
    th = th - np.polyval(np.polyfit(t - up, th, 1), t - up)
    model = model - np.polyval(np.polyfit(t - up, model, 1), t - up)
    fig, ax = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True)
    inside = (w >= t0) & (w < t1)
    before = level[w < t0][-1]
    ax[0].step(np.concatenate([[t0], w[inside], [t1]]) - up, np.concatenate([[before], level[inside], [level[inside][-1]]]),
               where="post", color="C0", lw=1.6)
    ax[0].set(ylabel="FC less its value\nbefore the steps (fine steps)", title="The input, FC as written (each jump at the tick read right after its write)")
    ax[1].plot(t - up, th, ".", color="0.45", ms=2, label="θ of each pair of bursts (every 4 ms)")
    ax[1].plot(t - up, model, "-", color="C3", lw=1.2,
               label=f"the fitted answer, dead time {f['dead_s'] * 1000:.1f} ms, lag {f['lag_s']:.3f} s, "
                     f"{f['ps_per_s_per_step']:.3f} ps/s per step")
    ax[1].set(xlabel="time from a jump up, on B's tick (s)", ylabel="θ less its best straight\nline over the span (ps)",
              title="The output, θ as the link measures it")
    ax[1].legend(fontsize=8, loc="lower left")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "fc_steps_io.png", dpi=130)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--size", type=int, default=400, help="fine steps of FC up, back, down and back")
    parser.add_argument("--hold", type=float, default=2.0, help="seconds at each level")
    parser.add_argument("--analyze", type=Path, metavar="DIR", help="analyze an earlier run again")
    parser.add_argument("--io-figure", type=Path, metavar="DIR", help="only draw fc_steps_io.png of an analyzed run")
    args = parser.parse_args()
    if args.io_figure is not None:
        io_figure(args.io_figure)
        return
    if args.analyze is not None:
        analyze(args.analyze)
        return
    link = subprocess.run([sys.executable, str(Path(__file__).with_name("m4c_v2_link.py")), "--scenario", "fc-steps",
                           "--seconds", str(args.seconds), "--a-args=--pps-trig",
                           f"--b-args=--fc-step-size {args.size} --fc-step-hold {args.hold}"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(link.stdout[-3000:], flush=True)
    found = re.search(r"results in (\S+)", link.stdout)
    if found is None:
        sys.exit("the link printed no results directory")
    analyze(Path(found.group(1)))


if __name__ == "__main__":
    main()
