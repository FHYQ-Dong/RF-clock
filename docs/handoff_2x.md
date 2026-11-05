# Moving from rf-clock 1.x to 2.x

For those who used the 1.x package (last release 1.3.0.12) and move to 2.x (now 2.2.0.9). The details are in [docs/ABI.md](ABI.md) and [docs/board_setup.md](board_setup.md). The reports linked below live in `results/`, which is not in Git; a copy is on the lab NAS, `smb://kinabalu2.stdusr.yale.internal/kinabalu/Haoyu/RF-clock/results` (on Windows `\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results`).

## What changed

| | 1.x | 2.x |
| --- | --- | --- |
| Measurement | once a second: the PL captures an RX window into DDR, the ARM correlates it | every 2 ms slot (500 a second, A and B in turn): the PL tracks the peer's burst in each window (peak, sub-sample position, carrier phase) |
| Exchange between the nodes | TCP over Ethernet, records paired by system time, so both boards need NTP | inside the bursts: each burst carries its node's last measurement (the measurement packet), paired by `PPS_COUNT` (the number of the second: each PL counts its PPS edges) and slot (which 2 ms turn of that second, 0-499); no network, no NTP |
| Start with two clocks | not supported (M4 ran both boards on one FS725) | initial acquisition (`--acquire`): before its slots start, B records an 11 ms window into DDR, finds A's bursts in it and decodes their measurement packets, which carry A's send times; that places A's PPS on B's time to within the assumed one-way delay, and B moves its FS725's 1PPS onto it by whole ticks (PP), so that each node's bursts arrive inside the other's RX windows |
| Steering B's FS725 | `rf_clock.twtt.steer` beside the node, the Kalman control law, a correction every 10 s | inside the link check (`m4c_link`), with either law. The Kalman control law: a Kalman filter estimates θ and the two clocks' frequency difference, and SF is set so that θ's error decays with a time constant τ. The predictive control law: a model of the FS725's frequency lock loop takes its own corrections out of θ, the free clocks' frequency is predicted from its recent past, and the next changes of SF are planned by least squares; up to five corrections a second |
| PPS out of the node | the FS725's own 1PPS | the output PPS: one pulse a second on DAC_B (J20) made by the PL from its tick, and a validity line (Pmod J1 pin 1, high = not valid; LED PL_LED0) that goes low once the link is locked and steered |
| FS725 driver | status, SF, PP | also its loop's FC, its gain GA, a fast SF write, and a check of the unit against the board config |

A 1.x node and a 2.x node cannot measure each other: put 2.x on both boards. The 1.x registers keep their offsets, but the PL refuses the 1.x TX/RX commands while the slot engine runs. The 1.x once-a-second exchange (`rf_clock.twtt.node`, `link`) is deprecated. The board config (`/etc/rf-clock/board.json`) keeps what 1.3 put there, so a board set up under 1.3 keeps its config, and gains one entry, `fs725`: the FS725 that clocks the board (step 3 below).

## How to use it

