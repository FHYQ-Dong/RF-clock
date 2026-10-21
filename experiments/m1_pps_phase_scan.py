"""M1 sub-experiment: how far is each board's PPS edge from a 100 MHz sampling edge?

Problem.  P1 captures the PPS on the 100 MHz tick (PPS_TICK).  An edge close to a
sampling edge is captured one tick early in some seconds and on time in others,
and every target tick derived from the PPS (TX bursts, RX windows) then jumps by
10 ns from second to second.  On 2026-10-18 887329260072A's edge sat on a sampling
edge (one tick early in about a third of the seconds); this scan measured each
board's margin and chose the EPG1 output delay that puts the edge mid-tick
(output 2: 6.6 ns; results/pps-phase-scan-20261018_*).  Rerun it after any change
to the PPS distribution (cables, EPG1 trigger threshold, boards).

Method.  The captured PPS_TICK moves by one tick when the edge, delayed by the
EPG1, crosses a sampling edge.  This sets the
delay of the given EPG1 outputs to 0 and then to each of `--delays` (all outputs
alike, one RUN restart per step) and logs PPS_TICK mod 1e8 for every edge on each
board.  The delay at which half the edges land one tick later is how far the edge
is from the next sampling edge at delay 0.

Run on RF-clock-PC, the EPG1's host, with the project's ``instruments``
dependency group (``epg1.py`` next to this script drives the EPG1 through
``tanglab_instruments``)::

    .venv/bin/python experiments/m1_pps_phase_scan.py --pair 192.168.2.99:2 --pair 192.168.2.101:1
    .venv/bin/python experiments/m1_pps_phase_scan.py --pair 192.168.2.99:2 --delays 0.25,0.5,1

(EPG1 output 1 feeds 887329260101 at .101, output 2 feeds 887329260072A at
.99; checked 2026-10-18.)

It copies itself to the boards and runs the logger part there (`--log-pps`:
PL.open without MTS, one line per PPS edge); the boards must be idle.  In the
EPG1's synchronous channel mode a delay change takes effect only at a RUN
restart, which interrupts every output for about a second, including output 3
(another experiment): agree with its owner first.  The delays are set back to
what they were at the start (``epg1.py`` gives output 2 6.6 ns).
Results go to results/pps-phase-scan-<time>/.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

PERIOD = 100_000_000  # ticks per PPS
REMOTE = "/tmp/m1_pps_phase_scan.py"
LOG = "/tmp/m1_pps_phase_scan.log"
LOGGER = f"^[.]venv/bin/python {REMOTE} --log-pps"  # anchored: never matches the ssh shell


def log_pps(seconds: float) -> None:
    """On the board: one line 'PPS <count> <tick>' per PPS edge."""
    from rf_clock.pl import PL
    from rf_clock.pl.client import WaitTimeout

    pl = PL.open(mts=False)
    ctrl = pl.ctrl
    print("ready", flush=True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            count, tick = ctrl.wait_next_pps(timeout=2.5)
        except WaitTimeout:
            print("timeout", flush=True)  # the EPG1 restarting
            continue
        print(f"PPS {count} {tick}", flush=True)


def ssh(ip: str, cmd: str, timeout: float = 30) -> str:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"xilinx@{ip}", cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def edges(ip: str) -> list[tuple[int, int]]:
    out = []
    for line in ssh(ip, f"cat {LOG}").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "PPS":
            out.append((int(parts[1]), int(parts[2])))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--pair", action="append", metavar="IP:OUTPUT",
                        help="a board and the EPG1 output feeding its PPS; repeat to scan several boards "
                             "at once (default: 192.168.2.99:2)")
    parser.add_argument("--edges", type=int, default=15, help="PPS edges logged per delay")
    parser.add_argument("--delays", default="1,2,3,4,5,6,7,8,9,10",
                        help="comma-separated delays in ns after the baseline at 0 (default: 1 ns steps)")
    parser.add_argument("--log-pps", type=float, metavar="SECONDS", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.log_pps is not None:
        log_pps(args.log_pps)
        return

    import epg1  # only on the EPG1's host

    pairs = [(p.split(":")[0], int(p.split(":")[1])) for p in (args.pair or ["192.168.2.99:2"])]
    root = Path(__file__).resolve().parent.parent
    out = root / "results" / f"pps-phase-scan-{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True)
    for ip, _ in pairs:
        if ssh(ip, "ps -eo args | grep -cE '^(sudo -n env BOARD|[.]venv/bin/python)'").strip() != "0":
            sys.exit(f"board {ip} is running a PL program; stop it first")
    for ip, _ in pairs:
        subprocess.run(["scp", "-q", __file__, f"xilinx@{ip}:{REMOTE}"], check=True)
        ssh(ip, f"cd ~/RF-clock && (nohup setsid sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python "
                f"{REMOTE} --log-pps 7200 > {LOG} 2>&1 < /dev/null &)")
    for ip, _ in pairs:
        for _ in range(90):
            if len(edges(ip)) >= 3:
                break
            time.sleep(2)
        else:
            sys.exit(f"logger on {ip} did not start:\n" + ssh(ip, f"tail -n 20 {LOG}"))

    epg = epg1.open_epg1()
    steps: list[dict] = []
    original = {ch: float(epg.get(f"PULSE{ch}:DEL")) for _, ch in pairs}

    def measure(delay: float | dict[int, float], label: str = "scan") -> dict:
        """Set every output to *delay* (or each to its own), restart RUN if anything changed, log."""
        delays = delay if isinstance(delay, dict) else {ch: delay for _, ch in pairs}
        changed = False
        for ch, d in delays.items():
            if d != float(epg.get(f"PULSE{ch}:DEL")):
                epg.set(f"PULSE{ch}:DEL", d)
                changed = True
        if changed:
            epg1.restart(epg)  # synchronous channel mode: a new delay starts with RUN
            time.sleep(3)
        n0 = {ip: len(edges(ip)) for ip, _ in pairs}
        step: dict = {"label": label, "delay_set": delay, "boards": {}}
        for ip, ch in pairs:
            while len(got := edges(ip)) < n0[ip] + args.edges:
                time.sleep(1)
            phases = Counter(tick % PERIOD for _, tick in got[n0[ip]:n0[ip] + args.edges])
            step["boards"][ip] = {"output": ch, "delay_read": epg.get(f"PULSE{ch}:DEL"),
                                  "realdel": epg.get(f"PULSE{ch}:REALDEL"), "phases": dict(phases)}
        steps.append(step)
        shown = " ".join(f"{d * 1e9:.2f}" for d in delays.values())
        print(f"{label}: delay {shown} ns: " + "  ".join(
            f"{ip} {dict(sorted(b['phases'].items()))}" for ip, b in step["boards"].items()), flush=True)
        return step

    try:
        measure(0.0)
        for d in filter(None, args.delays.split(",")):
            measure(float(d) * 1e-9)
    finally:
        try:
            measure(original, "restored")
        finally:
            if epg.get("RUN") == str(epg1.RUN_STOP):
                epg.set("RUN", epg1.RUN_START)
            epg.close()
            for ip, _ in pairs:
                (out / f"pps_log_{ip}.txt").write_text(ssh(ip, f"cat {LOG}"), newline="\n")
                ssh(ip, f"sudo -n pkill -f '{LOGGER}'")

    summary: dict = {"pairs": [f"{ip}:{ch}" for ip, ch in pairs], "edges_per_step": args.edges, "boards": {}}
    for ip, _ in pairs:
        # The earlier tick seen at delay 0 is "on time"; the fraction one tick later rises to 1
        # as the delay pushes the edge across the next sampling edge.
        r0 = min(steps[0]["boards"][ip]["phases"])
        curve = [(s["delay_set"], sum(n for r, n in s["boards"][ip]["phases"].items()
                                      if (r - r0) % PERIOD == 1) / args.edges)
                 for s in steps if s["label"] == "scan"]
        curve.sort()
        crossing = next((d0 + (0.5 - f0) / (f1 - f0) * (d1 - d0)
                         for (d0, f0), (d1, f1) in zip(curve, curve[1:]) if f0 < 0.5 <= f1), None)
        summary["boards"][ip] = {"tick_phase_at_0": r0, "late_fraction": curve, "crossing_s": crossing}
        where = ("already past half at delay 0" if curve[0][1] >= 0.5 else
                 "not within the scan" if crossing is None else f"at delay {crossing * 1e9:.2f} ns")
        print(f"{ip}: half the edges one tick later {where}")
    summary["steps"] = steps
    (out / "scan.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
    print(f"results in {out}")


if __name__ == "__main__":
    main()
