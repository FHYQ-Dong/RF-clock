"""The predictive control law steering a simulated FS725, on a development machine.

    python -m rf_clock.examples.twtt.02_fake_steer

No FS725 needed.  On the board ``rf_clock.checks.m4c_link --steer-mode predictive`` does the
same with node B's theta and its FS725 (``rf_clock.twtt.control.FrequencyControl``).

The law steers by the FS725's frequency lock loop: the integrator's gain k, the crystal's lag
behind FC and the dead time, measured on each unit at the gain it runs at and kept in its
board's config (``fs725``, written by ``rf_clock.checks.fs725_loop``); the entry below is B's.
Each second it is given theta and returns the new SF, or None to leave SF as it is.  Here
the two clocks drift 66 ps/s apart with a 6 s wander, as on the bench, and B's frequency
follows each change of SF as that loop makes it (``LoopModel``).
"""

from __future__ import annotations

import numpy as np

from rf_clock.pl.board_config import BoardConfig
from rf_clock.twtt.steer import predictive_for

CONFIG = BoardConfig(serial="0" * 32, label="example", fs725={  # on the board: load_board_config()
    "serial": "107129", "gain": 8, "loops": {"8": {"k_per_s": 2.2, "lag_s": 1.2, "dead_s": 0.12}}})
TARGET_PS = -21.5  # theta0 of the wiring (links/)
SECONDS = 900


def free_clocks(seconds: int, seed: int = 1) -> np.ndarray:
    """theta of two free clocks each second, ps: 66 ps/s apart, the rate wandering at 6 s."""
    rng = np.random.default_rng(seed)
    a1, a2 = 2 * 0.9 * np.cos(2 * np.pi / 6), -0.81
    f = np.zeros(seconds)
    for k in range(2, seconds):
        f[k] = a1 * f[k - 1] + a2 * f[k - 2] + rng.normal(0, 8.0)
    return 500.0 + np.cumsum(-66.0 + f)


def main() -> None:
    fs725 = CONFIG.fs725
    loop = fs725["loops"][str(fs725["gain"])]
    law = predictive_for(TARGET_PS, sf0=3, loop=(loop["k_per_s"], loop["lag_s"], loop["dead_s"]))
    asked: list[int] = []  # the seconds that changed SF
    changes: list[int] = []  # by how much
    errors = []
    for k, free in enumerate(free_clocks(SECONDS)):
        # theta: the free clocks plus what each change of SF has done to B's time by now
        own = float(np.dot(changes, law.model.answer(k - np.asarray(asked)))) if asked else 0.0
        theta = free + own
        record = law.step(k, theta)  # theta of second k; on the board the mean over its last 0.2 s
        if record["sf"] is not None:  # on the board: FS725.set_frequency(record["sf"])
            asked.append(k)
            changes.append(record["sf"] - (law.sf0 + sum(changes)))
        errors.append(theta - TARGET_PS)
    late = np.array(errors[300:])
    rate_sd = np.std(np.diff(free_clocks(SECONDS)))
    print(f"FS725 S/N {fs725['serial']} at GA {fs725['gain']}: {len(changes)} changes of SF in {SECONDS} s; "
          f"steering error after 300 s: mean {late.mean():+.1f} ps, rms {np.sqrt(np.mean(late ** 2)):.1f} ps, "
          f"where the free clocks' rate scatters by {rate_sd:.0f} ps/s")


if __name__ == "__main__":
    main()
