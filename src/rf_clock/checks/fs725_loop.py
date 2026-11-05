"""This board's FS725 into its board config: which unit it is, and its frequency lock loop
at a gain, measured from FC's answer to steps of SF.

Inside the FS725 a PRS10 holds its 10 MHz crystal to the rubidium resonance with a
frequency lock loop (docs/fs725_principles.md): an integrator of the resonance error sets
FC, the crystal's tuning, and the crystal follows FC through a 1 s filter.  A step of SF
moves the frequency the loop holds the crystal to; the integrator then drives FC until the
crystal, lagging behind FC, gets there.  So FC's answer to the step alone gives the loop,
the integrator's gain k, the crystal's lag and the dead time (``rf_clock.twtt.steer.fit_loop``,
results/fs725-fll-20261105 section 5.1), and neither the other board nor the link is
needed.  The predictive control law steers by that loop, so it must be this unit's at the
gain it runs at: at GA 7, A's unit's integrator is 1.7 times B's (results/two-loops-20261105
section 5.5).

Wiring: the FS725's RS-232 on this board's USB (``--port``, by default the port the board
config records).  The unit's frequency moves by
``--step`` parts in 1e12 and back, ``--steps`` times, ``--hold`` seconds each, so its 10 MHz
and 1PPS move by up to step x hold (8 ns at the defaults) and return: no link may use this
board's clock meanwhile.  One k, lag and dead time are fitted to all the steps together:
FC's own wander moves the fit of a single step, and with steps of 100 the lag of A's unit,
whose FC moves only about 0.65 steps per unit of SF, came out at 1.05 s against 1.65 s
from steps of 200 (2026-11-05).  The 1 pps PLL is off for the run and back on at the end if it was
on; SF ends where it started; GA stays at ``--gain`` with ``--write`` and goes back to what it
was otherwise.

``--write`` records the unit (its PRS10's serial number and identity), the port, ``--gain`` as
the gain the link sets at its start, and the loop at that gain in the board config (``fs725``, docs/ABI.md 11.1); another unit's entry is replaced, loops at other
gains of the same unit are kept.  One record per run, with every FC read, goes to ``--log``.

    sudo /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.fs725_loop \\
        --port /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A9NRKYDL-if00-port0 --gain 8 --write
"""

from __future__ import annotations

import argparse
import datetime
import json
import time
from dataclasses import replace
from typing import Callable

import numpy as np

from .board_common import mark
from rf_clock.fs725 import FS725, FS725Error
from rf_clock.pl.board_config import check_board, config_path, load_board_config, read_board_serial, save_board_config
from rf_clock.pl.overlay import package_version
from rf_clock.twtt.steer import fit_loop

SETTLE_S = 20.0  # FC read before the first step: the fit takes 10 s before each step


