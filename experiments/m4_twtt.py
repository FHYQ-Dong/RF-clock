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
    .venv/bin/python experiments/m4_twtt.py pair --seconds 60 --runs 5 --reboot
    .venv/bin/python experiments/m4_twtt.py pair --seconds 60 --runs 20 --reboot --alternate
    .venv/bin/python experiments/m4_twtt.py pair --seconds 60 --runs 5 --power-cycle

``--dev`` runs this repository's src/rf_clock on the boards (copied to /tmp and put
ahead of the installed package on PYTHONPATH) instead of the package installed in
PYNQ's environment.  Results go to results/m4-twtt-<time>/: each board's log
(``rf_clock.twtt.link``), its console output, and summary.json with theta and the
one-way delay (mean, sd, and the overlapping Allan deviation of theta).  ``--runs``
repeats the run, each in ``runNN/``, and ``--reboot`` reboots the boards before each
one (with ``--alternate`` only before the even-numbered runs, so that each reboot
follows a run without one): series.json then lists every run's theta, m, capture
codes, each board's mean die temperature and the mean carrier phase of each
direction, which shows a 120-degree NCO step of either board.  ``--power-cycle``
instead shuts both boards down before each run and waits while someone at the bench
switches their power off and on again (the boards cannot come back without that).
``--save-every N`` keeps every Nth second's RX windows (``rf_clock.twtt.node``), in
``rx_<node>/`` next to the log; ``experiments/m4_channel.py`` reads them.
"""

from __future__ import annotations

import argparse
import cmath
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from m3_common import ROOT, check_idle, reboot, results_dir, ssh

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


def start(ip: str, node: str, seconds: int, dev: bool, peer: str | None = None, save_every: int = 0) -> None:
    env = "BOARD=RFSoC4x2 XILINX_XRT=/usr" + (f" PYTHONPATH={DEV_DIR} PYTHONDONTWRITEBYTECODE=1" if dev else "")
    options = f"{node} --seconds {seconds} --log /tmp/twtt_{node}.jsonl" + (f" --peer {peer}" if peer else "")
    if save_every:
        options += f" --save-every {save_every} --save-dir /tmp/rx_{node}"
    cmd = (f"sudo -n rm -rf /tmp/twtt_{node}.jsonl /tmp/twtt_{node}.out /tmp/rx_{node}; cd /tmp && "
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


def boot_id(ip: str) -> str:
    return ssh(ip, "cat /proc/sys/kernel/random/boot_id", timeout=15).stdout.strip()


def power_cycle(ips: list[str], timeout: float = 3600) -> None:
    """Shut the boards down, then wait while someone at the bench switches their power
    off and on again: until each is back with a new boot id."""
    before = {ip: boot_id(ip) for ip in ips}
    for ip in ips:
        ssh(ip, "sudo -n systemctl poweroff", timeout=15)
    while any(ssh(ip, "true", timeout=10).returncode == 0 for ip in ips):
        time.sleep(5)
    print(time.strftime("%H:%M:%S") + ": both boards shut down; switch their power off, wait 10 s, switch it on",
          flush=True)
    deadline = time.monotonic() + timeout
    for ip in ips:
        while boot_id(ip) in ("", before[ip]):
            if time.monotonic() > deadline:
                sys.exit(f"board {ip} did not come back within {timeout:.0f} s")
            time.sleep(5)
    print(time.strftime("%H:%M:%S") + ": both boards are up again", flush=True)


def wait_ntp(ip: str, timeout: float = 300) -> None:
    """Wait until the board's clock is NTP-synchronized again (after a reboot)."""
    deadline = time.monotonic() + timeout
    while ssh(ip, "timedatectl show -p NTPSynchronized --value").stdout.strip() != "yes":
        if time.monotonic() > deadline:
            sys.exit(f"{ip}: no NTP synchronization within {timeout:.0f} s")
        time.sleep(5)


