"""M5: how well can steering B's FS725 hold theta, and at which time constant?

Problem.  ``rf_clock.twtt.steer`` corrects B's frequency offset SF every
``interval`` seconds so that theta's error decays with time constant ``tau``.  Too
short a tau feeds theta's measurement noise into B's frequency; too long a tau lets
the clocks' own frequency noise move theta before it is corrected.  Which tau, and
what is left?  The measurement noise is known from M4 (theta's envelope scatters by
about 3 ps per second, results/m4-twtt-20261021); the clocks' noise is not measured
yet, so this simulates the FS725's specification (Allan deviation 2e-11 at 1 s,
2e-12 at 100 s: white frequency noise) and a quieter pair (5e-12 at 1 s), each with
random-walk frequency noise of 1e-13 at a day.

Each run starts 500 ps from the target with B 3e-11 fast, steers for ``--seconds``
and reports, after the first ``--settle-out`` seconds, the rms of theta's true error
and the rms change of SF per correction.  SF moves the frequency in steps of
``--sf-step`` units (1: the nominal 1e-12; the magnetic-field DAC may make it about
4 near the PRS10's operating point, rf_clock.fs725).

    .venv/bin/python experiments/m5_steer_sim.py
    .venv/bin/python experiments/m5_steer_sim.py --sf-step 4
"""

from __future__ import annotations

import argparse
import statistics

import numpy as np

from rf_clock.twtt.steer import Steering, filter_for

DAY_S = 86400.0


def simulate(tau: float, adev: float, *, seconds: int, settle_out: int, interval: int, theta_sd: float,
             sf_step: int, seed: int) -> tuple[float, float]:
    """rms theta error (ps) after ``settle_out`` and rms SF change per correction."""
    rng = np.random.default_rng(seed)
    rwfm = 1e-13 * 1e12 * np.sqrt(3 / DAY_S)  # ps/s per sqrt(s): sigma_y^2(tau) = q tau / 3
    steering = Steering(target_ps=0.0, sf0=0, filter=filter_for(adev, theta_sd), tau_s=tau, interval_s=interval,
                        max_offset=2000)
    x, y_free, errors, changes = 500.0, 30.0, [], []
    for count in range(seconds):
        record = steering.step(count, x + rng.normal(0, theta_sd))
        if record["sf"] is not None and count >= settle_out:
            changes.append(record["sf"])
        applied = sf_step * round(steering.sf / sf_step)  # what the DAC realizes
        x += y_free + applied + rng.normal(0, adev * 1e12)
        y_free += rng.normal(0, rwfm)
        if count >= settle_out:
            errors.append(x)
    steps = np.diff(changes) if len(changes) > 1 else np.zeros(1)
    return float(np.sqrt(np.mean(np.square(errors)))), float(np.sqrt(np.mean(np.square(steps))))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0], allow_abbrev=False)
    parser.add_argument("--seconds", type=int, default=20000)
    parser.add_argument("--settle-out", type=int, default=3000)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--theta-sd", type=float, default=3.0)
    parser.add_argument("--sf-step", type=int, default=1)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args()
    taus = [20, 50, 100, 200, 500, 1000]
    print(f"interval {args.interval} s, theta sd {args.theta_sd} ps, SF step {args.sf_step}; "
          f"rms theta error (ps) / rms SF change per correction, over {args.seeds} seeds")
    print(f"{'tau/s':>12}" + "".join(f"{t:>14}" for t in taus))
    for adev in (2e-11, 5e-12):
        cells = []
        for tau in taus:
            runs = [simulate(tau, adev, seconds=args.seconds, settle_out=args.settle_out, interval=args.interval,
                             theta_sd=args.theta_sd, sf_step=args.sf_step, seed=seed) for seed in range(args.seeds)]
            cells.append(f"{statistics.fmean(r[0] for r in runs):7.1f} / {statistics.fmean(r[1] for r in runs):4.1f}")
        print(f"{f'adev {adev:.0e}':>12}" + "".join(f"{c:>14}" for c in cells))


if __name__ == "__main__":
    main()
