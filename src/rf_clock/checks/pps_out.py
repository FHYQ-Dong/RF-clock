"""The output PPS on the board: P10's pulse on DAC_B (J20) once a second.

Bring-up with MTS (``PL.open()`` puts DAC tile 0 at baseband), then the output
PPS with ``--offset``, ``--sample``, ``--width``, ``--amplitude`` and ``--idle``
(``rf_clock.pl.pps_out``), and one line per second: the second's PPS_COUNT, where
its pulse was presented against its PPS_TICK, the pulse and flywheel counts, the
validity line and the die temperature.  PASS when, from the first pulse on, every
second has one pulse ``offset`` ticks after its PPS_TICK, none from the flywheel,
and, with ``--synced``, the validity line low (without it the line stays high by
design).

The analog half is on the scope: J20 (DAC_B) into 50 ohm, beside the node's
10 MHz and its FS725 1PPS.  The output edge must hold against the 10 MHz from
second to second and over restarts (plan/pps_output.md section 5).  Records go
to ``pps_out_runs.jsonl``.

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.pps_out 60 --synced
"""

from __future__ import annotations

import argparse
import sys
import time

from .board_common import RunLog, add_common_args, add_rfdc_args, mark, open_pl
from rf_clock.pl import abi
from rf_clock.pl.board import die_c
from rf_clock.pl.pps_out import MIN_OFFSET

R = abi.ABI.REG


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    add_common_args(parser)
    add_rfdc_args(parser)
    parser.add_argument("seconds", nargs="?", type=int, default=60)
    parser.add_argument("--offset", type=int, default=100,
                        help=f"ticks from the second's start to the pulse ({MIN_OFFSET} .. 99999999; default 100)")
    parser.add_argument("--sample", type=int, default=0, help="the step's first sample in its beat, 0 .. 7")
    parser.add_argument("--width", type=int, default=4, help="beats of the pulse (default 4, 40 ns)")
    parser.add_argument("--idle", type=int, default=0,
                        help="signed 16-bit DAC code between pulses (since 2.2; -amplitude doubles the step)")
    parser.add_argument("--amplitude", type=int, default=24000,
                        help="signed 16-bit DAC code (default 24000; negative to invert the pulse)")
    parser.add_argument("--synced", action="store_true",
                        help="set the synchronized bit, so the validity line may go low")
    parser.add_argument("--keep", action="store_true", help="leave the output PPS on at exit")
    parser.add_argument("--log", default="pps_out_runs.jsonl")
    args = parser.parse_args()

    log = RunLog(args.log, check="pps_out", seconds=args.seconds, offset=args.offset, sample=args.sample,
                 width=args.width, amplitude=args.amplitude, idle=args.idle, synced=args.synced)
    pl = open_pl(args, log)
    if pl is None:
        return 1
    log.record["board_serial"] = pl.board.serial
    out = pl.pps_out
    out.configure(amplitude=args.amplitude, offset=args.offset, sample=args.sample, width=args.width,
                  idle=args.idle)
    out.set_synced(args.synced)
    mark(f"output PPS: offset {args.offset} ticks, sample {args.sample}, width {args.width} beats, "
         f"amplitude {args.amplitude}, idle {args.idle}, synced {args.synced}; first pulse in the next second")

    print(f"{'count':>9} {'pulse-pps':>9} {'pulses':>6} {'flywheel':>8} {'invalid':>7} {'pps_lk':>6} {'die_c':>6}")
    seen = bad = 0
    first_bad: list[dict] = []
    last_count = None
    last_pulses = last_checked = None
    t_end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < t_end:
            count, pps_tick = pl.ctrl.latest_pps()
            if count == last_count:
                time.sleep(0.02)
                continue
            last_count = count
            due = pps_tick + args.offset + args.width + 1000
            while pl.ctrl.read64(R.TICK_LO) < due:
                time.sleep(0.01)
            st = out.status()
            locked = pl.ctrl.status().pps_locked
            temp = die_c()
            where = str(st.last_tick - pps_tick) if st.pulses else "-"
            print(f"{count:9d} {where:>9} {st.pulses:6d} {st.flywheel_pulses:8d} "
                  f"{int(st.invalid):7d} {int(locked):6d} {'-' if temp is None else f'{temp:.1f}':>6}", flush=True)
            if st.pulses == 0:
                continue  # ENABLE is taken at the start of a second
            if last_pulses is not None and last_checked is not None:
                seen += 1
                problems = [name for name, failed in (  # a second this loop missed still has its pulse
                    ("pulse count", st.pulses != last_pulses + (count - last_checked)),
                    ("pulse tick", st.last_tick != pps_tick + args.offset),
                    ("flywheel", st.flywheel),
                    ("validity line", args.synced and st.invalid),
                    ("pps unlocked", not locked)) if failed]
                if problems:
                    bad += 1
                    if len(first_bad) < 20:
                        first_bad.append({"count": count, "problems": problems,
                                          "pulse_minus_pps": st.last_tick - pps_tick, "pulses": st.pulses})
            last_pulses, last_checked = st.pulses, count
    except KeyboardInterrupt:
        mark("interrupted")
    finally:
        final = out.status()
        if not args.keep:
            out.disable()
            out.set_synced(False)
    log.record.update(seconds_checked=seen, seconds_bad=bad, first_bad=first_bad, pulses=final.pulses,
                      flywheel_pulses=final.flywheel_pulses, kept_on=args.keep)
    passed = seen > 0 and bad == 0
    print(f"{seen} seconds checked, {bad} bad; {final.pulses} pulses, {final.flywheel_pulses} from the flywheel")
    print("PASS" if passed else "FAIL")
    log.write(passed)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
