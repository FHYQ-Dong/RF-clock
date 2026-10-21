"""Record each converter's MTS SYSREF capture codes for the board config.

MTS samples SYSREF in every tile through a delay of 128 codes, about 5.3 ps each,
and takes the middle of a window where the capture is stable; the windows repeat
every clock period, about 20 codes on the DAC and 37 on the ADC
(results/lmx-sync-loopback-20261019 section 1.3).  Left to itself the driver takes
the window nearest code 64.  Where two windows lie about as far from 64, a few
picoseconds of clock phase from one power-up to the next pick the other one, and
the converter moves by a whole sample (887329260101's DAC on 2026-10-21: codes 54/53,
then 73/73, 104 ps later).  With codes recorded in the board config, ``PL.open()``
makes the driver pick the windows nearest them instead (``rf_clock.pl.rfdc.run_mts``):
the same windows at every power-up while the clock stays within half a window of
where it was recorded.

``record``  ``--runs`` power-ups (``PL.open``, each in its own process, with the board
            config's VCO bands but without its codes) and, for each converter, clock
            and tile, the codes picked.  Codes within ``DTC_MARGIN`` of each other
            are one window; the window picked most often gives the code, the median
            of its picks.  ``--write`` stores the codes in the board config.
``check``   ``--runs`` power-ups with the board config's codes: each picked code
            against the one recorded.

    run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
    $run -m rf_clock.checks.mts_dtc record --runs 6 --write
    $run -m rf_clock.checks.mts_dtc check --runs 6

Run it after ``vco_bands scan --write`` (the codes depend on the forced bands, and
``scan --write`` drops them) and before any timing calibration.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import subprocess
import sys
from dataclasses import replace

from rf_clock.pl.board import DTC_MARGIN
from rf_clock.pl.board_config import (DEFAULT_PATH, LMX_SIDES, PATH_ENV, check_board, config_path,
                                      load_board_config, read_board_serial, save_board_config)
from rf_clock.pl.overlay import package_version

RESULT = "MTS_DTC_RESULT "
CLOCKS = ("t1", "pll")


def windows(codes: list[int]) -> list[list[int]]:
    """The picks grouped into windows: codes within DTC_MARGIN of their neighbours."""
    groups: list[list[int]] = []
    for code in sorted(codes):
        if groups and code - groups[-1][-1] <= DTC_MARGIN:
            groups[-1].append(code)
        else:
            groups.append([code])
    return groups


def choose(codes: list[int]) -> int:
    """The code of the window picked most often (the lower one on a tie): its median."""
    best = max(windows(codes), key=len)
    return int(round(statistics.median(best)))


def power_up(args, with_codes: bool) -> dict:
    """PL.open in a child process; its MTS capture codes, or an error."""
    argv = [sys.executable, "-m", "rf_clock.checks.mts_dtc", *(["--config", args.config] if args.config else []),
            "open", *([] if with_codes else ["--without-codes"])]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    for line in done.stdout.splitlines():
        if line.startswith(RESULT):
            return json.loads(line[len(RESULT):])
    return {"error": ((done.stdout + done.stderr).strip().splitlines() or ["no output"])[-1]}


def open_once(args) -> int:
    """The child: one PL.open, then one RESULT line."""
    from rf_clock.pl import PL

    try:
        config = load_board_config(args.config)
        if config is None:
            raise RuntimeError(f"no board config at {config_path()}")
        pl = PL.open(board_config=replace(config, mts_dtc={}) if args.without_codes else config)
        mts = pl.rfdc.mts
        result = {kind: {"tiles": list(r.tiles), **{c: list(r.dtc[f"{c}_code"]) for c in CLOCKS},
                         "target": {c: list(v) for c, v in r.dtc_target.items()}}
                  for kind, r in (("dac", mts.dac), ("adc", mts.adc))}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    print(RESULT + json.dumps(result), flush=True)
    return 0 if "error" not in result else 1


def run(args, with_codes: bool) -> tuple[object, list[dict]]:
    serial = read_board_serial()
    config = load_board_config(args.config)
    if config is None or set(config.lmx_vco_band) != set(LMX_SIDES):
        print(f"the board config ({config_path()}) must force both VCO bands first: run "
              "'rf_clock.checks.vco_bands scan --write'")
        sys.exit(1)
    check_board(config, serial)
    if with_codes and not any(k in config.mts_dtc for k in LMX_SIDES):
        print("the board config records no capture codes: run 'record --write' first")
        sys.exit(1)
    results = []
    for n in range(1, args.runs + 1):
        result = power_up(args, with_codes)
        results.append(result)
        if "error" in result:
            print(f"{n:2d}: {result['error']}", flush=True)
        else:
            print(f"{n:2d}: " + "; ".join(f"{kind.upper()} {clock.upper()} " + "/".join(
                str(result[kind][clock][t]) for t in result[kind]["tiles"])
                for kind in LMX_SIDES for clock in CLOCKS if result[kind][clock][0] >= 0), flush=True)
    return config, [r for r in results if "error" not in r]


def record(args) -> int:
    config, results = run(args, with_codes=False)
    if not results:
        return 1
    tiles = results[0]["dac"]["tiles"]
    codes: dict = {}
    for kind in LMX_SIDES:
        for clock in CLOCKS:
            if results[0][kind][clock][tiles[0]] < 0:  # no PLL scan on the DAC tiles
                continue
            row = [-1] * 4
            for tile in tiles:
                picks = [r[kind][clock][tile] for r in results]
                row[tile] = choose(picks)
                found = ", ".join(f"{int(round(statistics.median(w)))} ({len(w)}x)" for w in windows(picks))
                print(f"{kind.upper()} {clock.upper()} tile {tile}: windows {found}; recorded {row[tile]}"
                      + ("  <- the choice changed between power-ups" if len(windows(picks)) > 1 else ""))
            codes.setdefault(kind, {})[clock] = row
    if args.write:
        codes["recorded"] = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                             "runs": len(results), "bands": config.lmx_vco_band, "rf_clock": package_version()}
        path = save_board_config(replace(config, mts_dtc=codes), args.config)
        print(f"written to {path}; PL.open picks these windows from now on")
    return 0 if len(results) == args.runs else 1


def check(args) -> int:
    _, results = run(args, with_codes=True)
    worst = 0
    for r in results:
        for kind in LMX_SIDES:
            for clock, target in r[kind]["target"].items():
                for tile in r[kind]["tiles"]:
                    if target[tile] >= 0:
                        worst = max(worst, abs(r[kind][clock][tile] - target[tile]))
    print(f"largest distance of a picked code from its record: {worst} codes (warning above {DTC_MARGIN})")
    return 0 if results and len(results) == args.runs and worst <= DTC_MARGIN else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--config", default=None,
                        help=f"board config file (default: ${PATH_ENV}, else {DEFAULT_PATH})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="power up without recorded codes and record the windows picked")
    r.add_argument("--runs", type=int, default=6, help="power-ups")
    r.add_argument("--write", action="store_true", help="store the codes in the board config")
    c = sub.add_parser("check", help="power up with the recorded codes and compare")
    c.add_argument("--runs", type=int, default=6, help="power-ups")
    o = sub.add_parser("open", help="one power-up, printing its codes (used by record and check)")
    o.add_argument("--without-codes", action="store_true")
    args = parser.parse_args()
    return {"record": record, "check": check, "open": open_once}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
