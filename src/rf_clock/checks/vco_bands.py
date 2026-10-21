"""Choose each LMX2594's VCO band for the board config, and measure each band's loopback delay.

Every clock write lets each LMX2594 calibrate its VCO band (VCO_CAPCTRL).  Where
7.68 GHz lies in reach of two bands, the calibration picks one or the other from
one write to the next, and one band higher puts that converter's clock 8-10 ps
later (results/lmx-sync-loopback-20261019 section 5).  The board config
(``rf_clock.pl.board_config``) makes ``PL.open()`` force one band per chip instead.

``scan``     No cable needed.  Write the clocks ``--calibrations`` times and count the
             band each LMX2594's calibration picks; the suggested band is the one
             it picks most often, which keeps the chip at the calibration's own
             operating point.  Then force each band from it down and up until one
             no longer locks, to show the margin on both sides (on 887329260072A
             the calibration picks one of the top two bands that lock).
             ``--write`` saves the suggestion, the counts and the bands that lock
             in the board config, with this board's serial number, and starts a
             new band table; the MTS capture codes recorded for the old bands go.
``delays``   Wired loopback DAC_A -> ADC_B, as for M3.  For each LMX2594 and each
             band that locks within ``--span`` of its configured one, the other
             chip at its configured band: ``--runs`` power-ups (``PL.open``, each
             in its own process) of ``--captures`` M3 windows.  Each band's
             loopback delay comes from three times the carrier phase of the
             correlation peak (about 0.1 ps; its whole turns of 57.5 ps counted
             with the envelope delay), relative to both chips at their configured
             bands.  ``--write`` saves it in the band table.

    run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
    $run -m rf_clock.checks.vco_bands scan --write --label 887329260072A
    $run -m rf_clock.checks.vco_bands delays --write

``delays`` appends one record per power-up to ``vco_bands_runs.jsonl``.
"""

from __future__ import annotations

import argparse
import cmath
import datetime
import json
import math
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

from rf_clock.pl.board_config import (DEFAULT_PATH, LMX_CAPCTRL_MAX, LMX_SIDES, PATH_ENV,
                                      BoardConfig, check_board, config_path, load_board_config,
                                      read_board_serial, save_board_config)
from rf_clock.pl.overlay import package_version

CARRIER_HZ = 5.8e9
K3 = 3 * 2 * math.pi * CARRIER_HZ * 1e-12  # rad of three times the correlation phase per ps
SETTLE_S = 1.0  # after a clock write, as PL.open
AMPLITUDE = 16000.0  # M3's defaults: PRN amplitude, slot offset after the PPS, RX margin
OFFSET_TICKS = 100_000
MARGIN_BEATS = 1250
MIN_PSL_DB = 10.0
RESULT = "VCO_BANDS_RESULT "


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def die_c() -> float | None:
    """The hottest RFSoC die sensor (AMS), in degrees C; the LMX2594 have none."""
    temps = [int(p.read_text()) for p in Path("/sys/bus/iio/devices/iio:device0").glob("in_temp*_input")]
    return max(temps) / 1000 if temps else None


def time_shift(delay_ps: float, phase3: float, ref_delay_ps: float, ref_phase3: float) -> float:
    """A run's loopback time minus a reference run's, from three times the carrier phase.

    A later delay gives a lower phase; the whole turns of 57.5 ps are the ones
    that bring the result closest to the difference of the envelope delays.
    """
    x = (phase3 - ref_phase3 + math.pi) % (2 * math.pi) - math.pi
    turns = round(((delay_ps - ref_delay_ps) * K3 + x) / (2 * math.pi))
    return (2 * math.pi * turns - x) / K3


# --- scan ---------------------------------------------------------------------------------

