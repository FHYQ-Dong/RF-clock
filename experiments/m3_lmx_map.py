"""M3 sub-experiment: which LMX2594 clocks which RFDC tiles?

Problem.  The PYNQ device tree names the LMX2594 on spi0.1 "lmxdac" and the one
on spi0.2 "lmxadc", and the first M3 restart series took the names at face
value; then a series meant for the DAC's chip moved the ADC's MTS capture codes.
This powers each LMX2594 down in turn (R0 POWERDOWN) after a normal PL.open and
reads the PLL lock status of DAC and ADC tiles 0 and 2: the tiles that lose lock
are the ones that chip clocks.  A PL.open before each step and at the end starts
every step from locked clocks and leaves the board usable.

Outcome (results/lmx-sync-loopback-20261019 section 2): spi0.1 ("lmxdac")
clocks the ADC tiles and spi0.2 ("lmxadc") the DAC tiles; the names are swapped,
as the schematic (docs/rfsoc4x2_sch.pdf) and the BSP's MIO table confirm.

Run on RF-clock-PC as the normal user, with the board idle::

    .venv/bin/python experiments/m3_lmx_map.py

The board output goes to results/m3-lmx-map-<time>/output.log.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import m3_common

POWERDOWN = 1 << 0  # LMX2594 R0
LOCK = {1: "unlocked", 2: "locked"}


def spidev(node: str) -> Path:
    for dev in Path("/sys/bus/spi/devices").glob("*"):
        name = dev / "of_node" / "name"
        if name.exists() and name.read_bytes().rstrip(b"\0").decode() == node:
            return Path("/dev") / next(dev.glob("spidev/*")).name
    sys.exit(f"no SPI device {node}")


def on_board() -> None:
    from rf_clock.pl import board
    from rf_clock.pl.board import PL
    from rf_clock.pl.bringup import wait_for_boot

    def locks(rfdc) -> str:
        return ", ".join(f"{kind} {t} {LOCK.get(tiles[t].PLLLockStatus, tiles[t].PLLLockStatus)}"
                         for kind, tiles in (("DAC", rfdc.dac_tiles), ("ADC", rfdc.adc_tiles)) for t in (0, 2))

    print(f"boot state: {wait_for_boot()}", flush=True)
    r0 = board.clock_registers(f"LMX2594_{board.LMX_FREQ_MHZ}.txt")[-1]
    for node in ("lmxdac", "lmxadc"):  # by device-tree name, which is what is being checked
        rfdc = PL.open().rfdc.driver
        print(f"after PL.open: {locks(rfdc)}", flush=True)
        path = spidev(node)
        with open(path, "rb+", buffering=0) as f:
            f.write(struct.pack(">I", (r0 & ~m3_common.LMX_FCAL_EN) | POWERDOWN)[1:])
        time.sleep(0.5)
        print(f"{node} ({path.name}) powered down: {locks(rfdc)}", flush=True)
    PL.open()
    print("clocks restored by PL.open", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("--board", default=m3_common.BOARD)
    parser.add_argument("--on-board", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.on_board:
        on_board()
        return
    m3_common.check_idle(args.board)
    m3_common.copy_to_board(args.board, Path(m3_common.__file__), Path(__file__))
    r = m3_common.ssh(args.board, m3_common.board_python(Path(__file__).name, "--on-board"), timeout=600)
    out = m3_common.results_dir("m3-lmx-map")
    (out / "output.log").write_text(r.stdout + r.stderr, newline="\n")
    print(r.stdout + r.stderr, end="")
    print(f"saved in {out}")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
