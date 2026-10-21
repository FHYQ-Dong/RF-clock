"""M4: two-way time transfer between the two boards, or one board through its own loopback cable.

Problem.  M4 of the plan: with both boards on one FS725 and cabled with
attenuators (A's DAC_A -> B's ADC_B, B's DAC_A -> A's ADC_B), theta = (m_AB - m_BA)/2
of ``rf_clock.twtt`` is the zero-baseline constant (Delta/2 and the PPS
distribution), and its scatter is the precision of the system.  ``self`` first runs
one board as both nodes through its own loopback cable (``rf_clock.twtt.node AB``):
theta must be 0 and the one-way delay the M3 loopback delay, which checks the
slots, the timestamps and the two-way equations on the hardware before the second
board is cabled.

Run on RF-clock-PC as the normal user, with the boards idle::

    .venv/bin/python experiments/m4_twtt.py self --seconds 60     # 887329260072A alone
    .venv/bin/python experiments/m4_twtt.py pair --seconds 600    # A = .99, B = .101

``--dev`` runs this repository's src/rf_clock on the boards (copied to /tmp and put
ahead of the installed package on PYTHONPATH) instead of the package installed in
PYNQ's environment.  Results go to results/m4-twtt-<time>/: each board's log
(``rf_clock.twtt.link``), its console output, and summary.json with theta and the
one-way delay (mean, sd, and the overlapping Allan deviation of theta).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

from m3_common import ROOT, check_idle, results_dir, ssh

BOARDS = {"A": "192.168.2.99", "B": "192.168.2.101"}  # S/N 887329260072A, 887329260101
PYTHON = "/usr/local/share/pynq-venv/bin/python"
INSTALLED_OVERLAY = "/usr/local/share/pynq-venv/lib/python3.10/site-packages/rf_clock/overlay"
DEV_DIR = "/tmp/rf-clock-dev"
NODE = f"^{PYTHON} -m rf_clock[.]twtt[.]node "  # pgrep pattern of a running node (and its link process)
START_S = 180  # PL.open and the first PPS
K3 = 3 * 2 * math.pi * 5.8e9 * 1e-12  # rad of three times the correlation phase per ps of delay


def deploy_dev(ip: str) -> None:
    """This repository's src/rf_clock to DEV_DIR on the board, the installed overlay linked in."""
    tar = subprocess.run(["tar", "--exclude=__pycache__", "--exclude=overlay", "-C", str(ROOT / "src"), "-cf", "-",
                          "rf_clock"], capture_output=True, check=True).stdout
    cmd = (f"sudo -n rm -rf {DEV_DIR} && mkdir -p {DEV_DIR} && tar -xf - -C {DEV_DIR} && "
           f"ln -s {INSTALLED_OVERLAY} {DEV_DIR}/rf_clock/overlay")
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", f"xilinx@{ip}", cmd], input=tar, capture_output=True)
    if done.returncode:
        sys.exit(f"cannot copy src/rf_clock to {ip}: {done.stderr.decode().strip()}")


def start(ip: str, node: str, seconds: int, dev: bool, peer: str | None = None) -> None:
    env = "BOARD=RFSoC4x2 XILINX_XRT=/usr" + (f" PYTHONPATH={DEV_DIR} PYTHONDONTWRITEBYTECODE=1" if dev else "")
    options = f"{node} --seconds {seconds} --log /tmp/twtt_{node}.jsonl" + (f" --peer {peer}" if peer else "")
    cmd = (f"sudo -n rm -f /tmp/twtt_{node}.jsonl /tmp/twtt_{node}.out; cd /tmp && "
           f"(setsid nohup sudo -n env {env} {PYTHON} -m rf_clock.twtt.node {options} "
           f"> /tmp/twtt_{node}.out 2>&1 < /dev/null &)")
    if ssh(ip, cmd).returncode:
        sys.exit(f"cannot start node {node} on {ip}")
    print(f"node {node} started on {ip}", flush=True)


def running(ip: str) -> bool:
    return ssh(ip, f"pgrep -fc '{NODE}'").stdout.strip() not in ("", "0")


def wait_all(ips: list[str], timeout: float) -> bool:
    """Wait until no node runs on these boards; False if one still runs after ``timeout`` (then stopped)."""
    deadline = time.monotonic() + timeout
    while any(running(ip) for ip in ips):
        if time.monotonic() > deadline:
            for ip in ips:
                ssh(ip, f"sudo -n pkill -TERM -f '{NODE}'")
            return False
        time.sleep(10)
    return True


def fetch(ip: str, node: str, out: Path) -> Path:
    for suffix in ("jsonl", "out"):
        subprocess.run(["scp", "-q", f"xilinx@{ip}:/tmp/twtt_{node}.{suffix}", str(out / f"twtt_{node}.{suffix}")])
    return out / f"twtt_{node}.jsonl"


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": statistics.fmean(values),
            "sd": statistics.stdev(values) if len(values) > 1 else 0.0, "min": min(values), "max": max(values)}


