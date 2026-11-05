"""M4c: the rf-clock 2 link between the two boards, measurements carried in the bursts.

Problem.  rf-clock 2 moves the exchange of measurements from Ethernet into the
bursts (docs/ABI.md 4.8-4.12): each node's PL tracks the peer's bursts, puts its
own measurement into the packet behind its next burst, and decodes the peer's.
Simulation showed the chain to the bit (PL/sim/tb_p_top2.v); on the boards it has
to show that both PLs find each other's bursts, lock after the ARM accepts the
search's peak, keep the lock, and decode every packet, and that the two-way
equations close: with both boards on one reference, theta = (m_AB - m_BA) / 2 is
the zero-baseline constant of M4 (the PPS distribution and Delta/2), its scatter
the system's precision, and each node computes it from its own queue alone.

Wiring: A's DAC_A -> band-pass -> attenuator -> B's ADC_B, B's DAC_A back the same
way; both boards on the EPG1 10 MHz and PPS.  Run on RF-clock-PC as the normal
user, with rf-clock 2 installed on both boards and both idle::

    .venv/bin/python experiments/m4c_v2_link.py --seconds 60
    .venv/bin/python experiments/m4c_v2_link.py --scenario relock

``--scenario`` adds the protocol tests of ``rf_clock.checks.m4c_link`` (SCENARIOS
below), and experiments/m4c_check.py judges the run: ``copy`` copies a window
to DDR every 5 s on both nodes; ``search`` asks for a search every 3 s while
locked; ``relock`` silences B from 20 s to 23 s, so that A loses the lock and must
take it again by itself; ``reject`` gives A an expected arrival 568 samples off, so
that A rejects every search and must stay unlocked; ``edge-low`` and ``edge-high``
put the peak near the first and the last lag of the window; ``refusals`` checks
P2's refused commands and the refused burst memory write, and lets the queue
overflow for 3 s; ``acquire`` (each board on its own FS725) lets B find A's PPS in a
long window and move its own onto it with its FS725's PP before its slots start.
With two clocks theta drifts at their frequency difference: summary.json gives its
slope and the scatter about the fitted line.  The M5 scenarios, each board on its
own FS725, all start with B's acquisition and log one line per second instead of
the queue entries: ``free`` lets the clocks run, ``sf-step`` raises B's SF by 100
from 120 s to 240 s (theta's rate must rise by 100 ps/s), and ``steer`` steers B's
FS725 so that theta goes to theta0, the shared-reference theta of the wiring on the bench
(links/887329260072A_8873292600AB.json: its ``current`` wiring, or ``--wiring NAME``;
``--target-ns`` overrides it).  B's FS725 is the one its board config names, on the port
the config records (``m4c_link --fs725``).  ``--scope`` logs the two FS725s' 10 MHz on the
HDO9404-MS for the length of the run (experiments/m5_hdo_phase.py, run as root
through sudo), with ``--scope-pps`` the two 1PPS too, with ``--scope-out-pps`` the two
output PPS instead (C3 A's DAC_B, C4 B's; pass ``--node-args "--pps-out 20"``).  ``--a-args`` / ``--b-args`` go to
one node only: since 2026-11-04 A times its Schmitt-trigger PPS copy (``--a-args
--pps-trig``), whose edge lies clear of A's sampling edge where the comparator's did not.

Each board runs ``rf_clock.checks.m4c_link`` (A: even slots, B: odd), both told
to enable their slot engines at one time and disable them at another, half a
second into a second, so that both run from the same PPS edge to the same PPS edge.  Results go
to results/m4c-v2-link-<time>/: each node's queue log and console output, and
summary.json with each node's result, theta, m_AB, m_BA and the one-way delay
(mean, sd), computed from each node's own queue as the node does
(``rf_clock.twtt.logs.pairs``).
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import statistics
import subprocess
import sys
import time
from pathlib import Path

from m3_common import check_idle, results_dir, ssh

from rf_clock.twtt import logs

BOARDS = {"A": "192.168.2.99", "B": "192.168.2.100"}  # S/N 887329260072A, 8873292600AB
PYNQ_PYTHON = "/usr/local/share/pynq-venv/bin/python"
OUT_DIR = "/dev/shm"  # node output in RAM: SD card writes have stalled node loops
NODE = " -m rf_clock[.]checks[.]m4c_link "
START_S = 150  # time for both PL.open
LINK = Path(__file__).parents[1] / "links" / "887329260072A_8873292600AB.json"  # theta0 by wiring
SCENARIOS = {  # scenario: (node A's arguments, node B's)
    "link": ("", ""),
    "copy": ("--copy-every 5", "--copy-every 5"),
    "search": ("--search-every 3", "--search-every 3"),
    "relock": ("", "--silent-at 20 --silent-for 3"),
    "reject": ("--rx-offset 904 --arrival-ticks 100", ""),
    "edge-low": ("--rx-offset 1025", "--rx-offset 1025"),
    "edge-high": ("--tx-offset 2000 --rx-offset 660", "--tx-offset 2000 --rx-offset 660"),
    "refusals": ("--refusal-checks --pause-reads-at 20 --pause-for 3",
                 "--refusal-checks --pause-reads-at 20 --pause-for 3"),
    # each board on its own FS725: B finds A's PPS and moves its own onto it (plan section 5.2)
    "acquire": ("", "--acquire --fs725"),
    "free": ("--no-entries", "--no-entries --acquire --fs725"),
    "sf-step": ("--no-entries", "--no-entries --acquire --fs725 --sf-step 100 --sf-step-at 120 --sf-step-for 120"),
    "steer": ("--no-entries", "--no-entries --acquire --fs725 --steer-target-ns {target}"),
}


def start(ip: str, role: str, start_at: float, stop_at: float, extra: str) -> None:
    out = f"{OUT_DIR}/m4c_{role}"
    cmd = (f"sudo -n rm -rf {out}.jsonl {out}.out {OUT_DIR}/m4c_copies_{role}; cd /tmp && "
           f"(setsid nohup sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr {PYNQ_PYTHON} -m rf_clock.checks.m4c_link "
           f"--role {role} --start-at {start_at} --stop-at {stop_at} --out {out}.jsonl "
           f"--copy-dir {OUT_DIR}/m4c_copies_{role} {extra} > {out}.out 2>&1 < /dev/null &)")
    if ssh(ip, cmd).returncode:
        sys.exit(f"cannot start node {role} on {ip}")
    print(f"node {role} started on {ip}", flush=True)


def running(ip: str) -> bool:
    return ssh(ip, f"pgrep -fc '^{PYNQ_PYTHON}{NODE}'").stdout.strip() not in ("", "0")


def fetch(ip: str, role: str, out: Path) -> Path:
    for suffix in ("jsonl", "out"):
        subprocess.run(["scp", "-q", f"xilinx@{ip}:{OUT_DIR}/m4c_{role}.{suffix}", str(out)])
    if ssh(ip, f"test -d {OUT_DIR}/m4c_copies_{role}").returncode == 0:
        subprocess.run(["scp", "-q", "-r", f"xilinx@{ip}:{OUT_DIR}/m4c_copies_{role}", str(out / f"copy_{role}")])
    return out / f"m4c_{role}.jsonl"


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": statistics.fmean(values),
            "sd": statistics.stdev(values) if len(values) > 1 else 0.0}


def two_way(log: Path, entries: list[dict]) -> dict:
    """theta, m_AB, m_BA and the one-way delay of every pair in one node's queue
    (``rf_clock.twtt.logs.pairs``), and the quality of its own measurements."""
    slot_ticks = logs.config(entries)["slot_ticks"]
    pairs = logs.pairs(log)
    theta = [p.theta_ns for p in pairs]
    when = [p.count + p.slot * slot_ticks / 1e8 for p in pairs]
    own = [e for e in entries if e["type"] == "own" and e["locked"]]
    fit = {}
    if len(theta) > 2:
        t0 = when[0]
        slope, icept = np.polyfit(np.array(when) - t0, np.array(theta), 1)
        resid = np.array(theta) - (slope * (np.array(when) - t0) + icept)
        fit = {"slope_ps_per_s": float(slope * 1000), "residual_sd_ps": float(resid.std(ddof=2) * 1000)}
    return {"pairs": len(theta), "theta_ns": stats(theta), "theta_fit": fit,
            "delay_ns": stats([p.delay_ns for p in pairs]), "m_ab_ns": stats([p.m_ab_ns for p in pairs]),
            "m_ba_ns": stats([p.m_ba_ns for p in pairs]), "quality_db": stats([e["quality"] / 4 for e in own])}


def per_second(entries: list[dict]) -> dict:
    """theta from the nodes' ``second`` lines (one mean per second): its line and the
    scatter about it, and the steering's SF changes."""
    rows = [e for e in entries if e["type"] == "second"]
    out: dict = {"seconds": len(rows)}
    if len(rows) > 2:
        t = np.array([r["count"] for r in rows], dtype=float)
        th = np.array([r["theta_ns"] for r in rows])
        slope, icept = np.polyfit(t - t[0], th, 1)
        out.update(theta_first_ns=float(th[0]), theta_last_ns=float(th[-1]),
                   slope_ps_per_s=float(slope * 1000),
                   residual_sd_ps=float((th - (slope * (t - t[0]) + icept)).std(ddof=2) * 1000),
                   last_600_s=stats([r["theta_ns"] for r in rows[-600:]]))
    out["sf"] = [{"time": e["time"], "sf": e["sf"], "why": e["why"]} for e in entries if e["type"] == "sf"]
    out["events"] = [e for e in entries if e["type"] in ("steer_stopped", "restore_failed", "frequency_control")]
    return out


