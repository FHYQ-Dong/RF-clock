"""M3 sub-experiment: which VCO band does each LMX2594 calibrate to, and how does it move the timing?

Problem.  With both LMX2594 phase-synchronized and the NCOs restarted at SYSREF
(rf-clock 1.3.0.5), the loopback time still took two values 9.7 ps apart from
one PL.open to the next, with both ADC tiles' MTS capture codes moving together:
the ADC's LMX2594.  Every clock write recalibrates each LMX2594's VCO, which
picks a core, an amplitude and a capacitor band (VCO_CAPCTRL).  This reads the
calibration back after every clock write (R110-R112 on MUXout, m3_common.py
``write-clocks --readback``) together with the RFSoC die temperature, and with
``--adc-capctrl``/``--dac-capctrl`` forces that chip's band instead (R8
VCO_CAPCTRL_FORCE, R19), to tell cause from coincidence.

Outcome (results/lmx-sync-loopback-20261019 section 5): the ADC's chip picks
band 117 or 118, the DAC's 122 or 123, with or without a reboot; one band higher
puts that converter 8-10 ps later, and forcing the band reproduces it.  The ADC's
chip does not lock at 119.

Run on RF-clock-PC as the normal user, with the board idle::

    .venv/bin/python experiments/m3_vco_band.py --runs 20                   # calibrated, read back
    .venv/bin/python experiments/m3_vco_band.py --adc-capctrl 117 --runs 4  # forced
    .venv/bin/python experiments/m3_vco_band.py --reboot --runs 20

A forced band that cannot lock leaves the clocks unlocked until the next PL.open.
Results go to results/m3-vco-band-<time>/ (m3_common.py).  The series of the
report were made with the earlier m3_restarts.py, as results/m3-restarts-*.
"""

from __future__ import annotations

import argparse

import m3_common


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    for side in ("adc", "dac"):
        parser.add_argument(f"--{side}-capctrl", type=int, choices=range(184), metavar="0..183",
                            help=f"force the {side.upper()} LMX2594's VCO band (VCO_CAPCTRL) instead of calibrating it")
    m3_common.add_series_args(parser)
    args = parser.parse_args()
    forced = {side: code for side, code in (("adc", args.adc_capctrl), ("dac", args.dac_capctrl)) if code is not None}
    m3_common.run_series("m3-vco-band", args,
                         clocks="--readback" + "".join(f" --{side}-capctrl {code}" for side, code in forced.items()),
                         record={"adc_capctrl": args.adc_capctrl, "dac_capctrl": args.dac_capctrl})


if __name__ == "__main__":
    main()
