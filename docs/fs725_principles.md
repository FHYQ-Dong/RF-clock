# How the FS725 works

This is the reference version; `docs/fs725_principles_zh.md` is the Chinese translation.

Each node of RF-clock takes its time from an SRS FS725 rubidium frequency standard: its 10 MHz clocks the whole board, its 1PPS starts the node's seconds, and on node B its RS-232 lets the ARM steer it. This note explains, from no background, what is inside the box and how the parts work together, so that the measurements of `results/m5-v2-20261103/REPORT.md` can be read against the mechanism. The sources are the PRS10 manual (`docs/PRS10m.pdf`, SRS; page numbers are the manual's printed ones) and the FS725 manual; what was measured on our units is marked as measured, the rest as the manual's or as inferred.

## 1. What is in the box

The FS725 is a half-width 2U box around an SRS PRS10 rubidium module, with a power supply, distribution amplifiers and an RS-232 port (FS725 manual, "Overview"). Everything that sets the time is in the PRS10:

```text
                               FS725
 ┌───────────────────────────────────────────────────────────────────────┐
 │  PRS10 rubidium module                               distribution amps │
 │   10 MHz crystal oscillator (in an oven) ─────────> 2 x 10 MHz, 5 MHz ──────> board: LMK04828 (and the scope)
 │   1 pps generator, timed from that 10 MHz ────────> 1PPS ───────────────────> board: J7
 │   microcontroller <── RS-232 ──────────────────────────────────────────────── B: USB adapter (SF, PP, queries)
 └───────────────────────────────────────────────────────────────────────┘
```

The 10 MHz output is the crystal oscillator's own signal; the rubidium atoms never produce an output. Their job is to tell the microcontroller how far the crystal is off, so that it can be pulled back. The rest of this note is that loop.

## 2. The idea: a crystal held to an atom

A quartz crystal oscillator is a good flywheel: from one second to the next its frequency hardly moves. Over hours and days it drifts, with temperature and with age. An atom has the opposite property: one of its transitions has a frequency fixed by nature, the same in every rubidium atom, but no usable output can be taken from it directly. A rubidium standard combines the two. The crystal makes the output; the atoms serve as a frequency detector that measures, over and over, whether the crystal is fast or slow; and a feedback loop corrects the crystal. On short time scales the output is as quiet as the crystal, on long ones as steady as the atom.

## 3. The atom: a transition at 6.834 GHz

Rubidium has one electron outside closed shells. In the isotope Rb87 the ground state of that electron is split in two by the orientation of the electron's magnetic spin against the nucleus's; the energy between the two halves is that of a microwave photon of 6 834 682 612.8 Hz, the hyperfine transition (PRS10 manual, "Theoretical Overview", p. 8). An atom can be moved from one half to the other by microwaves at exactly that frequency, and only at that frequency.

How the PRS10 sees whether its microwaves hit that frequency is optical, in the physics package (p. 8-10):

```text
  Rb87 discharge lamp ──light (red, about 780 nm)──> resonance cell (Rb87 + Rb85 + buffer gas) ──> photocell ──> current
                                                       ▲   inside a mu-metal shield and a field coil
                                                       └── microwaves near 6.834 GHz
```

1. The lamp glows with rubidium's red lines. The Rb85 in the cell absorbs one of the lines that start from Rb87's two ground levels, so what reaches the Rb87 atoms mostly lifts atoms out of one of the two levels.
2. An atom lifted that way falls back into either level at random, so over and over the light empties that level and fills the other: "optical pumping". With fewer atoms left that can absorb, more light passes the cell and reaches the photocell.
3. Microwaves at the hyperfine frequency move atoms back into the emptied level, so the light is absorbed again and the photocell's current drops, by about 0.1 % (SRS product page). Off that frequency the microwaves do nothing.

So the photocell current, as a function of the microwave frequency, has a narrow dip at the atoms' frequency: a frequency detector. The buffer gas and the lamp shift the dip by about 3 kHz from the free atom's value, and the shift differs a little from unit to unit (p. 10); this is why each unit is calibrated, and why SF (section 5) is a correction against the unit's own calibration.

## 4. From 10 MHz to 6.834 GHz

