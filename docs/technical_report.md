# RF-clock: picosecond two-way time transfer between two RFSoC nodes

**Technical report and handoff**

Haoyu Dong (h.dong@yale.edu), Tang Lab, Yale University · 2026-11-06 · rf-clock 2.3.0.2 · Chinese translation: [technical_report_zh.md](technical_report_zh.md)

## Abstract

RF-clock synchronizes two clocks over a radio link. Each of two nodes is a Real Digital RFSoC 4x2 board clocked by its own SRS FS725 rubidium frequency standard. The nodes take turns, every 2 ms, sending each other 5.8 GHz spread-spectrum bursts. Each node measures, on the 100 MHz clock of its own PL, when the other's burst arrives and sends that measurement back inside its next burst; from the two directions both nodes compute θ, the offset of node B's clock against node A's, with the propagation delay cancelled. Node B then steers its FS725 so that θ stays at its calibrated target, and both nodes make an output PPS from the 100 MHz clock of their own PL. On the bench, with the two boards cabled through filters and attenuators, θ is measured 250 times a second with 0.8 ps of noise per measurement; B's clock follows A's with θ − θ₀ at 5.3-5.6 ps rms per second (5 ps from 15 ps by driving the FS725's crystal directly, this report's last step), and the two output PPS differ by 9.2 ps rms per second as an oscilloscope sees them, at the scope's own resolution. This report explains, for readers new to RFSoCs and to time measurement, what the system does and why, how it is built, how to run and maintain it, what has been measured, the problems found on the way, and what remains for the planned 48 km link across Long Island Sound.

## Contents