def fetch(ip: str, node: str, out: Path, rx: bool = False) -> Path:
    for suffix in ("jsonl", "out"):
        subprocess.run(["scp", "-q", f"xilinx@{ip}:/tmp/twtt_{node}.{suffix}", str(out / f"twtt_{node}.{suffix}")])
    if rx:
        subprocess.run(["scp", "-q", "-r", f"xilinx@{ip}:/tmp/rx_{node}", str(out)])
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
    records = {(e["node"], e["count"]): e for e in entries if e["type"] in ("local", "peer")}
    phases: dict[str, list[complex]] = {"AB": [], "BA": []}
    die: dict[str, list[float]] = {}
    for r in valid:  # the carrier phase of each direction, as the receiving board measured it
        a = records.get(("A", r["count_a"])) or records.get(("AB", r["count_a"]))
        b = records.get(("B", r["count_b"])) or records.get(("AB", r["count_b"]))
        if a and b:
            phases["AB"].append(cmath.exp(1j * b["slots"]["AB"]["rx"]["phase"]))
            phases["BA"].append(cmath.exp(1j * a["slots"]["BA"]["rx"]["phase"]))
            for record in {id(a): a, id(b): b}.values():
                if record.get("die_c") is not None:
                    die.setdefault(record["node"], []).append(record["die_c"])
    return {"info": [e for e in entries if e["type"] in ("info", "hello")],
            "events": [e["event"] for e in entries if e["type"] == "event"],
            "seconds": len(results), "valid": len(valid),
            "theta_ns": stats([e["theta_ns"] for e in valid]), "delay_ns": stats([e["delay_ns"] for e in valid]),
            "m_ab_ns": stats([e["m_ab_ns"] for e in valid]), "m_ba_ns": stats([e["m_ba_ns"] for e in valid]),
            "theta_adev": allan(by_second),
            "carrier_theta_change_ns": stats(list(carrier.values())), "carrier_theta_adev": allan(carrier),
            "carrier_phase_deg": {d: math.degrees(cmath.phase(sum(v))) for d, v in phases.items() if v},
            "die_c": {node: stats(v) for node, v in die.items()},
            "t1_code": {e["label"]: e["t1_code"] for e in entries
                        if e["type"] in ("info", "hello") and e.get("t1_code")}}


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
    parser.add_argument("--runs", type=int, default=1, help="runs, each powering the boards up again")
    parser.add_argument("--reboot", action="store_true", help="reboot the boards before every run")
    parser.add_argument("--power-cycle", action="store_true",
                        help="shut the boards down before every run and wait for their power to be switched off and on")
    parser.add_argument("--save-every", type=int, default=0, metavar="N",
                        help="keep every Nth second's RX windows (rx_<node>/)")
    parser.add_argument("--alternate", action="store_true",
                        help="with --reboot or --power-cycle: only before even-numbered runs")
    args = parser.parse_args()
    if args.reboot and args.power_cycle:
        parser.error("--reboot or --power-cycle, not both")

    nodes = {"AB": BOARDS["A"]} if args.mode == "self" else dict(BOARDS)
    ips = sorted(set(nodes.values()))
    for ip in ips:
        check_idle(ip)
    out = results_dir(f"m4-twtt-{args.mode}")
    (out / "settings.json").write_text(json.dumps({"mode": args.mode, "seconds": args.seconds, "dev": args.dev,
                                                   "runs": args.runs, "reboot": args.reboot,
                                                   "power_cycle": args.power_cycle, "alternate": args.alternate,
                                                   "save_every": args.save_every,
                                                   "boards": nodes},
                                                  indent=2) + "\n", newline="\n")
    series = []
    for n in range(1, args.runs + 1):
        run = out if args.runs == 1 else out / f"run{n:02d}"
        run.mkdir(exist_ok=True)
        restart = not args.alternate or n % 2 == 0
        rebooted, power_cycled = args.reboot and restart, args.power_cycle and restart
        if rebooted:
            print(f"run {n}: rebooting {', '.join(ips)}", flush=True)
            with ThreadPoolExecutor() as pool:
                list(pool.map(reboot, ips))
        elif power_cycled:
            print(f"run {n}: power cycle of {', '.join(ips)}", flush=True)
            power_cycle(ips)
        if rebooted or power_cycled:
            for ip in ips:
                wait_ntp(ip)
                # PL.open waits for the boot to finish (PYNQ loads its base overlay about a minute
                # in); waiting here first lets both nodes start together and overlap fully.
                ssh(ip, "systemctl is-system-running --wait", timeout=400)
        if args.dev:
            for ip in ips:
                deploy_dev(ip)
        for node, ip in nodes.items():  # A first, so that it listens when B connects
            start(ip, node, args.seconds, args.dev, peer=BOARDS["A"] if node == "B" else None,
                  save_every=args.save_every)
        finished = wait_all(ips, args.seconds + START_S)
        logs = {node: fetch(ip, node, run, rx=bool(args.save_every)) for node, ip in nodes.items()}
        if not finished:
            print("a node was still running after the expected time and was stopped")
        summary = {node: summarize(log) for node, log in logs.items() if log.exists()}
        (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
        print(f"results in {run}", flush=True)
        for node, s in summary.items():
            print(f"--- node {node}")
            report(s)
        first = next(iter(summary.values()), None)
        if first and first["theta_ns"]["n"]:
            series.append({"run": n, "rebooted": rebooted, "power_cycled": power_cycled, "valid": first["valid"],
                           "theta_ns": first["theta_ns"]["mean"], "m_ab_ns": first["m_ab_ns"]["mean"],
                           "m_ba_ns": first["m_ba_ns"]["mean"], "carrier_phase_deg": first["carrier_phase_deg"],
                           "die_c": {k: v.get("mean") for k, v in first["die_c"].items()},
                           "t1_code": first["t1_code"]})
    if args.runs > 1:
        (out / "series.json").write_text(json.dumps(series, indent=2) + "\n", newline="\n")
        print(f"{'run':>3} {'boot':>4} {'s':>4} {'theta/ns':>10} {'m_AB/ns':>10} {'m_BA/ns':>10} {'phase AB':>9} "
              f"{'phase BA':>9} {'die A':>6} {'die B':>6}")
        for r in series:
            phase, die = r["carrier_phase_deg"], r["die_c"]
            boot = "yes" if r["rebooted"] else "off" if r["power_cycled"] else ""
            print(f"{r['run']:3d} {boot:>4} {r['valid']:4d} {r['theta_ns']:10.4f} "
                  f"{r['m_ab_ns']:10.4f} {r['m_ba_ns']:10.4f} {phase.get('AB', float('nan')):9.1f} "
                  f"{phase.get('BA', float('nan')):9.1f} {die.get('A') or float('nan'):6.1f} "
                  f"{die.get('B') or float('nan'):6.1f}")

if __name__ == "__main__":
    main()
