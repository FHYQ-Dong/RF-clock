"""Shared steps for the board checks in this package.

The checks run on the RFSoC 4x2 as root, for example::

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.m1_check 60

(xrfclk needs ``BOARD``; PYNQ needs ``XILINX_XRT``.)  The overlay is the one
packaged with rf-clock unless ``--bitstream`` names another.  If the board hangs,
recover it from RF-clock-PC with ``tools/jtag_reset_board.tcl``.
"""

from __future__ import annotations

import argparse
import datetime
import json
from dataclasses import asdict

from rf_clock.pl import PL
from rf_clock.pl.board import ADC_TARGET_LATENCY, DAC_TARGET_LATENCY
from rf_clock.pl.bringup import BringupError
from rf_clock.pl.overlay import DEFAULT_BITSTREAM, VersionMismatch, package_version
from rf_clock.pl.rfdc import RFDCError


def mark(msg: str) -> None:
    """Progress marker on stdout and the kernel log (serial console), so a
    hard hang still shows the last step reached."""
    print("m:", msg, flush=True)
    try:
        with open("/dev/kmsg", "w") as k:
            k.write(f"<3>rf-clock: {msg}\n")
    except OSError:
        pass


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bitstream", default=str(DEFAULT_BITSTREAM),
                        help="overlay .bit with its .hwh next to it (default: packaged with rf-clock)")
    parser.add_argument("--no-version-check", action="store_true",
                        help="skip the manifest and BUILD_VERSION checks (bitstreams before ABI 1.2)")
    parser.add_argument("--no-clocks", action="store_true",
                        help="skip writing LMK04828/LMX2594 (already written since boot)")
    parser.add_argument("--pps-trig", action="store_true",
                        help="time the Schmitt-trigger PPS copy (PPS_CONFIG bit 0)")
    parser.add_argument("--pps-invert", action="store_true",
                        help="time the falling edge (PPS_CONFIG bit 1)")


def add_rfdc_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ref-tile", type=int, default=0, help="MTS reference tile")
    parser.add_argument("--dac-target", type=int, default=DAC_TARGET_LATENCY,
                        help=f"DAC MTS target latency (default {DAC_TARGET_LATENCY}; "
                             "-1 = align to the largest measured)")
    parser.add_argument("--adc-target", type=int, default=ADC_TARGET_LATENCY,
                        help=f"ADC MTS target latency (default {ADC_TARGET_LATENCY}; "
                             "-1 = align to the largest measured)")


class RunLog:
    """One JSON record per run, appended to a .jsonl file for comparing restarts."""

    def __init__(self, path: str, **fields) -> None:
        self.path = path
        self.record: dict = {
            "time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            **fields,
        }

    def write(self, passed: bool) -> None:
        self.record["pass"] = passed
        with open(self.path, "a") as f:
            f.write(json.dumps(self.record, default=str) + "\n")
        print(f"record appended to {self.path}")


def mts_record(result) -> dict:
    return {
        "status": result.status,
        "ok": result.ok,
        "ref_tile": result.ref_tile,
        "target_latency": result.target_latency,
        "latency": {t: result.latency[t] for t in result.tiles},
        "offset": {t: result.offset[t] for t in result.tiles},
        "dtc": {key: {t: values[t] for t in result.tiles} for key, values in result.dtc.items()},
    }


def open_pl(args, log: RunLog) -> PL | None:
    """``PL.open`` with RFDC MTS (docs/ABI.md 7.1), logged.

    Returns None after printing the failure and appending it to ``log``.
    """
    log.record["clocks_written"] = not args.no_clocks
    log.record["rf_clock_version"] = package_version()
    try:
        pl = PL.open(args.bitstream, write_clocks=not args.no_clocks,
                     pps_source_trig=args.pps_trig, pps_invert=args.pps_invert,
                     ref_tile=args.ref_tile, dac_target_latency=args.dac_target,
                     adc_target_latency=args.adc_target,
                     check_version=not args.no_version_check, progress=mark)
    except (BringupError, RFDCError, VersionMismatch) as exc:
        print("bringup: FAILED:", exc)
        mts = getattr(exc, "mts", None)
        if mts is not None:
            log.record["mts"] = {"dac": mts_record(mts.dac), "adc": mts_record(mts.adc)}
        log.record["error"] = str(exc)
        log.write(False)
        return None
    mts = pl.rfdc.mts
    log.record["abi_version"] = f"0x{pl.ctrl.info.version:08x}"
    log.record["mts"] = {"dac": mts_record(mts.dac), "adc": mts_record(mts.adc)}
    log.record["tile_startup_call_needed"] = mts.started_after_startup_call
    log.record["nco_synced"] = mts.nco_synced
    log.record["mixers"] = mts.mixers
    log.record["board"] = {
        "serial": pl.board.serial,
        "label": pl.board.config.label if pl.board.config else None,
        "lmx_vco": {side: {**asdict(v), "band": v.band} for side, v in pl.board.lmx_vco.items()},
    }
    for kind, settings in mts.mixers.items():
        print(f"{kind} tile 2 block 0 mixer: Freq {settings.get('Freq')} MHz, "
              f"MixerType {settings.get('MixerType')}, MixerMode {settings.get('MixerMode')}")
    return pl