1. [Introduction](#1-introduction)
2. [Background](#2-background)
3. [System design](#3-system-design)
4. [Hardware, bench and infrastructure](#4-hardware-bench-and-infrastructure)
5. [Operating the system](#5-operating-the-system)
6. [Results](#6-results)
7. [Problems found and lessons learned](#7-problems-found-and-lessons-learned)
8. [Open issues and future work](#8-open-issues-and-future-work)
9. [Handover checklist](#9-handover-checklist)
10. [References](#10-references)

Appendices: [A. Glossary](#appendix-a-glossary) · [B. Command reference](#appendix-b-command-reference) · [C. Troubleshooting](#appendix-c-troubleshooting) · [D. Repository map](#appendix-d-repository-map) · [E. Index of reports](#appendix-e-index-of-reports)

## 1. Introduction

### 1.1 What the system does

Two places each have an atomic clock, and the clock at one place, B, should keep the time of the clock at the other, A, to picoseconds, without a cable between them. RF-clock does it in two steps that repeat continuously:

1. **Measure** how far B's clock is from A's. The two nodes exchange radio bursts; because each node measures the other's burst on its own clock, and the radio path delay enters the two directions with opposite signs relative to the clock offset, the delay cancels and the offset remains (two-way time transfer, section 2.4).
2. **Correct** B's clock. B adjusts the frequency of its FS725 so that the offset stays at its target (steering, section 2.7).

```text
            node A (master)                                   node B (follower)
  FS725 A ── 10 MHz, 1PPS ──> RFSoC A                RFSoC B <── 10 MHz, 1PPS ── FS725 B
                               │  ▲                    ▲  │                         ▲
                               │  └── 5.8 GHz bursts ──┘  │                         │
                               └──── every 2 ms, in turn ─┘── θ ── steering ───────┘ RS-232
                    output PPS (J20) and 10 MHz                    output PPS (J20) and 10 MHz
                    = A's time                                     = B's time, held on A's
```

Everything a node measures with is made from its own FS725's 10 MHz (section 3.2), so steering B's FS725 moves everything B times with: its 10 MHz, its sample clocks, the 100 MHz clock of its PL with the tick counter, and its output PPS.

### 1.2 Why

The goal is a 48 km radio link between Yale's Kline Biology Tower in New Haven and the Stony Brook University Hospital, across Long Island Sound (milestone M6, section 8.2). Over water there is no practical fiber; satellite two-way time transfer reaches about a nanosecond; free-space optical links have been shown only over kilometres. A ground-to-ground radio link with picosecond-level synchronization over that distance would be new (literature search, 2026-11-04). The bench work reported here builds and characterizes the whole system with cables in place of the air path, so that M6 changes only the RF path.

### 1.3 What has been achieved

| Quantity (bench, cabled) | Value | Where |
| -------------------- | ------------------------- | --------------- |
| θ measurements | 250 per second, one every 4 ms | section 6.1 |
| θ noise, both boards on one reference | 0.81-0.87 ps per measurement | [link report](../results/v2-link-20261103/REPORT.md) |
| θ − θ₀, B steered through FC (2.3.0.2) | 5.34 and 5.56 ps rms per second, 1.7-1.9 ps over 60 s | [precision report](../results/precision-20261105/REPORT.md) section 8 |
| B's 10 MHz against A's on an oscilloscope | follows θ (slope 0.991), 6.85 ps per second including the scope's noise | same |
| Output PPS of B against A's, plain | 9.19 ps rms per second, the scope resolving 7.8-8.2 ps | same |
| Output PPS, θ-compensated (2.3) | 8.6-8.8 ps rms per second, 0.9-1.0 ps over 60 s | same, section 7 |
| Output PPS constant over a new start and acquisition | within 0.1-0.9 ps | sections 6.4, 6.5 |
| Measurement packets | 4.9 million each way overnight, no CRC error, no lost lock | [M5 report](../results/m5-v2-20261103/REPORT.md) section 8 |

The milestones of the design ([plan/RF_clock_modules.md](../plan/RF_clock_modules.md) section 9), M0 (the reference into the board) to M5 (two clocks, steered), have all been run on the bench; [plan/checklist.md](../plan/checklist.md) gives the evidence item by item and keeps some sub-items open, chiefly the PPS difference on a time-interval counter, which the output PPS on the scope stands in for, and power cycles and temperature steps (section 8.1). M6, the air link, has not started.

### 1.4 How to read this report

- To **run** the existing bench: section 5.1, then 5.2 and appendix B.
- To **understand** it: section 2 if the field is new, then section 3.
- To **maintain or change** it: sections 3, 4, 5.4-5.6, 7 and appendix D; the programming manual [docs/ABI.md](ABI.md) has every register and every function.
- To **continue the research**: sections 6, 7 and 8, and the reports in appendix E.

The deeper documents are [plan/RF_clock_modules.md](../plan/RF_clock_modules.md) (the design), [docs/ABI.md](ABI.md) (the ARM-PL interface and the Python library), [docs/board_setup.md](board_setup.md) (a new board, step by step), [docs/debug_overview.md](debug_overview.md) (every problem on the clock chain), [docs/fs725_principles.md](fs725_principles.md) (how the FS725 works), [plan/checklist.md](../plan/checklist.md) (every item with its evidence) and [AGENTS.md](../AGENTS.md) (the design rules any change must keep). Reports live in `results/<name>/REPORT.md`; `results/` is not in Git (section 4.4).

## 2. Background

This section gives what a reader needs for the rest of the report, without assuming any knowledge of clocks or of RFSoCs.

### 2.1 Clocks, frequency and time

A clock is an oscillator and a counter: the oscillator produces a periodic signal, the counter counts its periods. Laboratory clocks deliver two signals:

- **10 MHz**: a sine wave at 10 MHz, whose phase is the clock's time to within a fraction of its 100 ns period. Instruments lock their own oscillators to it.
- **1PPS** (one pulse per second): a pulse whose edge marks the start of each second. It says *which* period of the 10 MHz starts the second.

Two clocks never run at exactly the same rate. If clock B runs faster than clock A by a fraction y, B gains y × t on A in time t. Rates are therefore given as dimensionless fractions:

| y (fractional frequency) | B gains per second | per day |
| --- | --- | --- |
| 1 × 10⁻⁹ | 1 ns | 86 µs |
| 1 × 10⁻¹² | 1 ps | 86 ns |
| 1 × 10⁻¹⁵ | 1 fs | 86 ps |

A useful identity follows: **a fractional frequency of 10⁻¹² is a drift of 1 ps per second**, so a frequency change of “1 ps/s” means 10⁻¹². For scale, light travels 0.3 mm in 1 ps, and one period of the 5.8 GHz carrier is 172 ps.

### 2.2 How stability is described

A clock's frequency is not constant but varies. Its stability is described by the **Allan deviation** σ_y(τ): roughly, the typical change of the average fractional frequency from one interval of length τ to the next. It is plotted against τ on log-log axes, and its slope tells the kind of noise:

```text
 log σ_y
   │╲ slope −1: white phase noise (a measurement's own noise; averaging helps as 1/τ)
   │  ╲__ slope −1/2: white frequency noise (the clock's rate jitters; time error grows as √τ)
   │      ‾‾‾‾‾‾‾  slope 0: flicker frequency noise (a floor)
   │               ╱ slope +1/2: random-walk frequency noise (the rate drifts away)
   └────────────────────────────────── log τ
```

Read it as: σ_y(1 s) = 1.6 × 10⁻¹¹ means that over one second the two clocks' rate typically changes by 1.6 × 10⁻¹¹, so their time difference can change by about 16 ps that nobody predicted. The **time deviation** (TDEV) gives the same for the time error directly. When a time difference is averaged over n samples of white noise, its scatter falls as 1/√n; this is why the report quotes “per second” and “over 60 s” figures.

### 2.3 The FS725 rubidium frequency standard

The SRS FS725 contains a PRS10 rubidium oscillator ([docs/fs725_principles.md](fs725_principles.md) explains it in full):

```text
  quartz crystal in an oven ──> 10 MHz outputs, 1PPS output
        ▲  tuning (FC, a 22-bit DAC, through a 1 s RC filter)
        │
  frequency lock loop (FLL): compares the crystal, multiplied to 6.834 GHz, with the
  rubidium atoms' transition, one error every 0.2 s, and integrates it into FC (gain GA)
        ▲
  SF: a small offset of the atoms' frequency (magnetic field), parts in 10¹²
```

- The **crystal** is quiet over short times but drifts; the **rubidium atoms** are stable over long times but their detector is noisy over short times. The FLL combines them: it corrects the crystal towards the atoms with a time constant of about 2 s at its normal gain (GA 7; each GA step halves it).
- **SF** shifts the atoms' frequency through the magnetic field; the FLL then pulls the crystal after it, 0.5-1.3 s later depending on GA. This is the classic way to steer it.
- **FC** is the crystal's tuning voltage, which the FLL normally owns. Written directly with the loop slowed to GA 1 (a 128 s time constant), it reaches the crystal through a 1 s RC filter alone (section 3.7). Stopping the loop completely (`LO 0`, or GA 0) makes the unit report itself unlocked and stop its 1PPS (section 7.4).
- Its 1PPS is made from the 10 MHz by a counter and an analog delay; it jitters by 22-44 ps against the 10 MHz and its position command (PP) misses by up to 0.23 ns, which is why the nodes make their own output PPS (section 3.8).

Two free FS725s on the bench drift apart at about 65 ps/s (their calibrations differ by 6.5 × 10⁻¹¹) and vary about that line with an Allan deviation of 1.4-1.6 × 10⁻¹¹ at 1 s ([M5 report](../results/m5-v2-20261103/REPORT.md) section 4).

### 2.4 Two-way time transfer

Let θ = T_B − T_A be the offset of B's clock reading against A's at the same instant. A sends a burst at its time T_tx,A; B receives it at its own time T_rx,B; B sends one back at T_tx,B, received by A at T_rx,A. With the propagation delay p, each node's transmit hardware delay δt and receive hardware delay δr:

```text
  m_AB = T_rx,B − T_tx,A = δt_A + p + δr_B + θ      (B measures; B's clock is ahead by θ)
  m_BA = T_rx,A − T_tx,B = δt_B + p + δr_A − θ      (A measures)

  θ = (m_AB − m_BA)/2 − Δ/2,       Δ = (δt_A + δr_B) − (δt_B + δr_A)
  one-way delay = (m_AB + m_BA)/2
```

The path delay p appears in both directions with the same sign and cancels in the difference, as long as it is the same both ways (reciprocity), which holds when the two bursts travel the same path within a short time: here 2 ms apart. What does not cancel is Δ, the difference of the two directions' hardware delays (cables, filters, converters). It is measured once with both boards on one clock, where the true θ is zero: the θ seen then is **θ₀**, the “zero-baseline” constant, and B is steered to θ₀ rather than to zero. θ₀ depends on the boards and on every cable and filter between them, so it is stored per pair of boards and per wiring in [links/](../links/README.md); today's is −21.5 ps.

### 2.5 Timing a radio burst

A node times the other's burst by correlation. The burst is a pseudo-random binary code (PRN): 4096 chips of ±1 at 100 Mchip/s (one chip per 10 ns tick), smoothed by a root-raised-cosine (RRC) filter so that it occupies 125 MHz around 5.8 GHz. The receiver knows the code, slides a copy of it across the received samples and finds where they match best:

```text
 received samples (800 MS/s, 1.25 ns apart) ──> correlate with the known burst at each lag
                                                       │
     correlation │        ∧            peak: the burst's arrival, to a whole sample
     magnitude   │       ╱ ╲           three-point parabola through the peak: a fraction of a sample
                 │______╱   ╲______    carrier phase at the peak: a fraction of the 172 ps carrier period
                 └──────────────────── lag
```

The correlation adds up all 32 841 samples of the burst, which averages the noise down; with a peak-to-noise ratio of about 45 dB on the cabled bench, the interpolated peak gives the arrival to well under a picosecond, although a sample is 1250 ps. The carrier phase at the peak is measured as well; it resolves about 1/1000 of the 172 ps period but repeats every period, so the envelope peak decides which period. θ today uses the envelope peak.

### 2.6 The RFSoC 4x2

An RFSoC (Radio-Frequency System-on-Chip, here the AMD/Xilinx Zynq UltraScale+ XCZU48DR) puts three things on one chip:

| Part | What it is | What RF-clock uses it for |
| ------------ | ----------------------- | ------------------------- |
| PS (processing system) | four ARM Cortex-A53 cores running Linux (PYNQ 3.1.1) | Python: configuration, acquisition, two-way equations, steering, logs |
| PL (programmable logic) | FPGA fabric, programmed with a bitstream | the tick count on the 100 MHz clock and everything that must happen within 2 ms: slots, bursts, correlation, packets, output PPS |
| RFDC (RF data converters) | ADCs and DACs that sample directly at GHz rates | DAC at 9.6 GS/s transmits; ADC at 4.8 GS/s receives |

The Real Digital RFSoC 4x2 board around it adds the clock chips (an LMK04828 that cleans the 10 MHz reference and makes the PL clock and SYSREF, and two LMX2594 synthesizers that make the converters' reference clocks), SMA connectors for two DACs and four ADCs, a PPS input (J7), Ethernet, USB and a micro-SD card that holds Linux.

Three converter ideas recur in this report:

- **Nyquist zones.** A converter sampling at f_s sees frequencies in zones of width f_s/2; a signal in a higher zone appears folded into the first. The DAC at 9.6 GS/s generates 3.8 GHz digitally and transmits its image at 5.8 GHz in the second zone (a band-pass filter removes the 3.8 GHz); the ADC at 4.8 GS/s receives 5.8 GHz in its third zone, where it folds to 1.0 GHz, and mixes it to baseband.
- **Deterministic latency (MTS).** Each converter tile has its own clock and FIFOs; without care, its delay from the digital side to the connector changes by whole samples at every power-up. Multi-Tile Synchronization (MTS) uses a common SYSREF signal to bring every tile to the same latency on every power-up, which is what makes a calibrated θ₀ survive a reboot.
- **All clocks from one 10 MHz.** Every clock on the board, from the 100 MHz PL clock to the 9.6 GHz DAC clock, is derived from the same 10 MHz reference, so one count of the PL's tick (10 ns) is exactly 8 samples of the 800 MS/s baseband stream, and the sample index and the tick map onto each other exactly.

### 2.7 Steering a clock

B cannot move its time instantly; it can only change its frequency, which moves its time over the following seconds. A feedback loop measures θ − θ₀ and changes the frequency to bring it back. The difficulty is delay: a change made now acts on the clock only after the actuator's delay (1-2 s through the FS725's FLL, about 1 s through FC), and during that time the two clocks vary unpredictably. The loop must therefore predict the clocks' next second or two. The **Kalman steering algorithm** used first estimates θ and the frequency difference and corrects with a time constant; the **predictive steering algorithm** used now models the actuator, removes its own corrections from θ to see the free-running clocks, predicts them with an autoregressive (AR) model and plans the next changes by least squares (model predictive control). What is left of θ − θ₀ is the clocks' variation that could not be predicted over the actuator's delay (section 6.6).

## 3. System design

### 3.1 Responsibilities: PL and ARM

The design rule ([AGENTS.md](../AGENTS.md)) is: **the ARM runs the algorithms; the PL keeps time and runs the per-burst measurement chain.** The measurements travel inside the bursts of a 2 ms ping-pong, so each must be ready and on air within 2 ms of the burst it measures, which Linux on the ARM cannot guarantee; everything slower stays in Python.

| Task | Where |
| ----------------------------------------- | ------------------- |
| 64-bit tick counter at 100 MHz, PPS capture, timestamps | PL |
| Slot engine: every burst and every receive window at its exact tick | PL |
| Burst generation from chips (PRN, guard, packet) through the RRC filter | PL (chips and filter coefficients computed by the ARM) |
| Correlation of every window (tracker), whole-window search when lock is lost | PL |
| Measurement packets: assemble, spread, despread, CRC; a queue of results | PL |
| Output PPS and its validity line | PL (the ARM says when it is valid) |
| Clocks (LMK, LMX), RFDC start-up and MTS | ARM, through drivers |
| Acquisition: finding the other node in a long window | ARM (the PL captures the window into DDR) |
| Pairing the two directions, θ, logs | ARM |
| Steering B's FS725 over RS-232 | ARM |

### 3.2 A node's clocks

```text
 FS725 10 MHz ──> LMK04828: PLL1 (VCXO 160 MHz) ──> PLL2, VCO 2400 MHz ──┬─ ÷24  ──> PL clock 100 MHz ──> tick (10 ns)
                                                                          ├─ ÷480 ──> SYSREF 5 MHz ──> RFDC tiles (MTS), PL
                                                                          ├─ ÷5 ──> LMX2594 ──> 480 MHz ──> ADC tile PLLs ──> 4.8 GS/s
                                                                          └─ ÷5 ──> LMX2594 ──> 480 MHz ──> DAC tile PLLs ──> 9.6 GS/s
 FS725 1PPS ──> J7 ──> comparator ──> PL: sampled on each tick ──> PPS_TICK (the tick of each second's edge), PPS_COUNT
```

A node's time is “second k plus ticks since that second's PPS edge plus the sample within the tick”. The PL never resets its tick; each second is anchored to the tick that latched the PPS edge, and the PL checks that every second is exactly 100 000 000 ticks. Every divider and synthesizer in this tree has to start in a defined phase, and several did not at first (section 7.2): the board configuration (section 3.10) and the clock files fix them.

### 3.3 Frequency plan

| Item | Value |
| ------------- | ----------------------------------------------- |
| Carrier | 5.8 GHz (5.725-5.875 GHz ISM band) |
| DAC | tile 2 block 0 (DAC_A connector), 9.6 GS/s, NCO 3.8 GHz, 5.8 GHz as the second-Nyquist image |
| ADC | tile 2 block 0 (ADC_B connector), 4.8 GS/s, 5.8 GHz in the third Nyquist zone, NCO −1.0 GHz |
| Baseband | 800 MS/s complex; 8 samples per tick |
| Code | 4096-chip PRN at 100 Mchip/s; RRC roll-off 0.25, 81 taps; 125 MHz occupied |
| Tiles 0 | ADC and DAC tile 0 enabled for MTS; DAC tile 0 (DAC_B, J20) carries the output PPS |

### 3.4 The slot protocol

```text
  slot k = 0 … 499 starts at PPS + k × 2 ms
  k even: A transmits, B's window is open          k odd: B transmits, A's window is open

        slot 2j (A)                         slot 2j+1 (B)                       slot 2j+2 (A)
        |<------------ 2 ms ------------>|<------------ 2 ms ------------>|
 A TX   |o|PRN|g| packet: A's last result |                                |o|PRN|g| packet ...
 B RX   |o|d|[window: tracker][packet]                                    |
 B TX   |                                 |o|PRN|g| packet: B's result of 2j |
 A RX   |                                 |o|d|[window: tracker][packet]
```

- **Burst**: the PRN (40.96 µs), a 10-chip guard, then the **measurement packet**: 36 bytes (288 bits), each bit spread over 64 chips of another code (0.18 ms). It carries the sender's second (`PPS_COUNT`) and slot, the send time of this burst, the arrival time and carrier phase of the peer's previous burst, a quality figure, status flags and a CRC.
- **Window**: B opens its receive window 1.25 µs before A's burst is expected (54.8 µs, 11 008 lags of search range). The PL's tracker correlates a few lags around the expected peak in every window, interpolates the peak and measures the carrier phase; the despreader decodes the packet behind it.
- **Pairing**: a pair is the two directions 2 ms apart. Each node pairs its own measurement with the peer's measurement that arrives in the next burst, by `PPS_COUNT` and slot; no network and no NTP are involved. Both nodes compute the same θ from their own queues.
- **Search**: when the tracker has no lock (at start, or after a loss), the PL searches the whole window with 1024 parallel correlators in integer arithmetic (3.9 ms); it re-locks by itself only if the new peak is within one sample of the old one; otherwise the ARM decides.

### 3.5 The PL design

The PL modules are named P0-P10 (design [plan/RF_clock_modules.md](../plan/RF_clock_modules.md) section 6; registers [docs/ABI.md](ABI.md) section 4; RTL `PL/src/`):

| Module | Function |
| -------------- | ---------------------------------------------- |
| P0 clock and reset | lock monitoring, faults |
| P1 tick and PPS | 64-bit tick, PPS capture and period check, PPS source and polarity |
| P2 control registers | the AXI-Lite register map (`PL/src/p_regs.vh`), single TX/RX commands for tests |
| P3 TX burst | chip memories, RRC FIR, gap-free playback from the start tick |
| P4 RX capture | timed windows into the correlator and, on request, through a FIFO and DMA into DDR |
| P5 RFDC | the RF data converter IP |
| P6 status | latched faults |
| P7 slot engine | the 2 ms ping-pong from each PPS |
| P8 correlator | tracker in every window, whole-window search on demand |
| P9 packet | measurement packet assembly, spreading, despreading, CRC; measurement queue |
| P10 output PPS | one pulse a second on DAC_B, flywheel, validity line, fractional edge delay (2.3) |

The PL's correlation equals the ARM's float64 correlation to the bit on the same windows (`rf_clock.plmodel`, [link report](../results/v2-link-20261103/REPORT.md)). Rules that the PL design keeps ([AGENTS.md](../AGENTS.md)): bursts are made from on-chip data and played without a gap (a DAC that stops accepting data is a fault, never a pause); no sample may be lost inside a receive window (a FIFO overflow invalidates that window, counted, without a fault); every burst and window has one start tick and per-sample times follow from it.

### 3.6 Acquisition

Before the slots can start, B must know where A's seconds begin to within a fraction of the window: with two free-running clocks they can be anywhere. B captures an 11 ms window into DDR, the ARM correlates it (about 3 s in Python since rf-clock 2.3.0.4, 25 s before), finds three or four of A's bursts, decodes their packets, which carry A's send times and second numbers, and so places A's PPS on B's time to within the assumed one-way delay (`--arrival-ticks`). B then moves its FS725's 1PPS by that offset with the FS725's PP command, in whole ticks, and both nodes start their slots. Whole ticks, because the PL latches the PPS on its tick: a whole-tick move moves PPS_TICK, and every time the PL counts from it, by exactly that much, while a fraction would change the edge's place within its tick unseen ([M5 report](../results/m5-v2-20261103/REPORT.md) section 2). The rest of the offset, under half a tick, is taken out by the steering.

### 3.7 Steering B

B's ARM feeds θ to a steering algorithm and writes the FS725 over a USB-RS232 adapter. Two actuators exist:

```text
 through SF (until 2.3.0.1):  algorithm ──> SF ──> magnetic field ──> FLL detector (0.2 s frames) ──> integrator ──> FC ──> 1 s RC ──> crystal
 through FC (2.3.0.2):        algorithm ──> FC ─────────────────────────────────────────────────────────────────────────> 1 s RC ──> crystal
                                             (the unit at GA 1: the FLL's own pull, 128 s, undone by writing FC every 0.1 s)
```

- **Through SF**, a correction acts 1-2 s later, through the FLL's integrator and dead time, and the predictive steering algorithm on 0.2 s steps held θ − θ₀ to about 15 ps.
- **Through FC** (the regular way since 2.3.0.2, “steering through FC”), the algorithm writes the crystal's tuning every 0.1 s with B's unit at GA 1; the crystal follows through its 1 s RC alone (lag 1.12 s, no dead time, 0.86 ps/s per fine step of FC, measured), and θ − θ₀ is 5.3-5.6 ps. The algorithm's model is deliberately slower than the measured crystal (lag × 1.25, dead time + 0.1 s): in replay a model that is faster than the FS725's response oscillates.
- The algorithm ([docs/ABI.md](ABI.md) section 11.12; `rf_clock.twtt.steer.PredictiveSteering`): every 0.1 s it takes θ over the last 0.05 s of the step, subtracts what its own past changes do, predicts the free-running clocks over 3 s with an AR(40) model, and plans four changes by least squares at a cost per change; it sends the first. At the end of a run, or if the steering stops, GA goes back to the board config's gain.

### 3.8 The output PPS

The node's PPS for other equipment is made by the PL (P10) from its 100 MHz clock, not taken from the FS725: in every second, the beat on DAC tile 0's stream at PPS_TICK + OFFSET carries a step, which the AC-coupled DAC_B output (J20) turns into a pulse of about 0.19 V into 50 Ω rising in 1 ns. It follows the PL's 100 MHz clock, which θ aligns between the nodes, so neither the FS725's 1PPS jitter nor a PP's error enters it, and it moves with the tick by exactly whole ticks. A flywheel carries it over a missing input edge, and a validity line (Pmod J1 pin 1, high = not valid; an LED) says whether it may be used. Two modes ([plan/pps_output.md](../plan/pps_output.md)):

- **Plain** (default): the edge keeps a fixed place against the node's own 10 MHz. With B steered through FC the two nodes' plain output PPS differ by 9.2 ps rms per second.
- **θ-compensated** (`m4c_link --pps-out-comp`, since 2.3): before each edge B delays its edge by θ − θ₀ measured just before, by a fraction of a 1.25 ns DAC sample (`PPS_OUT_FRAC`). B's PPS then follows A's to the measurement rather than to the steering (8.6-8.8 ps per second through SF steering), but no longer keeps its place against B's 10 MHz; equipment that takes both signals should use the plain mode.

### 3.9 Software and versions

The Python package `rf_clock` runs on the boards (in PYNQ's environment) and on a development machine (with a fake PL for tests):

| Module | Content |
| -------------- | ---------------------------------------------- |
| `rf_clock.pl` | power-up (`PL.open()`: overlay, clocks, RFDC, MTS, PPS lock), registers, slots, bursts, correlator, queue, acquisition, output PPS, board config |
| `rf_clock.twtt` | the two-way equations (`pairs`), the node's FS725 and SYNCED (`control`), the steering algorithms (`steer`), log reading (`logs`) |
| `rf_clock.fs725` | the FS725 over RS-232 (queries, SF, GA, FC, PP) |
| `rf_clock.checks` | board checks M1-M3 and the link node `m4c_link`, calibrations (`vco_bands`, `mts_dtc`, `fs725_loop`, `pps_pp_scan`) |
| `rf_clock.examples` | annotated examples |
| `rf_clock.plmodel`, `correlator` | the PL's arithmetic in Python; float64 correlation |

The package ships with its bitstream (`src/rf_clock/overlay/`). Its version **A.B.C.D** binds the two: A.B is the register map version (`VERSION`), C counts bitstreams for it, D counts Python-only releases; `PL.open()` refuses a bitstream whose A.B.C differs from the package. The current release is 2.3.0.2 with bitstream 2.3.0.0.

### 3.10 Calibrated constants and where they live

| Constant | Belongs to | Stored in | Measured by |
| ------------------ | ----------- | ---------------- | --------------- |
| VCO band of each LMX2594 | one board | board config `/etc/rf-clock/board.json` ([boards/](../boards/README.md)) | `rf_clock.checks.vco_bands scan --write` |
| MTS SYSREF capture codes and offsets | one board | board config | `rf_clock.checks.mts_dtc record --write` |
| The FS725, its port, its FLL at its gain, its crystal's answer to FC | one board (its unit) | board config, `fs725` | `rf_clock.checks.fs725_loop --write`; `experiments/m5_fll_off.py` for `fc` |
| θ₀ (Δ/2 and the two PPS paths) | a pair of boards and a wiring | `links/<A>_<B>.json` | both boards on one reference, several power-ups |
| The output PPS's fraction slope | the DAC_B output (measured on B) | `m4c_link` default 0.0275 ps per unit | `experiments/m5_pps_frac.py` |
| PPS capture margin | one board and its PPS cable | not stored; kept by EPG1 delays and `--pps-trig` on A | `experiments/m1_pps_phase_scan.py`, `rf_clock.checks.pps_pp_scan` |

The rule is that no guessed constant is ever subtracted: every constant is measured, stored with how and when, and found where it belongs (a board's in its config, a link's in `links/`).

## 4. Hardware, bench and infrastructure

### 4.1 Inventory

| Item | Identity | Role and notes |
| ------------ | ---------------- | -------------------------------- |
| RFSoC 4x2 | S/N 887329260072A, 192.168.2.99 | node A |
| RFSoC 4x2 | S/N 8873292600AB, 192.168.2.100 | node B (since 2026-11-03) |
| RFSoC 4x2 | S/N 887329260101, 192.168.2.101 | spare; used as B in 1.x; no FS725 in its config; its VCO band table lacks band delays (needs its own loopback cable) |
| SRS FS725 | S/N 41175 | A's clock, GA 7 |
| SRS FS725 | S/N 107129 | B's clock, GA 8, steered through FC; its FC answer in B's config |
| Thorlabs EPG1 | pulse and delay generator | distributed one FS725's 10 MHz (square, locked) and 1PPS (with per-output delays) to both boards for the one-reference experiments; not in the M5 wiring |
| Teledyne LeCroy HDO9404-MS | oscilloscope, USB to RF-clock-PC (root) | the outside check: the two 10 MHz, the 1PPS or the output PPS (`experiments/m5_hdo_phase.py`) |
| Signal Hound SA124B | spectrum analyzer, USB to RF-clock-PC | DAC spectrum, filter checks |
| Mini-Circuits ZVBP-5800-S+ | band-pass 5725-5875 MHz, two | one in each cross path; required (section 7.3) |
| attenuators | 10 dB and 20 dB | one per direction |
| USB-RS232 adapters (FTDI) | on each board's USB | each FS725's RS-232 to its board |
| RF-clock-PC | Ubuntu 22.04, 192.168.2.1 on the bench network (`eno1`) | bench host: runs `experiments/`, the scope and instruments, Vivado 2024.1, the build directories `~/build/`, the reference `~/ref/RFSoC4x2-BSP` |

### 4.2 The bench wiring today (M5)

```text
 FS725 41175 ── 10 MHz ──> REF SMA of 887329260072A (A)          FS725 107129 ── 10 MHz ──> REF SMA of 8873292600AB (B)
             ── 1PPS ───> J7 of A (A times its Schmitt copy)                  ── 1PPS ───> J7 of B
             ── 10 MHz ──> scope C2                                           ── 10 MHz ──> scope C1
                                                                              ── RS-232 <── USB-RS232 on B's USB
 A DAC_A ──> ZVBP-5800-S+ ──> attenuator ──> ADC_B of B
 B DAC_A ──> ZVBP-5800-S+ ──> attenuator ──> ADC_B of A       (10 dB one way, 20 dB the other; which is not recorded)
 A DAC_B (J20, output PPS) ──> scope C3 (50 Ω)                 B DAC_B (J20) ──> scope C4 (50 Ω)
 boards' Ethernet ──> switch ──> RF-clock-PC eno1;  boards' USB (JTAG + console) ──> RF-clock-PC
```

The wiring name is `cabled-20261103` and its θ₀ is −21.5 ps ([links/887329260072A_8873292600AB.json](../links/887329260072A_8873292600AB.json)). Any change of an RF cable, attenuator or filter changes θ₀ and needs a new entry there (section 5.4). The scope inputs C3/C4 take the output PPS in the current runs; `--scope-pps` runs need the FS725 1PPS on them instead, through a tee beside each J7.

### 4.3 Access

- **Boards**: user `xilinx` (password `xilinx`, PYNQ's default), SSH key from RF-clock-PC; root through `sudo`. Serial console from RF-clock-PC at 115 200 baud on `/dev/serial/by-id/usb-Xilinx_JTAG+Serial_<serial>-if01-port0` (never write to `if00`, the JTAG channel). Programs that touch the PL run as root: `run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"`.
- **RF-clock-PC**: the project checkout used by the experiments is `~/Program/RF-clock` with its `.venv`; Vivado is sourced from `/tools/Xilinx/Vivado/2024.1/settings64.sh`. The scope needs root (`sudo`). It serves NTP to the boards (chrony): both nodes start their slot engines at a Unix time, so the boards' clocks must agree to well within half a second ([docs/board_setup.md](board_setup.md) step 5 of section 1).
- **A hung board** (a PL access while its clock is off hangs the bus): JTAG reset from RF-clock-PC, `xsdb tools/jtag_reset_board.tcl <JTAG serial>`, about 40 s; a power cycle if that fails.

### 4.4 Code, releases and data

- **Repository**: `FHYQ-Dong/RF-clock` on GitHub, and `haoyu.dong/RF-clock` on the lab's Gitea server (`kinabalu2.stdusr.yale.internal`, SSH port 2222); the branch `v2-rf-data-transfer` holds rf-clock 2. Binaries (bitstream, data sheets) are in Git LFS: after cloning, `git lfs install` and `git lfs pull`.
- **Releases**: pushing a tag `vA.B.C.D` builds the wheel and publishes a release on GitHub (and on Gitea where the tag is pushed there); the wheel is all a board needs.
- **Results**: every experiment writes `results/<name>-<time>/` with its logs and, for the main questions, a `REPORT.md` (and `REPORT_zh.md`); Appendix E lists the reports. `results/` is not in Git. It exists on the author's machine and on RF-clock-PC, and a mirror is on the lab NAS:
  - `smb://kinabalu2.stdusr.yale.internal/kinabalu/Haoyu/RF-clock/results`
  - `\\kinabalu2.stdusr.yale.internal\kinabalu\Haoyu\RF-clock\results` (from Windows)

## 5. Operating the system

### 5.1 Running the link on the existing bench

Both boards on the M5 wiring of section 4.2, idle (nobody else running a PL program), with rf-clock 2.3.0.2 installed. From RF-clock-PC:

```bash
cd ~/Program/RF-clock
.venv/bin/python experiments/m4c_v2_link.py --scenario steer --seconds 1500 "--node-args=--pps-out 20" \
    "--a-args=--pps-trig --fs725" --scope --scope-out-pps
```

What happens, in order:

1. Both nodes start `rf_clock.checks.m4c_link` about two minutes later, at the same Unix time. Each runs `PL.open()` (bitstream, clocks, RFDC, MTS, PPS lock, about 20 s), checks its FS725 against its board config and sets its gain.
2. B acquires A (about 3 s; 25 s before rf-clock 2.3.0.4), moves its PPS by whole ticks, and both start their slots; the PLs search and lock within a second.
3. B steers through FC (the steer scenario's default): its FS725 goes to GA 1, and θ − θ₀ settles within a minute or two. Both nodes send their output PPS on J20 (`--pps-out 20`: 20 ticks after each PPS edge).
4. With `--scope --scope-out-pps` the scope logs the two output PPS and the two 10 MHz once a second.
5. At the end each node prints a result line, B's FS725 goes back to GA 8, and the logs are collected into `results/m4c-v2-steer-<time>/`.

The summary printed at the end gives θ over the last 600 s (its mean should be θ₀, its sd the rms of θ − θ₀). To steer through SF instead, add `"--b-args=--steer-actuator sf --steer-step 0.2 --steer-late-slots 50"`. Other scenarios: `free` (no steering), `sf-step` (one step of SF, to check the response), `acquire` (keeps every queue entry), and protocol tests (`--scenario` list in the script).

### 5.2 Reading the results

Each node's log is one JSON object per line ([docs/ABI.md](ABI.md) section 13): `config` first, then a `second` line per second (θ over the second, the one-way delay, their scatter, the number of pairs), `steer` and `step` lines for each 0.1 s step of the algorithm, `fc_set` for each change of FC, `pps_comp` with the compensated output PPS, and a `result` line. On a development machine:

```python
from rf_clock.twtt import logs
b = logs.read("results/m4c-v2-steer-20261106_080117/m4c_B.jsonl", ("config", "second"))
print(logs.steering_error(b, settle_s=300))       # {'rms_ps': 5.56, ...}
```

`results/precision-20261105/fcsteer.py <run>` analyzes a steering run with the scope: θ − θ₀, the FC the algorithm used, and the scope's 10 MHz or output PPS against θ. `experiments/m4c_check.py scenario results/<run>` judges a protocol run.

### 5.3 Outside checks with the oscilloscope

θ is the system's own measurement; an outside instrument must confirm it. `experiments/m5_hdo_phase.py` (started by `m4c_v2_link.py --scope`) logs, once or twice a second:

- default: the two FS725 10 MHz (C1 B's, C2 A's), whose phase difference moves with θ;
- `--pps`: the two FS725 1PPS (C3 B's, C4 A's, through tees beside each J7);
- `--out-pps2`: the two output PPS (C3 A's J20, C4 B's), and each against its own 10 MHz, whose scatter is the scope's own resolution (about 8 ps per record).

### 5.4 A new board, a new wiring, a changed bench

[docs/board_setup.md](board_setup.md) takes a new board from the SD card to the link, each step with its command and its pass criterion: system and network, installation of the wheel, wiring, then in order the tick and PPS (M1), the PPS capture margin, the VCO bands, the MTS capture windows, timed transmission (M2), the loopback (M3), the band table, and for two clocks the PPS within its tick, the FS725 in the board config and the steering. Its section 6 says what to redo after each kind of change; the short form:

| Change | Redo |
| ------------------------ | ------------------------------------ |
| New wheel with the same A.B.C | nothing |
| New bitstream | MTS windows, M2, M3, band table, θ₀ |
| PPS cable or EPG1 delay | M1, PPS capture margin, θ₀ |
| RF cable, attenuator or filter | M3 (if the loopback), θ₀ as a new entry in `links/` |
| Another FS725 on a board | `fs725_loop --write` (and, for B, the FC answer) |
| SD card rewritten | system and installation, then put back `boards/<S/N>/board.json` |

θ₀ for a new wiring: both boards on one FS725 (the EPG1 distribution of [docs/board_setup.md](board_setup.md) section 3), `experiments/m4c_v2_link.py --seconds 60` over several power-ups, the mean θ as a new entry in `links/` ([links/README.md](../links/README.md)).

### 5.5 Building, versioning and releasing

1. **Version first** ([docs/ABI.md](ABI.md) section 14.1): a register change bumps A.B together with `VERSION` in `PL/src/p_regs.vh` and documents it in `docs/ABI.md`; a new bitstream bumps C; a Python-only change bumps D. Before the first edit of a new version, `pwsh -NoProfile -File tools/snapshot_version.ps1` snapshots the finished one (tests, type check and wheel in a temporary worktree, the tree kept as `refs/snapshots/<version>`).
2. **RTL tests**: `./PL/sim/run_pl_tests.ps1` (iverilog).
3. **Bitstream**, on RF-clock-PC: `vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl -tclargs <new build dir> ~/ref/RFSoC4x2-BSP/board_files impl` (about an hour), then `python3 tools/package_overlay.py <build dir>`, which refuses a build without met timing or for another version.
4. **Python checks**: `uv run pytest`, `uv run pyright`.
5. **Install on the boards** and run the checks a change calls for (section 5.4).
6. **Commit and release**: one signed commit per version from its snapshot, then push a signed tag `vA.B.C.D`; the release workflow builds and publishes the wheel.

### 5.6 Rules that protect the hardware and the measurements

- Never stop an FS725's loop with `LO 0` or `GA 0` while a board uses its 1PPS: the unit then reports itself unlocked and stops its 1PPS (section 7.4). Steering through FC uses GA 1.
- Never write an FS725's EEPROM (commands ending in `!`); rf-clock never does, so a power cycle restores each unit (its settings are backed up in `boards/<S/N>/fs725-<unit>-20261106.json`).
- Install releases of 2.3.0.2 or later on B: earlier ones refuse B's board config, which holds the FS725's FC answer.
- Before a transmitting run, check the wiring and that nobody else uses the boards; an RF path without the band-pass filters gives wrong θ (section 7.3), and an ADC must never see more than its maximum input.
- Do not access the PL while its clocks are off (it hangs the board, section 4.3); `PL.open()` does things in the safe order.
- Every RF change makes a new θ₀; never steer a new wiring to an old θ₀.
- Over the air (M6), transmit power and bandwidth must meet the 5.8 GHz ISM rules.

## 6. Results

All figures are from the cabled bench (section 4.2) unless noted.

### 6.1 Measurement noise

With both boards on one reference (true θ constant), θ scattered by 0.81-0.87 ps from one pair to the next, one pair every 4 ms; averaged over a second it is far below 0.1 ps. The one-way delay was 292.0040-292.0050 ns over 12 runs. The 1.x system, which exchanged one pair a second over Ethernet, scattered by 3.03 ps per second ([link report](../results/v2-link-20261103/REPORT.md), [M4 report](../results/m4-twtt-20261021/REPORT.md)). About half of the per-pair scatter is the converters' clock jitter and half the signal-to-noise ratio ([M5 report](../results/m5-v2-20261103/REPORT.md) section 7). The measurement is therefore 20-30 times finer than the clocks' own variation over a second: the steering is limited by the clocks, not by the measurement.

### 6.2 The free-running clocks

The two FS725s, free: θ drifts at their frequency difference (about −65 ps/s) and varies about that line. Allan deviation of θ ([M5 report](../results/m5-v2-20261103/REPORT.md) section 4; [precision report](../results/precision-20261105/REPORT.md) section 8):

| τ | 0.1 s | 1 s | 2 s | 5 s | 20 s | 256 s |
| --- | --- | --- | --- | --- | --- | --- |
| σ_y, both units together | 7 × 10⁻¹² | 1.6 × 10⁻¹¹ | 1.8 × 10⁻¹¹ | 8.5 × 10⁻¹² | 2.4 × 10⁻¹² | 4.3 × 10⁻¹³ |

The bump at 1-2 s is where each unit's crystal has varied and its FLL has not yet pulled it back (section 2.3). Slowing either unit's loop (B to GA 1, A to GA 5) leaves it at 1-2 s and raises it at 5-20 s, so over 0.1-2 s the variation is the crystals' own.

### 6.3 Steering: what each step gained

| Steering of B's FS725 | θ − θ₀, rms per second | Report |
| ---------------------------------- | -------------- | ------------ |
| Kalman steering algorithm, τ 50 s, a correction every 10 s, through SF | 66 ps | [M5](../results/m5-v2-20261103/REPORT.md) section 6 |
| Kalman, τ 5 s, every 2 s, the FS725's delay in the model | 39 ps (5.5 h overnight: mean +0.1 ps) | same, sections 6.5, 8 |
| B's FLL at GA 8 (half the delay) | 33-35 ps | [FLL](../results/fs725-fll-20261105/REPORT.md) |
| Predictive steering algorithm, 1 s steps, θ from the end of each second | 27-29 ps (28.6 ps over an hour) | [predictive steering](../results/predictive-steering-20261105/REPORT.md) |
| Predictive, 0.2 s steps | 14.7-16.8 ps | [two loops](../results/two-loops-20261105/REPORT.md), [precision](../results/precision-20261105/REPORT.md) |
| Predictive, 0.1 s steps, through FC (B at GA 1) | 5.34 and 5.56 ps | [precision](../results/precision-20261105/REPORT.md) section 8 |

Each step shortened the time over which the algorithm has to predict the clocks, or predicted them better. Over 60 s θ − θ₀ averages to 1.7-1.9 ps; over hours θ's mean stays at its target to about 0.1 ps.

### 6.4 The output PPS

On one board, against its own 10 MHz, the output PPS scatters by 7.7 ps per second, the scope's resolution, and averages as white noise to 1.0 ps at 64 s; through ten PP moves it moved by exactly the whole ticks PPS_TICK moved (3.1 ps rms), where the FS725's 1PPS missed them by 40-260 ps ([output PPS report](../results/pps-out-20261104/REPORT.md)). Between the two nodes with B steered:

| B's output PPS against A's | Per second | Over 60 s |
| ---------------------------------- | -------------- | ------------ |
| plain, B steered through FC | 9.19 ps | 2.0 ps |
| θ-compensated, B steered through SF | 8.75 and 8.62 ps | 0.92 and 1.00 ps |
| the scope's own resolution (one output against its 10 MHz) | 7.8-8.2 ps | |

### 6.5 Reproducibility

- **Over power-ups**: the zero-baseline θ held within 7.2 ps over 20 power-ups in 1.x ([M4 report](../results/m4-twtt-20261021/REPORT.md)); θ₀ of today's wiring has an sd of 1.95 ps over four power-ups. The fixes behind this are in section 7.2.
- **Over a new start and acquisition**: the difference of the two output PPS, with θ − θ₀ taken out, came back within 0.9 ps over a PL.open of both boards and a new acquisition with PP, where the FS725's 1PPS moved by 145 ps ([output PPS report](../results/pps-out-20261104/REPORT.md) section 8); the θ-compensated output's constant came back within 0.1 ps ([precision report](../results/precision-20261105/REPORT.md) section 7).
- **Not yet tested**: power cycles of the whole bench, temperature steps, days-long runs (section 8.1).

### 6.6 What limits it now

θ − θ₀ is the clocks' variation that the algorithm cannot predict over the actuator's lag. Through FC the lag is the FS725's 1 s RC; the error of predicting θ 1 s ahead is 12-13 ps from its own past, and neither a longer AR model nor a small neural network predicts it better out of sample (the residual is unpredictable noise). In replay, telling the algorithm the next 1 s of the clocks halves its error. So the remaining routes are a faster actuator into a quieter oscillator, for example an external oven-controlled crystal oscillator (OCXO) with a fast tuning input, disciplined by the link (section 8.3), or a better knowledge of the crystal's response (a less conservative algorithm; fragile). The measurement itself (0.8 ps per pair) is not the limit.

## 7. Problems found and lessons learned

This section keeps the problems that changed a measurement or a design, with the lesson; the full chains are in [docs/debug_overview.md](debug_overview.md) and the reports.

### 7.1 The bench

- **A split sine reference was too weak.** The FS725's 10 MHz through a resistive splitter left 0.7 Vpp at each board; the LMK04828 fell into holdover and the tick ran 0.1 ppm slow. The EPG1's locked square output fixed it. *Lesson: check the tick and PPS (M1: exactly 100 000 000 ticks per second) after any change of the reference path.*
- **An unlocked pulse generator walks the PPS.** The EPG1 retimes its trigger on its own clock; unlocked, that clock drifts against the boards and the PPS walked across ticks. Its reference input must be on the FS725, and its trigger threshold away from its clock edge (1.3 V, not 1.0 V).

### 7.2 The clock chain: picoseconds that move at power-up

The two-way measurement cancels everything common to both directions, so a clock phase that changes at power-up shows only as a step of θ. Each of these was found as such a step and fixed so that every power-up comes up in one state:

| Where | What moved | Size | Fix |
| ----------- | ---------------------- | -------- | ------------------ |
| LMK04828 PLL1 | not in zero-delay mode: tick phase not tied to the 10 MHz | up to a tick | nested zero delay (1.2.1.1) |
| LMK04828 dividers | not synchronized after PLL2 started, in 0.4 % of power-ups | 1.67-2.08 ns | the data sheet's SYNC after writing ([LMK report](../results/lmk-divider-sync-20261025/REPORT.md)) |
| LMX2594 output divider | started on any VCO edge | 26 ps steps | VCO phase SYNC ([LMX report](../results/lmx-phase-sync-20261019/REPORT.md)) |
| LMX2594 VCO band | recalibrated at every clock write, one of two bands | 8-10 ps | band forced from the board config ([loopback report](../results/lmx-sync-loopback-20261019/REPORT.md)) |
| RFDC SYSREF capture | window picked anew each power-up | one DAC sample, 104 ps | capture codes recorded per board ([M4 report](../results/m4-twtt-20261021/REPORT.md) section 4) |
| RFDC NCOs | started at an arbitrary instant | 120° of carrier phase | restarted at a SYSREF edge |
| RFDC latency | changes with the bitstream | 2 samples | fixed MTS targets |

*Lessons: a constant that is calibrated once must be shown to survive power-ups and clock writes; test over hundreds of power-ups (a 0.4 % event needed 1200); and record per board whatever a chip decides by itself at start-up.*

### 7.3 The RF path

- **The DAC's 3.8 GHz component.** The DAC transmits 3.8 GHz about as strongly as 5.8 GHz, and the ADC at 4.8 GS/s folds both onto the same 1.0 GHz; without a band-pass filter in each path θ scattered by 120-150 ps per run instead of 1.5 ps ([band-pass report](../results/bpf-compare-20261022/REPORT.md)). The filters are part of the design.
- **The envelope moves against the carrier.** Across power cycles the envelope timing moved by 37-59 ps while the carrier phase stayed within ±6 ps; the channel's group delay varies by ±0.5-0.9 ns across the burst band, so small changes of its shape move the envelope (open, section 8.1).

### 7.4 The PPS and the FS725

- **A PPS edge on a sampling edge** toggles between ticks: θ stepped by 10 ns between power-ups. The margin must be measured (`experiments/m1_pps_phase_scan.py`) and kept above about 3 ns; A times its Schmitt-trigger copy of the PPS (`--pps-trig`) because its comparator copy sat on a sampling edge.
- **The FS725's PP misses** by up to 0.23 ns and its 1PPS jitters by 22-44 ps: any constant measured on the 1PPS holds only until the next PP. Hence the output PPS.
- **The FS725's frequency lock loop sets the steering's delay** (an integrator around a 1 s filter); its model must be the unit's own at the gain in use (one unit's integrator is 1.7 times the other's at the same gain), measured from FC alone by `fs725_loop`.
- **Stopping the loop stops the 1PPS.** `LO 0` or GA 0 makes the unit report itself unlocked, and its LOCK/1PPS pin, which pulses only while locked (PRS10 manual p. 20), stays high; B's PL lost its PPS and its slot engine stopped. GA 1 keeps the unit locked and lets a written FC stay.
- **A unit that has had no SF command since power-up reads SF from its calibration pot**, which flickers by one unit; the first SF command hands SF over until the next restart.

### 7.5 Measurement practice

- **Outside checks catch what θ cannot see**: both boards moving together (a shared clock-chain state) is invisible to the two-way measurement; the scope showed it.
- **Oscilloscope traps**: the LeCroy's math traces keep the range of their first computation (clipped traces gave wrong delays), and its remote interface wedges under pyvisa-py; the scripts handle both. Timing a burst by its carrier (“the Nth peak”) moved with the carrier phase by up to 10 ns; use the envelope.
- **Temperature**: each board's clocks lag by about 1-1.5 ps per kelvin of its die; the die temperature is logged every second.
- **Replay before the bench**: replaying recorded free-running clocks through a steering algorithm predicted every bench result within about 1 ps, and found the failure modes (a model faster than the FS725's response oscillates) without risking the hardware.

## 8. Open issues and future work

### 8.1 Open on the bench

- The PPS difference on a time-interval counter, and the output PPS's absolute delay to J20 (D_board) stored per board.
- Power cycles of the whole bench, a deliberate temperature step of one board at a time, runs of days.
- The envelope against the carrier phase (section 7.3): where the channel's shape changes; whether to time θ by the carrier phase.
- SYNCED's thresholds with today's θ − θ₀; whether the AC-coupled pulse on J20 is enough as the system's PPS or a logic-level PPS is needed.
- PPS distribution without the EPG1's retiming for the one-reference experiments; the forced VCO bands at other temperatures; 887329260101's band table.

### 8.2 Towards M6: 48 km across Long Island Sound

- **Antennas**: undecided. Two parabolic dishes for the long link; a pair of L-com HG4958-19DP panels (17 dBi at 5.85 GHz) proposed for local over-the-air tests. Separate TX/RX antennas or one with a T/R switch (the PL would drive the switch at its ticks).
- **The path**: over water the sea surface reflects strongly at grazing angles, with a reflected ray only about 0.2 ns behind the direct one; ducts and tides change the path. Two-way cancels the path only if each node's channel is reciprocal (the same antenna and frequency both ways, within the 2 ms ping-pong); the earliest-peak report of the PL's search is there for multipath.
- **Link budget and the packet**: the spreading factor of the measurement packet (64 chips per bit today) and the receive window length follow from the measured signal-to-noise ratio and from how well the distance is known (A's first catch of B needs the delay to within ±3.4 µs).
- **The outside check** across 50 km of water cannot be a common oscilloscope: a portable clock carried between the sites, or GNSS common-view, is needed.
- **Regulations**: transmit power and occupied bandwidth in the 5.8 GHz ISM band.

### 8.3 Better precision

- **A faster, quieter oscillator on B**: an OCXO with an electronic tuning input of tens of Hz bandwidth, disciplined by the link (and by its FS725 when the link is down), would cut the actuator's lag from 1 s to about 0.1 s, where the prediction error is about 1 ps, and is quieter than the PRS10's crystal over 1 s; estimated 1-2 ps (inferred). This changes the design rule that each node's clocks come from its FS725 ([AGENTS.md](../AGENTS.md)) and needs a decision.
- **A third reference** for a three-cornered hat, to tell which unit's crystal dominates.
- **A precisely measured crystal response** (lag and gain to ±2 %) for a less conservative algorithm (about 3-4 ps in replay; fragile).

### 8.4 Security and robustness

[plan/ideas.md](../plan/ideas.md) discusses a code that changes every slot from a shared key (so that nobody can send a burst before the real one), an authentication code on the measurement packet, a check of the one-way delay against its calibrated value, and one shared reciprocal RF path through circulators. Its framing predates rf-clock 2 (it assumes the 1.x exchange over Ethernet); in rf-clock 2 the records travel in the bursts, so the authentication belongs on the measurement packet.

## 9. Handover checklist

What the author's departure changes, and what the lab should do:

| Item | Today | To do |
| ----------- | ------------------------- | ------------------------- |
| Repository | GitHub under a personal account (`FHYQ-Dong/RF-clock`); a copy on the lab's Gitea (`haoyu.dong/RF-clock`) | move or fork both into a lab-owned organization; make the lab's copy the one releases come from |
| `results/` | on the author's machine and RF-clock-PC; a mirror on the NAS updated every 4 hours from the author's machine | the mirror stops; keep the NAS copy as the archive; new experiments write on RF-clock-PC |
| RF-clock-PC's project directory | `~/Program/RF-clock` is kept in sync with the author's machine by Syncthing and has no Git metadata | clone the repository there afresh (with Git LFS), create its `.venv` (`uv sync`), and stop the Syncthing share |
| SSH access | an SSH key for AI tools on RF-clock-PC (`RF-clock-PC-ai`); the boards accept RF-clock-PC's key | remove keys that are no longer needed; give lab members their own accounts on RF-clock-PC |
| Board configs | on each board's SD card, copies in `boards/` | keep the copies current after any calibration |
| Signing | commits and tags signed with the author's key | lab members sign with their own keys |
| Release wheels | on GitHub and Gitea | keep releasing from the lab's repository |

## 10. References

Data sheets and manuals in the repository or online:

1. Stanford Research Systems, *PRS10 Rubidium Frequency Standard, Operation and Service Manual* (`docs/PRS10m.pdf`), and *FS725 Rubidium Frequency Standard* manual.
2. Real Digital, *RFSoC 4x2* reference manual and schematic (`docs/rfsoc4x2_sch.pdf`); board files `RealDigitalOrg/RFSoC4x2-BSP`.
3. AMD/Xilinx, *Zynq UltraScale+ RFSoC RF Data Converter LogiCORE IP Product Guide* (PG269), multi-tile synchronization.
4. Texas Instruments, *LMK04828* and *LMX2594* data sheets.
5. Mini-Circuits, *ZVBP-5800-S+* data sheet.
6. IEEE Std 1139, *Standard Definitions of Physical Quantities for Fundamental Frequency and Time Metrology*, for the Allan deviation.
7. D. W. Allan, N. Ashby, C. C. Hodge, *The Science of Timekeeping*, HP Application Note 1289 (1997), an introduction.
8. ITU-R TF.1153, *The operational use of two-way satellite time and frequency transfer employing pseudorandom noise codes*.
9. H. Hong et al., “Quantum two-way time transfer over a 103 km urban fiber”, J. Lightwave Technol. 42, 1479 (2024).
10. L. Narula and T. E. Humphreys, “Requirements for secure clock synchronization”, IEEE J. Sel. Topics Signal Process. 12, 749 (2018).

Project documents: [plan/RF_clock_modules.md](../plan/RF_clock_modules.md), [docs/ABI.md](ABI.md), [docs/board_setup.md](board_setup.md), [docs/debug_overview.md](debug_overview.md), [docs/fs725_principles.md](fs725_principles.md), [plan/pps_output.md](../plan/pps_output.md), [plan/checklist.md](../plan/checklist.md), [plan/ideas.md](../plan/ideas.md), [docs/handoff_2x.md](handoff_2x.md), [AGENTS.md](../AGENTS.md).

## Appendix A. Glossary

| Term | Meaning |
| --------------- | --------------------------------------------- |
| θ (theta) | B's clock reading minus A's at the same instant, from the two-way measurement |
| θ₀ | θ with both boards on one clock: the target B is steered to; per pair of boards and wiring (`links/`) |
| θ − θ₀ | while B is steered: the part of the clocks' relative variation the loop has not yet taken out |
| variation | the free-running clocks' movement about their drift line |
| two-way time transfer (TWTT) | timing signals sent both ways so that the path delay cancels |
| one-way delay | (m_AB + m_BA)/2: path plus hardware delays, a diagnostic |
| non-reciprocity Δ | the difference of the two directions' hardware delays |
| slot | one 2 ms turn of the ping-pong, identified by `PPS_COUNT` and its slot number (0-499) |
| burst | what a node transmits in its slot: PRN, guard, measurement packet |
| window | what a node receives in the peer's slot |
| measurement packet | the data behind the PRN in a burst: the sender's last measurement and its send time |
| acquisition | B finding A's seconds in a long window before the slots start; until then the bursts use a fixed, known code |
| tracker, search | the PL's correlation around the expected peak in every window; the whole-window correlation when lock is lost |
| PRN, chip, RRC | pseudo-random code; one of its ±1 symbols (10 ns); the pulse-shaping filter |
| tick | one period of the PL's 100 MHz clock (10 ns); `PPS_TICK` is the tick of a second's edge, `PPS_COUNT` the second's number |
| output PPS | the PPS edge the PL makes from its tick on DAC_B (J20), not the FS725's 1PPS, which is the node's PPS input |
| plain / θ-compensated output PPS | the output PPS at a fixed place against the 10 MHz / moved by θ − θ₀ before each edge |
| FS725, PRS10 | the rubidium frequency standard; the oscillator inside it |
| frequency lock loop (FLL) | the FS725's loop holding its crystal to the rubidium transition (not its 1 pps phase lock, PL) |
| SF, FC, GA, PP | the FS725's frequency offset command; its crystal's tuning; its loop gain; its 1PPS placement |
| steering through FC | the predictive steering algorithm setting FC with B's unit at GA 1 (`--steer-actuator fc`) |
| predictive / Kalman steering algorithm | the two ways of steering: `PredictiveSteering` (model, prediction, planned changes) and `Steering` (Kalman filter, time constant) |
| Allan deviation, TDEV | the stability of a frequency / of a time error over an averaging time τ |
| PS, PL, RFDC | the RFSoC's ARM processors, FPGA fabric and RF data converters |
| MTS, SYSREF | multi-tile synchronization; the common signal it aligns the converter tiles to |
| VCO band | the band an LMX2594's VCO calibrates to; forced per board |
| capture code (T1 / PLL capture code) | where an RFDC tile captures SYSREF; recorded per board |
| MTS offset | the delay MTS adds to bring a tile to its target latency |
| loopback delay | a board's own DAC → cable → ADC delay (M3) |
| board config | `/etc/rf-clock/board.json`: what belongs to one board |
| M0-M6 | the milestones of the design, section 1.3 |
| EPG1 | the Thorlabs pulse and delay generator used to distribute one reference |

## Appendix B. Command reference

On a board (`run` as in section 4.3):

```bash
$run -m rf_clock.checks.m1_check 60                    # tick against the PPS
$run -m rf_clock.checks.m2_tx_timing 10                # MTS and timed bursts
$run -m rf_clock.checks.m3_loopback 20                 # loopback delay (own DAC_A -> ADC_B cable)
$run -m rf_clock.checks.vco_bands scan --write --label <S/N>
$run -m rf_clock.checks.mts_dtc record --write; $run -m rf_clock.checks.mts_dtc check
$run -m rf_clock.checks.fs725_loop --port PORT --gain 8 --write   # the FS725, its port and loop into the board config
$run -m rf_clock.checks.pps_pp_scan --fs725 PORT       # where the PPS edge lies in its tick
$run -m rf_clock.fs725 status --port PORT              # the FS725's state; also: gain N, sf N
$run -m rf_clock.checks.pps_out 60 --offset 20 --amplitude -24000   # output PPS alone
```

On RF-clock-PC (`cd ~/Program/RF-clock`):

```bash
.venv/bin/python experiments/m4c_v2_link.py --scenario steer --seconds 1500 "--node-args=--pps-out 20" \
    "--a-args=--pps-trig --fs725" --scope --scope-out-pps
.venv/bin/python experiments/m4c_v2_link.py --scenario free --seconds 600 "--a-args=--pps-trig" --scope
.venv/bin/python experiments/m4c_check.py scenario results/<run>
.venv/bin/python experiments/m5_pps_frac.py            # calibrate the output PPS fraction on B
.venv/bin/python experiments/m5_fll_off.py             # B's crystal at GA 1 and its answer to FC
sudo .venv/bin/python experiments/m5_hdo_phase.py --seconds 600 --out results/x/hdo.jsonl [--pps|--out-pps2]
.venv/bin/python experiments/epg1.py status
source /tools/Xilinx/Vivado/2024.1/settings64.sh; xsdb tools/jtag_reset_board.tcl <JTAG serial>
```

On a development machine:

```bash
uv sync; uv run pytest; uv run pyright; ./PL/sim/run_pl_tests.ps1
uv run python -m rf_clock.examples.twtt.02_fake_steer
```

## Appendix C. Troubleshooting

| Symptom | Likely cause | What to do |
| ------------------- | ------------------- | ---------------------- |
| The board does not answer over SSH | a PL access hung the bus | JTAG reset (section 4.3); power cycle if that fails |
| `PL.open()`: `BringupError: PL pps_locked not set` | no PPS at J7 or no 10 MHz | run M1; check the cables |
| `BringupError: LMX2594 not locked` | a forced VCO band does not lock (temperature?) | redo `vco_bands scan --write`, `mts_dtc record --write` and the band table |
| `BoardConfigError` | config for another board, malformed, or from a newer release | check the S/N; install 2.3.0.2 or later on B |
| A warning about MTS offsets or capture codes | the converters came up in another state | open the PL again; if it persists, redo `mts_dtc record` |
| θ steps by 10 ns between power-ups | a PPS edge near a sampling edge | measure the PPS capture margin; move the edge (EPG1 delay, `--pps-trig`, PP) |
| θ scatters by tens to hundreds of ps | no band-pass filter in a path, weak signal | check the filters, attenuators and the quality figure |
| The link never locks | wrong one-way delay, peer not transmitting, wrong cable | check `--arrival-ticks`, the wiring, the other node's log |
| The steering stops (`steer_stopped`) | the FS725 restarted, lost lock, or was changed by someone | read the reason; check the unit with `rf_clock.fs725 status` |
| B's slot engine stops after an FS725 command | the FS725's loop was stopped (`LO 0`/`GA 0`): no 1PPS | `GA 8` (or the config's gain) and `LO 1` |
| `PL.open()` refuses the bitstream | package and bitstream versions differ | install the matching wheel |

## Appendix D. Repository map

| Path | Content |
| ---------------------- | -------------------------------------- |
| `PL/src/`, `PL/sim/`, `PL/bd/`, `PL/constraints/` | RTL (P0-P10; `p_regs.vh`, `p_defs.vh`), testbenches, the Vivado block design script, constraints |
| `src/rf_clock/` | the Python package (section 3.9); `overlay/` the bitstream; `clocks/` the LMK/LMX register files |
| `tests/` | unit tests (no hardware) |
| `experiments/` | bench experiments run on RF-clock-PC, `m<N>_*.py` one question of milestone M<N> each |
| `tools/` | ABI generator, overlay packaging, release notes, version snapshots, results backup, JTAG reset |
| `boards/` | each board's config and its FS725's settings |
| `links/` | θ₀ per pair of boards and wiring |
| `docs/` | this report, the ABI manual, board setup, debug overview, FS725 principles, the 1.x → 2.x handoff, data sheets |
| `plan/` | design, checklist, output PPS proposal, ideas |
| `results/` (not in Git) | every run and its report; NAS mirror (section 4.4) |

## Appendix E. Index of reports

| Report | Date | Question and answer |
| ---------------- | ----- | ---------------------------------------- |
| [dac-spectrum](../results/dac-spectrum-20261017/REPORT.md) | 10-17 | The DAC's output spectrum: 3.8 GHz about as strong as 5.8 GHz |
| [pps-phase-scan](../results/pps-phase-scan-20261018_203329/README.md) | 10-18 | Where each board's PPS edge lies within its tick |
| [lmx-phase-sync](../results/lmx-phase-sync-20261019/REPORT.md) | 10-19 | Half-sample timing states from the LMX2594 divider; phase SYNC fixes them |
| [lmx-sync-loopback](../results/lmx-sync-loopback-20261019/REPORT.md) | 10-19 | The same through the loopback; the VCO band's 8-10 ps; the NCO phase |
| [vco-bands](../results/vco-bands-20261020_214500/README.md) | 10-20 | VCO band scan and band delays of 887329260072A |
| [m2-restarts](../results/m2-restarts-20261016_212509/REPORT.md) | 10-16 | Burst timing over 105 restarts |
| [m4-twtt](../results/m4-twtt-20261021/REPORT.md) | 10-21 | 1.x two-way transfer on one FS725: 3 ps per second; the SYSREF window flip; temperature |
| [bpf-compare](../results/bpf-compare-20261022/REPORT.md) | 10-22 | Which band-pass filter: the 3.8 GHz image must be removed |
| [lmk-divider-sync](../results/lmk-divider-sync-20261025/REPORT.md) | 10-25 | Rare 1.7-2.1 ns jumps from the LMK04828 dividers, and the fix |
| [pl-fft-fixed-point](../results/pl-fft-fixed-point-20261031/README.md) | 10-31 | An FFT correlator in the PL (studied, not adopted) |
| [v2-link](../results/v2-link-20261103/REPORT.md) | 11-03 | rf-clock 2 on two boards: 0.8 ps per pair, protocol tests, three faults |
| [m5-v2](../results/m5-v2-20261103/REPORT.md) | 11-03 | Two clocks: acquisition, the PPS in its tick, first steering (66 → 39 ps), the PPS pair |
| [pps-out](../results/pps-out-20261104/REPORT.md) | 11-04 | The output PPS on the bench: 7.7 ps against its 10 MHz, 0.9 ps over a restart |
| [fs725-fll](../results/fs725-fll-20261105/REPORT.md) | 11-05 | B's FS725 frequency lock loop seen through FC; GA 8 |
| [predictive-steering](../results/predictive-steering-20261105/REPORT.md) | 11-05 | The predictive steering algorithm: 27-29 ps |
| [two-loops](../results/two-loops-20261105/REPORT.md) | 11-05 | Both loops beside θ; the algorithm on 0.2 s steps: 15 ps |
| [precision](../results/precision-20261105/REPORT.md) | 11-05/06 | What limits the steering; the θ-compensated output PPS; steering through FC: 5.3-5.6 ps |