def measure(unit: FS725, *, sf0: int, step: int, steps: int, hold_s: float, fc_every: float,
            settle_s: float = SETTLE_S, clock: Callable[[], float] = time.monotonic,
            sleep: Callable[[float], None] = time.sleep,
            say: Callable[[str], None] = mark) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    """FC every ``fc_every`` s, for ``settle_s``, then after each of ``steps`` changes of SF,
    alternately to ``sf0 + step`` and back, for ``hold_s``.  Returns the reads (time, FC in low
    DAC steps, 1000 high + low) and the commands (time sent, change of SF)."""
    reads: list[tuple[float, int]] = []
    commands: list[tuple[float, int]] = []

    def read_for(seconds: float) -> None:
        due = clock()
        end = due + seconds
        while clock() < end:
            before = clock()
            high, low = unit.frequency_control()
            reads.append(((before + clock()) / 2, 1000 * high + low))
            due += fc_every
            sleep(max(0.0, due - clock()))

    read_for(settle_s)
    sf = sf0
    for i in range(steps):
        new = sf0 + step if i % 2 == 0 else sf0
        commands.append((clock(), new - sf))
        unit.write_frequency(new)  # at once: the fit counts from the command
        if unit.frequency() != new:
            raise FS725Error(f"SF {new} not taken: SF? returns {unit.frequency()}")
        sf = new
        say(f"SF {new} ({i + 1} of {steps})")
        read_for(hold_s)
    return reads, commands


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--port", default=None, help="this board's FS725 serial port (default: the board config's)")
    parser.add_argument("--gain", type=int, required=True, help="the frequency lock loop's gain GA to measure at (0-10)")
    parser.add_argument("--step", type=int, default=200, help="the change of SF, parts in 1e12")
    parser.add_argument("--steps", type=int, default=4, help="changes of SF, alternately up and back")
    parser.add_argument("--hold", type=float, default=40.0, help="s after each change (at least 30: each fit "
                                                                  "takes FC from 10 s before to 20 s after)")
    parser.add_argument("--fc-every", type=float, default=0.1, help="s between FC reads")
    parser.add_argument("--write", action="store_true", help="record the unit, the gain and the loop in the board config")
    parser.add_argument("--config", default=None, help=f"board config file (default {config_path()})")
    parser.add_argument("--log", default="fs725_loop_runs.jsonl")
    args = parser.parse_args()
    if args.hold < 30:
        parser.error("--hold must be at least 30 s")

    board = read_board_serial()
    config = load_board_config(args.config)
    if config is None:
        raise SystemExit(f"no board config at {config_path()}: write it first (docs/board_setup.md section 4)")
    check_board(config, board)
    args.port = args.port or config.fs725.get("port")
    if not args.port:
        raise SystemExit(f"{config_path()} records no FS725 port: give it, --port /dev/serial/by-id/...")
    written: list[dict] = []
    unit = FS725.open(args.port, log=written.append)
    locked = unit.status().rb_locked
    identity, serial = unit.identify(), unit.serial()
    pll, gain0, sf0 = unit.phase_lock(), unit.gain(), unit.frequency()
    if not locked:
        unit.close()
        raise SystemExit("the FS725 is not locked to rubidium")
    done = False
    try:
        if pll:
            unit.set_phase_lock(False)  # SF is ignored while it phase-locks to a 1 pps input
        if gain0 != args.gain:
            unit.set_gain(args.gain)
        unit.set_frequency(sf0)  # hands SF over from the calibration pot, whose reading flickers
        mark(f"FS725 {identity} on board {config.label or board}: GA {gain0} -> {args.gain}, SF {sf0}; "
             f"{args.steps} steps of {args.step}, {args.hold:.0f} s each")
        reads, commands = measure(unit, sf0=sf0, step=args.step, steps=args.steps, hold_s=args.hold,
                                  fc_every=args.fc_every)
        done = True
    finally:
        if unit.frequency() != sf0:
            unit.set_frequency(sf0)
        if not (done and args.write) and unit.gain() != gain0:
            unit.set_gain(gain0)
        if pll:
            unit.set_phase_lock(True)
        unit.close()

    t = np.array([r[0] for r in reads])
    fc = np.array([r[1] for r in reads], dtype=float)
    fit = fit_loop(t, fc, [at for at, _ in commands])
    loop = {key: fit[key] for key in ("k_per_s", "lag_s", "dead_s")}
    steps = [{"sf_change": change, **alone} for (_, change), alone in zip(commands, fit["alone"])]
    for f in steps:
        print(f"SF {f['sf_change']:+d} alone: k {f['k_per_s']:.2f} per s, lag {f['lag_s']:.2f} s, "
              f"dead {f['dead_s']:.2f} s; FC {f['fc_change']:+.0f} steps, {f['rms_steps']:.1f} rms about the fit")
    print(f"FS725 S/N {serial} at GA {args.gain}, all steps: k {loop['k_per_s']} per s, lag {loop['lag_s']} s, "
          f"dead {loop['dead_s']} s, FC {fit['rms_steps']:.1f} steps rms about the fit; SF acts on the frequency "
          f"about {loop['dead_s'] + 1 / loop['k_per_s']:.2f} s after the command")
    when = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if args.write:
        entry = config.fs725 if config.fs725.get("serial") == serial else {}
        if config.fs725 and not entry:
            print(f"the board config named FS725 S/N {config.fs725['serial']}: replaced")
        loops = {**entry.get("loops", {}), str(args.gain): {**loop, "measured": {
            "time": when, "rf_clock": package_version(), "sf_step": args.step, "hold_s": args.hold,
            "rms_steps": round(fit["rms_steps"], 1),
            "steps": [{key: round(f[key], 3) for key in ("sf_change", "k_per_s", "lag_s", "dead_s", "rms_steps")}
                      for f in steps]}}}
        path = save_board_config(replace(config, fs725={"serial": serial, "identity": identity, "port": args.port,
                                                        "gain": args.gain, "loops": loops}), args.config)
        print(f"written to {path}: the link checks this unit and sets GA {args.gain} at its start "
              f"(copy the file to boards/<S/N>/board.json in the repository)")
    t0 = reads[0][0]
    with open(args.log, "a") as f:
        f.write(json.dumps({"time_utc": when, "board": board, "label": config.label, "identity": identity,
                            "gain": args.gain, "gain_before": gain0, "sf0": sf0, "step": args.step, "hold_s": args.hold,
                            "loop": loop, "rms_steps": fit["rms_steps"], "fc_changes": fit["fc_changes"],
                            "steps": steps, "written": args.write, "settings": written,
                            "commands": [[round(at - t0, 3), change] for at, change in commands],
                            "fc": [[round(at - t0, 3), v] for at, v in reads]}) + "\n")
    print(f"record appended to {args.log}")


if __name__ == "__main__":
    main()
