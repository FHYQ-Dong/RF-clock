"""Where this board's PPS edge lies within its tick, found with the FS725's PP.

The PL captures the PPS on its 100 MHz tick (P1), so PPS_TICK says in which tick the
edge arrived, not where in it.  The FS725's PP moves its 1 pps output earlier in 1 ns
steps against its 10 MHz, from which this board's tick is made.  Let d be how long
before the sampling edge that captures it the PPS edge arrives (0 to 10 ns).  Moved
earlier by P in all, the edge is captured one tick earlier as soon as d + P passes
10 ns, and again at 20 ns.  So stepping PP by 1 ns and watching PPS_TICK gives d to
within a nanosecond, and with it both capture margins (the edge may come up to d
later or 10 ns - d earlier before PPS_TICK changes); a step at which the edges toggle
between two ticks shows the width of the capture's uncertainty, and the distance
between the two crossings checks that 10 PP steps make one tick.  At the end PP
moves the edge to the middle of its tick (d = 5 ns), unless ``--no-center``.

Every later move should then be by whole ticks (``rf_clock.pl.acquire.place_pulse_ns``),
which keeps the edge where this scan put it.  The edge moves by the scan's total
(--steps ns plus the centring move) against the peer's PPS; the next acquisition
(``m4c_link --acquire``) takes that out in whole ticks.

Wiring: the board's PPS from its own FS725, whose RS-232 is on this board's USB
(``--fs725``).  No link may run: every PP that changes PPS_TICK breaks the PPS lock
for a few seconds.  One JSON line per PPS edge and a summary go to ``--out``.

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python \\
        -m rf_clock.checks.pps_pp_scan --fs725 /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A9NRKYDL-if00-port0
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

from .board_common import RunLog, add_common_args, add_rfdc_args, mark, open_pl
from rf_clock.pl.abi import ABI

TICKS_PER_SECOND = 100_000_000


def ticks_moved(tick: int, tick0: int) -> int:
    """How many ticks an edge at ``tick`` lies after the edge at ``tick0``, less whole seconds."""
    d = (tick - tick0) % TICKS_PER_SECOND
    return d - TICKS_PER_SECOND if d > TICKS_PER_SECOND // 2 else d


def crossings(steps: list[tuple[int, list[int]]]) -> list[float]:
    """The PP totals (ns) at which the edge moved to the tick before: for each change of
    the most frequent tick between neighbouring steps, the point where half the edges had
    moved, interpolated within the step (a step that toggles counts by its fractions)."""
    out = []
    for (p0, m0), (p1, m1) in zip(steps, steps[1:]):
        level0, level1 = statistics.median_low(m0), statistics.median_low(m1)
        if level1 >= level0:
            continue
        lower = min(m1)
        f0 = sum(1 for m in m0 if m <= lower) / len(m0)  # share of edges already in the earlier tick
        f1 = sum(1 for m in m1 if m <= lower) / len(m1)
        out.append(p0 + (p1 - p0) * (0.5 - f0) / (f1 - f0) if f1 != f0 else (p0 + p1) / 2)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--fs725", required=True, help="this board's FS725 serial port")
    parser.add_argument("--steps", type=int, default=22, help="1 ns PP steps")
    parser.add_argument("--edges", type=int, default=8, help="PPS edges recorded per step")
    parser.add_argument("--no-center", action="store_true", help="leave the edge where the scan ends")
    parser.add_argument("--out", default="/dev/shm/pps_pp_scan.jsonl")
    parser.add_argument("--log", default="pps_pp_scan_runs.jsonl")
    add_common_args(parser)
    add_rfdc_args(parser)
    args = parser.parse_args()

    from rf_clock.fs725 import FS725

    log = RunLog(args.log, steps=args.steps, edges=args.edges)
    pl = open_pl(args, log)
    if pl is None:
        raise SystemExit(1)
    pl.slots.disable()
    out = open(args.out, "w")

    def emit(kind: str, **fields) -> None:
        out.write(json.dumps({"type": kind, "time": time.time(), **fields}) + "\n")
        out.flush()

    unit = FS725.open(args.fs725, log=lambda e: emit("fs725", **e))
    emit("start", identity=unit.identify(), serial=pl.board.serial, steps=args.steps, edges=args.edges)
    deadline = time.monotonic() + 30
    while not pl.ctrl.status().pps_locked:
        if time.monotonic() > deadline:
            raise SystemExit("no PPS lock within 30 s")
        time.sleep(0.5)
    _, tick0 = pl.ctrl.wait_next_pps()

    def record(total: int) -> list[int]:
        pl.ctrl.wait_next_pps()  # the edge after the PP command may still be the old one
        moved = []
        for _ in range(args.edges):
            count, tick = pl.ctrl.wait_next_pps()
            m = ticks_moved(tick, tick0)
            moved.append(m)
            emit("edge", pp_total_ns=total, count=count, tick=tick, moved=m,
                 jitter=pl.ctrl.read32(ABI.REG.PPS_JITTER_CNT), period=pl.ctrl.read32(ABI.REG.PPS_LAST_PERIOD))
        return moved

    steps = [(0, record(0))]
    mark(f"PP 0 ns: PPS_TICK moved {sorted(set(steps[-1][1]))} ticks")
    for k in range(1, args.steps + 1):
        unit.place_pulse(1)
        steps.append((k, record(k)))
        mark(f"PP {k} ns: PPS_TICK moved {sorted(set(steps[-1][1]))} ticks")
    found = crossings(steps)
    result: dict = dict(crossings_ns=found, steps=[dict(pp_total_ns=p, moved=m) for p, m in steps])
    if found:
        d = 10.0 - found[0]  # the edge's lead on its sampling edge before the scan
        result["lead_ns"] = d
        result["margins_ns"] = dict(later=d, earlier=10.0 - d)
        if len(found) > 1:
            result["ns_per_tick"] = found[1] - found[0]
        now = (d + args.steps) % 10.0  # the lead after the scan
        move = round((5.0 - now) % 10.0)
        result["lead_after_scan_ns"] = now
        if not args.no_center and move:
            unit.place_pulse(move)
            mark(f"PP {move} ns more: the edge to the middle of its tick")
            result["center_move_ns"] = move
            steps.append((args.steps + move, record(args.steps + move)))
            result["after_center"] = steps[-1][1]
    unit.close()
    time.sleep(1.5)
    pl.ctrl.clear_fault()  # the moves of PPS_TICK are latched as PPS faults
    emit("result", **{k: v for k, v in result.items() if k != "steps"})
    out.close()
    mark(f"crossings at {found} ns of PP; lead {result.get('lead_ns')} ns, "
         f"ns per tick {result.get('ns_per_tick')}, centring move {result.get('center_move_ns')}")
    passed = bool(found) and ("after_center" not in result or len(set(result["after_center"])) == 1)
    log.record.update({k: v for k, v in result.items() if k != "steps"})
    log.write(passed)
    print(f"PPS PP scan: {'PASS' if passed else 'FAIL'}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
