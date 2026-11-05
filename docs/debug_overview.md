# Debugging overview: problems found along the 10 MHz distribution, and what changed

This is the reference version; `docs/debug_overview_zh.md` is the Chinese translation.

Every problem found on the bench from the first bring-up (2026-10-13) to rf-clock 1.3.0.10 (2026-10-25), in the order the FS725's 10 MHz is distributed: the bench, the LMK04828, then its three branches (PL_CLK into the FPGA, the 480 MHz references through the LMX2594 into the RFDC, and SYSREF into both), then the RF path between the boards and the measurements on top.  The PPS joins the chain where the FPGA captures it.  Each section opens with a diagram of the module, on which tags such as `[K2]` mark where a problem sat; the entries below it give what was seen, the cause, the change and how it was checked.  This is an index: the full cause-to-measurement chains, with each link marked measured or inferred, are in the linked reports (`results/`, kept outside Git) and in `plan/checklist.md`, which also holds the evidence of every closed item.

## 0. The chain at a glance

```text
bench
  FS725 10 MHz ──> EPG1 REF IN;  EPG1 REF OUT (10 MHz square) ──> splitter ──> each REF SMA       [B1][B2]
  FS725 1PPS ───> EPG1 TRIG IN ──> retimed, delayed ──> EPG1 outputs 1, 2 ──> each J7             [B3][B4]

each board
  REF SMA ──> LMK04828: PLL1 ──> VCXO 160 MHz ──> PLL2 ──> VCO 2400 MHz                           [K1][K3]
              └─ dividers, all counting that VCO                                                  [K2]
                 ├─ ÷24  ──> PL_CLK 100 MHz ──> FPGA: MMCM ──> fabric clock                       [F1][F2][F3]
                 │                    fabric clock ──> raw_tick; PPS capture <── J7               [F4][F5]
                 │                    fabric clock ──> TX buffer, RX capture, AXIS                [F7]-[F9]
                 ├─ ÷480 ──> SYSREF 5 MHz ──> RFDC tiles: SYSREF capture, MTS                     [R1]-[R4]
                 │                        └─> FPGA PL_SYSREF                                      [F6]
                 ├─ ÷5   ──> 480 MHz ──> LMX2594 ──> 480 MHz ──> ADC tile PLLs ──> 4.8 GS/s       [X1][X2][X3]
                 └─ ÷5   ──> 480 MHz ──> LMX2594 ──> 480 MHz ──> DAC tile PLLs ──> 9.6 GS/s
                                                             RFDC data path, NCOs                 [R5]-[R7]

RF path
  DAC_A (5.8 GHz and 3.8 GHz) ──> band-pass ──> attenuator ──> ADC_B of the other board           [A1]-[A3]
```

