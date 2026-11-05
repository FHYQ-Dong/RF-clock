"""The bench's PPS distribution on the Thorlabs EPG1 pulse and delay generator.

On the bench it distributes the FS725 1PPS to both boards: the FS725 1PPS
drives TRIG IN, output 1 goes to the PPS input (J7, 50 ohm) of board S/N
8873292600AB (.100) and output 4 to S/N 887329260072A (.99), since 2026-11-03
(before: output 1 to 887329260101, output 2 to 887329260072A, output 4 the
scope's PPS reference).  This is the M0 bench set-up
(plan section 9) the experiments here rely on, not an experiment itself.
Run on the machine the EPG1 is plugged into (RF-clock-PC), with the
project's ``instruments`` dependency group (``uv sync --only-group instruments
--no-install-project`` there)::

    .venv/bin/python experiments/epg1.py status    # read every setting
    .venv/bin/python experiments/epg1.py pps       # external trigger -> outputs 1 and 4

The driver is ``tanglab_instruments.EPG1``; this module keeps the bench's
settings.  In the synchronous channel mode (SYS:CHMODE 0, the default) an
output changed while running only takes effect at the next RUN start, and
stopping and starting interrupts every output for about a second.
"""

from __future__ import annotations

import argparse

from tanglab_instruments import EPG1
from tanglab_instruments.epg1 import CHANNEL, GLOBAL, POL_POSITIVE, RUN_START, RUN_STOP, TERM_50OHM, TRIGGER_EXTERNAL

RESOURCE = "ASRL/dev/serial/by-id/usb-Thorlabs_EPG1_Electrical_Pulse_and_Delay_Generator_U00176-if00::INSTR"
BACKEND = "@py"  # pyvisa-py; pyserial asserts DTR, without which the EPG1 does not answer

PPS_WIDTH = 10e-6  # same as the FS725 1PPS
PPS_AMPLITUDE = 2.5  # V into 50 ohm, the level the boards saw from the FS725
# Output -> delay: 1 to 8873292600AB, 4 to 887329260072A.  Each is delayed so
# that its board's PPS edge sits mid-tick: at delay 0 both edges lay 1.5 ns
# before a 100 MHz sampling edge (m1_pps_phase_scan.py, results/
# pps-phase-scan-20261103_120343: half the edges one tick later at 1.46 ns on
# 887329260072A, 1.50 ns on 8873292600AB), and from one power-up to the next the
# link's theta jumped by a tick; 6.5 ns puts both edges 5 ns from either sampling
# edge.  (2026-10-18, on output 2, 887329260072A needed 6.6 ns.)
PPS_DELAYS = {1: 6.5e-9, 4: 6.5e-9}
# The EPG1 retimes TRIG IN on a 2.5 ns clock, and the slow FS725 1PPS edge
# crosses the threshold later the higher it is.  At 1.0 V the crossing sat on
# an EPG1 clock edge (outputs jumping 2.5 ns, ~50 % of seconds on 2026-10-19);
# 0.25-0.75 V and 1.25-1.5 V are clean, one clock apart, so 1.3 V is about
# 1.25 ns from either edge.  1.75 V is one clock later again, 2.0 V ~7.5 ns.
TRIGIN_THRESHOLD = 1.3


def open_epg1() -> EPG1:
    return EPG1(RESOURCE, backend=BACKEND)


def restart(epg: EPG1) -> None:
    """Stop (unless stopped: stopping a stopped EPG1 is an error) and start RUN,
    which applies changed outputs in the synchronous channel mode."""
    if epg.get("RUN") != str(RUN_STOP):
        epg.set("RUN", RUN_STOP)
    epg.set("RUN", RUN_START)


def print_status(epg: EPG1) -> None:
    status = epg.status()
    for header in GLOBAL:
        print(f"{header:18s} {status[header]}")
    for ch in range(1, 5):
        print(f"PULSE{ch}  " + "  ".join(f"{k}={status[f'PULSE{ch}:{k}']}" for k in CHANNEL))


def pps(epg: EPG1) -> None:
    """FS725 1PPS on TRIG IN -> one pulse per second on the PPS_DELAYS outputs.

    The trigger input is resampled by the EPG1's clock (2.5 ns peak-to-peak
    trigger-to-output jitter).  Locked to the FS725 10 MHz on REF IN that
    sampling phase is nearly fixed, but on 2026-10-18 about 7 % of the edges
    apparently came out about 2.5 ns early (the PPS close to an EPG1 sampling
    edge), and the phase had moved by about a nanosecond since 2026-10-17; hence
    the per-output delays that keep each board's edge mid-tick and the trigger
    threshold that keeps the PPS away from the EPG1's sampling edges.
    REFCLK:IN:STATUS reads 1 once the reference is locked.
    """
    if epg.get("RUN") != str(RUN_STOP):
        epg.set("RUN", RUN_STOP)
    epg.set("TRIGGER", TRIGGER_EXTERNAL)
    epg.set("REPRATE", 1)  # internal rate only bounds the width check
    epg.set("TRIGIN:POL", POL_POSITIVE)
    epg.set("TRIGIN:TERM", TERM_50OHM)
    epg.set("TRIGIN:THRESH", TRIGIN_THRESHOLD)
    epg.set("REFCLK:IN:EN", 1)
    for ch, delay in PPS_DELAYS.items():
        epg.set(f"PULSE{ch}:TERM", TERM_50OHM)
        epg.set(f"PULSE{ch}:POL", POL_POSITIVE)
        epg.set(f"PULSE{ch}:AMPL", PPS_AMPLITUDE)
        epg.set(f"PULSE{ch}:DEL", delay)
        epg.set(f"PULSE{ch}:WID", PPS_WIDTH)
        epg.set(f"PULSE{ch}:EN", 1)
    epg.set("RUN", RUN_START)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("action", choices=("status", "pps"))
    args = parser.parse_args()
    with open_epg1() as epg:
        if args.action == "pps":
            pps(epg)
        print_status(epg)


if __name__ == "__main__":
    main()