def scan(args) -> int:
    from xrfclk import xrfclk  # board-only  # pyright: ignore[reportMissingImports]

    from rf_clock.pl.board import program_clocks, read_lmx_vco
    from rf_clock.pl.bringup import wait_for_boot

    serial = read_board_serial()
    config = load_board_config(args.config)
    if config is not None:
        check_board(config, serial)
    print(f"board {serial}; boot state: {wait_for_boot()}", flush=True)

    def write(bands: dict[str, int]):
        program_clocks(xrfclk, bands)
        time.sleep(SETTLE_S)
        return read_lmx_vco(xrfclk, bands)

    counts: dict[str, Counter[int]] = {side: Counter() for side in LMX_SIDES}
    for n in range(1, args.calibrations + 1):
        vco = write({})
        print(f"clock write {n}: " + "; ".join(f"{side.upper()} {vco[side].describe()}" for side in LMX_SIDES),
              flush=True)
        if not all(v.locked for v in vco.values()):
            print("a calibrated band does not lock: check the clocks")
            return 1
        for side in LMX_SIDES:
            counts[side][vco[side].band] += 1
    bands = {side: counts[side].most_common(1)[0][0] for side in LMX_SIDES}
    locked: dict[str, list[int]] = {}
    for side in LMX_SIDES:
        print(f"{side.upper()} LMX2594 from band {bands[side]}:", flush=True)
        found = [bands[side]]
        for step in (-1, 1):
            band = bands[side] + step
            while 0 <= band <= LMX_CAPCTRL_MAX:
                vco = write({side: band})[side]
                print(f"  band {band}: {vco.describe()}", flush=True)
                if not vco.locked:
                    break
                found.append(band)
                band += step
        locked[side] = sorted(found)
    final = write(bands)
    for side in LMX_SIDES:
        low, high = locked[side][0], locked[side][-1]
        print(f"{side.upper()} LMX2594: calibration picked " + ", ".join(
            f"{band} {count}x" for band, count in counts[side].most_common())
              + f" in {args.calibrations} clock writes; bands {low}-{high} lock")
        print(f"  suggested band {bands[side]}, {bands[side] - low} above the lowest and {high - bands[side]} "
              f"below the highest that lock: {final[side].describe()}")
    temp = die_c()
    print(f"die temperature {temp} C")
    if not all(v.locked for v in final.values()):
        return 1
    if args.write:
        label = args.label if args.label is not None else (config.label if config else "")
        table = {**{side: {"calibrated": {str(b): c for b, c in sorted(counts[side].items())},
                           "locked": locked[side]} for side in LMX_SIDES},
                 "scanned": {"time": now(), "die_c": temp, "rf_clock": package_version(),
                             "writes": args.calibrations}}
        path = save_board_config(BoardConfig(serial=serial, label=label, lmx_vco_band=bands,
                                             band_table=table), args.config)
        print(f"written to {path}; the next PL.open forces these bands.  Record the MTS capture codes for "
              "them next (rf_clock.checks.mts_dtc record --write)")
    return 0


# --- delays -------------------------------------------------------------------------------