The microwaves are made from the crystal's 10 MHz (p. 11-12, Figure 2): a synthesizer locked to the 10 MHz makes about 359.72 MHz, and a step-recovery diode multiplies that by 19 to about 6.834 GHz. So the microwave frequency is the crystal's frequency times a fixed ratio: if the crystal runs fast by one part in 10¹¹, the microwaves do too, and they miss the dip by that fraction. That is the link the loop uses: a microwave frequency off the dip means a crystal frequency off its target.

The synthesizer's steps are coarse, about one part in 10⁹. The fine adjustment is made on the atoms instead: a small magnetic field from a coil around the cell shifts the transition (the Zeeman effect, quadratic in the field and always upward), and it is set so that the dip sits exactly at the synthesizer's frequency (p. 13). The coil's current is reversed at 5 Hz, so that an external field adds in one half and subtracts in the other and averages out (p. 13).

## 5. SF: moving the atoms' frequency

SF ("set frequency", p. 26) is the command node B steers with. It sets the field coil's current, and with it where the dip lies; the firmware makes the quadratic field-to-frequency law linear, so that one SF unit is one part in 10¹² of output frequency, over ±2000. The chain from a command to the 10 MHz is:

1. SF changes the coil's current, so the transition, the dip, moves at once (on the next 5 Hz coil cycle, inferred).
2. The crystal has not moved yet, so the microwaves now lie off the dip, and the detector of section 6 reports an error.
3. The loop of section 6 pulls the crystal until the microwaves sit on the dip again: the 10 MHz follows SF through that loop, on B's unit at GA 7 with its phase about 1.1-1.3 s late (measured, [FLL report](../results/fs725-fll-20261105/REPORT.md) section 5.1).

On B's unit the field is set by a DAC that steps in about 3.4 SF units (measured, `rf_clock.fs725`: SF +100 moved the magnetic DAC by 29 steps), so a finer SF is realized only on average. SF is ignored while the unit's own 1 pps phase lock (PL, section 8) is on; B keeps it off (measured: `PL? 0`).

## 6. The frequency lock loop: how the crystal is held to the dip

The frequency lock loop (FLL) is the loop of section 2 (p. 13-14, "Control Algorithm"):

```text
          ┌──────────────── frequency lock loop (an error every 0.2 s) ─────────────────────┐
          │                                                                                 │
  SC-cut crystal in an oven ──> 10 MHz output                                               │
      │   ▲                                                                                 │
      │   └── varactor <── 1 s low-pass <── FC: 22-bit DAC <── FC += gain (GA) x error      │
      │                                                                              ▲      │
      └──> synthesizer x19 ──> 6.834 GHz, swept over 300 Hz at 70 Hz ──> physics package ──> photocell
                                                                     ▲                 70 Hz detection
                                                       field coil <── SF               (sign: which side of the dip)
```

1. **Asking which side of the dip.** The loop needs a number that is zero when the microwaves sit exactly at the bottom of the dip, negative on one side and positive on the other. The light level alone cannot give it: the dip is symmetric, so a frequency too low and one too high dim the light alike, and the lamp's own brightness drifts. So the synthesizer moves the microwave frequency back and forth around its mean, 70 times a second over about 300 Hz (p. 13), and the microcontroller watches how the light follows:

   ```text
     light
      │╲                          ╱
      │ ╲   A                    ╱     A: mean on the dip's left slope
      │  ╲ ←→                C  ╱      B: mean at the bottom
      │   ╲                 ←→ ╱       C: mean on the right slope
      │    ╲       B          ╱
      │     ╲     ←→         ╱
      │      ╲______________╱
      └───────────────────────────────> microwave frequency
                atoms' frequency
   ```

   - At A, a swing to the right goes down the slope, so the light dims; a swing to the left brightens it. The light wiggles 70 times a second, against the frequency's wiggle.
   - At C the light wiggles 70 times a second with the frequency's wiggle.
   - At B both swings climb out of the dip, so the light brightens twice per wiggle: it varies at 140 Hz, with nothing at 70 Hz.

   So the light's 70 Hz part grows with the distance from the bottom (in proportion, near it), its phase against the wiggle says which side, and it is zero at the bottom. To measure it with its sign, the microcontroller multiplies the digitized photocell signal by the same 70 Hz waveform it wiggles with and averages ("synchronous detection"): in phase (C) the product is always positive, against it (A) always negative, and at B, or for noise, it averages to about zero. It averages over 14 wiggles, 0.2 s, a whole number of periods of 50 Hz, 60 Hz and the coil's 5 Hz switching, so these average out too (p. 13-14). The result is the error: how far, and which way, the crystal is off. (`DS?` returns it, with the size of the 140 Hz part, which says how deep the dip is.)