def summarize(log: Path) -> dict:
    entries = logs.read(log)
    result = next((e for e in reversed(entries) if e["type"] == "result"), None)
    reports = [e for e in entries if e["type"] == "report"]
    return {"result": result, "searches": len(reports),
            "first_report": reports[0] if reports else None,
            "accepted": [e for e in entries if e["type"] == "accept"],
            **two_way(log, entries), "per_second": per_second(entries)}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="link")
    parser.add_argument("--node-args", default="", help="extra arguments for rf_clock.checks.m4c_link")
    parser.add_argument("--target-ns", type=float, default=None,
                        help="steer: theta to steer to (default: theta0 of the wiring, from links/)")
    parser.add_argument("--wiring", default=None, help="the wiring of links/ (default: its current one)")
    parser.add_argument("--scope", action="store_true", help="log the two 10 MHz on the HDO9404-MS")
    parser.add_argument("--scope-pps", action="store_true", help="with --scope: also the two 1PPS on C3 (B) and C4 (A)")
    parser.add_argument("--scope-out-pps", action="store_true",
                        help="with --scope: the two output PPS on C3 (A) and C4 (B) instead")
    parser.add_argument("--a-args", default="", help="extra arguments for node A only (e.g. --pps-trig)")
    parser.add_argument("--b-args", default="", help="extra arguments for node B only")
    args = parser.parse_args()

    link = json.loads(LINK.read_text(encoding="utf-8"))
    wiring = args.wiring or link["current"]
    target = args.target_ns if args.target_ns is not None else link["wirings"][wiring]["theta0_ns"]
    if args.scenario == "steer":
        print(f"steering to {target * 1000:+.1f} ps"
              + ("" if args.target_ns is not None else f", theta0 of wiring {wiring} ({LINK.name})"), flush=True)
    for ip in BOARDS.values():
        check_idle(ip)
    out = results_dir(f"m4c-v2-{args.scenario}")
    own = {"A": args.a_args, "B": args.b_args}
    extra = {role: f"{a.format(target=target)} {args.node_args} {own[role]}".strip()
             for role, a in zip(BOARDS, SCENARIOS[args.scenario])}
    (out / "settings.json").write_text(json.dumps({"seconds": args.seconds, "boards": BOARDS,
                                                   "scenario": args.scenario, "node_args": extra,
                                                   "link": {"file": LINK.name, "wiring": wiring,
                                                            "target_ns": target}},
                                                  indent=2) + "\n",
                                       encoding="utf-8", newline="\n")
    start_at = int(time.time() + START_S) + 0.5  # both PL.open done by then
    stop_at = start_at + args.seconds
    for role, ip in BOARDS.items():
        start(ip, role, start_at, stop_at, extra[role])
    scope = None
    if args.scope:
        scope = subprocess.Popen(["sudo", "-n", sys.executable, str(Path(__file__).with_name("m5_hdo_phase.py")),
                                  "--seconds", str(stop_at - time.time() + 5), "--out", str(out / "hdo.jsonl")]
                                 + (["--out-pps2"] if args.scope_out_pps else ["--pps"] if args.scope_pps else []),
                                 stdout=open(out / "hdo.out", "w"), stderr=subprocess.STDOUT)
    deadline = time.monotonic() + (stop_at - time.time()) + 60
    while any(running(ip) for ip in BOARDS.values()):
        if time.monotonic() > deadline:
            for ip in BOARDS.values():
                ssh(ip, f"sudo -n pkill -TERM -f '^{PYNQ_PYTHON}{NODE}'")
            print("a node overran its time and was stopped", flush=True)
            break
        time.sleep(10)
    if scope is not None:
        try:
            scope.wait(timeout=60)
        except subprocess.TimeoutExpired:
            subprocess.run(["sudo", "-n", "pkill", "-TERM", "-f", "^" + sys.executable + " .*m5_hdo_phase[.]py"])
    summary = {}
    for role, ip in BOARDS.items():
        log = fetch(ip, role, out)
        summary[role] = summarize(log) if log.exists() else {"error": "no log"}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    for role, s in summary.items():
        r = s.get("result") or {}
        t, d = s.get("theta_ns", {}), s.get("delay_ns", {})
        print(f"node {role}: {r}")
        if t.get("n"):
            print(f"  {t['n']} pairs: theta {t['mean']:.4f} ns (sd {t['sd'] * 1000:.2f} ps), "
                  f"delay {d['mean']:.4f} ns, m_AB {s['m_ab_ns']['mean']:.4f}, m_BA {s['m_ba_ns']['mean']:.4f} ns, "
                  f"quality {s['quality_db']['mean']:.1f} dB")
            fit = s.get("theta_fit") or {}
            if fit:
                print(f"  theta slope {fit['slope_ps_per_s']:.3f} ps/s, sd about the line {fit['residual_sd_ps']:.2f} ps")
        ps = s.get("per_second") or {}
        if ps.get("seconds", 0) > 2:
            print(f"  {ps['seconds']} seconds: theta {ps['theta_first_ns']:.4f} -> {ps['theta_last_ns']:.4f} ns, "
                  f"slope {ps['slope_ps_per_s']:.3f} ps/s, last 600 s mean {ps['last_600_s']['mean']:.4f} ns "
                  f"(sd {ps['last_600_s']['sd'] * 1000:.1f} ps), {len(ps['sf'])} SF changes")
    print(f"results in {out}")
    print(f"judge it: uv run python experiments/m4c_check.py scenario results/{out.name}")


if __name__ == "__main__":
    main()
