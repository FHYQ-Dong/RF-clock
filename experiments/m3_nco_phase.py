"""M3 sub-experiment: does every PL.open start the signal NCOs in the same carrier phase?

Problem.  Over clock writes and reboots the carrier phase of M3's correlation
peak took one of three values 120 degrees apart at each PL.open (and so did the
DAC output alone on the scope), with no change of the timing: a pure phase
rotation, so an oscillator, the DAC's 3.8 GHz or the ADC's -1.0 GHz NCO.  This
restarts the NCO phase of the signal block(s) after PL.open (m3_common.py,
``run-m3 --nco-reset``):

- ``--nco-reset dac|adc|both``: which NCO(s) restart; restarting only one shows
  whether the other has the steps too;
- ``--nco-event sysref``: the mixer event source becomes SYSREF and
  ResetNCOPhase arms the reset, which the next SYSREF edge applies;
  ``--nco-event tile``: the same reset on a tile event issued by the software,
  at an arbitrary instant, as the IP starts the NCOs in PL.open.

Outcome (results/lmx-sync-loopback-20261019 section 4): both NCOs have the
steps; restarting both at a SYSREF edge leaves one phase, on a tile event three.
Since rf-clock 1.3.0.5 PL.open does the SYSREF restart itself (PG269 Use Case 1),
so ``--nco-reset none`` now checks that fix, over reboots with ``--reboot``.

Run on RF-clock-PC as the normal user, with the board idle::

    .venv/bin/python experiments/m3_nco_phase.py --reboot --runs 10      # PL.open alone
    .venv/bin/python experiments/m3_nco_phase.py --nco-reset both --nco-event tile --runs 12

Results go to results/m3-nco-phase-<time>/ (m3_common.py).  The series of the
report were made with the earlier m3_restarts.py, as results/m3-restarts-*.
"""

from __future__ import annotations

import argparse

import m3_common


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("--nco-reset", choices=("none", "dac", "adc", "both"), default="none",
                        help="restart the signal NCO phase after PL.open")
    parser.add_argument("--nco-event", choices=("sysref", "tile"), default="sysref",
                        help="event that applies --nco-reset: a SYSREF edge, or a tile event issued by software")
    m3_common.add_series_args(parser)
    args = parser.parse_args()
    m3_common.run_series("m3-nco-phase", args, m3=f"--nco-reset {args.nco_reset} --nco-event {args.nco_event}",
                         record={"nco_reset": args.nco_reset, "nco_event": args.nco_event})


if __name__ == "__main__":
    main()
