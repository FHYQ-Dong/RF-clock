"""M3 sub-experiment: how does each LMX2594's phase SYNC change the loopback delay across clock writes?

Problem.  results/lmx-phase-sync-20261019 traced the half-sample DAC states to
the LMX2594 output divider and removed them with its phase SYNC (rf-clock
1.3.0.4), but measured only the DAC side, on the scope (two states 52 ps
apart, read as 260.4 ps reference steps).  Its section 3.5 predicted the ADC
side: four states 52 ps apart without the SYNC, one with it.  This repeats M3
(DAC_A -> attenuator -> band-pass -> ADC_B on one board) over clock writes,
with each LMX2594 either synchronized as PL.open does it (``sync``) or
rewritten with the image from before rf-clock 1.3.0.4 and left unsynchronized
(``nosync``): ``--adc-lmx nosync`` alone shows the ADC's states,
``--dac-lmx nosync`` alone the DAC's, both ``sync`` the baseline.  ``nosync2``
writes that image twice in a row, so that the second write starts from an
unsynchronized chip (the ADC took other lattice positions then than with
``nosync``).  ``legacy`` writes the clocks as PL.open did before 1.3.0.4: that
LMX2594 gets only the unsynchronized image, never the synchronized one first;
with ``--reboot`` it starts from the clocks of PYNQ's boot-time base overlay,
as in the scope series of results/lmx-phase-sync-20261019.

Each run is timed two ways: the envelope delay (M3's correlation peak, about
5 ps per run) and the carrier phase of the correlation peak, which moves by
2 pi x 5.8 GHz per unit of delay (0.036 rad/ps) and gives the time shift to a
fraction of a ps.  Each PL.open adds a random multiple of 120 degrees to that
phase (not a time shift), so three times the phase is used; its 57.5 ps
cycles are counted with the envelope delay.  Each run also prints that
120-degree step (k = 0, 1, 2) relative to the first run.

The 120-degree steps were also on the DAC output alone (the scope data of
results/lmx-phase-sync-20261019), presumably an NCO phase reset landing on one
of three tile-clock edges.  ``--nco-reset dac|adc|both`` restarts the NCO
phase of the signal block(s) at a SYSREF edge after PL.open (mixer event
source SYSREF, then ResetNCOPhase; the tiles keep capturing SYSREF after MTS),
which should leave one k; resetting only one side shows whether the other has
the steps too.  ``--nco-event tile`` applies the same reset on a tile event
issued by the software instead, at an arbitrary instant: if the steps come from
the instant of the reset, they return.  ``--reboot`` reboots the board before
every run but the first.

With everything synchronized, reboots still left the ADC in one of two states
9.7 ps apart (results/lmx-sync-loopback-20261019 section 6), with both ADC
tiles' MTS capture codes moving together: the ADC's LMX2594 or its reference.
``--readback`` reads back each LMX2594's VCO calibration result after the
clocks are written (core, CAPCTRL, DACISET and the Vtune lock detect, R110-R112),
to see whether the state follows the ADC LMX2594's calibration.

Procedure.  For each run, on the board: write the LMK04828 and both LMX2594 as
PL.open does (``--write-clocks``, the part of this script copied to the
board), rewrite the ``nosync`` LMX2594(s), then run
``rf_clock.checks.m3_loopback --no-clocks`` (bitstream reload, RFDC start-up,
MTS, ``--captures`` windows) through ``--run-m3``, which adds the NCO reset
after PL.open.  The board must be idle.

Run on RF-clock-PC-ai as the normal user (board commands over ssh)::

    .venv/bin/python experiments/m3_restarts.py --runs 10                      # both synchronized
    .venv/bin/python experiments/m3_restarts.py --adc-lmx nosync --runs 20
    .venv/bin/python experiments/m3_restarts.py --dac-lmx nosync --runs 20
    .venv/bin/python experiments/m3_restarts.py --nco-reset dac --runs 10
    .venv/bin/python experiments/m3_restarts.py --readback --reboot --runs 20

Results go to results/m3-restarts-<time>/: runNNN.log (the board output),
runs.jsonl (one record per run, with M3's record) and summary.json.
"""

from __future__ import annotations