| Tag | Module | Problem | Size | Change | Since | Status |
|---|---|---|---|---|---|---|
| B1 | bench | resistive splitter left 0.7 Vpp at each board, LMK fell into holdover | tick −0.1 ppm | 10 MHz through the EPG1's REF OUT | 2026-10-16 | fixed |
| B2 | bench | EPG1 not locked: its retiming walked the PPS across a tick | ±1 tick | EPG1 REF IN on the FS725 10 MHz | 2026-10-16 | fixed |
| B3 | bench | EPG1 trigger threshold at its own clock edge | 2.5 ns, half the pulses | threshold 1.0 → 1.3 V | 2026-10-19 | fixed |
| B4 | bench | which output feeds which board | wiring | outputs checked by delaying them; output 2 +6.6 ns for F5 | 2026-10-18 | fixed; recheck after rewiring |
| K1 | LMK04828 | PLL1 not in zero-delay mode | tick phase not tied to the 10 MHz | nested zero delay | 1.2.1.1 | fixed |
| K2 | LMK04828 | dividers not synchronized after PLL2 starts | 4-5 VCO periods (1.67-2.08 ns), 0.4 % of power-ups | data-sheet SYNC | 1.3.0.9 | fixed |
| K3 | LMK04828 | clocks written before our overlay hung the system | hang | overlay first, then clocks | 2026-10-13 | fixed |
| F1 | FPGA | PL_CLK/PL_SYSREF received single-ended | MMCM no lock | LVDS inputs (run4) | 2026-10-13 | fixed |
| F2 | FPGA | MMCM not locking after a clock change | no fabric clock | `pl_resetn0` pulse | 2026-10-13 | fixed |
| F3 | FPGA | PYNQ's base overlay replacing ours a minute after boot | design gone | wait for boot to finish | 2026-10-14 | fixed |
| F4 | FPGA | no PPS edges | no PPS | cable on J7; pull-up, second input, `PPS_CONFIG` (run3) | 2026-10-13 | fixed |
| F5 | FPGA | 887329260072A's PPS edge on a tick sampling edge | 1 tick (10 ns) | EPG1 output 2 delayed 6.6 ns | 2026-10-18 | fixed; drift to watch |
| F6 | FPGA | PL_SYSREF input timing copied from a 500 MHz design | — | none | — | not checked |
| F7 | FPGA | TX beats packed planar, DAC reads interleaved | scrambled waveform | `TX_LAYOUT` interleaved | 2026-10-14 | fixed |
| F8 | FPGA | 69 632-beat TX buffer not inferred as URAM | 548 BRAM36 | 17 banks of 4096 beats (run8) | 2026-10-14 | fixed |
| F9 | FPGA | an arm reaching the PL too late stopped M2 | M2 aborted | missed burst counted | 1.3.0.3 | fixed |
| X1 | LMX2594 | channel divider started on any VCO edge | 26 ps lattice (52 ps on the DAC) | phase SYNC | 1.3.0.4 | fixed |
| X2 | LMX2594 | VCO band recalibrated at every clock write | 8-10 ps per band | band forced from the board config | 1.3.0.6 | fixed; lock at other temperatures open |
| X3 | LMX2594 | device tree names the two chips the wrong way round | labels only | experiments relabeled | 2026-10-20 | noted |
| R1 | RFDC | MTS latency changes with the bitstream | 2 samples | fixed target latencies | 2026-10-14 | fixed |
| R2 | RFDC | SYSREF capture window picked anew at each power-up | 1 DAC sample (104 ps) | windows recorded per board | 1.3.0.8 | fixed |
| R3 | RFDC | K2's state seen only as other MTS offsets | — | offsets recorded and checked | 1.3.0.9 | fixed |
| R4 | RFDC | no margin left by K2's fix | would fail MTS | DAC target 1896 → 1944 | 1.3.0.10 | fixed |
| R5 | RFDC | NCOs started at an arbitrary instant | 120° steps of the carrier | NCO restart at a SYSREF edge | 1.3.0.5 | fixed |
| R6 | RFDC | MTS needs channel 0 of every tile; distributed clock limits the DAC | build fails or 7 GS/s | tiles 0 and 2, own PLLs | 2026-10-13 | design |
| R7 | RFDC | PYNQ's `xrfdc` has no MTS in Python | — | cffi to `libxrfdc.so` | 2026-10-13 | design |
| A1 | RF path | DAC's 3.8 GHz component folds onto 5.8 GHz in the ADC | theta per run 1.5 ps with the filters, 120-150 ps without | band-pass filter in each cross path | design | filters needed |
| A2 | RF path | envelope timing moves against the carrier phase | theta +37-59 ps after power cycles | none yet | — | open |
| A3 | RF path | ADC input connector; DAC_A polarity | — | ADC_B (`vin2_01`); DAC_A inverted to I | 2026-10-14/19 | noted |
| M1 | measurement | scope reading depended on the carrier phase | up to 10 ns | baseband square wave, then envelope timing | 2026-10-14/16 | fixed |
| M2 | measurement | scope math range and remote interface traps | wrong delays, wedged USB | handled in the scripts | 2026-10-16/17 | fixed |
| M3 | measurement | M3 loopback amplitude too low | 4-6 ps per run | amplitude 16000 | 1.3.0.5 | fixed |
| E1 | environment | each board's clocks lag as it warms | 1-1.5 ps/K | die temperature recorded | 1.3.0.9 | open |
| E2 | environment | m_AB 0.6 ns higher for an hour, once | 0.6 ns | none | — | unexplained |

## 1. Bench: FS725 → EPG1 → boards

The FS725 is the only reference.  Its 10 MHz reaches the boards through the EPG1, which locks to it and puts out a square wave; its 1PPS reaches them through the EPG1 too, which retimes it and can delay each output (`plan/checklist.md` section 6, `experiments/epg1.py`).

