"""M3 sub-experiment: where does each LMX2594 put its converter without the phase SYNC?

Problem.  results/lmx-phase-sync-20261019 traced the half-sample DAC states to
the LMX2594 channel divider and removed them with its phase SYNC (rf-clock
1.3.0.4), but measured only the DAC, on the scope (two states 52 ps apart, read
as 260.4 ps reference steps), and predicted four ADC states 52 ps apart.  This
measures both converters over the wired loopback (m3_common.py), with each
LMX2594 written as PL.open does it (``sync``) or otherwise:

- ``nosync``: rewritten with the image from before 1.3.0.4, without SYNC, over
  the synchronized chip;
- ``nosync2``: that image written twice in a row, so that the second write
  starts from an unsynchronized chip;
- ``legacy``: the clocks written as PL.open did before 1.3.0.4, that LMX2594
  only ever getting the old image; with ``--reboot`` it starts from the clocks of
  PYNQ's boot-time base overlay, as in the scope series.

One LMX2594 is left synchronized, so a change between runs belongs to the other.
Outcome (results/lmx-sync-loopback-20261019 section 3): both converters
sit on one 26.04 ps lattice; over clock writes the DAC took three adjacent
positions of its four and the ADC two adjacent ones; the old writing after a
reboot moved both chips by pairs of VCO periods only, the DAC between the
scope's two states 52.5 ps apart.

Run on RF-clock-PC as the normal user, with the board idle::

    .venv/bin/python experiments/m3_lmx_sync.py --runs 10                   # both synchronized
    .venv/bin/python experiments/m3_lmx_sync.py --dac-lmx nosync --runs 20
    .venv/bin/python experiments/m3_lmx_sync.py --adc-lmx nosync2 --runs 20
    .venv/bin/python experiments/m3_lmx_sync.py --dac-lmx legacy --reboot --runs 15

Results go to results/m3-lmx-sync-<time>/ (m3_common.py).  The series of the
report were made with the earlier m3_restarts.py, as results/m3-restarts-*.
"""

from __future__ import annotations

import argparse

import m3_common


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    for side in ("adc", "dac"):
        parser.add_argument(f"--{side}-lmx", choices=m3_common.LMX_MODES, default="sync",
                            help=f"how the {side.upper()} LMX2594 is written (see the docstring)")
    m3_common.add_series_args(parser)
    args = parser.parse_args()
    m3_common.run_series("m3-lmx-sync", args, clocks=f"--adc-lmx {args.adc_lmx} --dac-lmx {args.dac_lmx}",
                         record={"adc_lmx": args.adc_lmx, "dac_lmx": args.dac_lmx})


if __name__ == "__main__":
    main()
