"""P11 on the board: the phase of the 10 MHz on ADC_D against the PL's 100 MHz clock, once a second.

Bring-up with MTS (``PL.open()`` sets ADC tile 0's NCO to 0 Hz), then one line per
second P11 keeps (``rf_clock.pl.ref_phase``), ``second`` and a JSON object: its
PPS_COUNT, beats, peak, amplitude, that phase as tau_ref in ns and the die
temperature.  The run's record (MTS, the LMX2594 VCO bands, the mean and scatter of
tau_ref over the whole seconds) is appended to ``--log`` and printed last, ``record``
and a JSON object.  PASS when every second after the first is whole (100 000 000
beats) and the input is there without clipping (amplitude above ``--min-amplitude``,
peak below 32000).

Wiring: a copy of the node's FS725 10 MHz on ADC_D (docs/ABI.md 4.14).

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.ref_phase 20
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from .board_common import RunLog, add_common_args, add_rfdc_args, mark, open_pl
from rf_clock.pl.board import die_c

WHOLE = 100_000_000  # beats of a whole second
PEAK_MAX = 32000  # above it the ADC clips


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    add_common_args(parser)
    add_rfdc_args(parser)
    parser.add_argument("seconds", nargs="?", type=int, default=20)
    parser.add_argument("--min-amplitude", type=float, default=1000.0, help="ADC codes (default 1000)")
    parser.add_argument("--log", default="ref_phase_runs.jsonl")
    args = parser.parse_args()

    log = RunLog(args.log, check="ref_phase", seconds=args.seconds)
    pl = open_pl(args, log)
    if pl is None:
        return 1
    seconds = []
    seen = 0
    t_end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < t_end:
            if pl.ref_phase.seq() == seen:
                time.sleep(0.02)
                continue
            second = pl.ref_phase.read()
            if second is None:
                continue
            seen = second.seq
            row = {"pps_count": second.pps_count, "pps_tick_mod10": second.pps_tick % 10, "beats": second.beats,
                   "peak": second.peak, "amplitude": round(second.amplitude, 2), "delay_ns": second.delay_ns,
                   "die_c": die_c(), "time": time.time()}
            seconds.append(row)
            print("second", json.dumps(row), flush=True)
    except KeyboardInterrupt:
        mark("interrupted")
    later = seconds[1:]  # the first may have begun before reset ended
    whole = [r for r in later if r["beats"] == WHOLE]
    delay = np.array([r["delay_ns"] for r in whole])
    if delay.size:
        delay = np.angle(np.exp(2j * np.pi * (delay - delay[0]) / 100.0)) / (2 * np.pi) * 100.0 + delay[0]
    amplitude = float(np.mean([r["amplitude"] for r in whole])) if whole else 0.0
    peak = max((r["peak"] for r in whole), default=0)
    log.record.update(
        seconds_kept=len(seconds), seconds_whole=len(whole), amplitude=amplitude, peak=peak,
        delay_mean_ns=float(np.mean(delay)) if delay.size else None,
        delay_sd_ps=float(np.std(delay, ddof=1) * 1e3) if delay.size > 1 else None,
        delay_slope_ps_per_s=float(np.polyfit(np.arange(delay.size), delay, 1)[0] * 1e3) if delay.size > 2 else None,
        die_c=die_c())
    passed = bool(later) and len(whole) == len(later) and amplitude >= args.min_amplitude and peak < PEAK_MAX
    print("record", json.dumps(log.record, default=str), flush=True)
    print("PASS" if passed else "FAIL")
    log.write(passed)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
