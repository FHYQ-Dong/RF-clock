"""M5: hours of steering through FC, one run after another, with the scope outside the loop.

Problem.  Steering through FC holds theta to its target within 5.3-5.6 ps rms per second in
runs of 25 minutes (results/precision-20261105 section 8), and the output PPS of the two
nodes then differ by a constant, the same to 0.1-0.9 ps over the few restarts so far.  Two
questions need hours: (1) theta held at theta0 shows only that the measurement is held; if
the delays of the cables, filters and boards change with the room's temperature in a way that
is not the same in both directions, the clocks' true offset moves while theta does not, which
only an outside measurement shows: here the scope's out_B - out_A and the two 10 MHz, whose
time deviation over minutes to an hour shows such a drift as a floor or a slope; (2) how
repeatable the constant and theta - theta0 are over many new starts (PL.open, a new
acquisition, a new PP).

Procedure.  ``--runs`` runs of ``--seconds`` each of experiments/m4c_v2_link.py ``--scenario
steer`` (B through FC with the package's settings; A logs its FS725's FC every second unless
``--a-args`` leaves its FS725 out) with ``--scope --scope-out-pps``
(C1, C2 the two 10 MHz, C3, C4 the two output PPS), back to back: a run per hour, so that each
starts with B's FC fine DAC near its middle and a scope that hangs costs one run.  Then, with
``--free-seconds``, one run with both clocks free (``--scenario free``, the two 10 MHz on the
scope), for the pair's own stability over hours.  Before each run both boards must run no PL
program and no notebook kernel, or the series stops (someone else may be using the boards);
after each, B's FS725 must be back at its board config's gain, or it is put back and noted.
runs.jsonl lists the runs.  Results in results/m5-overnight-<time>/ (the runs in their own
results/m4c-v2-*/ directories).

    .venv/bin/python experiments/m5_overnight.py [--runs 8] [--seconds 3600] [--free-seconds 7200] [--a-args ARGS]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from m3_common import results_dir, ssh

A, B = "192.168.2.99", "192.168.2.100"
PYNQ = "sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
# any PL program or notebook kernel, however started; not the notebook server itself
BUSY = "ps -eo args | grep -E 'rf_clock|ipykernel|[.]venv/bin/python' | grep -vcE 'grep|jupyter-notebook'"
GAIN = (f"{PYNQ} -c \"from rf_clock.fs725 import FS725; from rf_clock.pl.board_config import load_board_config; "
        "c = load_board_config(); u = FS725.open(c.fs725['port']); g = u.gain(); want = c.fs725['gain']; "
        "u.set_gain(want) if g != want else None; print(g, want, u.gain(), u.query('LO?')); u.close()\"")


def busy() -> list[str]:
    return [ip for ip in (A, B) if ssh(ip, BUSY).stdout.strip() not in ("0", "")]


def run(out: Path, scenario: str, seconds: int, extra: list[str]) -> dict:
    cmd = [sys.executable, str(Path(__file__).with_name("m4c_v2_link.py")), "--scenario", scenario,
           "--seconds", str(seconds), "--scope"] + extra
    t0 = time.time()
    done = subprocess.run(cmd, capture_output=True, text=True)
    text = done.stdout + done.stderr
    found = re.search(r"results in (\S+)", text)
    gain = ssh(B, GAIN).stdout.split()
    entry = dict(scenario=scenario, started=t0, seconds=round(time.time() - t0), returncode=done.returncode,
                 run=None if found is None else Path(found.group(1)).name, b_gain_after=gain,
                 tail=text.strip().splitlines()[-6:])
    with open(out / "runs.jsonl", "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps(entry), flush=True)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--free-seconds", type=int, default=7200, help="0: no free run at the end")
    parser.add_argument("--a-args", default="--pps-trig --fs725 --fc-every 1",
                        help="node A's arguments (without --fs725 when A's FS725 cannot be reached)")
    args = parser.parse_args()
    out = results_dir("m5-overnight")
    plan = [("steer", args.seconds, ["--scope-out-pps", "--node-args=--pps-out 20", f"--a-args={args.a_args}"])] * args.runs
    if args.free_seconds:
        plan.append(("free", args.free_seconds, [f"--a-args={args.a_args}"]))
    for i, (scenario, seconds, extra) in enumerate(plan):
        others = busy()
        if others:
            print(f"run {i}: {', '.join(others)} busy (someone else?): the series stops", flush=True)
            with open(out / "runs.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(dict(stopped=time.time(), busy=others, before_run=i)) + "\n")
            sys.exit(1)
        entry = run(out, scenario, seconds, extra)
        if entry["returncode"] != 0 or entry["run"] is None:
            print(f"run {i} failed: the series stops", flush=True)
            sys.exit(1)
        if len(entry["b_gain_after"]) < 2 or entry["b_gain_after"][0] != entry["b_gain_after"][1]:
            print(f"run {i}: B's FS725 was left at GA {entry['b_gain_after'][:1]}; put back", flush=True)
    print(f"results in {out}")


if __name__ == "__main__":
    main()