```text
  FS725
   ├─ 10 MHz out 1 (sine) ──> EPG1 REF IN ──> EPG1 clock, locked (2.5 ns steps)                   [B2]
   ├─ 10 MHz out 2 (sine) ··· not used: through the splitter 0.7 Vpp at each board                [B1]
   └─ 1PPS (2.5 V, 10 µs) ──> EPG1 TRIG IN ──> threshold ──> retimed (EPG1 clock)                 [B3]
                                                             └─> delay per output ──> out 1, 2, 4 [B4]

  EPG1 REF OUT (10 MHz square) ──> resistive splitter ┬─> REF SMA of 887329260072A
                                                      └─> REF SMA of 887329260101
  EPG1 output 1 ──> J7 of 887329260101
  EPG1 output 2 (+6.6 ns) ──> J7 of 887329260072A
  EPG1 output 4 ──> scope C1 (trigger)
```

**[B1] The 10 MHz too weak after the splitter.**
- Seen: with the FS725's sine split resistively, PLL1 kept losing lock and the LMK04828 fell into holdover; the tick ran about −0.1 ppm (periods of exactly 99 999 990 ticks on one board, 0/−10 alternating on the other).
- Cause: the splitter's 6 dB loss left about 0.7 Vpp of slow sine at each board's CLKin0.
- Change: the EPG1's locked 10 MHz square output (REF OUT) feeds the splitter.
- Check: M1 on both boards, 120 s, every period 100 000 000, jitter 0.

**[B2] The PPS walking across a tick.**
- Seen: periods of 99 999 999 / 100 000 000 / 100 000 001 ticks and jitter counts on M1.
- Cause: the EPG1 resamples TRIG IN on its own clock (2.5 ns steps); unlocked, that clock drifts against the boards' ticks.
- Change: EPG1 REF IN on the FS725 10 MHz (`REFCLK:IN:STATUS` = 1).

**[B3] The EPG1 trigger threshold** (2026-10-19).
- Seen: half of all EPG1 pulses 2.5 ns early; scope trigger jitter 1.2 ns rms.
- Cause: at `TRIGIN:THRESH` 1.0 V the slow FS725 1PPS edge crossed the threshold right at an EPG1 clock edge.
- Change: threshold 1.3 V (about 1.25 ns from either clock edge): 19 ps rms.

**[B4] Which output feeds which board.**  Found by delaying each output by 50 ns and watching which board's PPS_TICK moved (2026-10-18; earlier notes had it reversed).  Output 2 carries the 6.6 ns delay of F5.  The two PPS cables were swapped once after the boards went into their cases (2026-10-21, put back), and the PPS cable was not on J7 at the first bring-up (F4).  After any rewiring, run M1 and a short M4 before trusting a measurement.

## 2. LMK04828

The LMK04828 cleans the 10 MHz with PLL1 and its 160 MHz VCXO, multiplies it to 2400 MHz with PLL2, and divides that one VCO into PL_CLK (÷24), SYSREF (÷480) and the two 480 MHz LMX references (÷5) (`src/rf_clock/clocks/files/README.md`).  Every output's phase against the others is a fixed delay plus a whole number of VCO periods (416.7 ps), set by when its divider started.

```text
                    PLL1                                     PLL2
  CLKin0 ──> ÷12 ──> PFD ──> loop ──> VCXO 160 MHz ──> ÷2 ──> PFD ──> loop ──> VCO 2400 MHz ──┐
  10 MHz              ^ 833 kHz                                ^ 80 MHz                       │
  [B1]                │                                        └── ÷30 <──────────────────────┤
                      └── ÷120 <── FB_MUX <── DCLKout8  [K1]                                  │
                                                 ^                                            │
  dividers, each counting the VCO <──────────────┼────────────────────────────────────────────┘
    ÷24  ──> DCLKout8 = PL_CLK 100 MHz ──────────┴──> FPGA (section 3)
    ÷480 ──> SYSREF 5 MHz ──> RFDC tiles (section 5), FPGA PL_SYSREF (section 3)
    ÷5   ──> 480 MHz ──> LMX2594 of the ADC tiles (section 4)
    ÷5   ──> 480 MHz ──> LMX2594 of the DAC tiles (section 4)
  each divider's reset: one shared line, a switch per divider (figure: LMK report, section 2)     [K2]
  SPI: xrfclk writes the register file in address order, after our overlay is loaded              [K2][K3]
```

