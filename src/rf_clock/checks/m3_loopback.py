"""M3 on the board: wired DAC -> ADC loopback, timed RX capture and correlation.

Wiring: DAC tile 2 output (vout20) -> attenuator -> ADC tile 2 input
(vin2_01).  Put the 5.8 GHz band-pass filter in the loop if it is available:
without it the DAC's 3.8 GHz component also aliases onto the ADC's 1.0 GHz
NCO and adds to the 5.8 GHz image (same delay, different constant phase).

Modes (all start with the M2 bring-up: clocks, MTS, PPS lock):

default        Every second: TX the PRN+RRC burst at next PPS + offset and
               open an RX window at the same tick, ``--margin-beats`` longer
               than the burst.  Correlate the capture with the TX reference;
               the peak is the loopback delay (DAC + cable + ADC) in samples
               of 1.25 ns.  PASS: every window valid, TX_ACTUAL and RX_ACTUAL
               equal the target, peak-to-sidelobe >= --min-psl-db, and the
               delay standard deviation <= --max-std-ps if given.
--lane-check   TX one single-lane impulse per lane (lane k in beat 256*(k+1))
               and locate them in the averaged |RX|.  If TX_LAYOUT/RX_LAYOUT
               match the hardware lane order, every impulse arrives with the
               same delay; a wrong order shows as a lane-dependent delay
               (packing TX planar gave D - ceil(k/2), which is how the
               interleaved DAC format was found).
--stress       RX-only windows of --stress-beats while a background thread
               copies memory, to test S2MM against DDR contention.  PASS: no
               invalid window.

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.m3_loopback 20
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import numpy as np

from .board_common import RunLog, add_common_args, add_rfdc_args, mark, open_pl
from rf_clock.correlator import estimate_delay, find_peak
from rf_clock.pl import PL, TX_LAYOUT, WaveformGenerator, abi
from rf_clock.pl.beats import BeatPacker
from rf_clock.pl.client import RXWindowInvalid, TargetMissed

PPS_PERIOD = 100_000_000
SAMPLES_PER_BEAT = 8
SAMPLE_NS = 1.25


def next_slot(pl: PL, offset_ticks: int) -> int:
    """Tick offset_ticks after the coming PPS (waits for a PPS edge first)."""
    _, pps = pl.ctrl.wait_next_pps()
    return pps + PPS_PERIOD + offset_ticks


def tx_and_rx(pl: PL, target: int, tx_beats: int, rx_beats: int):
    """Burst and RX window from the same tick: returns (TX_ACTUAL, RXWindow, RXData)."""
    pl.rx_dma.prepare(rx_beats)  # S2MM before the RX doorbell
    pl.ctrl.arm_tx(target, tx_beats)
    pl.ctrl.arm_rx(target, rx_beats)
    tx_actual = pl.ctrl.wait_tx(timeout=2.5)
    window = pl.ctrl.wait_rx(timeout=2.5)
    return tx_actual, window, pl.rx_dma.wait(window.beats)


def run_delay(pl: PL, args, log: RunLog) -> bool:
    gen = WaveformGenerator(amplitude=args.amplitude)
    ref = gen.generate().astype(np.float64)
    beats = gen.generate_beats()
    pl.tx_buffer.write_beats(beats)
    rx_beats = len(beats) + args.margin_beats
    log.record["waveform"] = {"config_id": gen.config_id, "chips": gen.chip_count,
                              "amplitude": gen.amplitude, "beats": len(beats), "rx_beats": rx_beats}
    print(f"{'n':>3} {'delay/samples':>13} {'delay/ns':>9} {'PSL/dB':>7} {'phase':>6} "
          f"{'|rx| peak':>9} {'rx rms':>7}")
    delays, psls, invalid, timing_bad = [], [], 0, 0
    last = None
    for n in range(args.captures):
        target = next_slot(pl, args.offset_ticks)
        try:
            tx_actual, window, data = tx_and_rx(pl, target, len(beats), rx_beats)
        except RXWindowInvalid as exc:
            invalid += 1
            print(f"{n:3d} window invalid: flags {exc.flags!r}")
            continue
        except TargetMissed as exc:
            timing_bad += 1
            print(f"{n:3d} {exc}")
            continue
        samples = data.samples
        peak = estimate_delay(samples, ref)
        # RX opens at the TX tick, so the peak index is the loopback delay.
        delay = peak.index + (window.actual - tx_actual) * SAMPLES_PER_BEAT
        delays.append(delay)
        psls.append(peak.peak_to_sidelobe_db)
        last = samples
        print(f"{n:3d} {delay:13.3f} {delay * SAMPLE_NS:9.3f} {peak.peak_to_sidelobe_db:7.1f} "
              f"{peak.phase:6.2f} {np.abs(samples).max():9.0f} {np.sqrt(np.mean(np.abs(samples) ** 2)):7.1f}",
              flush=True)
    if args.save and last is not None:
        np.savez(args.save, rx=last, ref=ref, offset_ticks=args.offset_ticks)
        print(f"last capture saved to {args.save}")

    d = np.array(delays)
    std_ps = float(d.std(ddof=1) * SAMPLE_NS * 1000) if d.size > 1 else float("nan")
    passed = (invalid == 0 and timing_bad == 0 and d.size == args.captures
              and min(psls, default=0.0) >= args.min_psl_db
              and (args.max_std_ps is None or std_ps <= args.max_std_ps))
    log.record.update({"mode": "delay", "captures": args.captures, "invalid_windows": invalid,
                       "timing_mismatches": timing_bad, "delays_samples": [round(x, 4) for x in delays],
                       "delay_mean_ns": float(d.mean() * SAMPLE_NS) if d.size else None,
                       "delay_std_ps": std_ps, "min_psl_db": min(psls, default=None)})
    print("summary:")
    if d.size:
        print(f"  loopback delay       {d.mean():.4f} samples = {d.mean() * SAMPLE_NS:.4f} ns")
        print(f"  standard deviation   {std_ps:.1f} ps over {d.size} captures")
        print(f"  min PSL              {min(psls):.1f} dB (need >= {args.min_psl_db})")
    print(f"  invalid windows      {invalid}")
    print(f"  TX/RX actual != target  {timing_bad}")
    return passed


LANE_SPACING = 256  # beats between lane-check impulses: 2048 samples, 2.56 us


def impulse_pattern(amplitude: int, spacing: int = LANE_SPACING, lanes: int = SAMPLES_PER_BEAT):
    """I samples with one impulse per lane (lane k in beat spacing*(k+1)),
    and the sample index of each impulse."""
    positions = [spacing * (k + 1) * lanes + k for k in range(lanes)]
    samples = np.zeros(spacing * (lanes + 1) * lanes, dtype=np.int64)
    samples[positions] = int(amplitude)
    return samples, positions


def locate_impulses(magnitude, positions, spacing: int = LANE_SPACING, lanes: int = SAMPLES_PER_BEAT):
    """RX position of each impulse, searched from 64 samples before its TX
    position to just short of the next one (delays up to about 2.4 us are
    unambiguous), refined with a parabola."""
    reach = spacing * lanes - 64
    found = []
    for tx in positions:
        lo = tx - 64
        found.append(lo + find_peak(magnitude[lo: tx + reach], guard=4).index)
    return found


def run_lane_check(pl: PL, args, log: RunLog) -> bool:
    samples, tx_positions = impulse_pattern(args.impulse)
    beats = BeatPacker(TX_LAYOUT).pack_samples([(int(v), 0) for v in samples])
    pl.tx_buffer.write_beats(beats)
    rx_beats = len(beats) + args.margin_beats
    magnitude = None
    for n in range(args.captures):
        target = next_slot(pl, args.offset_ticks)
        try:
            _, _, data = tx_and_rx(pl, target, len(beats), rx_beats)
        except TargetMissed as exc:
            print(f"capture {n}: {exc}")
            return False
        rx = np.abs(data.samples)
        magnitude = rx if magnitude is None else magnitude + rx
    delays = []
    print(f"{'lane':>4} {'TX sample':>9} {'RX sample':>10} {'RX - TX':>8}")
    for k, (tx, rx_pos) in enumerate(zip(tx_positions, locate_impulses(magnitude, tx_positions))):
        delays.append(rx_pos - tx)
        print(f"{k:4d} {tx:9d} {rx_pos:10.2f} {rx_pos - tx:8.2f}")
    spread = max(delays) - min(delays)
    passed = spread < 0.5
    log.record.update({"mode": "lane_check", "captures": args.captures, "impulse": args.impulse,
                       "lane_delays_samples": [round(x, 3) for x in delays], "spread_samples": spread})
    print("summary:")
    print(f"  per-lane delay spread {spread:.2f} samples "
          f"({'consistent with BeatLayout' if passed else 'lane order differs from BeatLayout'})")
    return passed


def run_stress(pl: PL, args, log: RunLog) -> bool:
    stop = threading.Event()
    copied = [0]

    def hog() -> None:
        src = np.ones(64 << 20, dtype=np.uint8)
        dst = np.empty_like(src)
        while not stop.is_set():
            np.copyto(dst, src)
            copied[0] += src.nbytes

    thread = threading.Thread(target=hog, daemon=True)
    t0 = time.monotonic()
    thread.start()
    invalid, flags_seen = 0, abi.RxFlag(0)
    try:
        for n in range(args.captures):
            try:
                target = next_slot(pl, args.offset_ticks)
                pl.rx_dma.prepare(args.stress_beats)
                pl.ctrl.arm_rx(target, args.stress_beats)
                window = pl.ctrl.wait_rx(timeout=2.5)
                pl.rx_dma.wait(window.beats)
                print(f"{n:3d} window ok ({args.stress_beats} beats)", flush=True)
            except TargetMissed as exc:
                invalid += 1
                print(f"{n:3d} {exc}", flush=True)
            except RXWindowInvalid as exc:
                invalid += 1
                flags_seen |= exc.flags
                print(f"{n:3d} window invalid: flags {exc.flags!r}", flush=True)
    finally:
        stop.set()
        thread.join()
    gbps = copied[0] * 2 / (time.monotonic() - t0) / 1e9  # read + write traffic
    passed = invalid == 0
    log.record.update({"mode": "stress", "captures": args.captures, "stress_beats": args.stress_beats,
                       "invalid_windows": invalid, "flags_seen": int(flags_seen), "cpu_copy_gbps": gbps})
    print("summary:")
    print(f"  window size          {args.stress_beats} beats = {args.stress_beats * 32 / 1e6:.1f} MB "
          f"({args.stress_beats * 10 / 1e6:.2f} ms at 3.2 GB/s)")
    print(f"  ARM memory traffic   {gbps:.2f} GB/s during the test")
    print(f"  invalid windows      {invalid} of {args.captures} (flags {flags_seen!r})")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    add_common_args(parser)
    add_rfdc_args(parser)
    parser.add_argument("captures", nargs="?", type=int, default=20)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--lane-check", action="store_true", help="locate single-lane impulses")
    mode.add_argument("--stress", action="store_true", help="long RX windows under DDR load")
    parser.add_argument("--offset-ticks", type=int, default=100_000,
                        help="slot start after the PPS, in 10-ns ticks (default 1 ms)")
    parser.add_argument("--amplitude", type=float, default=16000.0,
                        help="PRN waveform amplitude (16000: about 1 ps per run in the wired loopback)")
    parser.add_argument("--impulse", type=int, default=16384, help="lane-check impulse amplitude")
    parser.add_argument("--margin-beats", type=int, default=1250,
                        help="RX window beyond the burst, in beats (default 10 us)")
    parser.add_argument("--stress-beats", type=int, default=200_000, help="stress window (default 2 ms)")
    parser.add_argument("--min-psl-db", type=float, default=10.0)
    parser.add_argument("--max-std-ps", type=float, default=None)
    parser.add_argument("--save", default=None, help="save the last delay capture to this .npz")
    parser.add_argument("--log", default="m3_runs.jsonl", help="append one JSON record per run")
    args = parser.parse_args()

    log = RunLog(args.log, bitstream=args.bitstream, offset_ticks=args.offset_ticks)
    pl = open_pl(args, log)
    if pl is None:
        return 1
    mark("M3 " + ("lane check" if args.lane_check else "stress" if args.stress else "loopback delay"))
    if args.lane_check:
        passed = run_lane_check(pl, args, log)
    elif args.stress:
        passed = run_stress(pl, args, log)
    else:
        passed = run_delay(pl, args, log)
    passed = passed and pl.rfdc.mts.dac.ok and pl.rfdc.mts.adc.ok
    print("M3:", "PASS" if passed else "FAIL")
    log.write(passed)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
