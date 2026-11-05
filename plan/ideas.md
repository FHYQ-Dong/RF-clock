# RF-clock ideas

This is the reference version; `plan/ideas_zh.md` is the Chinese translation.

Ideas not yet scheduled or tested. Each states the problem, the mechanism link by link (saying which links are measured and which inferred), how to test it, and what it costs. They came from comparing RF-clock with quantum two-way time transfer (Q-TWTT, references at the end) on 2026-10-26. None of them touches the architecture constraints in `AGENTS.md`.

Last updated: 2026-10-26

## 1. One shared path through a circulator, and equalizing the band-pass filter

### Problem

In the M4 wiring each direction has its own cable, band-pass filter (Mini-Circuits ZVBP-5800-S+) and attenuator: 10 dB from 887329260072A to 887329260101, 20 dB the other way.

1. Each filter delays the burst by its group delay, 3.53–4.11 ns across the burst band (5.8 GHz ± 62.5 MHz), 586 ps peak to peak, rising towards the band edges (Mini-Circuits typical S-parameters, `results/bpf-compare-20261022`; our two units are not measured).
2. Each filter sits in one direction only, so its delay enters only that direction's one-way measurement, m_AB or m_BA, and the difference of the two filters enters theta = (m_AB − m_BA)/2 at half its size: a constant difference is part of the non-reciprocity Δ, and any change after calibration moves theta (from the two-way equations in `src/rf_clock/twtt/timing.py`).
3. The envelope timing follows the group delay weighted by the burst's spectrum, the carrier timing the phase at 5.8 GHz, so when a filter's response shifts or changes shape the two move by different amounts (simulated with the S-parameters: shifting the whole response by 1 MHz moves the envelope by 1.9 ps against the carrier).
4. In M4 the envelope moved against the carrier by up to 41 ps in one night and by +37 to +59 ps across power cycles, the carrier by a few ps (measured, `results/m4-twtt-20261021`). The filter's typical temperature coefficient, 0.25–0.29 ps/K envelope against carrier, is far too small for that (inferred from the typical device); the cause is open.

Q-TWTT sends both directions through one fiber, with an optical circulator at each end separating transmit from receive, so the fiber's delay and its changes are common to both directions.

### 1a. Shared path

Wiring: at each board a circulator joins DAC_A (port 1 → 2) and ADC_B (port 2 → 3) on its port 2; one cable with one band-pass filter and one attenuator joins the two boards' port 2.

1. The filter, the cable and the attenuator are passive and linear, hence reciprocal (S21 = S12): the burst sees the same group delay and the same ripple in both directions (physics; the reciprocity of our units is not measured).
2. The shared part therefore adds the same delay to m_AB and m_BA: it cancels in theta and appears only in the one-way delay (m_AB + m_BA)/2, its changes with temperature included.
3. What remains non-reciprocal is local to each board: DAC_A to port 1, the circulator's port 1 → 2 and port 2 → 3, port 3 to ADC_B. It goes into Δ as the converters' delays do now, and its temperature dependence can be measured board by board.
4. One residue stays in the shared part: the filter's two ends see different terminations (each board's port 2, behind which the DAC and ADC mismatches leak through the circulator's finite isolation), so the reflections between the filter and its ends need not be the same in both directions (inferred; size unknown).

Feasibility:

- The slots are time-division: A transmits 100 ms after the PPS edge, B at 600 ms (`SLOTS` in `src/rf_clock/twtt/timing.py`). A board never receives while it transmits, so the circulator's finite isolation (typically about 20 dB) only lets the board's own burst into its own ADC while that ADC is not capturing. Check that level against the ADC's maximum input.
- Both directions then share one attenuator and have the same loss; choose it so that both ADCs see a sensible level.
- A two-way splitter (Wilkinson or resistive) instead of the circulator is also reciprocal and broadband. Its 3–6 dB loss per end costs nothing while the link is attenuated anyway (10–20 dB now), but a resistive splitter isolates DAC from ADC by only about 6 dB.
- Hardware: two 5.8 GHz circulators (or splitters), and one filter instead of two.