**[K1] PLL1 not in zero-delay mode** (rf-clock 1.2.1.1, 2026-10-14).
- Seen: the PPS-to-burst delay on the scope differed by up to about one tick between clock writes.
- Cause: 0x13F had FB_MUX_EN = 0, so PLL1 locked the VCXO, not DCLKout8, to the 10 MHz, and the ÷24 divider could start on any of 24 VCO edges: the tick could take one of 24 phases 0.42 ns apart against the 10 MHz and the PPS.  The scope reading was later found to depend on the carrier phase (M1), so that the phase did change was not proven.
- Change: nested zero delay (0x13F = 0x0B, PLL1_N 120): PLL1 compares DCLKout8 itself with the 10 MHz.  `PL.open()` writes the packaged clock files itself.
- Check: 5 restarts at 180.00 ns on the scope (baseband square wave).

**[K2] Dividers not synchronized after PLL2 starts** (rf-clock 1.3.0.9; [report](../results/lmk-divider-sync-20261025/REPORT.md)).
- Seen: in about 0.4 % of power-ups one board's converters came up 4 or 5 VCO periods (1.667 or 2.083 ns) late against its PPS tick, transmitting and receiving alike; theta stepped by ±1.67 or ±2.08 ns; MTS showed other offsets but the same SYSREF capture codes (R3).
- Cause: the register file, written in address order, opens every divider's reset switch before it programs PLL2, and nothing synchronizes the dividers afterwards.  Zero delay (K1) holds only DCLKout8 on the 10 MHz; SYSREF and the LMX references moved against it, and MTS moved the converters with SYSREF.
- Change: after the file, the data sheet's SYNC: the dividers' shared reset line switched from the SYSREF to the SYNC, every divider connected to it, the line raised and dropped from software, then the file's settings (diagram in the report, section 2).  A first version left the line on the SYSREF, so the SYNC never reached the dividers.
- Check: 1200 power-ups each, back to back: 10 jumps without, 0 with (p = 0.001); one state on both boards.
- Side effect: the new state is 10 VCO periods (4.16 ns) earlier against the PPS on both boards, which the two-way theta cannot see; the DAC target follows in R4.

**[K3] Clocks written before our overlay hung the board** (2026-10-13).
- Seen: the first clock write hung the whole system within about 4 s; another attempt did not hang but the MMCM did not lock.
- Cause: not found; PYNQ's base overlay was still in the PL and runs on LMK clocks.
- Change: load our overlay first, then write the clocks, then pulse `pl_resetn0` (ABI §7.1 steps 1-4).  A hung board is recovered over JTAG (`tools/jtag_reset_board.tcl`).

## 3. PL_CLK into the FPGA: fabric clock, PPS capture, data path

PL_CLK and PL_SYSREF enter the FPGA as LVDS pairs.  The MMCM turns PL_CLK into the fabric clock, which counts the tick, captures the PPS, plays TX bursts and captures RX windows at target ticks, and clocks the RFDC's AXIS side.

```text
  FPGA: our bitstream, loaded after boot finishes                                                 [F3]
  PL_CLK 100 MHz  ──> AN11/AP11, IBUFDS (LVDS) ──> MMCM ──> fabric clock 100 MHz                  [F1][F2]
  PL_SYSREF 5 MHz ──> AP18/AR18, IBUFDS (LVDS) ──> synchronizer ──> RFDC IP                       [F1][F6]
  J7 PPS ──> 49.9 Ω ──> comparator ──> level translator ──> AJ13 (FPGA pull-up)                   [F4]

  fabric clock 100 MHz
   ├─ P0  raw_tick counter (10 ns)
   ├─ P1  PPS capture: AJ13 sampled on each tick ──> PPS_TICK                                     [F5]
   ├─ P2  control: TX and RX armed by the ARM for a target tick                                   [F9]
   ├─ TX buffer (URAM, 17 banks × 4096 beats) ──> TX player ──> AXIS ──> RFDC DAC                 [F7][F8]
   └─ RFDC ADC ──> AXIS ──> RX capture ──> async FIFO ──> S2MM DMA ──> DDR
```