import argparse
import cmath
import json
import math
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = "192.168.2.99"  # S/N 887329260072A, the board with the loopback cable
REMOTE = "/tmp/m3_restarts.py"
M3_LOG = "/tmp/m3_restarts.jsonl"
ENV = "env BOARD=RFSoC4x2 XILINX_XRT=/usr"
BUSY = "ps -eo args | grep -cE '^(sudo -n env BOARD|[.]venv/bin/python)'"
# Device-tree nodes of the two LMX2594 on the RFSoC 4x2 PYNQ image.  Their names are
# swapped: powering down "lmxdac" (spi0.1) unlocks the ADC tiles' PLLs, "lmxadc"
# (spi0.2) the DAC tiles' (2026-10-20).  Series before then have the two swapped.
LMX_NODES = {"adc": "lmxdac", "dac": "lmxadc"}
# The two words that differ in the image before rf-clock 1.3.0.4: R0 without
# VCO_PHASE_SYNC and R36 PLL_N = 160 (the packaged image has 0x00641C, N = 40).
SYNCED = {0: 0x00641C, 36: 0x240028}
UNSYNCED = {0: 0x00241C, 36: 0x2400A0}
CAPTURE_LINE = re.compile(r"^\s*(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+([\d.]+)\s*$")
CARRIER_HZ = 5.8e9
K3 = 3 * 2 * math.pi * CARRIER_HZ * 1e-12  # rad of three times the correlation phase per ps of delay
SIGNAL_TILE = 2  # DAC and ADC tile 2, block 0 carry the signal
# LMX2594 readback (TI SNAS696C 7.3.5.2 and 7.6.13): R0 bit MUXOUT_LD_SEL = 0 puts the
# registers on MUXout, which the board buffers onto the shared SDO line while CSB is low.
LMX_FCAL_EN = 1 << 3
LMX_MUXOUT_LD_SEL = 1 << 2
LMX_READBACK = {"ld_vtune": (110, 9, 2), "vco_sel": (110, 5, 3), "vco_capctrl": (111, 0, 8), "vco_daciset": (112, 0, 9)}
SPI_IOC_MESSAGE_1 = 0x40206B00  # _IOW('k', 0, struct spi_ioc_transfer), 32 bytes
LMX_CAPCTRL_FORCE = 1 << 11  # R8; the forced value is R19[7:0] VCO_CAPCTRL
READBACK_LINE = re.compile(r"^LMX_READBACK (\{.*\})$", re.M)


def lmx_readback(device: dict, r0: int) -> dict:
    """On the board: the VCO calibration result of one LMX2594, read back over SPI.

    ``r0`` is the R0 word last written.  Both R0 writes here clear FCAL_EN, so that
    neither recalibrates the VCO, and keep VCO_PHASE_SYNC as it is, so that neither
    is a SYNC; the second restores MUXout to lock detect."""
    import ctypes
    import fcntl
    import struct

    r0 &= ~LMX_FCAL_EN
    with open(device["spi_device"], "rb+", buffering=0) as f:

        def transfer(data: bytes) -> bytes:
            tx = ctypes.create_string_buffer(data, len(data))
            rx = ctypes.create_string_buffer(len(data))
            fcntl.ioctl(f, SPI_IOC_MESSAGE_1, struct.pack("=QQIIHBBBBBB", ctypes.addressof(tx),
                                                          ctypes.addressof(rx), len(data), 0, 0, 8, 0, 0, 0, 0, 0))
            return rx.raw

        transfer(struct.pack(">I", r0 & ~LMX_MUXOUT_LD_SEL)[1:])
        words = {addr: int.from_bytes(transfer(bytes([0x80 | addr, 0, 0]))[1:], "big") for addr in (110, 111, 112)}
        transfer(struct.pack(">I", r0 | LMX_MUXOUT_LD_SEL)[1:])
    return {name: (words[addr] >> shift) & ((1 << bits) - 1) for name, (addr, shift, bits) in LMX_READBACK.items()}