2. **The correction.** The firmware adds the error, times a gain, to FC. The error comes once per 0.2 s frame, but FC changed between most of our reads 0.1 s apart (78 % at GA 7, 89 % at GA 8), so the firmware moves FC more often than once a frame; how is not known (measured, [FLL report](../results/fs725-fll-20261105/REPORT.md)). FC keeps moving as long as the error lasts, so inside the loop the manual's "first-order digital filter" (p. 14) acts as an integrator. On B's unit FC's rate is 0.95 per s times the error at GA 7 and 2.2 per s at GA 8, the error counted in units of FC's final answer to it (measured, [FLL report](../results/fs725-fll-20261105/REPORT.md) section 5.1).
3. **FC, the crystal's tuning.** FC ("frequency control", p. 24-25) is the voltage on a varactor, a capacitor tuned by voltage, in the crystal's circuit, made by two 12-bit DACs added at 1000:1 into one 22-bit value. The manual gives the fine DAC's step as 1.5 parts in 10¹² (p. 24) and as about 1 part in 10¹² (p. 47); on B's unit it is 0.97 (measured, [FLL report](../results/fs725-fll-20261105/REPORT.md) section 5.1). The DAC's voltage reaches the varactor only through a low-pass filter of 1 s time constant, which keeps the DAC's noise off the crystal ("Circuit Descriptions", p. 47), so the crystal follows FC about 1 s late (fitted on B's unit: 1.0-1.25 s). FC is the crystal's correction as the loop applies it.
4. **GA, the loop's speed** (p. 26-27). GA sets the filter's gain and with it the loop's time constant, the time the loop takes to remove 67 % of a frequency error:

| GA | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| time constant | infinite (loop off) | 128 s | 64 s | 32 s | 16 s | 8 s | 4 s | 2 s | 1 s | 0.5 s | 0.25 s |
| noise bandwidth | 0 | 0.002 Hz | 0.004 | 0.008 | 0.016 | 0.032 | 0.064 | 0.128 | 0.256 | 0.512 | 1.024 Hz |

The firmware uses GA 8 to lock after a restart and GA 7 in normal operation; B's unit reads GA 7 (measured, 2026-11-04). `GA` without `!` holds until the next restart (`rf_clock.fs725 gain`). On B's unit the integrator's gain grew 2.4 times from GA 7 to GA 8 (measured, [FLL report](../results/fs725-fll-20261105/REPORT.md)).

**What the loop does to the noise.** The error is measured on the crystal behind the 1 s filter, so the loop is an integrator around a lag: a second-order loop, and an underdamped one (damping ratio 0.49 at GA 7 and 0.31 at GA 8, from the loop fitted on B's unit). On periods longer than about 10 s (GA 7) the loop removes the crystal's wander and the output follows the atoms. Near the loop's natural period it overshoots, so there it amplifies the crystal's noise, by up to 1.7 times near 6 s at GA 7 and 2.6 times near 4.5 s at GA 8. On shorter periods the crystal's own noise passes. The detector's noise (the photocell's shot noise) passes on long periods and is amplified near the same period. A faster loop (higher GA) follows the atoms sooner, but moves the peak to shorter periods and raises it (computed; [FLL report](../results/fs725-fll-20261105/REPORT.md) section 3).

## 7. The 1PPS output

The 1PPS output is made from the 10 MHz in three stages (p. 51 "1PPS Output", p. 56-57 "1PPS Output Pulse Delay"):

```text
  CPU timer (400 ns steps) ──> re-timed to the 10 MHz, one of four 100 ns phases ──> analog delay ──> 1PPS out (10 µs pulse)
                                                                                  │
                         capacitor ramp, comparator at 9.0 V; ramp level set by an 8-bit DAC (about 0.5 ns steps)
```

1. The microcontroller's timer starts the pulse to 400 ns; logic re-times it to the 10 MHz and picks one of four phases 100 ns apart.
2. An analog delay adds the last part: a capacitor is discharged through a comparator's threshold at 9.0 V, from a starting level that an 8-bit DAC sets, so the crossing comes later or earlier in steps of about 0.5 ns. Together the three stages place the pulse with "accuracy, resolution, and differential non-linearity of about 1 ns".
3. PP ("place pulse") sets this delay in 1 ns units; a move later by m ns is PP 10⁹ − m.

This explains what the M5 report measured on the 1PPS (inferred links):

- A PP missed its move by up to 0.23 ns (151 ps rms), the same error for the same setting, a move and its way back returning within 16 ps ([M5 report](../results/m5-v2-20261103/REPORT.md) section 9.6): the analog delay's DAC and ramp are not exactly linear, so each setting has its own fixed error, well inside the manual's 1 ns.
- The 1PPS jitters by 22-34 ps against its own 10 MHz (M5 report section 9.6; 44 ps with another scope setting, [output PPS report](../results/pps-out-20261104/REPORT.md) section 4): the pulse's last stage is a slow capacitor ramp through a comparator, whose noise moves the crossing.
- The pulse is 10 µs long (as seen in M2, `plan/checklist.md` section 5).

The output PPS (P10, `plan/pps_output.md`) avoids all three: the FS725's 1PPS only picks which tick starts the node's second, and the PL makes the pulse from the converters' clocks.

## 8. The 1PPS input and the phase lock

The FS725 can also lock its 1PPS to an external 1 pps on its rear input (p. 15 onward): it time-tags the input (TT, 1 ns), and a slow phase-lock loop, with a natural time constant of minutes to hours, then sets SF itself. While that lock is on, SF commands are ignored. RF-clock does not use it: B's unit has nothing on its rear input and the lock off (`PL? 0`, measured), because the link steers SF instead (`rf_clock.fs725`).

## 9. What this means for the steering

The steering of B (`rf_clock.twtt.steer`, plan section 7.1) acts through SF, so it sees the FLL:

1. SF moves the dip at once; the crystal, and with it the 10 MHz and the tick, follows through the FLL: the integrator and the 1 s filter put theta's ramp the dead time plus 1/k after the command, 1.05-1.31 s at GA 7 and 0.54-0.60 s at GA 8, where the ramp fits of theta give 1.14-1.16 s and 0.52-0.72 s (measured, [FLL report](../results/fs725-fll-20261105/REPORT.md) section 5.1). This is the 1.1-1.6 s of the [M5 report](../results/m5-v2-20261103/REPORT.md) section 5.
2. The steering models this as a dead time of 1.9 s after the second that asked for it. A lag model (`--steer-response-lag`) leaves the same error in the replay, so the dead time is enough (FLL report section 5.4).
3. GA 8 halves the delay. Replayed on the GA 7 record, it lowers the steering error by 2-5 ps (FLL report section 5.4).
4. The two units' relative frequency wanders in a band of 5-9 s periods (M5 report section 4). B's loop puts about 9 ps/s into theta's 19 ps/s second-to-second rate, and setting B to GA 8 did not change theta's spectrum (measured, FLL report section 5.2); that the rest comes from A's loop, which amplifies near 6 s at GA 7, is inferred.

## 10. What can be read over RS-232

The commands below are queries and change nothing (p. 6-7, 24-27); B's unit answered them on 2026-11-04 (measured):

| Query | Reads | B's unit, 2026-11-04 |
| --- | --- | --- |
| `GA?` | the FLL's gain (section 6) | 7 |
| `LO?` | FLL running (1) or stopped (0) | 1 |
| `FC?` | the crystal's tuning, two DACs (high, low); the low step is 0.97 × 10⁻¹² on B's unit | 1675,1618 → 1675,1606 within 5 s; 157 reads in 5 s |
| `DS?` | the 70 Hz error and the 140 Hz signal strength, one modulation cycle each (noisy) | −5,942; 25,954; 11,953 … |
| `SF?` | the frequency offset (section 5) | 3 |
| `MR?` | the magnetic field DAC | 2612 |
| `PL?` | the 1 pps phase lock | 0 |
| `ST?` | six status bytes | 0,0,0,0,129,0 |

FC can be read about 30 times a second (`FS725.frequency_control()`; `m4c_link --fc-every` logs it). Logged beside theta ([FLL report](../results/fs725-fll-20261105/REPORT.md) section 5.2), FC itself does not show in theta: its slow wander is the loop cancelling the crystal's own drift. Its rate does: divided by the loop's gain it is the loop's error, B's frequency against its rubidium plus the detector's noise, and it accounts for about 9 of theta's 19 ps/s second-to-second rate.