**[F1] PL_CLK and PL_SYSREF single-ended** (runs 2-4).
- Seen: run2 locked the MMCM only sometimes, run3 never.
- Cause: the constraints copied from RFSoC-MTS took only the P pin of each LVDS pair as LVCMOS18; with run3's pull-ups the N pin was pulled up.
- Change: run4: IBUFDS on both pairs, LVDS, 100 Ω differential termination, DQS_BIAS.

**[F2] MMCM not locking after a clock change.**  The MMCM's input changes with the clocks; without a reset it may not lock after the first load.  `PL.open()` pulses `pl_resetn0` and waits for the clock-status GPIO (ABI §7.1 steps 4-5).

**[F3] PYNQ's base overlay replacing ours.**
- Seen: once, on a cold boot, our design disappeared after loading.
- Cause: `bootpy.service` loads the base overlay about a minute after the kernel starts.
- Change: `PL.open()` first waits for `systemctl is-system-running --wait` (ABI §7.1 step 0).

**[F4] No PPS edges** (runs 2-4).
- Seen: `PPS_COUNT` = 0.
- Cause: the PPS cable was not on J7.  On the way, the input chain (LMV7235 comparator and a level translator with no pull-up on its 1.8 V side) was found to need the FPGA pull-up.
- Change: run3 added the pull-up, a second PPS input and `PPS_CONFIG` / `PPS_INPUTS` (ABI 1.1), so software chooses the input and polarity.

**[F5] 887329260072A's PPS edge on a sampling edge** (`results/pps-phase-scan-20261018_*`).
- Seen: 40-60 % of its PPS edges captured one tick early in 15-edge samples; M2 reported `actual-PPS` ±1; overnight once in about 23 000 edges.
- Cause: its PPS edge lay on a 100 MHz sampling edge; the phase moves by about 1 ns a day.
- Change: EPG1 output 2 delayed 6.6 ns (B4).  That board now captures the later tick, which is why theta is about +10 ns.
- Open: the PPS phase drift against the EPG1's ±1.25 ns retiming margin; a fan-out buffer on the FS725 1PPS would take the EPG1 out of the PPS path.

**[F6] PL_SYSREF input timing** (not checked).  Its input delay constraint (2.000/2.031 ns) was copied from RFSoC-MTS, where PL_CLK is 500 MHz.  A slip there would move the fabric side by a whole 10 ns tick; none has been seen.