def measure(args) -> int:
    """One power-up at the bands given, then M3 captures; prints one RESULT line."""
    import numpy as np

    from rf_clock.correlator import estimate_delay
    from rf_clock.pl import PL, WaveformGenerator
    from rf_clock.pl.client import RXWindowInvalid, TargetMissed

    from .m3_loopback import SAMPLE_NS, SAMPLES_PER_BEAT, next_slot, tx_and_rx

    bands = {"adc": args.adc, "dac": args.dac}
    record: dict = {"bands": bands}
    try:
        config = load_board_config(args.config)
        if config is None:
            raise RuntimeError(f"no board config at {config_path()}")
        pl = PL.open(board_config=replace(config, lmx_vco_band=bands))
    except Exception as exc:
        print(RESULT + json.dumps({**record, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1
    mts = pl.rfdc.mts
    record["lmx_vco"] = {side: {**asdict(v), "band": v.band} for side, v in pl.board.lmx_vco.items()}
    record["t1_code"] = {"dac": mts.dac.dtc.get("t1_code"), "adc": mts.adc.dtc.get("t1_code")}

    gen = WaveformGenerator(amplitude=AMPLITUDE)
    ref = gen.generate().astype(np.float64)
    beats = gen.generate_beats()
    pl.tx_buffer.write_beats(beats)
    delays, phasors, psls = [], [], []
    for _ in range(args.captures):
        try:
            tx_actual, window, data = tx_and_rx(pl, next_slot(pl, OFFSET_TICKS), len(beats),
                                               len(beats) + MARGIN_BEATS)
        except (RXWindowInvalid, TargetMissed) as exc:
            print(f"capture skipped: {exc}", flush=True)
            continue
        peak = estimate_delay(data.samples, ref)
        delays.append((peak.index + (window.actual - tx_actual) * SAMPLES_PER_BEAT) * SAMPLE_NS * 1000)
        phasors.append(cmath.exp(3j * peak.phase))
        psls.append(peak.peak_to_sidelobe_db)
    record.update(die_c=die_c(), captures=len(delays), min_psl_db=min(psls, default=None))
    if len(delays) < max(2, args.captures // 2) or record["min_psl_db"] < MIN_PSL_DB:
        record["error"] = (f"{len(delays)} of {args.captures} captures, min PSL {record['min_psl_db']} dB: "
                           "is DAC_A cabled to ADC_B?")
    else:
        record.update(delay_ps=statistics.fmean(delays), delay_sd_ps=statistics.stdev(delays),
                      phase3=cmath.phase(sum(phasors)))
    print(RESULT + json.dumps(record), flush=True)
    return 0 if "error" not in record else 1


def run_child(bands: dict[str, int], args) -> dict:
    argv = [sys.executable, "-m", "rf_clock.checks.vco_bands", *(["--config", args.config] if args.config else []),
            "measure", "--adc", str(bands["adc"]), "--dac", str(bands["dac"]), "--captures", str(args.captures)]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=180 + 3 * args.captures)
    except subprocess.TimeoutExpired:
        return {"bands": bands, "error": "timeout"}
    for line in done.stdout.splitlines():
        if line.startswith(RESULT):
            return json.loads(line[len(RESULT):])
    return {"bands": bands, "error": ((done.stdout + done.stderr).strip().splitlines() or ["no output"])[-1]}


def delays(args) -> int:
    serial = read_board_serial()
    config = load_board_config(args.config)
    if config is None:
        print(f"no board config at {config_path()}: run 'scan --write' first")
        return 1
    check_board(config, serial)
    reference = config.lmx_vco_band
    locked = {side: config.band_table.get(side, {}).get("locked") for side in LMX_SIDES}
    if set(reference) != set(LMX_SIDES) or not all(locked.values()):
        print("the board config needs lmx_vco_band and the bands that lock for both chips: run 'scan --write'")
        return 1
    tried = {side: [band for band in locked[side] if abs(band - reference[side]) <= args.span]
             for side in LMX_SIDES}
    # Each round has the reference first, so that a slow drift shows in the reference runs.
    plan = ([dict(reference)] + [{**reference, side: band} for side in LMX_SIDES for band in tried[side]
                                 if band != reference[side]]) * args.runs
    print(f"board {serial} ({config.label}); reference bands {reference}; {len(plan)} power-ups", flush=True)
    runs: list[dict] = []
    for n, bands in enumerate(plan, 1):
        run = {"time": now(), "serial": serial, **run_child(bands, args)}
        runs.append(run)
        first = next((r for r in runs if "error" not in r and r["bands"] == reference), None)
        if "error" in run:
            line = f"error: {run['error']}"
        elif first is None:
            line = "no reference run yet"
        else:
            run["shift_ps"] = time_shift(run["delay_ps"], run["phase3"], first["delay_ps"], first["phase3"])
            line = (f"envelope {run['delay_ps'] / 1000:.4f} ns (sd {run['delay_sd_ps']:.1f} ps), "
                    f"time from the first reference run {run['shift_ps']:+.2f} ps, die {run['die_c']} C")
        with open(args.log, "a") as f:
            f.write(json.dumps(run) + "\n")
        print(f"{n:3d}/{len(plan)} ADC {bands['adc']} DAC {bands['dac']}: {line}", flush=True)

    good = [r for r in runs if "error" not in r and r.get("shift_ps") is not None]
    zero = [r["shift_ps"] for r in good if r["bands"] == reference]
    if not zero:
        print("no reference run succeeded")
        return 1
    offset = statistics.fmean(zero)
    table: dict[str, dict[str, float]] = {}
    for side in LMX_SIDES:
        other = "dac" if side == "adc" else "adc"
        print(f"{side.upper()} LMX2594, {other.upper()} at band {reference[other]}: loopback delay against "
              f"both at {reference['adc']}/{reference['dac']}")
        table[side] = {}
        for band in tried[side]:
            values = [r["shift_ps"] - offset for r in good if r["bands"] == {**reference, side: band}]
            if not values:
                print(f"  band {band}: no run succeeded")
                continue
            table[side][str(band)] = round(statistics.fmean(values), 2)
            print(f"  band {band}: {statistics.fmean(values):+7.2f} ps "
                  f"(spread {max(values) - min(values):.2f} ps, {len(values)} runs)")
    if args.write:
        band_table = dict(config.band_table)
        for side in LMX_SIDES:
            band_table[side] = {**band_table[side], "loopback_ps": table[side]}
        temps = [r["die_c"] for r in good if r.get("die_c") is not None]
        band_table["measured"] = {"time": now(), "die_c": round(statistics.fmean(temps), 1) if temps else None,
                                  "rf_clock": package_version(), "runs": args.runs,
                                  "captures": args.captures, "reference": reference}
        path = save_board_config(replace(config, band_table=band_table), args.config)
        print(f"band table written to {path}")
    return 0 if len(good) == len(runs) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--config", default=None,
                        help=f"board config file (default: ${PATH_ENV}, else {DEFAULT_PATH})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="suggest the band each calibration picks most often; find the bands that lock")
    s.add_argument("--calibrations", type=int, default=20, help="clock writes to count the calibrated bands over")
    s.add_argument("--write", action="store_true", help="save the suggestion in the board config")
    s.add_argument("--label", default=None, help="board label for the config, e.g. its S/N")
    d = sub.add_parser("delays", help="measure each band's loopback delay (cable DAC_A -> ADC_B)")
    d.add_argument("--span", type=int, default=2, help="bands each way from the configured one")
    d.add_argument("--runs", type=int, default=3, help="power-ups per band")
    d.add_argument("--captures", type=int, default=20, help="M3 windows per power-up")
    d.add_argument("--write", action="store_true", help="save the band table in the board config")
    d.add_argument("--log", default="vco_bands_runs.jsonl", help="append one JSON record per power-up")
    m = sub.add_parser("measure", help="one power-up at the given bands, then M3 captures (used by delays)")
    m.add_argument("--adc", type=int, required=True)
    m.add_argument("--dac", type=int, required=True)
    m.add_argument("--captures", type=int, default=20)
    args = parser.parse_args()
    return {"scan": scan, "delays": delays, "measure": measure}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