def allan(theta_by_second: dict[int, float]) -> dict[int, float]:
    """Overlapping Allan deviation of the fractional frequency from theta (phase data,
    one point per second), for tau = 1, 2, 4, ... s, over the seconds present."""
    out = {}
    tau = 1
    while True:
        terms = [(theta_by_second[s + 2 * tau] - 2 * theta_by_second[s + tau] + theta_by_second[s]) * 1e-9
                 for s in theta_by_second if s + tau in theta_by_second and s + 2 * tau in theta_by_second]
        if len(terms) < 10:
            return out
        out[tau] = math.sqrt(sum(t * t for t in terms) / (2 * tau * tau * len(terms)))
        tau *= 2


def carrier_theta(entries: list[dict], valid: list[dict]) -> dict[int, float]:
    """theta's change from the first valid second, in ns, from three times the carrier phase
    of the correlation peak in both directions (as M3, results/lmx-sync-loopback-20261019
    section 1.2); a later arrival gives a lower phase.  Changes beyond +-28 ps (half a turn
    of 57.5 ps) would alias; with one FS725 they stay far below."""
    records = {(e["node"], e["count"]): e for e in entries if e["type"] in ("local", "peer")}
    out, first = {}, None
    for r in valid:
        a = records.get(("A", r["count_a"])) or records.get(("AB", r["count_a"]))
        b = records.get(("B", r["count_b"])) or records.get(("AB", r["count_b"]))
        if a is None or b is None:
            continue
        phases = (3 * b["slots"]["AB"]["rx"]["phase"], 3 * a["slots"]["BA"]["rx"]["phase"])
        first = first or phases
        d_ab, d_ba = (-((p - p0 + math.pi) % (2 * math.pi) - math.pi) / K3 for p, p0 in zip(phases, first))
        out[r["count_a"]] = (d_ab - d_ba) / 2 / 1000
    return out


def summarize(log: Path) -> dict:
    entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    results = [e for e in entries if e["type"] == "result"]
    valid = [e for e in results if e["theta_ns"] is not None]
    by_second = {e["count_a"]: e["theta_ns"] for e in valid}  # A's PPS_COUNT: consecutive seconds
    carrier = carrier_theta(entries, valid)
    return {"info": [e for e in entries if e["type"] in ("info", "hello")],
            "events": [e["event"] for e in entries if e["type"] == "event"],
            "seconds": len(results), "valid": len(valid),
            "theta_ns": stats([e["theta_ns"] for e in valid]), "delay_ns": stats([e["delay_ns"] for e in valid]),
            "m_ab_ns": stats([e["m_ab_ns"] for e in valid]), "m_ba_ns": stats([e["m_ba_ns"] for e in valid]),
            "theta_adev": allan(by_second),
            "carrier_theta_change_ns": stats(list(carrier.values())), "carrier_theta_adev": allan(carrier)}


def report(summary: dict) -> None:
    theta, delay = summary["theta_ns"], summary["delay_ns"]
    print(f"{summary['valid']} of {summary['seconds']} seconds paired and valid")
    if theta["n"]:
        print(f"theta {theta['mean']:+.4f} ns, sd {theta['sd'] * 1000:.2f} ps "
              f"(range {theta['min']:+.4f} to {theta['max']:+.4f} ns)")
        print(f"one-way delay {delay['mean']:.4f} ns, sd {delay['sd'] * 1000:.2f} ps")
        print(f"m_AB {summary['m_ab_ns']['mean']:.4f} ns (sd {summary['m_ab_ns']['sd'] * 1000:.2f} ps), "
              f"m_BA {summary['m_ba_ns']['mean']:.4f} ns (sd {summary['m_ba_ns']['sd'] * 1000:.2f} ps)")
        print("Allan deviation of theta: " + ", ".join(f"{tau} s {v:.2e}" for tau, v in summary["theta_adev"].items()))
        carrier = summary["carrier_theta_change_ns"]
        print(f"theta from the carrier phase: change sd {carrier['sd'] * 1000:.3f} ps "
              f"(range {carrier['min'] * 1000:+.3f} to {carrier['max'] * 1000:+.3f} ps); Allan deviation "
              + ", ".join(f"{tau} s {v:.2e}" for tau, v in summary["carrier_theta_adev"].items()))
    for event in summary["events"]:
        print(f"event: {event}")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("mode", choices=("self", "pair"), help="self: node AB on A's board; pair: A and B")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--dev", action="store_true", help="run this repository's src/rf_clock on the boards")
    args = parser.parse_args()

    nodes = {"AB": BOARDS["A"]} if args.mode == "self" else dict(BOARDS)
    for ip in set(nodes.values()):
        check_idle(ip)
        if args.dev:
            deploy_dev(ip)
    out = results_dir(f"m4-twtt-{args.mode}")
    (out / "settings.json").write_text(json.dumps({"mode": args.mode, "seconds": args.seconds, "dev": args.dev,
                                                   "boards": nodes}, indent=2) + "\n", newline="\n")
    for node, ip in nodes.items():  # A first, so that it listens when B connects
        start(ip, node, args.seconds, args.dev, peer=BOARDS["A"] if node == "B" else None)
    finished = wait_all(list(nodes.values()), args.seconds + START_S)
    logs = {node: fetch(ip, node, out) for node, ip in nodes.items()}
    if not finished:
        print("a node was still running after the expected time and was stopped")
    summary = {node: summarize(log) for node, log in logs.items() if log.exists()}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
    print(f"results in {out}")
    for node, s in summary.items():
        print(f"--- node {node}")
        report(s)


if __name__ == "__main__":
    main()
