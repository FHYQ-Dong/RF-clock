"""One node of the rf-clock 2 link on the board: bursts from chips, the tracker, the
measurement queue, and theta every second.

Run on both boards as root, ``A`` on one and ``B`` on the other, in either order (each node
searches until the other's bursts come):

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.examples.pl.04_link_node A

Wiring: this board's DAC_A -> 5800 MHz band-pass -> attenuator -> the peer's ADC_B, and back
the same way; each board on its clock (one shared reference, or each its own FS725).  The PL
does the per-burst work (docs/ABI.md 4.8-4.12): the slot engine sends a burst made from
chips through the RRC filter in each of this node's slots and opens a window for the
peer's; the correlator searches whole windows until the ARM accepts a peak, then tracks it
in every window; each burst carries this node's last measurement in its packet; the queue
gets this node's measurement of each window and the peer's packet.  The ARM only accepts
the first peak, at the lag the expected one-way delay gives, and forms theta from the
queue (``rf_clock.twtt.pairs``).  ``rf_clock.checks.m4c_link`` is the full node: logs,
acquisition with two clocks, the FS725, steering, the output PPS.
"""

from __future__ import annotations

import argparse
import time

from rf_clock.pl import PL
from rf_clock.pl.abi import ABI
from rf_clock.pl.slots import rx_offset_for
from rf_clock.pl.tracking import quality_offset
from rf_clock.pl.waveform import (DEFAULT_BETA, DEFAULT_SAMPLES_PER_SYMBOL, DEFAULT_SPAN_CHIPS, generate_test_chips,
                                  rrc_config_id)
from rf_clock.plmodel import fir
from rf_clock.twtt.pairs import SecondTheta

PRN = generate_test_chips(4096, 0xACE1)  # both nodes must send the same code (m4c_link's)
TAPS = fir.coefficients(16000)  # the RRC filter's taps, at the DAC amplitude of M3/M4
TX_OFFSET = 1000  # ticks from the slot start to the burst


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("role", choices=("A", "B"))
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--arrival-ticks", type=int, default=29, help="expected one-way delay, 10 ns ticks")
    args = parser.parse_args()

    pl = PL.open(progress=print)
    pl.slots.disable()  # the burst memory takes writes only while the engine is off
    pl.burst.load(PRN, TAPS)  # PRN, packet code and taps: the PL makes each burst from them
    reference = fir.shape(PRN, TAPS)  # what the correlator correlates with: the burst as sent
    pl.correlator.configure(quality_offset=quality_offset(8 * pl.slots.window_beats, reference))
    pl.ctrl.write32(ABI.REG.PACKET_CONFIG_ID, rrc_config_id(DEFAULT_BETA, DEFAULT_SPAN_CHIPS,
                                                            DEFAULT_SAMPLES_PER_SYMBOL))  # sent in every packet
    rx_offset = rx_offset_for(TX_OFFSET, args.arrival_ticks)  # the window opens 1.25 us before the burst
    expected_lag = 8 * (TX_OFFSET + args.arrival_ticks - rx_offset)  # samples into the window
    pl.queue.clear()
    pl.slots.configure(role_b=args.role == "B", rx_offset=rx_offset, tx_offset=TX_OFFSET)  # at the next PPS edge
    print(f"node {args.role}: slot engine on from the next PPS edge; peak expected at lag {expected_lag}")

    seen = 0
    deadline = time.monotonic() + 120
    while not pl.correlator.status().locked:  # each search report: accept the peak where the peer should be
        report = pl.correlator.wait_report(seen, timeout=max(0.1, deadline - time.monotonic()))
        seen = report.seq
        near = abs(report.strongest.lag - expected_lag) <= 400
        if near and report.peak_to_sidelobe_db >= 10 and pl.correlator.status().waiting:
            pl.correlator.accept()
            print(f"accepted the peak at lag {report.strongest.index:.3f}, PSL {report.peak_to_sidelobe_db:.1f} dB")
        time.sleep(0.05)

    seconds = SecondTheta(args.role, pl.slots.slots_per_second, pl.slots.slot_ticks, TX_OFFSET)
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        for entry in pl.queue.read():  # this node's measurements and the peer's packets, oldest first
            record = seconds.add(entry)
            if record is not None:
                print(f"second {record['count']}: {record['pairs']} pairs, theta {record['theta_ns'] * 1000:+.1f} ps, "
                      f"one-way delay {record['delay_ns']:.4f} ns")
        time.sleep(0.02)
    pl.slots.disable()
    print(f"lock lost {pl.correlator.status().lost} times; packets with a bad CRC: "
          f"{pl.ctrl.read32(ABI.REG.PACKET_CRC_CNT)}")


if __name__ == "__main__":
    main()
