"""M5: by how much does P10's fraction (PPS_OUT_FRAC, rf-clock 2.3) delay B's output edge?

Problem.  The theta-compensated output PPS (plan/pps_output.md section 7) moves B's output
edge by the steering error, a few tens of ps, through a fraction of one 1.25 ns DAC sample:
the step's first sample becomes amplitude + (idle - amplitude) x frac / 2^16.  After the DAC's
interpolation the edge's 50 % crossing should move nearly in proportion to frac, but by how
many ps per unit, and how straight, has to be measured before ``m4c_link --pps-out-comp`` can
use it (``--pps-out-ps-per-frac``).

Procedure.  On B (rf-clock 2.3 installed, nothing else running): PL.open, the output PPS at
OFFSET 20 with the amplitude and idle level of the link runs, then the fraction held at each
of ``--fracs`` for ``--hold`` seconds in turn, the plain step (FRAC clear) first and last.  On
RF-clock-PC at the same time experiments/m5_hdo_phase.py --out-pps2 times B's output against
B's FS725 10 MHz (C4 against C1) once a second.  Each record goes to the fraction in force:
the first two seconds of each hold are left out, since the fraction is taken at the next
second, and so is its last second, since an acquisition armed then catches the edge that
takes the next fraction.  The mean edge per fraction is fitted by a straight line and a
cubic about the base 32768, and by a line over the fractions within 4096 of it (the slope
``m4c_link --pps-out-ps-per-frac`` wants: the response is S-shaped, steepest at the base).
Results in results/m5-pps-frac-<time>/; ``--analyze DIR`` fits a run's records again.

    .venv/bin/python experiments/m5_pps_frac.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from m3_common import check_idle, results_dir, ssh

B = "192.168.2.100"
PYNQ = "sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
BOARD_CODE = r'''
import json, sys, time
from rf_clock.pl import PL
fracs, hold = json.loads(sys.argv[1]), float(sys.argv[2])
pl = PL.open()
pl.pps_out.configure(amplitude=-24000, offset=20, idle=0)
pl.ctrl.wait_next_pps()
for f in fracs:
    pl.pps_out.set_frac(None if f < 0 else f)
    print(json.dumps({"time": time.time(), "frac": None if f < 0 else f}), flush=True)
    time.sleep(hold)
print(json.dumps({"time": time.time(), "end": True}), flush=True)
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--fracs", default="-1,0,8192,16384,24576,28672,32768,36864,40960,49152,57344,65535,-1",
                        help="fractions in turn (-1: FRAC clear, the plain step)")
    parser.add_argument("--hold", type=float, default=15.0, help="seconds at each")
    parser.add_argument("--analyze", type=Path, metavar="DIR", help="fit the records of an earlier run again")
    args = parser.parse_args()
    if args.analyze is not None:
        analyze(args.analyze)
        return
    fracs = [int(f) for f in args.fracs.split(",")]
    check_idle(B)
    out = results_dir("m5-pps-frac")
    (out / "board.py").write_text(BOARD_CODE, encoding="utf-8", newline="\n")
    subprocess.run(["scp", "-q", str(out / "board.py"), f"xilinx@{B}:/tmp/pps_frac_board.py"], check=True)
    board = subprocess.Popen(["ssh", f"xilinx@{B}", f"cd /tmp && {PYNQ} /tmp/pps_frac_board.py "
                              f"'{json.dumps(fracs)}' {args.hold}"], stdout=subprocess.PIPE, text=True)
    assert board.stdout is not None
    first = board.stdout.readline()  # the first hold has begun: PL.open is done
    steps = [json.loads(first)]
    seconds = len(fracs) * args.hold + 5
    scope = subprocess.Popen(["sudo", "-n", sys.executable, str(Path(__file__).with_name("m5_hdo_phase.py")),
                              "--seconds", str(seconds), "--out", str(out / "hdo.jsonl"), "--out-pps2"],
                             stdout=open(out / "hdo.out", "w"), stderr=subprocess.STDOUT)
    for line in board.stdout:
        steps.append(json.loads(line))
    board.wait()
    scope.wait()
    (out / "steps.json").write_text(json.dumps(steps, indent=1) + "\n", encoding="utf-8", newline="\n")
    analyze(out)


def analyze(out: Path) -> None:
    steps = json.loads((out / "steps.json").read_text(encoding="utf-8"))
    rec = [json.loads(x) for x in (out / "hdo.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rec = [r for r in rec if r.get("type") == "acq" and r.get("out_b_vs_10mhz_ns") is not None]
    rows = []
    for i, s in enumerate(steps):
        if "end" in s:
            break
        end = steps[i + 1]["time"]
        v = [r["out_b_vs_10mhz_ns"] * 1e3 for r in rec if s["time"] + 2.0 <= r["asked"] < end - 1.0]
        if v:
            rows.append(dict(frac=s["frac"], records=len(v), edge_ps=float(np.mean(v)), sd_ps=float(np.std(v))))
    plain = [r["edge_ps"] for r in rows if r["frac"] is None]
    fr = [r for r in rows if r["frac"] is not None]
    x = np.array([r["frac"] for r in fr], dtype=float)
    y = np.array([r["edge_ps"] for r in fr]) - (plain[0] if plain else 0.0)
    line = np.polyfit(x - 32768, y, 1)
    cubic = np.polyfit(x - 32768, y, 3)
    near = np.abs(x - 32768) <= 4096
    result = dict(rows=rows, plain_drift_ps=float(plain[-1] - plain[0]) if len(plain) > 1 else None,
                  ps_per_frac=float(line[0]), at_base_ps=float(line[1]),
                  line_residual_ps=float(np.std(y - np.polyval(line, x - 32768))),
                  ps_per_frac_at_base=float(cubic[2]),
                  ps_per_frac_near_base=float(np.polyfit(x[near] - 32768, y[near], 1)[0]) if near.sum() >= 3 else None)
    (out / "result.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    for r in rows:
        print(r)
    print({k: v for k, v in result.items() if k != "rows"})
    print(f"results in {out}")


if __name__ == "__main__":
    main()