def write_clocks(adc: str, dac: str, readback: bool = False, capctrl: dict[str, int | None] | None = None) -> None:
    """On the board: the clocks as PL.open writes them, then each "nosync" LMX2594 rewritten
    with the unsynchronized image (reset, all registers, VCO calibration) and no SYNC.

    "legacy" instead writes the clocks as PL.open did before rf-clock 1.3.0.4: the
    LMK04828, then each LMX2594 once, a "legacy" one with the unsynchronized image
    and no SYNC (never with the synchronized image first), a "sync" one as now.

    ``capctrl`` ({"adc": N, "dac": None}) then rewrites that LMX2594 (synchronized)
    with its VCO band forced to capcode N instead of calibrated."""
    from types import SimpleNamespace

    from xrfclk import xrfclk  # board-only  # pyright: ignore[reportMissingImports]

    from rf_clock.pl import board
    from rf_clock.pl.bringup import wait_for_boot

    # As PL.open: after a reboot PYNQ's boot.py loads the base overlay and its clocks
    # about a minute in, which would replace the clocks written here.
    print(f"boot state: {wait_for_boot()}", flush=True)
    image = board.clock_registers(f"LMX2594_{board.LMX_FREQ_MHZ}.txt")
    if {w >> 16: w for w in image if w >> 16 in SYNCED} != SYNCED:
        sys.exit("the packaged LMX2594 image is not the one this script expects")
    unsynced = [UNSYNCED.get(w >> 16, w) for w in image]
    if "legacy" in (adc, dac):
        if not {adc, dac} <= {"sync", "legacy"}:
            sys.exit("legacy combines only with sync")
        if not xrfclk.lmk_devices and not xrfclk.lmx_devices:
            xrfclk._find_devices()
        xrfclk._write_LMK_regs(board.clock_registers(f"LMK04828_{board.LMK_FREQ_MHZ}.txt"),
                               xrfclk.lmk_devices[0])
        for side, mode in (("adc", adc), ("dac", dac)):  # the order of xrfclk.set_ref_clks
            device = lmx_device(xrfclk.lmx_devices, LMX_NODES[side])
            if mode == "legacy":
                xrfclk._write_LMX_regs(unsynced, device)
            else:
                xrfclk._write_LMX_regs(image, device)
                board.sync_lmx_phase(SimpleNamespace(lmx_devices=[device]), image[-1])
    else:
        board.program_clocks(xrfclk)
        writes = {side: {"sync": 0, "nosync": 1, "nosync2": 2}[mode] for side, mode in (("adc", adc), ("dac", dac))}
        for n in range(max(writes.values())):
            if n:
                time.sleep(1.0)  # locked and unsynchronized before the next write
            for side, count in writes.items():
                if count > n:
                    xrfclk._write_LMX_regs(unsynced, lmx_device(xrfclk.lmx_devices, LMX_NODES[side]))
    for side, code in (capctrl or {}).items():
        if code is None:
            continue
        if {"adc": adc, "dac": dac}[side] != "sync":
            sys.exit(f"--{side}-capctrl needs --{side}-lmx sync")
        forced = [w | LMX_CAPCTRL_FORCE if w >> 16 == 8 else (w & ~0xFF) | code if w >> 16 == 19 else w
                  for w in image]
        device = lmx_device(xrfclk.lmx_devices, LMX_NODES[side])
        xrfclk._write_LMX_regs(forced, device)
        board.sync_lmx_phase(SimpleNamespace(lmx_devices=[device]), image[-1])
        print(f"{side.upper()} LMX2594 rewritten with VCO_CAPCTRL forced to {code}", flush=True)
    time.sleep(1.0)  # as PL.open: let the PLLs settle
    print(f"clocks written, ADC LMX2594 {adc}, DAC LMX2594 {dac}", flush=True)
    if readback:
        values = {side: lmx_readback(lmx_device(xrfclk.lmx_devices, LMX_NODES[side]),
                                     (image if mode == "sync" else unsynced)[-1])
                  for side, mode in (("adc", adc), ("dac", dac))}
        # RFSoC die temperatures (AMS, millidegrees C): the LMX2594 have no sensor, but they share the board.
        values["temp_mc"] = {p.name.split("_")[1]: int(p.read_text()) for p in
                             sorted(Path("/sys/bus/iio/devices/iio:device0").glob("in_temp*_input"))}
        print("LMX_READBACK " + json.dumps(values), flush=True)