Test: run M4 on the shared path, then warm the filter (heat gun or hand). Prediction: with the present wiring the warmed direction's envelope moves and theta with it; on the shared path the one-way delay moves and theta stays. Afterwards calibrate Δ again on the zero baseline.

### 1b. Equalizing the filter's shape on the ARM

The Q-TWTT counterpart is nonlocal dispersion cancellation: a dispersion-compensating fiber in the local (idler) arm cancels, in the coincidence, the dispersion the remote photon met. It narrows the correlation peak but leaves the group delay itself, and in the 103 km experiment the compensating fiber is itself a 9.1 ps non-reciprocal term of the uncertainty budget. A coherent receiver needs no second fiber: the same can be done in the correlation on the ARM.

1. Estimate each direction's channel response H(f) from kept RX windows (`experiments/m4_channel.py` does this now).
2. Correlate with the PRN template passed through the nonlinear-phase part of H, or remove that phase from the received spectrum before correlating.
3. The filter's shape then no longer pulls the envelope away from the carrier (inferred; to be tested on saved windows).

What it cannot do: the linear-phase part of H is a pure delay, which one direction cannot tell from the time offset, so a drift of the filter's delay stays in m (with the shared path of 1a it cancels in theta). A fixed H goes stale when the filter changes; a tracked H can follow its shape only. It is also what keeps idea 2 from turning the filter into timing noise (see there).

Test: offline first, on the windows kept by `rf_clock.twtt.node --save-every`: envelope minus carrier over a night, with and without the equalization.

Alternative that removes the problem at its source: a filter with flat group delay (VBFZ-5500-S+, about 2 ps across the band in the typical data), which needs extra rejection at 3.8 GHz (about 63 dB below the 5.8 GHz signal for 1 ps; `results/bpf-compare-20261022`).

## 2. A new code every second from a shared key

### Problem

The burst is always the same: a 4096-chip BPSK code from a fixed LFSR seed (`DEFAULT_LFSR_SEED` = 0xACE1 in `src/rf_clock/pl/waveform.py`), written into the TX buffer once when the node starts (`run_node` in `src/rf_clock/twtt/node.py`) and sent at fixed ticks after the PPS edge. Whoever knows the code and the schedule can synthesize a burst before the real one arrives. What the two-way measurement can and cannot see of an attack on the radio path:

1. An attacker who makes the AB burst arrive δ_AB later (earlier if negative) and the BA burst δ_BA later changes theta by (δ_AB − δ_BA)/2 and the one-way delay by (δ_AB + δ_BA)/2 (two-way equations).
2. Comparing the one-way delay with its calibrated value catches any change of the sum, but not δ_AB = −δ_BA: one direction delayed, the other advanced by as much, theta moved by δ_AB and the delay unchanged.
3. Advancing a burst needs the burst before it arrives. With a known code that is possible: synthesize it and send it, stronger, before the real one arrives. With a code the attacker cannot predict it is not: the code is known only once received, and relaying it costs time.
4. With delays alone (δ_AB, δ_BA ≥ 0), the theta change is at most the delay change, so a delay check with tolerance ε bounds an undetected theta error by ε. Unpredictable codes and the delay check protect only together (Narula and Humphreys 2018: measuring the round trip is necessary for secure clock synchronization, not sufficient).
5. One more condition: the legitimate path must be the fastest. An attacker who receives near one board and forwards over a faster route gains the difference as an advance (a straight line through air beats a coaxial cable at about 0.7 c). Line-of-sight radio meets it, the bench cables do not.

Q-TWTT gets link 3 from physics: photon pairs are emitted at random times, and no one can send a photon before it exists. It still needs link 4 (the asymmetric delay attack of Lee et al. 2019 works against it) and an authenticated exchange (idea 3).

### Idea

