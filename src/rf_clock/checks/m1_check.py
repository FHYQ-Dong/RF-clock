"""M1 on the board: raw_tick against the FS725 1PPS.

Bring-up (overlay -> FS725 clocks -> pl_resetn0 -> MMCM lock -> P2 checks ->
PPS lock; no RFDC), then one line per second.  PASS when every sampled PPS
period is exactly 100,000,000 ticks, PPS_JITTER_CNT and the fault count do
not move, and pps_locked stays set.  P1 checks every period in hardware, so
the jitter and fault counters cover the periods between samples too.

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.m1_check 600 --no-clocks

Needs a PL ABI 1.1 bitstream (PPS_CONFIG / PPS_INPUTS); add --no-version-check
for one before 1.2.
"""

from __future__ import annotations

import argparse
import sys
import time

from .board_common import add_common_args, mark
from rf_clock.pl import PL, abi
from rf_clock.pl.bringup import BringupError, mmio_from_overlay, pynq_pl_reset
from rf_clock.pl.client import PLClient

R = abi.ABI.REG


def wait_mmcm(gpio, limit: float) -> bool:
    """Pulse pl_resetn0 every 2 s until the MMCM locks (diagnostic)."""
    t_ref = time.monotonic()
    while time.monotonic() < t_ref + limit:
        pynq_pl_reset()
        time.sleep(0.2)
        locked = gpio.read(0) & 1
        mark(f"t+{time.monotonic() - t_ref:6.1f} s: MMCM {'LOCKED' if locked else 'unlocked'}")
        if locked:
            time.sleep(1.0)
            mark(f"1 s later: MMCM {'still locked' if gpio.read(0) & 1 else 'LOST LOCK'}")
            return bool(gpio.read(0) & 1)
        time.sleep(1.8)
    return False


def recover_client(exc: BringupError, args) -> PLClient | None:
    """After a failed bring-up, keep resetting the MMCM and build a client."""
    overlay = getattr(exc, "overlay", None)
    if overlay is None:
        return None
    gpio = mmio_from_overlay(overlay, "clk_status_gpio")
    print(f"clk_status_gpio = 0x{gpio.read(0):x}")
    if not gpio.read(0) & 1 and not wait_mmcm(gpio, args.lock_wait):
        mark(f"MMCM did not lock within {args.lock_wait:g} s")
        return None
    client = PLClient(mmio_from_overlay(overlay, "p_top_0"), gpio)
    client.validate()
    client.set_pps_config(source_trig=args.pps_trig, invert=args.pps_invert)
    return client


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    add_common_args(parser)
    parser.add_argument("seconds", nargs="?", type=int, default=60)
    parser.add_argument("--lock-wait", type=float, default=180.0,
                        help="if bring-up leaves the MMCM unlocked, keep resetting it this long")
    args = parser.parse_args()

    try:
        pl = PL.open(args.bitstream, write_clocks=not args.no_clocks, mts=False,
                     pps_source_trig=args.pps_trig, pps_invert=args.pps_invert,
                     check_version=not args.no_version_check, progress=mark)
        client = pl.ctrl
        res = pl.bringup
        print(f"bringup: OK  ABI 0x{res.info.version:08x}  initial FAULT_BITS 0x{res.initial_fault_bits:x} "
              f"FAULT_INFO 0x{res.initial_fault_info:08x}")
    except BringupError as exc:
        print("bringup: FAILED:", exc)
        client = recover_client(exc, args)
        if client is None:
            return 1
    print(f"PPS_CONFIG = 0x{client.read32(R.PPS_CONFIG):x} (bit0 trig copy, bit1 inverted)")

    print(f"{'t/s':>5} {'PPS_COUNT':>9} {'LAST_PERIOD':>11} {'JITTER':>6} {'pps_lk':>6} "
          f"{'FAULT_BITS':>10} {'FAULT_CNT':>9} {'comp lvl/edges':>14} {'trig lvl/edges':>14} "
          f"{'tick/s (ARM raw)':>16}")
    periods: dict[int, int] = {}
    first = None
    edges = {"comp": 0, "trig": 0}
    prev_pin = None
    t0 = time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    last_count = None
    while True:
        # Raw oscillator time: CLOCK_MONOTONIC is slewed by NTP (up to 500 ppm).
        now = time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
        tick = client.read64(R.TICK_LO)
        count, _ = client.latest_pps()
        period = client.read32(R.PPS_LAST_PERIOD)
        jitter = client.read32(R.PPS_JITTER_CNT)
        st = client.status()
        fbits = client.read32(R.FAULT_BITS)
        fcnt = client.read32(R.FAULT_INFO) >> abi.ABI.FAULT_INFO_COUNT_LSB
        pin = client.pps_inputs()
        if first is None:
            first = (now, tick, count, jitter, fcnt)
        if prev_pin is not None:  # 8-bit counters: accumulate per-sample deltas
            edges["comp"] += (pin.comp_edges - prev_pin.comp_edges) % 256
            edges["trig"] += (pin.trig_edges - prev_pin.trig_edges) % 256
        prev_pin = pin
        rate = (tick - first[1]) / (now - first[0]) if now > first[0] else float("nan")
        if count != last_count and last_count is not None:
            periods[period] = periods.get(period, 0) + 1
        last_count = count
        print(f"{now - t0:5.0f} {count:9d} {period:11d} {jitter:6d} {int(st.pps_locked):6d} "
              f"0x{fbits:08x} {fcnt:9d} {int(pin.comp_level):>6}/{pin.comp_edges:<7d} "
              f"{int(pin.trig_level):>6}/{pin.trig_edges:<7d} {rate:16.1f}", flush=True)
        if now - t0 >= args.seconds:
            break
        time.sleep(1.0)

    print("summary:")
    print(f"  PPS edges timed       {count - first[2]}")
    print(f"  sampled periods       {dict(sorted(periods.items()))}")
    print(f"  jitter count delta    {jitter - first[3]}")
    print(f"  fault count delta     {fcnt - first[4]}")
    print(f"  pps_locked at end     {st.pps_locked}")
    print(f"  comp: idle level {int(pin.comp_level)}, rising edges {edges['comp']}")
    print(f"  trig: idle level {int(pin.trig_level)}, rising edges {edges['trig']}")
    ok = (set(periods) == {100_000_000} and jitter == first[3] and fcnt == first[4] and st.pps_locked)
    print("M1 tick/PPS:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