def reset_ncos(rfdc, which: str, event: str) -> dict:
    """On the board: restart the NCO phase of the signal block(s) at a SYSREF edge or a tile event.

    ``sysref``: the mixer event source becomes SYSREF and ResetNCOPhase arms the
    reset; the tiles keep capturing SYSREF after MTS (SysRef_Enable defaults to
    1), so the 5 MHz SYSREF applies it within 200 ns, which holds whole NCO
    periods (760 at 3.8 GHz, 200 at 1.0 GHz).  ``tile``: the event source stays
    TILE, as PL.open leaves it, and UpdateEvent applies the reset at once, at
    whatever instant the software reaches it."""
    import xrfdc  # board-only  # pyright: ignore[reportMissingImports]

    done = {}
    for kind, tiles in (("dac", rfdc.dac_tiles), ("adc", rfdc.adc_tiles)):
        if which in (kind, "both"):
            block = tiles[SIGNAL_TILE].blocks[0]
            if event == "sysref":
                block.MixerSettings = {**block.MixerSettings, "EventSource": xrfdc.EVNT_SRC_SYSREF}
                block.ResetNCOPhase()
            else:
                block.MixerSettings = {**block.MixerSettings, "EventSource": xrfdc.EVNT_SRC_TILE}
                block.ResetNCOPhase()
                block.UpdateEvent(xrfdc.EVENT_MIXER)
            done[kind] = dict(block.MixerSettings)
    time.sleep(0.01)
    return done


def run_m3(nco_reset: str, nco_event: str, m3_argv: list[str]) -> int:
    """On the board: rf_clock.checks.m3_loopback with the NCO reset right after its PL.open."""
    from rf_clock.checks import m3_loopback

    open_pl = m3_loopback.open_pl

    def open_pl_and_reset(args, log):
        pl = open_pl(args, log)
        if pl is not None and nco_reset != "none":
            log.record["nco_reset"] = {"event": nco_event, **reset_ncos(pl.rfdc.driver, nco_reset, nco_event)}
            print(f"NCO phase reset on a {nco_event} event: {nco_reset}", flush=True)
        return pl

    m3_loopback.open_pl = open_pl_and_reset  # main() looks it up at call time
    sys.argv = ["m3_loopback", *m3_argv]
    return m3_loopback.main()


def lmx_device(lmx_devices: list[dict], node: str) -> dict:
    """The xrfclk device entry of the LMX2594 with this device-tree node name."""
    for dev in Path("/sys/bus/spi/devices").glob("*"):
        name = dev / "of_node" / "name"
        if name.exists() and name.read_bytes().rstrip(b"\0").decode() == node:
            path = Path("/dev") / next(dev.glob("spidev/*")).name
            (device,) = [d for d in lmx_devices if Path(d["spi_device"]) == path]
            return device
    sys.exit(f"no SPI device with the device-tree name {node}")


def reboot(ip: str, timeout: float = 600) -> None:
    """Reboot the board and wait until it is back (a new boot id) and settled."""
    before = ssh(ip, "cat /proc/sys/kernel/random/boot_id", timeout=15).stdout.strip()
    ssh(ip, "sudo -n systemctl reboot", timeout=15)
    deadline = time.monotonic() + timeout
    while True:
        r = ssh(ip, "cat /proc/sys/kernel/random/boot_id", timeout=15)
        if r.returncode == 0 and r.stdout.strip() not in ("", before):
            break
        if time.monotonic() > deadline:
            sys.exit(f"board {ip} did not come back within {timeout:.0f} s")
        time.sleep(5)
    time.sleep(10)


def ssh(ip: str, cmd: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    argv = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"xilinx@{ip}", cmd]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 255, "", "timeout")


def phase_shifts(records: list[dict]) -> list[float]:
    """Each run's time shift from the first run, from three times the carrier phase, its
    57.5 ps cycles counted with the envelope delay.  A later delay gives a negative phase."""
    runs = [r for r in records if r.get("phase3") is not None and r.get("delay_mean_ps") is not None]
    if not runs:
        return []
    d0, p0 = runs[0]["delay_mean_ps"], runs[0]["phase3"]
    shifts = []
    for r in runs:
        x = (r["phase3"] - p0 + math.pi) % (2 * math.pi) - math.pi
        m = round(((r["delay_mean_ps"] - d0) * K3 + x) / (2 * math.pi))
        shifts.append((2 * math.pi * m - x) / K3)
    return shifts


def nco_steps(records: list[dict]) -> list[int]:
    """Each run's carrier phase step from the first run in units of 120 degrees (0, 1, 2),
    after taking out the phase of its time shift."""
    runs = [r for r in records if r.get("phase1") is not None and r.get("delay_mean_ps") is not None]
    shifts = phase_shifts(runs)
    if not runs:
        return []
    return [round(((r["phase1"] - runs[0]["phase1"] + K3 / 3 * s) % (2 * math.pi)) / (2 * math.pi / 3)) % 3
            for r, s in zip(runs, shifts)]