**[F7] TX beat layout** (2026-10-14, ABI §4.5.8).
- Seen: the 8 lane delays of the loopback lane check differed by ⌈k/2⌉ samples.
- Cause: the DAC reads I and Q interleaved per word; TX was packed planar, so every waveform sent until then was scrambled (M2's timing, which used edges, stands).
- Change: `TX_LAYOUT` interleaved by default.

**[F8] TX buffer memory** (run7, run8).  A single 69 632-beat memory was not inferred as URAM (address-qualified write enable, depth not a power of two) and fell back to 548 BRAM36; 17 banks of 4096 beats fixed it.

**[F9] An arm too late stopped M2** (rf-clock 1.3.0.3).  A burst armed less than `MIN_LEAD_TICKS` before its target is rejected with `REJ_STALE`; when the process stalled between the PPS and the arm, M2 stopped.  It now counts a missed burst and resynchronizes.

## 4. LMX2594

Each LMX2594 multiplies its 480 MHz reference to a 7.68 GHz VCO and divides it back to 480 MHz for its tiles: one chip for the ADC tiles, one for the DAC tiles.

```text
  OSCin 480 MHz ──> pre-R ÷2 ──> R ÷5 ──> PFD 48 MHz ──> charge pump, loop ──> VCO 7.68 GHz ──┐
  (LMK ÷5)                                 ^                                   band [X2]      │
                                           └── N ÷40 <── SEG0 ÷2, SEG1 ÷2 <───────────────────┤
                                               VCO_PHASE_SYNC = 1 since 1.3.0.4  [X1]         │
                                               (before: N ÷160 counted the VCO itself)        │
  RFoutA, RFoutB 480 MHz <── SEG3 <── SEG2 <── SEG1 <── SEG0 ÷2 <─────────────────────────────┘
    │                        └─────── ÷16 in all ──────────┘
    └──> RFDC tile PLLs (section 5)
  two chips: spi0.1 clocks the ADC tiles, spi0.2 the DAC tiles (device tree swaps the names)      [X3]
```

**[X1] Channel divider started on any VCO edge** (rf-clock 1.3.0.4; [report](../results/lmx-phase-sync-20261019/REPORT.md), [loopback report](../results/lmx-sync-loopback-20261019/REPORT.md)).
- Seen: overnight, 104 reboots: each board's DAC output at one of two times 52 ps apart, unseen by MTS ([M2 report](../results/m2-restarts-20261016_212509/REPORT.md)).
- Cause: without phase SYNC the channel divider starts on any VCO edge (130.2 ps); MTS removes whole samples only, leaving positions on a 26.04 ps lattice (1/4 DAC sample, 1/8 ADC sample).
- Change: VCO_PHASE_SYNC = 1, PLL_N 40 (SEG0 and SEG1 inside the feedback), and a software SYNC after writing (TI category 2).
- Check: ten clock writes and ten reboots at one time (scope sd 2-7 ps); the M3 loopback the same to 0.10 ps over ten clock writes.

**[X2] VCO band picked anew at every clock write** (rf-clock 1.3.0.6; [loopback report](../results/lmx-sync-loopback-20261019/REPORT.md) section 5).
- Seen: after X1's fix, a rare 7.6-9.7 ps step of one converter over reboots and clock writes.
- Cause: each clock write recalibrates the VCO, which picks one of two neighbouring VCO bands at 7.68 GHz (887329260072A: ADC 117/118, DAC 122/123); one band higher moves that converter 8-10 ps later.
- Change: a per-board config (`/etc/rf-clock/board.json`, named by the board's AT24MAC402 serial number) sets each chip's band, which `PL.open()` forces, reads back and requires to lock; `rf_clock.checks.vco_bands` finds the bands and measures the band table.
- Check: within a band the loopback delay held to 0.1 ps over 2.5 h.
- Open: whether the forced bands lock at other temperatures; 887329260101's band table needs its own loopback cable.

**[X3] The device tree names the two chips the wrong way round.**  `lmxdac` (spi0.1) clocks the ADC tiles, `lmxadc` (spi0.2) the DAC tiles, found by powering each down.  rf-clock writes both alike and is not affected; the first loopback series had ADC and DAC swapped in its labels (corrected in [the loopback report](../results/lmx-sync-loopback-20261019/REPORT.md)).

## 5. RFDC tiles: sample clocks, SYSREF, MTS, NCOs

Each tile's PLL makes the sample clock from its LMX's 480 MHz.  SYSREF, from the LMK, is captured on that sample clock; MTS then measures each tile's latency through the FIFO between the fabric clock and the converter clock and adds an offset to reach a fixed target, so the converters are fixed against SYSREF.  The NCOs shift the band to and from 5.8 GHz.

```text
  DAC tile (tile 2 carries the signal; tile 0 is there for MTS)                                   [R6]
    480 MHz ──> tile PLL ×20 ──> 9.6 GHz sample clock
    SYSREF ──> captured on the sample clock at a capture code ──> marker                          [R2]
    AXIS ──> FIFO ──> interpolation ×12 ──> mixer, NCO ──> IMR high-pass ──> DAC core ──> DAC_A
              │                                     └ NCO phase restart at a SYSREF edge          [R5]
              └ MTS: latency from the marker across the FIFO, plus an offset
                (steps of 6 samples) to reach the target                                          [R1][R3][R4]

  ADC tile
    480 MHz ──> tile PLL ×10 ──> 4.8 GHz sample clock
    SYSREF ──> captured at a capture code, as on the DAC                                          [R2]
    ADC_B ──> ADC core ──> mixer, NCO -1.0 GHz ──> decimation ×6 ──> FIFO ──> AXIS
                                   └ [R5]                             └ MTS [R1][R3]

  driver: XRFdc_MultiConverter_Init/Sync in libxrfdc.so, through cffi                             [R7]
```

**[R1] Latency changes with the bitstream** (2026-10-14).
- Seen: run6's ADC latency 1248 against 1236 before; the loopback delay moved by 2.0 samples.
- Change: fixed targets DAC 1896, ADC 1296 (the user's decision); MTS fails if a tile cannot reach them.

**[R2] SYSREF capture window flipping** (rf-clock 1.3.0.8; [M4 report](../results/m4-twtt-20261021/REPORT.md) section 4).
- Seen: theta 51.3 ps lower in one run: 887329260101's DAC had come up one sample (104 ps) later; its T1 capture codes moved from 54/53 to 73/73 with the latencies unchanged.
- Cause: two candidate windows lay about equally far from code 64, the driver's target, so a few ps of clock phase picked the other.
- Change: each tile's codes recorded in the board config (`rf_clock.checks.mts_dtc record`), and MTS picks the window nearest them (the driver's reload scan); `PL.open()` warns when a code moves more than 6 from its record.
- Check: with deliberately the other windows as targets, MTS picked them.

**[R3] K2's state seen only as other offsets** (rf-clock 1.3.0.9).  When K2 put SYSREF and the converter clocks a few VCO periods off against the fabric clock, the T1 capture codes stayed (SYSREF and sample clock moved together) and only the FIFO, between fabric and converter clocks, saw it: DAC tile 0 offset 12 instead of 8, ADC tile 0 8 instead of 10.  The board config now records each tile's usual offset, and `PL.open()` warns when a power-up's differ; in every power-up with a step, the offsets were other.

**[R4] No margin after K2's fix** (rf-clock 1.3.0.10).
- Seen: in the new state the DAC tiles measure 1896 before alignment, the target itself (offset 0): a tile that measured more would fail MTS (`TARGET_LOW`).
- Change: DAC target 1944, 8 steps above, as before; the DAC output moves 5 ns later (scope +5.012/+4.998 ns), 0.83-0.85 ns later than before 1.3.0.9 in all.  Offsets recorded again: DAC 8/8, ADC 12/12.
- Open: absolute delays against the PPS are to be measured again, with the cables and scope channels calibrated.

**[R5] NCOs in one of three phases** (rf-clock 1.3.0.5; [loopback report](../results/lmx-sync-loopback-20261019/REPORT.md) section 4).
- Seen: after each `PL.open()` the DAC and ADC NCOs each started in one of three phases 120° apart (carrier phase only; timing unchanged).
- Cause: the IP starts the NCOs on a tile event at an arbitrary instant.
- Change: both restarted at one SYSREF edge after MTS (PG269 Use Case 1).
- Check: one phase over 38 M3 runs (35 after a reboot), over M4 power-ups and reboots, and over five power cycles.
- Open, not needed: which tile clock (300 or 600 MHz) gives the three positions.

**[R6] What MTS needs from the build** (`plan/checklist.md` section 3).  MTS is enabled only if channel 0 of every participating tile is enabled; a DAC tile in on-chip clock distribution is limited to 7.0 GS/s.  Hence ADC and DAC tiles 0 and 2, each on its own PLL, DAC 9.6 GS/s, tile 0 for MTS; since 2.1 DAC tile 0 also sends the output PPS on DAC_B (AGENTS.md).

**[R7] MTS through PYNQ.**  PYNQ 3.1.1's `xrfdc` has no MTS in Python; `src/rf_clock/pl/rfdc.py` calls `XRFdc_MultiConverter_Init/Sync` in `libxrfdc.so` through cffi.

## 6. RF path between the boards

```text
  887329260072A DAC_A ──> ZVBP-5800-S+ ──> 10 dB ──> 887329260101 ADC_B   (AB)
  887329260101 DAC_A  ──> ZVBP-5800-S+ ──> 20 dB ──> 887329260072A ADC_B  (BA)

  DAC_A, mix-mode:   3.8 GHz about as strong as 5.8 GHz; band-pass 5725-5875 MHz                  [A1]
  ADC at 4.8 GS/s:   5.8 - 4.8 = 1.0 GHz and 4.8 - 3.8 = 1.0 GHz, in the same form                [A1]
  across the burst:  group delay ±0.5 ns (AB), ±0.9 ns (BA)                                       [A2]
```

**[A1] The DAC's 3.8 GHz component** ([BPF report](../results/bpf-compare-20261022/REPORT.md), [DAC spectrum](../results/dac-spectrum-20261017/REPORT.md)).
- Seen: without the band-pass filters the BA direction's PSL fell from about 21 to 10-11 dB and theta scattered by 118-148 ps per run, against 1.5 ps with them.
- Cause: in mix-mode the DAC puts out 3.8 GHz about as strongly as 5.8 GHz (−12.7 / −13.7 dBm at full scale), and the ADC at 4.8 GS/s folds both onto 1.0 GHz in the same form, the conjugate of the transmitted waveform, so the correlator takes the 3.8 GHz copy for the signal ([BPF report](../results/bpf-compare-20261022/REPORT.md) section 4).
- Change: one band-pass filter (ZVBP-5800-S+, 94 dB at 3.8 GHz) in each cross path.  A cheaper wideband filter (VBFZ-5500-S+) would need about 63 dB at 3.8 GHz for 1 ps; not adopted.

**[A2] Envelope against carrier phase** (open).
- Seen: after five power cycles (2026-10-22) the envelope theta was 37-59 ps higher while the carrier theta stayed within ±6 ps, mostly in m_AB (+58 to +92 ps), and it did not go back over 3 hours as the boards warmed.  Between 14:30 and 23:09 the same day both directions' delays from the waveform's shape grew (envelope peak +52/+49 ps, group delay +36/+37 ps) with the carrier unchanged, with no measurement in between.
- What is known: the channel is far from a pure delay: across the burst its group delay varies by about ±0.5 ns (AB) and ±0.9 ns (BA, like an echo near 6.7 ns), so small changes of the channel's shape move the envelope tens of ps while the carrier phase stays (`results/m4-twtt-pair-20261022_142951`, `experiments/m4_channel.py`).  Where the shape changes is not known.
- Consequence: the carrier phase, which R5 makes usable across power-ups, is the timing to rely on below about 50 ps.

**[A3] Connectors and polarity.**  The ADC input moved from `vin2_23` to `vin2_01`, which is the ADC_B connector (checked with a cable on 2026-10-19).  DAC_A's output is inverted relative to I (scope, 2026-10-14).

## 7. Measurement and environment

```text
  EPG1 output 4 (PPS) ──> C1, trigger
  887329260072A DAC_A ──> C2 ──> F1 = Filter(Square(C2)) ──> P3 = Delay at 50 %, C1 to F1         [M1][M2]
  887329260101 DAC_A  ──> C3 ──> F2 = Filter(Square(C3)) ──> P4 = Delay at 50 %, C1 to F2
```

**[M1] Scope readings that depended on the carrier phase** (M2).  The first method read the "Nth peak" of a 3.8 GHz burst after resetting the NCO each run, so the reading moved within a carrier period (up to 10 ns).  It was replaced by a baseband square wave (2026-10-14), then by the envelope Filter(Square(C)) at 50 % (2026-10-16).

**[M2] Scope traps.**  The LeCroy's math traces keep the range of their first computation (clipped traces gave wrong delays), and its remote interface wedges under pyvisa-py; both are handled in `experiments/m2_restarts.py` and `tanglab_instruments.HDO9404`.

**[M3] Loopback amplitude** (M3, rf-clock 1.3.0.5).  At TX amplitude 4096 the envelope delay scattered 4-6 ps per run; at 16000 about 1 ps, now the default.

**[E1] Temperature** ([M4 report](../results/m4-twtt-20261021/REPORT.md) section 5).  Each board's clocks lag as its die warms, by roughly 1-1.5 ps/K, so theta rises with 887329260072A's die temperature and falls with 887329260101's (theta = k_A·T_A + k_B·T_B + C).  Where in the clock chain is inferred, not measured.  The die temperature is logged every second since rf-clock 1.3.0.9.  Open: each board's own slope from a temperature step of one board at a time.

**[E2] Unexplained, once.**  In a power-up series without the K2 fix, m_AB lay 0.60 ns above its median for 110 runs (2026-10-24 01:39-02:43, filters off) with every MTS offset usual, and not by a whole number of VCO periods ([LMK report](../results/lmk-divider-sync-20261025/REPORT.md) section 7).

## 8. Still open

1. Absolute delays against the PPS with rf-clock 1.3.0.10 (R4): cable and scope-channel calibration (swap method, needs an SMA tee or splitter), then each board's DAC output against its PPS.
2. The zero-baseline constant, and Delta, with the band-pass filters back; Delta also needs each board's PPS sub-tick phase.
3. Envelope against carrier phase (A2): where the channel's shape changes; H(f) over power cycles.
4. Temperature slopes per board (E1).
5. PPS distribution without the EPG1's retiming (F5).
6. The forced VCO bands at other temperatures, and 887329260101's band table (X2).
7. Not yet checked: the PL_SYSREF input timing (F6), and the DAC's 5.8 GHz output and the ADC's reception on a calibrated instrument (`plan/checklist.md` section 4).