- A secret shared by the two boards, in a root-only file outside the package and the repository like the board config, but per pair of boards rather than per board. Separate keys are derived from it for separate uses (for example with HKDF): K_code here, K_link for idea 3.
- For each second and slot the chips are the first 4096 bits of AES-CTR under K_code with the nonce (second, slot). The second must be a number both boards agree on without talking: the pairing that matches records now (system time of the PPS edge, NTP within milliseconds, `PAIR_TOLERANCE_S`) can name it, for example the PPS edge's system time rounded. A nonce must never repeat under one key. If the boards disagree, the correlation finds no peak: a lost second, not a wrong time.
- Transmitter: the ARM computes the next second's burst and writes it into the TX buffer after the last slot of the current second, before it is armed (TX bursts stay preloaded into on-chip buffers).
- Receiver: the ARM generates the same second's reference to correlate with.
- The delay check of link 4: compare `delay_ns` with its calibrated value and flag the second (not `FAULT`) outside the tolerance. How tight the tolerance can be depends on how much the one-way delay drifts on its own.

Costs and risks:

- Code-dependent bias: through a filter with group-delay ripple the timing depends on the code's spectrum. With one fixed code that error is a constant inside Δ; with a new code every second it becomes noise. Rough estimate for ZVBP-5800-S+: 1–2 ps per burst (its 586 ps ripple averaged over the about 5000 independent spectral bins of a random 41 µs code; not simulated). Equalization (1b) or a flat filter removes it; the shared path (1a) alone does not, because the two directions then carry different codes.
- Sidelobes: every code has its own correlation sidelobes; the highest one of a random 4096-chip code is about −24 dB (rough). Check that `psl_db` stays well above `MIN_PSL_DB`.
- ARM time each second: generating and shaping two bursts and writing one into the TX buffer (to be measured).
- The security is computational, not information-theoretic. For timing this matters little: an attack has to work in real time, and breaking a key afterwards gains nothing. Where a QKD link joins the two sites, its key would make the code a one-time pad.

Test: M4 alternating seconds with the fixed and the keyed code; compare theta's mean and scatter between the two.

## 3. Authenticating the exchanged records (HMAC)

### Problem

The two boards exchange their records over plain TCP (JSON lines on port 50725, `src/rf_clock/twtt/link.py`). The records carry everything theta is computed from: transmit tick, receive tick, correlation peak and phase. Anyone on the network between the boards can change them: adding x ns to B's reported receive time of slot AB moves theta by x/2 on both boards, without going near the radio. Ideas 1 and 2 protect only the radio path.

### Idea

- Each message gets a tag: HMAC-SHA256 over its exact bytes under K_link (see idea 2 for the key).
- The receiver computes the tag again (compared with `hmac.compare_digest`), and drops and logs a message whose tag does not match.
- Replay: a recorded genuine message would stay valid, so the tagged bytes include a number that must increase, such as the record's PPS count together with a random number each side sends in `hello` for that connection; an old message is dropped.
- HMAC gives integrity and authenticity, not secrecy; the records need no secrecy.
- Alternative: TLS with certificates around the socket. The boards' Python 3.10 supports it (TLS with a pre-shared key only from Python 3.13). More setup, the same result here.

Test: unit tests in `tests/` (altered message, replayed message, wrong key), then an M4 run with tags.

## References

- H. Hong et al., "Quantum two-way time transfer over a 103 km urban fiber", J. Lightwave Technol. 42, 1479 (2024); arXiv:2305.01897
- X. Xiang et al., "Quantum two-way time transfer over a hybrid free-space and fiber link", arXiv:2212.01741
- H. Hong et al., "Cascaded quantum time transfer breaking the no-cloning barrier with entanglement relay architecture", arXiv:2506.12882
- M. Krelina et al., "Quantum protocols for time synchronisation and distribution: a critical assessment", arXiv:2604.10243
- J. Lee et al., "Asymmetric delay attack on an entanglement-based bidirectional clock synchronization protocol", Appl. Phys. Lett. 115, 141101 (2019)
- L. Narula and T. E. Humphreys, "Requirements for secure clock synchronization", IEEE J. Sel. Topics Signal Process. 12, 749 (2018); arXiv:1710.05798
