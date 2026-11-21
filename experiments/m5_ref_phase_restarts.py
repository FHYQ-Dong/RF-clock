"""M5: how far does each node's 10 MHz move against its PL's 100 MHz clock at a restart, as P11 sees it?

Problem.  Over the night of 2026-11-07 B's 10 MHz against A's, less (theta - theta0), jumped
at each restart (results/m5-overnight-20261107_011058); experiments/m5_10mhz_restarts.py put the
jumps in the boards (rms 3.75 ps with the clocks written again, 2.2 ps without, n = 5 each,
against 0.92 ps when only the scope restarted).  Since 2.4 P11 measures each node's 10 MHz on
ADC_D against the PL's 100 MHz clock, tau_ref (docs/ABI.md 4.14), so each board's share
can be seen alone and without the link.  If both boards move independently by the same amount,
each board's jump is the pair's divided by sqrt(2): about 2.7 ps with the clocks written, 1.6 ps
without (inferred).  P11 samples on ADC tile 0, whose MTS offset varied between 6, 12 and 14 on
B under 2.3 at the same latency (A: always 12): if that offset moved the samples, tau_ref would
jump with it by whole samples.

Procedure.  Both boards at once, ``--opens`` times per mode: ``rf_clock.checks.ref_phase``
(PL.open, then ``--seconds`` of P11).  Modes: ``clocks`` (the LMK04828 and LMX2594 written
again) and ``no-clocks`` (``--no-clocks``: left as they are; PL.open still loads the bitstream,
starts the RFDC and runs MTS).  Nothing is transmitted, and the FS725s run free.  For each open
the mean tau_ref over its whole seconds, and the jump from the open before (attributed to the
mode of the later open), per board and for B less A.

    .venv/bin/python experiments/m5_ref_phase_restarts.py [--opens 8] [--seconds 20] [--modes clocks,no-clocks]

Results in results/m5-ref-phase-restarts-<time>/: runs.jsonl (each open's record), board_<ip>.out
and summary.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
from pathlib import Path

import numpy as np

from m3_common import results_dir

A, B = "192.168.2.99", "192.168.2.100"
PYNQ_PYTHON = "/usr/local/share/pynq-venv/bin/python"


def open_once(ip: str, mode: str, seconds: int, out: Path) -> dict | None:
    """One ``rf_clock.checks.ref_phase`` run on the board; its record, or None if it failed."""
    flags = " --no-clocks" if mode == "no-clocks" else ""
    cmd = (f"cd /tmp && sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr {PYNQ_PYTHON} -m rf_clock.checks.ref_phase "
           f"{seconds}{flags} --log /tmp/ref_phase_runs.jsonl")
    p = subprocess.run(["ssh", "-o", "BatchMode=yes", f"xilinx@{ip}", cmd], capture_output=True, text=True,
                       timeout=seconds + 300)
    with (out / f"board_{ip}.out").open("a", encoding="utf-8", newline="\n") as f:
        f.write(f"==== {mode}\n{p.stdout}{p.stderr}")
    for line in p.stdout.splitlines():
        if line.startswith("record "):
            return json.loads(line[len("record "):])
    return None


def wrap_ps(x: float) -> float:
    """A delay difference (ps) into +-50 ns."""
    return (x + 50_000.0) % 100_000.0 - 50_000.0


def rms(x: list[float]) -> float | None:
    return round(float(np.sqrt(np.mean(np.square(x)))), 2) if x else None


def summary(runs: list[dict]) -> dict:
    """Per mode: each board's and B less A's jumps (ps) from the open before."""
    out: dict = {}
    for i in range(1, len(runs)):
        prev, cur = runs[i - 1], runs[i]
        if any(r[k] is None or r[k].get("delay_mean_ns") is None for r in (prev, cur) for k in ("A", "B")):
            continue
        m = out.setdefault(cur["mode"], {"A": [], "B": [], "B_minus_A": [], "adc_tile0_offset": []})
        jump = {k: wrap_ps((cur[k]["delay_mean_ns"] - prev[k]["delay_mean_ns"]) * 1e3) for k in ("A", "B")}
        m["A"].append(round(jump["A"], 2))
        m["B"].append(round(jump["B"], 2))
        m["B_minus_A"].append(round(jump["B"] - jump["A"], 2))
        m["adc_tile0_offset"].append({k: (prev[k]["mts"]["adc"]["offset"]["0"], cur[k]["mts"]["adc"]["offset"]["0"])
                                      for k in ("A", "B")})
    for m in out.values():
        m["rms_ps"] = {k: rms(m[k]) for k in ("A", "B", "B_minus_A")}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--opens", type=int, default=8)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--modes", default="clocks,no-clocks")
    args = parser.parse_args()
    out = results_dir("m5-ref-phase-restarts")
    runs: list[dict] = []
    for mode in args.modes.split(","):
        for i in range(args.opens):
            found: dict[str, dict | None] = {}
            threads = [threading.Thread(target=lambda k=k, ip=ip: found.__setitem__(k, open_once(ip, mode, args.seconds, out)))
                       for k, ip in (("A", A), ("B", B))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            run = {"mode": mode, "open": i, **found}
            runs.append(run)
            with (out / "runs.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(run) + "\n")
            line = " ".join(f"{k} {'failed' if r is None else '%.4f ns (sd %.2f ps, adc0 offset %s)' % (r['delay_mean_ns'] or 0, r['delay_sd_ps'] or 0, r['mts']['adc']['offset']['0'])}"
                            for k, r in found.items())
            print(f"{mode} {i}: {line}", flush=True)
    result = summary(runs)
    (out / "summary.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({m: v["rms_ps"] for m, v in result.items()}))
    print(f"results in {out}")


if __name__ == "__main__":
    main()
