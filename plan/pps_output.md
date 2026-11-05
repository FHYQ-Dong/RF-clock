# Proposal: an output PPS made by the PL from its tick

This is the reference version; `plan/pps_output_zh.md` is the Chinese translation.  Implemented in rf-clock 2.1.0.0 as module P10 (`plan/RF_clock_modules.md` section 6, registers `docs/ABI.md` section 4.13); the decisions are in section 7, what was built and where it differs from the proposal in section 8.  The bench test of section 5 is still open.

## 1. Problem

The follower's PPS is meant to follow the master's (M5, plan section 9).  Today each node's PPS is the 1PPS output of its FS725, and two measurements of 2026-11-04 (`results/m5-v2-20261103/REPORT.md` sections 9.5 and 9.6) show what that costs:

1. The two-way link aligns each node's time scale, which starts at the tick that latched the node's PPS: T = P + ℓ + d (P the edge at the FS725 output, ℓ the cable and input path, d the edge's lead on the tick that latches it).  Steering holds θ, which compares T_A and T_B; the PPS edges P follow only as long as ℓ and d stay put (measured: PPS_B − PPS_A moved with θ at slope −1.1).
2. The acquisition moves B's FS725 1PPS with PP.  PPS_TICK, and with it T, moves by exactly the whole ticks asked for, but the 1PPS moves by what the PP realizes, which misses its move by up to 0.23 ns (151 ps rms; measured against the unit's own 10 MHz).  The error goes into d_B, so the calibrated constant P_B − P_A at a given θ changes with every PP (measured: −145 ps between two runs).
3. The FS725's 1PPS itself jitters against its own 10 MHz by 22 ps (A's unit) and 34 ps (B's) from second to second (measured), while θ, made from the converters' clocks, follows the 10 MHz to a few ps.

An output edge made from T itself is free of both: it follows the time scale that θ aligns, so no PP error enters it, and it is timed by the converters' clocks, not by the FS725's 1PPS circuit.  The FS725's 1PPS stays what it is today, the input that starts the time scale.

## 2. Mechanism

```text
FS725 1PPS ──> J7 ──> P1: PPS_TICK(k) ─────────────┐
FS725 10 MHz ──> LMK04828 ──> fabric clock: raw_tick ┴─> P10: at raw_tick = PPS_TICK(k) + OFFSET
                         └──> LMX2594 ──> DAC tile 0 sample clock            │ beats of a step on DAC tile 0's stream
                                                       └──> DAC_B ──> 100 nF ──> balun ──> 100 nF ──> J20 (SMA)
```

1. P1 latches each PPS edge on the tick: PPS_TICK(k), the start of second k of the node's time scale.  While the PPS is locked every period is exactly 100 000 000 ticks (P1 checks each one).
2. P10 counts the ticks of second k from the edge that started it: a locked edge sets its counter to raw_tick − PPS_TICK(k), and at raw_tick = PPS_TICK(k) + OFFSET it starts a step on the stream of DAC tile 0.  Between edges the counter counts on and wraps every 100 000 000 ticks, so if one PPS edge is missing or wrong (P1 then drops the lock and the edge sets nothing), the output goes on from the last good edge in steps of 100 000 000 ticks (the flywheel; counted).  The edge reaches P10 three ticks after the tick that latched it, so OFFSET is at least 8 (section 8).
3. The step starts at sample SAMPLE (0 to 7) of its beat, so the output edge lies at T(k) + OFFSET × 10 ns + SAMPLE × 1.25 ns + L, where L is the latency from the PL stream to the DAC's pins.  MTS fixes L at every power-up (the same mechanism keeps DAC_A's bursts at one time against the PPS to about 10 ps over power-ups, `results/m2-restarts-20261025_162709`).
4. DAC tile 0 (block 0) exists today only for MTS: it is configured like tile 2 (9.6 GS/s, NCO 3.8 GHz, mix-mode) and its stream is tied to zero (`PL/bd/create_rf_clock_bd.tcl`).  Since 2.1 its stream comes from P10, and `PL.open()` switches it to baseband after MTS, mixer bypassed, IMR low-pass, Nyquist zone 1 (`rf_clock.pl.rfdc.baseband_dac`, which `rf_clock.checks.m2_tx_timing --scope-nco 0` also uses for tile 2); the DAC then outputs the stream's I samples, and a step rises in about a nanosecond (the interpolation filters' bandwidth, inferred).
5. Output network (RFSoC 4x2 schematic, `docs/rfsoc4x2_sch.pdf` sheet 28 "RF DAC"): DAC_VOUT0_228 → 100 nF in each leg → MABA-011118 balun (T5) → 100 nF → J20, labelled DAC_B.  The path is AC-coupled: edges pass (DAC_A's 25 MHz square wave of M2 had clean edges), a flat top of microseconds does not, so the output is a short pulse, a few tens of ns.  DAC_A's output was found inverted against I (checklist section 5); DAC_B is presumably alike.
6. The output edge's time against the node's time scale is T + OFFSET + D_board, with D_board = L plus the analog path: a board constant, measured once (scope or time-interval counter) and stored in the board config.  Its jitter is that of the converters' clocks: θ's signal-independent part of 0.72 ps per pair holds four converters and the reference (report section 7), about 0.5 ps per converter (inferred).

## 3. Other places an output could leave the board

| Path | What is between the PL and the connector | For a precision PPS |
| --- | --- | --- |
| DAC_B, J20 (sheet 28 "RF DAC") | RF DAC tile 0, AC coupling, balun | timed by the sample clock; AC-coupled pulse, a few hundred mV into 50 Ω |
| Pmod+ (manual "Pmod+ Port", p. 15; Appendix A p. 17; sheet 5 "PMOD HEADERS") | FPGA pin (LVCMOS18) → NVT2008BQ passive level translator with 1.15 kΩ pull-ups to 3.3 V | the rising edge is the pull-up charging the load (tens of ns, load-dependent), only the falling edge is driven; high-impedance loads only; timed by the fabric clock through the MMCM and the I/O buffers (tens of ps, inferred) |
| SYZYGY (sheet 6 "SYZYGY HEADER") | FPGA HP pins directly, LVDS possible | needs a SYZYGY breakout board, none on the bench |
| PL_CLK / CLK OUT | the LMK's outputs, not the PL | carries no tick; the board's unloaded "Clk_Out" SMA is not fitted |

## 4. Proposed design

- **P10 pps_out**, in the fabric clock domain beside P1: the schedule of section 2, step 2; the beat with the step (amplitude AMPLITUDE in the I samples from SAMPLE on, Q zero), then WIDTH − 1 beats of AMPLITUDE, then zeros; TVALID high always (the stream of tile 0 was tied to zero before 2.1).  A flywheel count of the pulses sent in seconds that no locked edge started (an edge missing, or one off its period, which drops P1's lock), and the count of pulses sent.
- **Registers** (as built, `docs/ABI.md` section 4.13, block 0x140): PPS_OUT_CONTROL (ENABLE; SYNCED, the ARM's "synchronized" bit), PPS_OUT_OFFSET (ticks, 8 to 99 999 999), PPS_OUT_SAMPLE (0 to 7), PPS_OUT_WIDTH (beats, 1 to 65535), PPS_OUT_AMPLITUDE (signed 16 bits), and read-only PPS_OUT_STATUS, PPS_OUT_CNT, PPS_OUT_FLYWHEEL_CNT and PPS_OUT_TICK (the tick of the last pulse).  Changes take effect at the next second, SYNCED at once.  Registers only added: VERSION 2.0 → 2.1, rf-clock 2.1.0.0 (docs/ABI.md section 14).
- **Block design**: tile 0's stream from P10 instead of the tie to zero.
- **Python**: `pl.pps_out` (configure, enable, synced, status); tile 0's baseband set-up moved from `m2_tx_timing` into `rf_clock.pl.rfdc` and done by `PL.open()`; D_board goes into the board config (`/etc/rf-clock/board.json`), as calibrated constants do, once it is measured.
- **A validity line on the Pmod+ header** (decided 2026-11-04): other equipment that takes the output PPS must know when not to trust it.  P10 drives one Pmod pin (PMOD0_0, FPGA pin AF16, J1 pin 1) high while the output PPS is not valid: the PPS input not locked, the output on the flywheel, the clocks unlocked, DAC tile 0 not taking the stream, or the ARM's "synchronized" bit (PPS_OUT_CONTROL) clear; the ARM sets that bit once the link is locked and the steering has settled.  High means not valid because the NVT2008BQ's pull-up to 3.3 V pulls the pin high whenever the FPGA does not drive it (no bitstream, board off), so a board that cannot vouch for its PPS reads as not valid.  The Pmod's slow rising edge (section 3) does not matter for a level that changes rarely.  The user LED W_LED_0 (FPGA pin AR11, silkscreen PL_LED0) is lit while the output PPS is valid.
- **Unchanged**: the link (DAC_A, tile 2), the slot engine, the FS725's 1PPS as the PPS input.

## 5. How to test it

1. Simulation (iverilog): the step at the beat and sample section 2 gives; the flywheel when one edge is missing; nothing while disabled.  Done, section 8.
2. One board, B (its FS725 is under its control and its PPS capture margin is known, 5.0-5.5 ns): J20 to the scope beside B's 10 MHz and B's FS725 1PPS.  The output edge against B's 10 MHz must hold (a) from second to second to about a picosecond, (b) through PP steps of B's FS725, moving by exactly whole ticks (the FS725's 1PPS moved by up to 0.23 ns more or less), (c) over `PL.open()` and power-ups, (d) against die temperature.
3. Two boards, steered: both outputs on the scope; the constant between them calibrated once must hold over re-acquisitions (with PP) and power-ups, where the FS725 1PPS lost 145 ps.

## 6. Cost

RTL (P10 and its testbench, about 150 lines), the register map in `p_regs.vh` / `p_defs.vh` / `docs/ABI.md`, one change to the block design, a Vivado build (about an hour on RF-clock-PC), about 100 lines of Python, and a coaxial cable from each board's J20 to the scope (for one board, to C2 or C4 in place of one of A's signals).

## 7. Decisions

Decided (user, 2026-11-04):

- One pulse a second.
- Without a PPS lock the output goes on, on the flywheel, counted, and the validity line of section 4 says it is not valid.
- The term "output PPS" (输出 PPS) for this edge; added to the term list of `AGENTS.md`.

Decided (user, 2026-11-05):

- A theta-compensated mode, to come with the next bitstream that is built for another reason: a PPS_OUT_CONTROL bit, clear by default, with which B's output edge also moves by the steering error the ARM writes (a fractional delay of the edge, written as the step's first beat), so that the output PPS follows A's time scale to the measurement rather than to the steering. The plain mode stays the default: there the output edge keeps a fixed place against the node's own 10 MHz, which equipment that takes both may expect. A White Rabbit grandmaster takes its fine phase from the 10 MHz and uses the PPS only to pick the cycle, so it gains nothing from the mode.
- SYNCED from `m4c_link` (rf-clock 2.2.0.4, `SyncedRule`): node A while its link is locked and B's packets come in; node B while its link is locked, the steering runs, and the steering error's 60 s rms is below 100 ps and its mean below 30 ps, cleared when the link or the steering stops or the 10 s rms exceeds 200 ps (options `--synced-rms`, `--synced-mean`, `--synced-clear-rms`). Thresholds to be revisited with the bench's numbers: with the predictive control law the 60 s rms stays below 38 ps and the 10 s rms below 68 ps.

Open:

1. Is an AC-coupled pulse of a few hundred mV into 50 Ω acceptable as the system's PPS output, or must there be a logic-level PPS (an external buffer on J20, or a Pmod marker from the same P10 at nanosecond quality)?

## 8. Implementation (rf-clock 2.1.0.0, 2026-11-04)

- `PL/src/p10_pps_out.v` schedules each second's pulse from that second's own edge, not from the previous edge as first proposed.  While the PPS is locked the two give the same tick, since then PPS_TICK(k) = PPS_TICK(k−1) + 100 000 000.  They differ in OFFSET's lower limit: the edge reaches P10 three ticks after the tick that latched it (P1's two synchronizer stages and its register), so OFFSET must be at least 8 (`PPSOUT_MIN_OFFSET`, P2 refuses less) for the edge to be in before its pulse, and the output edge cannot fall in the latch's own tick.  Nothing needs that tick: the output edge lies D_board after the time scale in any case, and D_board is calibrated.  In return a second that no locked edge started is told apart directly, which is what the flywheel count and the validity line need, and a PPS that moved earlier is followed in the same second.
- When an edge sets the counter later than the flywheel had it (the PPS moved later, after a PP and a new lock), that second's pulse may already be out; P10 sends no pulse within half a second of the last one, so it is not repeated.
- The validity line also needs DAC tile 0's TREADY, so a DAC that does not take the stream reads as not valid.
- Simulation, 2026-11-04: `PL/sim/tb_p10_pps_out.v` (a 1000-tick second) checks the pulse at the second's start + OFFSET with its step and width, the flywheel pulse and the line over a missing edge, no second pulse when the PPS moves 25 ticks later, the new edge followed when it moves 30 ticks earlier and a new configuration taken at that early edge (the counter does not wrap in that second, so P10 takes the configuration at every locked edge as well), a new configuration taken at the next second, nothing while disabled, SYNCED and TREADY on the line, and no blink of the line at the start of each second.  Four faults put into the RTL by hand (no half-second guard; no allowance for the edge's three ticks in the line; the counter one tick off; the configuration taken only where the counter wraps) each make it fail.  `PL/sim/tb_p_top.v` checks the registers through P2 (values out of range refused, read-back), the pulse at PPS_TICK + 50 on the tile 0 stream, PPS_OUT_TICK and the line and LED pins; the pulses sent while P1 locks again after a PPS_CONFIG change count as flywheel pulses.
- Bitstream: `RF-clock-PC:~/build/rf_clock_v2_run5` (timing met, WNS 0.245 ns), packaged with rf-clock 2.1.0.0.
- rf-clock 2.2 (2026-11-04) adds an idle level, `PPS_OUT_IDLE`: with −AMPLITUDE the step doubles, the only way left to a steeper edge; a faster stream into tile 0 would cost tile 2's MTS (report section 9).
- On board B (2026-11-04, `results/pps-out-20261104/REPORT.md`), section 5 item 2: against B's 10 MHz the output edge held to the scope's 7 ps per second, with no wander above about 1 ps up to two minutes; through ten PP moves it moved by exactly the whole ticks PPS_TICK moved (3.1 ps rms), while the 1PPS missed them by 40-260 ps; over six PL.opens it stayed within 8.7 ps.  DAC_B is inverted against I, so a negative AMPLITUDE gives a rising leading edge (about 0.19 V into 50 Ω).
- Two boards, B steered (2026-11-04, report section 8), section 5 item 3: the output PPS_B − output PPS_A, with theta's steering error taken out, was 5.1666 and 5.1657 ns in two runs, each after a PL.open of both boards and a new acquisition with PP: 0.9 ps apart, where the FS725 1PPS lost 145 ps.
- Not done yet: power cycles, more acquisitions, a temperature step, D_board. The ARM sets SYNCED since rf-clock 2.2.0.4 (section 7); on node A the LED (PL_LED0) lit once the link locked (2026-11-05, seen by the user).