def summarize(records: list[dict]) -> dict:
    means = [r["delay_mean_ps"] for r in records if r.get("delay_mean_ps") is not None]
    if not means:
        return {"runs": 0}
    shifts = phase_shifts(records)
    return {"runs": len(means), "mean_ps": statistics.fmean(means),
            "sdev_ps": statistics.stdev(means) if len(means) > 1 else 0.0,
            "pp_ps": max(means) - min(means), "sorted_ps": sorted(means),
            "within_run_sdev_ps": [r["delay_std_ps"] for r in records if r.get("delay_mean_ps") is not None],
            "phase_shift_ps": shifts, "nco_steps": nco_steps(records)}


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    for side in ("adc", "dac"):
        parser.add_argument(f"--{side}-lmx", choices=("sync", "nosync", "nosync2", "legacy"), default="sync",
                            help=f"{side.upper()} LMX2594 synchronized as PL.open does it, the pre-1.3.0.4 "
                                 "image without SYNC over the synchronized one (once or twice), or that "
                                 "image alone as PL.open wrote it before 1.3.0.4 (legacy)")
    parser.add_argument("--nco-reset", choices=("none", "dac", "adc", "both"), default="none",
                        help="restart the signal NCO phase after PL.open")
    parser.add_argument("--nco-event", choices=("sysref", "tile"), default="sysref",
                        help="event that applies --nco-reset: a SYSREF edge, or a tile event issued by software")
    parser.add_argument("--reboot", action="store_true", help="reboot the board before every run but the first")
    parser.add_argument("--readback", action="store_true",
                        help="read back each LMX2594's VCO calibration result after writing the clocks")
    for side in ("adc", "dac"):
        parser.add_argument(f"--{side}-capctrl", type=int, choices=range(184), metavar="0..183",
                            help=f"force the {side.upper()} LMX2594's VCO band (VCO_CAPCTRL) instead of calibrating it")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--captures", type=int, default=30, help="M3 windows per run")
    parser.add_argument("--board", default=BOARD)
    parser.add_argument("--m3-args", default="", help="extra rf_clock.checks.m3_loopback options")
    parser.add_argument("--write-clocks", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-m3", action="store_true", help=argparse.SUPPRESS)
    args, m3_argv = parser.parse_known_args()
    if args.write_clocks:
        write_clocks(args.adc_lmx, args.dac_lmx, args.readback, {"adc": args.adc_capctrl, "dac": args.dac_capctrl})
        return
    if args.run_m3:
        sys.exit(run_m3(args.nco_reset, args.nco_event, m3_argv))
    if m3_argv:
        parser.error(f"unrecognized arguments: {' '.join(m3_argv)}")
    ip = args.board
    if ssh(ip, BUSY).stdout.strip() != "0":
        sys.exit(f"board {ip} is running a PL program; stop it first")
    if subprocess.run(["scp", "-q", __file__, f"xilinx@{ip}:{REMOTE}"]).returncode:
        sys.exit(f"cannot copy this script to {ip}")
    out = ROOT / "results" / f"m3-restarts-{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True)
    print(f"results in {out}; ADC LMX2594 {args.adc_lmx}"
          + (f" (VCO_CAPCTRL forced to {args.adc_capctrl})" if args.adc_capctrl is not None else "")
          + f", DAC LMX2594 {args.dac_lmx}"
          + (f" (VCO_CAPCTRL forced to {args.dac_capctrl})" if args.dac_capctrl is not None else "") + ", "
          f"NCO reset {args.nco_reset} ({args.nco_event}), reboots {args.reboot}", flush=True)
    records: list[dict] = []
    try:
        for run in range(1, args.runs + 1):
            tag = f"run{run:03d}"
            if args.reboot and run > 1:
                reboot(ip)
                if subprocess.run(["scp", "-q", __file__, f"xilinx@{ip}:{REMOTE}"]).returncode:
                    sys.exit(f"cannot copy this script to {ip} after the reboot")
            rec: dict = {"run": run, "start": datetime.now().isoformat(timespec="seconds"), "board": ip,
                         "adc_lmx": args.adc_lmx, "dac_lmx": args.dac_lmx, "nco_reset": args.nco_reset,
                         "nco_event": args.nco_event, "adc_capctrl": args.adc_capctrl,
                         "dac_capctrl": args.dac_capctrl,
                         "rebooted": args.reboot and run > 1,
                         "captures": args.captures}
            r = ssh(ip, f"cd ~/RF-clock && sudo -n {ENV} .venv/bin/python {REMOTE} --write-clocks "
                        f"--adc-lmx {args.adc_lmx} --dac-lmx {args.dac_lmx}" + (" --readback" if args.readback else "")
                        + "".join(f" --{side}-capctrl {code}" for side, code in
                                  (("adc", args.adc_capctrl), ("dac", args.dac_capctrl)) if code is not None),
                    timeout=420)  # waits for the boot to finish (up to 300 s) after a reboot
            text = r.stdout + r.stderr
            if m := READBACK_LINE.search(text):
                rec["lmx_readback"] = json.loads(m.group(1))
            if r.returncode == 0:
                r = ssh(ip, f"cd ~/RF-clock && sudo -n {ENV} .venv/bin/python {REMOTE} --run-m3 "
                            f"--nco-reset {args.nco_reset} --nco-event {args.nco_event} {args.captures} "
                            f"--no-clocks --log {M3_LOG} {args.m3_args}",
                        timeout=180 + 3 * args.captures)
                text += r.stdout + r.stderr
                m3 = json.loads(ssh(ip, f"tail -n 1 {M3_LOG}").stdout or "{}")
                rec["m3"] = {k: m3.get(k) for k in ("pass", "rf_clock_version", "clocks_written", "mts",
                                                    "delays_samples", "delay_std_ps", "min_psl_db",
                                                    "invalid_windows", "timing_mismatches", "nco_reset")}
                if m3.get("delay_mean_ns") is not None:
                    rec["delay_mean_ps"] = m3["delay_mean_ns"] * 1000
                    rec["delay_std_ps"] = m3["delay_std_ps"]
                rec["phases"] = [float(m.group(5)) for m in map(CAPTURE_LINE.match, text.splitlines()) if m]
                if rec["phases"]:
                    rec["phase3"] = cmath.phase(sum(cmath.exp(3j * p) for p in rec["phases"]))
                    rec["phase1"] = cmath.phase(sum(cmath.exp(1j * p) for p in rec["phases"]))
            else:
                rec["error"] = "clock write failed"
            (out / f"{tag}.log").write_text(text, newline="\n")
            records.append(rec)
            with open(out / "runs.jsonl", "a", newline="\n") as f:
                f.write(json.dumps(rec) + "\n")
            mean = rec.get("delay_mean_ps")
            shift, steps = phase_shifts(records), nco_steps(records)
            print(f"{tag} {rec['start']}: " + (f"delay {mean:.1f} ps, sd {rec['delay_std_ps']:.1f} ps, "
                                              f"PSL >= {rec['m3']['min_psl_db']:.1f} dB, pass {rec['m3']['pass']}"
                                              + (f", phase shift from run 1 {shift[-1]:+.2f} ps, "
                                                 f"120-degree step k {steps[-1]}" if shift else "")
                                              if mean is not None else f"no delay ({rec.get('error', 'see log')})")
                  + "".join(f"; {side.upper()} LMX VCO {v['vco_sel']}, CAPCTRL {v['vco_capctrl']}, "
                            f"DACISET {v['vco_daciset']}, LD {v['ld_vtune']}"
                            for side, v in rec.get("lmx_readback", {}).items() if side in ("adc", "dac"))
                  + "".join(f"; die {max(t.values()) / 1000:.1f} C" for t in [rec.get("lmx_readback", {}).get("temp_mc")]
                            if t),
                  flush=True)
    finally:
        summary = summarize(records)
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
    if summary["runs"]:
        shifts = sorted(summary["phase_shift_ps"])
        print(f"{summary['runs']} runs: mean {summary['mean_ps']:.1f} ps, run-to-run sd {summary['sdev_ps']:.1f} ps, "
              f"p-p {summary['pp_ps']:.1f} ps; sorted relative to the lowest: "
              + " ".join(f"{v - summary['sorted_ps'][0]:.0f}" for v in summary["sorted_ps"])
              + "; phase shifts relative to the lowest: " + " ".join(f"{v - shifts[0]:.1f}" for v in shifts)
              + "; 120-degree steps k=0/1/2: " + "/".join(str(summary["nco_steps"].count(k)) for k in range(3)))


if __name__ == "__main__":
    main()
