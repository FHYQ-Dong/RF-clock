# Link calibrations

Chinese translation: [README_zh.md](README_zh.md).

θ₀ is the steering target of node B: the θ that the two boards show when both run on one reference, so that the true clock offset is zero (plan section 8, the zero-baseline calibration). It is the non-reciprocity Δ/2 of the two directions plus the difference of the two boards' PPS paths of that shared reference. Δ comes from the RF path, the cables, attenuators and filters between the boards and the RFDC configuration, so θ₀ belongs to a pair of boards and a wiring, not to one board: it is kept here, not in a board config (`boards/`) and not in the package.

One file per pair of boards, `<A's S/N>_<B's S/N>.json`, with one entry per wiring under `wirings`, and `current` naming the wiring now on the bench:

| Key | Content |
| --- | --- |
| `rf` | the RF path in each direction, as wired |
| `theta0_ns` | θ₀, ns: the mean θ of the runs below |
| `measured` | when (`date`), on which shared reference (`reference`), with which rf-clock, the runs and the θ of each (`runs`), their spread (`spread_ps`, sd over the power-ups), and the report |

`experiments/m4c_v2_link.py` steers to the `current` wiring's θ₀ (`--wiring NAME` for another, `--target-ns` to override) and records the entry it used in `settings.json`; a hand-run `rf_clock.checks.m4c_link --steer-target-ns` takes the value from here.

**A new wiring.** Software cannot see which cables are connected, so whoever changes the RF path (a cable, an attenuator, a filter, an antenna) or the RFDC configuration adds a new entry and makes it `current`; the old entry stays. Measure θ₀ first: both boards on one reference (docs/board_setup.md section 5.1, `experiments/m4c_v2_link.py --seconds 60` over several power-ups), each run's θ from its `summary.json`, their mean as `theta0_ns`.

| Pair | Wiring now | θ₀ |
| --- | --- | --- |
| [887329260072A (A), 8873292600AB (B)](887329260072A_8873292600AB.json) | `cabled-20261103` | −21.5 ps (4 power-ups, sd 1.95 ps) |
