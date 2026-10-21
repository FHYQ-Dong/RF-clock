"""One board's side of the two-way time transfer: the PL loop, and the command that runs it.

    run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
    $run -m rf_clock.twtt.node A --seconds 600                       # first board
    $run -m rf_clock.twtt.node B --peer 192.168.2.99 --seconds 600   # second board
    $run -m rf_clock.twtt.node AB --seconds 60    # one board through its own loopback cable

After ``PL.open()`` (with the board config) the PL loop runs in this process and the
link (``rf_clock.twtt.link``) in a second one, which writes the log
(``twtt_<node>.jsonl`` unless ``--log``).  Every second, right after the PPS edge,
the loop goes through the slots of ``rf_clock.twtt.timing.plan``: it arms the burst
and/or the RX window of a slot (the window from ``PRE_TICKS`` before it), waits for
them and takes the samples; after the last slot it correlates each window with the
PRN reference and hands the second's record to the link.  M4 wiring: A's DAC_A ->
attenuator -> B's ADC_B and B's DAC_A -> attenuator -> A's ADC_B, both boards on one
FS725.

The two boards' records are matched by the system time of their PPS edges, so A and
B need NTP-synchronized clocks (docs/ABI.md 9.1): the command refuses to start
without (``--no-ntp-check`` overrides).  SIGTERM or Ctrl-C stops it after the second
in progress.
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from multiprocessing import Pipe, Process
from typing import Any, Callable

from .link import DEFAULT_PORT, run_link
from .timing import PRE_TICKS, SLOTS, plan

AMPLITUDE = 16000.0  # as M3
MARGIN_BEATS = 1250  # RX window beyond the burst, 10 us, as M3
WAIT_S = 1.0  # longest wait for a slot, from arming it


def ntp_synchronized() -> bool:
    done = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                          capture_output=True, text=True)
    return done.stdout.strip() == "yes"


def run_node(pl: Any, node: str, seconds: int, emit: Callable[[dict], None], *,
             amplitude: float = AMPLITUDE, say: Callable[[str], None] = print) -> int:
    """The PL loop: one record per second to ``emit``, for ``seconds`` seconds (0:
    until interrupted).  Returns the number of seconds done."""
    import numpy as np

    from rf_clock.correlator import estimate_delay
    from rf_clock.pl import WaveformGenerator
    from rf_clock.pl.client import RXWindowInvalid, TargetMissed, WaitTimeout

    gen = WaveformGenerator(amplitude=amplitude)
    ref = gen.generate().astype(np.float64)
    beats = gen.generate_beats()
    pl.tx_buffer.write_beats(beats)
    rx_beats = len(beats) + PRE_TICKS + MARGIN_BEATS
    slots = plan(node)
    done = 0
    while not seconds or done < seconds:
        count, pps_tick = pl.ctrl.wait_next_pps()
        record: dict = {"node": node, "count": count, "pps_tick": pps_tick, "t_sys": time.time(), "slots": {}}
        windows = []
        for slot, offset, tx, rx in slots:
            entry = record["slots"][slot] = {}
            target = pps_tick + offset
            try:
                if rx:
                    pl.rx_dma.prepare(rx_beats)
                    pl.ctrl.arm_rx(target - PRE_TICKS, rx_beats)
                if tx:
                    pl.ctrl.arm_tx(target, len(beats))
                    entry["tx"] = pl.ctrl.wait_tx(timeout=WAIT_S)
                if rx:
                    window = pl.ctrl.wait_rx(timeout=WAIT_S)
                    # A copy: the next window reuses the DMA buffer.
                    windows.append((entry, window.actual, np.array(pl.rx_dma.wait(window.beats).samples)))
            except (RXWindowInvalid, TargetMissed, WaitTimeout) as exc:
                entry.clear()
                entry["error"] = str(exc)
                pl.ctrl.cancel_tx()  # whatever of this slot is still armed
                pl.ctrl.cancel_rx()
        for entry, actual, samples in windows:
            peak = estimate_delay(samples, ref)
            entry["rx"] = {"tick": actual, "peak": float(peak.index), "phase": float(peak.phase),
                           "psl_db": peak.peak_to_sidelobe_db}
        emit(record)
        say(describe(record))
        done += 1
    return done


def describe(record: dict) -> str:
    """One line per second: what each slot did."""
    parts = []
    for slot, entry in record["slots"].items():
        words = [slot]
        if "error" in entry:
            words.append(f"error: {entry['error']}")
        if "tx" in entry:
            words.append("tx")
        if "rx" in entry:
            words.append(f"rx peak {entry['rx']['peak']:.3f} samples, PSL {entry['rx']['psl_db']:.1f} dB")
        parts.append(" ".join(words))
    return f"{record['count']}: " + "; ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("node", choices=("A", "B", "AB"), help="AB: one board through its own loopback cable")
    parser.add_argument("--peer", help="A's address (node B)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--seconds", type=int, default=0, help="seconds to run (default: until stopped)")
    parser.add_argument("--log", help="JSON-lines log (default twtt_<node>.jsonl)")
    parser.add_argument("--amplitude", type=float, default=AMPLITUDE, help="PRN amplitude")
    parser.add_argument("--no-ntp-check", action="store_true", help="start without an NTP-synchronized clock")
    args = parser.parse_args()
    if args.node == "B" and not args.peer:
        parser.error("node B needs --peer, A's address")
    if args.node != "AB" and not args.no_ntp_check and not ntp_synchronized():
        print("the system clock is not NTP-synchronized (timedatectl), so the records could not be paired")
        return 1

    def stop(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    log_path = args.log or f"twtt_{args.node}.jsonl"
    ours, theirs = Pipe()
    # The link process starts before PL.open, so that it holds none of the hardware.
    link = Process(target=run_link, args=(theirs, args.node, args.peer, args.port, log_path), daemon=True)
    link.start()
    theirs.close()  # so that sending fails at once if the link process ends

    def emit(record: dict) -> None:
        try:
            ours.send({"type": "record", **record})
        except OSError as exc:
            raise RuntimeError(f"the link process ended (exit code {link.exitcode}); see its output") from exc

    from rf_clock.checks.board_common import mark
    from rf_clock.pl import PL
    from rf_clock.pl.overlay import package_version

    try:
        pl = PL.open(progress=mark)
        mts = pl.rfdc.mts
        ours.send({"type": "info", "serial": pl.board.serial,
                   "label": pl.board.config.label if pl.board.config else None,
                   "rf_clock": package_version(), "bands": {s: v.band for s, v in pl.board.lmx_vco.items()},
                   "t1_code": {"dac": mts.dac.dtc.get("t1_code"), "adc": mts.adc.dtc.get("t1_code")},
                   "slots": SLOTS, "pre_ticks": PRE_TICKS, "amplitude": args.amplitude})
        mark(f"twtt node {args.node}: log {log_path}")
        done = run_node(pl, args.node, args.seconds, emit, amplitude=args.amplitude)
        print(f"{done} seconds")
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if link.is_alive():
            ours.send(None)
        link.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
