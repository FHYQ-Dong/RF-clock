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
FS725 so that theta goes to theta0, through FC with the predictive algorithm on 0.1 s steps
(``--steer-actuator fc``, since rf-clock 2.3.0.2; ``--b-args`` overrides it), the shared-reference theta of the wiring on the bench
(links/887329260072A_8873292600AB.json: its ``current`` wiring, or ``--wiring NAME``;
``--target-ns`` overrides it).  ``fc-steps`` (rf-clock 2.4.0.1 on both boards) steps B's FC on a
schedule through the same path instead, B keeping its queue entries, for the FS725's response
(experiments/m5_fc_response.py).  B's FS725 is the one its board config names, on the port
the config records (``m4c_link --fs725``).  ``--scope`` logs the two FS725s' 10 MHz on the
HDO9404-MS for the length of the run (experiments/m5_hdo_phase.py, run as root
through sudo), with ``--scope-pps`` the two 1PPS too, with ``--scope-out-pps`` the two
output PPS instead (C3 A's DAC_B, C4 B's; pass ``--node-args "--pps-out 20"``).  ``--scope-tap`` uses the
scope for B's RS-232 command line instead (experiments/m5_serial_tap.py, with the line on C1 and B's output PPS on
C4 as the trigger, pass ``--node-args "--pps-out 20"``), not with ``--scope``.  ``--a-args`` / ``--b-args`` go to
one node only: since 2026-11-04 A times its Schmitt-trigger PPS copy (``--a-args
--pps-trig``), whose edge lies clear of A's sampling edge where the comparator's did not.

Each board runs ``rf_clock.checks.m4c_link`` (A: even slots, B: odd), both told
to enable their slot engines at one time and disable them at another, half a
second into a second, so that both run from the same PPS edge to the same PPS edge.
The two times are given once both nodes are ready (``m4c_link --start-file``, since
rf-clock 2.3.0.3): this script waits for both nodes' ready line, at most ``READY_S``,
and writes the times 2-3 s ahead into each node's start file; a node not ready by
then stops both.  Results go
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
# the longest wait for both nodes' ready lines: over the 132 node starts of 2026-11-03 to 11-06
# a node was ready 15.5-40.4 s (median 18.5 s) after this script started
READY_S = 120
READY = "ready: waiting for the start time in"  # rf_clock.checks.m4c_link.READY
MIN_VERSION = (2, 3, 0, 3)  # the first rf-clock whose m4c_link takes --start-file
SCENARIO_VERSION = {"fc-steps": (2, 4, 0, 1)}  # scenarios that need a later one (m4c_link --steer-mode steps)
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
    # B steered through FC, the regular way since rf-clock 2.3.0.2 (results/precision-20261105
    # section 8); --b-args overrides it, e.g. --steer-actuator sf --steer-step 0.2 --steer-late-slots 50
    "steer": ("--no-entries", "--no-entries --acquire --fs725 --steer-target-ns {target} --steer-mode predictive "
                              "--steer-step 0.1 --steer-late-slots 25 --steer-actuator fc"),
    # B's FC stepped on a schedule through the steering's path, B keeping its queue entries for
    # theta pair by pair (rf-clock 2.4.0.1, experiments/m5_fc_response.py)
    "fc-steps": ("--no-entries", "--acquire --fs725 --steer-target-ns {target} --steer-mode steps "
                                 "--steer-step 0.1 --steer-late-slots 25 --steer-actuator fc"),
}


def start(ip: str, role: str, extra: str) -> None:
    out = f"{OUT_DIR}/m4c_{role}"
    cmd = (f"sudo -n rm -rf {out}.jsonl {out}.out {out}_start.json {OUT_DIR}/m4c_copies_{role}; cd /tmp && "
           f"(setsid nohup sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr {PYNQ_PYTHON} -m rf_clock.checks.m4c_link "
           f"--role {role} --start-file {out}_start.json --start-timeout {READY_S + 60} --out {out}.jsonl "
           f"--copy-dir {OUT_DIR}/m4c_copies_{role} {extra} > {out}.out 2>&1 < /dev/null &)")
    if ssh(ip, cmd).returncode:
        sys.exit(f"cannot start node {role} on {ip}")
    print(f"node {role} started on {ip}", flush=True)