1. Install the 2.x wheel on both boards, as before ([docs/ABI.md §9.1](ABI.md)): `sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index rf_clock-2.2.0.9-py3-none-any.whl`.
2. Wiring: each board on its own FS725 (10 MHz and 1PPS); each board's DAC_A through a band-pass filter and an attenuator into the other's ADC_B; each FS725's RS-232 port on its board's USB. The output PPS is on DAC_B.
3. Record each board's FS725 in its board config, once per unit: `$run -m rf_clock.checks.fs725_loop --port /dev/serial/by-id/... --gain GA --write` (`run` as in the README), GA 8 on B and 7 on A; it records the port too, so later runs name none. It steps SF by 200 and back four times and fits the unit's frequency lock loop from FC alone, in about 3 minutes; it needs neither the other board nor the link. From then on every `m4c_link --fs725` checks at its start that the unit is the one recorded and sets its GA, with a warning that says from what to what; a unit the config does not name stops the node until it is recorded.
4. Run the link, either from RF-clock-PC with both boards idle:

   ```bash
   .venv/bin/python experiments/m4c_v2_link.py --scenario steer --seconds 3600 "--node-args=--pps-out 20" \
       "--a-args=--pps-trig --fs725" "--b-args=--steer-mode predictive --steer-step 0.2 --steer-late-slots 50"
   ```

   or by hand on each board as root, both with the same `--start-at` and `--stop-at` (Unix times), with θ₀ of the wiring from `links/` (−0.0215 ns for the bench's):

   ```bash
   $run -m rf_clock.checks.m4c_link --role A --start-at T0 --stop-at T1 --no-entries --pps-out 20 --pps-trig --fs725
   $run -m rf_clock.checks.m4c_link --role B --start-at T0 --stop-at T1 --no-entries --pps-out 20 \
       --acquire --fs725 --steer-target-ns -0.0215 --steer-mode predictive --steer-step 0.2 --steer-late-slots 50
   ```

5. Read the result in each node's log: one `second` line per second (θ, the one-way delay, their scatter, the steering), plus `step` lines for the 0.2 s steps; `rf_clock.twtt.logs` reads it back.

For your own code, the examples show the library: `rf_clock.examples.pl.04_link_node`, one node of the link on the board, and `rf_clock.examples.twtt`, the two-way equations and the predictive control law on a development machine (`rf_clock.twtt.pairs`, `control`, `steer`, `logs`; [docs/ABI.md §11.12](ABI.md)).

Three settings belong to a setup, not to the package:

- θ₀ (`--steer-target-ns`): θ with both boards on one reference for this wiring (the non-reciprocity of the cables, filters and boards, and the two PPS paths of that reference). It is kept per wiring in `links/<A's S/N>_<B's S/N>.json`, which `m4c_v2_link.py` reads ([links/README.md](../links/README.md)); a new cable, attenuator or filter needs a new entry, measured with both boards on one reference.
- `--arrival-ticks`: the expected one-way delay in 10 ns ticks (default 29, the bench's cables). Set it for another link.
- `--pps-trig` on A: board 887329260072A times its Schmitt-trigger copy of the PPS, because the edge of its comparator copy sits on a sampling edge of the tick and toggles between ticks. Check a new board with `rf_clock.checks.pps_pp_scan`.

## How much better

Measured on the bench (887329260072A as A, 8873292600AB as B, cabled with attenuators, each on its own FS725 unless noted).

| Quantity | 1.x | 2.x |
| --- | --- | --- |
| θ measurements | 1 a second | about 250 a second |
| θ noise, both boards on one reference | 2.95 ps sd per second | 0.81-0.87 ps per pair, well under 0.1 ps per second |
| Time from a measurement to the other node | about 1 s, over Ethernet | under 2 ms, in the next burst |
| Steering error (θ − θ₀ while B is steered, rms) | 66 ps (1.x's Kalman control law: τ 50 s, a correction every 10 s; run on the 2.x link) | 27-29 ps (predictive control law, 1 s steps); about 15 ps (0.2 s steps, 2.2.0.7: 15.3 and 15.7 ps in two 20-minute runs; longer runs still to come) |
| Node PPS against its own 10 MHz, second to second | FS725 1PPS: 43.9 ps rms | output PPS: 7.7 ps rms (the scope's resolution) |
| PPS moved by a PP command | the 1PPS misses the move by 40-260 ps | the output PPS moves by exactly the whole ticks, 3.1 ps rms |
| Two-board PPS constant over a new start and acquisition | 1PPS: moved by 145 ps | output PPS: 0.9 ps |

## How it got better

- **Measurement noise and rate.** The PL correlates every window with the same chips through the same RRC filter it sent, in integer arithmetic, and the 2 ms ping-pong gives about 250 two-way pairs a second instead of one ([results/v2-link-20261103/REPORT.md](../results/v2-link-20261103/REPORT.md)). θ's noise is now far below the clocks' own wander, so steering is limited by the clocks, not by the measurement.
- **Steering, step by step:**
  1. A faster Kalman control law (τ 5 s, a correction every 2 s) reaches faster wander: 66 → about 40 ps. It needed the FS725's delay in its model: an SF command acts on the frequency about 1.1-1.6 s later.
  2. That delay is the FS725's frequency lock loop (FLL): an integrator of the rubidium error drives the crystal through a 1 s filter. GA 8 raises the integrator's gain from 0.95 to 2.2 per second and halves the delay: 33-35 ps ([results/fs725-fll-20261105/REPORT.md](../results/fs725-fll-20261105/REPORT.md), [docs/fs725_principles.md](fs725_principles.md)).
  3. The predictive control law models that loop, takes its own corrections out of θ, predicts the clocks' frequency from its recent past and plans the next SF changes by least squares; θ comes from the last slots of each second, closest to the next command: 27-29 ps ([results/predictive-steering-20261105/REPORT.md](../results/predictive-steering-20261105/REPORT.md)).
  4. The same law on 0.2 s steps, five corrections a second: about 15 ps ([results/two-loops-20261105/REPORT.md](../results/two-loops-20261105/REPORT.md)). Most of what is left is A's FS725, which B can follow only with its loop's delay.
- **PPS.** The FS725's 1PPS circuit scatters by 22-44 ps from second to second and does not land PP moves exactly; the output PPS is a DAC sample at a fixed tick, so it inherits the 10 MHz's stability and moves only with the tick ([results/pps-out-20261104/REPORT.md](../results/pps-out-20261104/REPORT.md)).