def check_version(role: str, ip: str, need: tuple[int, ...] = MIN_VERSION) -> None:
    """The node's rf-clock must know ``--start-file`` (2.3.0.3), and ``need`` for scenarios that
    take a later one (``SCENARIO_VERSION``); an older one would refuse the arguments only once started."""
    got = ssh(ip, f"{PYNQ_PYTHON} -c 'import importlib.metadata as m; print(m.version(\"rf-clock\"))'").stdout.strip()
    try:
        ok = tuple(int(v) for v in got.split(".")) >= need
    except ValueError:
        ok = False
    if not ok:
        sys.exit(f"node {role} ({ip}) has rf-clock {got or '(none)'}; this script needs "
                 f"{'.'.join(map(str, need))} or later on both boards (m4c_link --start-file): install the "
                 f"release's wheel there (docs/handoff_2x.md, step 1), or run this script from that release")


def stop_nodes(why: str) -> None:
    for ip in BOARDS.values():
        ssh(ip, f"sudo -n pkill -TERM -f '^{PYNQ_PYTHON}{NODE}'")
    sys.exit(why)


def wait_ready() -> float:
    """Both nodes' ready lines (PL open, FS725 checked); the seconds it took.  Stops both
    nodes after ``READY_S`` or when one has exited."""
    t0 = time.monotonic()
    waiting = dict(BOARDS)
    while waiting:
        for role, ip in list(waiting.items()):
            if ssh(ip, f"grep -qF '{READY}' {OUT_DIR}/m4c_{role}.out").returncode == 0:
                del waiting[role]
            elif not running(ip):
                tail = ssh(ip, f"tail -3 {OUT_DIR}/m4c_{role}.out").stdout.strip()
                stop_nodes(f"node {role} exited before it was ready: {tail}")
        if waiting and time.monotonic() - t0 > READY_S:
            stop_nodes(f"node(s) {', '.join(waiting)} not ready within {READY_S} s: both stopped")
        if waiting:
            time.sleep(0.5)
    return time.monotonic() - t0


def give_times(start_at: float, stop_at: float) -> None:
    text = json.dumps({"start_at": start_at, "stop_at": stop_at})
    for role, ip in BOARDS.items():
        path = f"{OUT_DIR}/m4c_{role}_start.json"
        if ssh(ip, f"echo '{text}' > {path}.tmp && mv {path}.tmp {path}").returncode:
            stop_nodes(f"cannot write the start time on node {role}")


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
    parser.add_argument("--scope-tap", action="store_true",
                        help="B's RS-232 command line on C1, B's output PPS on C4 (experiments/m5_serial_tap.py)")
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
    for role, ip in BOARDS.items():
        check_idle(ip)
        check_version(role, ip, SCENARIO_VERSION.get(args.scenario, MIN_VERSION))
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
    for role, ip in BOARDS.items():
        start(ip, role, extra[role])
    ready_s = wait_ready()
    start_at = int(time.time() + 2.5) + 0.5  # 2-3 s ahead: both writes take well under a second
    stop_at = start_at + args.seconds
    give_times(start_at, stop_at)
    print(f"both nodes ready after {ready_s:.1f} s; start at {start_at}", flush=True)
    scope = None
    if args.scope_tap:
        scope = subprocess.Popen(["sudo", "-n", sys.executable, str(Path(__file__).with_name("m5_serial_tap.py")),
                                  "--seconds", str(stop_at - time.time() + 5), "--out", str(out / "tap.jsonl")],
                                 stdout=open(out / "tap.out", "w"), stderr=subprocess.STDOUT)
    elif args.scope:
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
            subprocess.run(["sudo", "-n", "pkill", "-TERM", "-f", "^" + sys.executable + " .*m5_(hdo_phase|serial_tap)[.]py"])
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
