# RF-clock design plan: 5.8 GHz two-way time transfer steering an FS725

> This is a fresh design. It does not inherit the earlier H00-H19 module split
> or the existing RTL. Principle: **all algorithms run on the ARM; the PL only
> provides deterministic time** — it keeps a raw tick, transmits/captures at an
> exact tick, and anchors each sample block to that tick.
>
> The Chinese version, `RF_clock_modules_zh.md`, has the same content.

## 1. Goal

Two nodes, A (master) and B (follower), each with an SRS FS725 rubidium standard and an RFSoC board. The goal is to **steer B's FS725 so that its 10 MHz and 1PPS follow A**:

- 10 MHz: B is syntonized to A;
- 1PPS: B's PPS edge is aligned with A's PPS edge.

The measurement is 5.8 GHz over-the-air two-way time transfer (TWTT): A and B exchange known PRN bursts, each node measures the arrival time of the other's burst, and differencing the two directions cancels the propagation delay and leaves the clock offset.

## 2. System structure

~~~text
 Node A (master)                                      Node B (follower)
 FS725_A                                              FS725_B  <── RS-232 steering ─┐
  │10 MHz   │1PPS                                     │10 MHz   │1PPS                 │
  v         v                                         v         v                     │
 board PLLs  PL: PPS capture                          board PLLs  PL: PPS capture     │
  │(LMK/LMX)                                          │                               │
  v                                                   v                               │
 RFDC DAC ─ BPF ─ PA ─ TX ant ~~~~ 5.8 GHz ~~~~> RX ant ─ LNA ─ BPF ─ RFDC ADC        │
 RFDC ADC ─ BPF ─ LNA ─ RX ant <~~~ 5.8 GHz ~~~~ TX ant ─ PA ─ BPF ─ RFDC DAC        │
  │                                                   │                               │
 ARM_A: waveform/correlation ── Ethernet (exchange measurements) ── ARM_B: TWTT/Kalman ┘
~~~

Key premise: **on each board, the DAC/ADC sample clocks, the PL fabric clock and the raw tick are all derived from that node's FS725 10 MHz through the board PLLs.** The raw tick is then the FS725's time scale; steering FS725_B moves node B's whole timebase and closes the loop.

### 2.1 Responsibilities

| Responsibility | ARM (Python first) | PL |
| --- | --- | --- |
| PRN generation, RRC shaping, quantization, packing | yes | no |
| Per-second TDD slot plan, target_tick computation | yes | no |
| Correlation, peak quality, fractional delay | yes | no |
| TWTT, Kalman filter, FS725 steering | yes | no |
| raw tick counting, PPS capture | no | yes |
| Gap-free TX burst playback from on-chip buffer at target_tick | no | yes |
| RX capture window starting at target_tick | no | yes |
| Actual start tick recording, fault detection | no | yes |
| RFDC configuration and MTS | via driver | IP and clocks |

The PL contains **no** DSP: no PRN generator, no FIR, no correlator, no carrier recovery.

## 3. RF frequency plan (accepted by the RFDC IP; not yet verified on the board)

| Item | Value | Notes |
| --- | --- | --- |
| Carrier | 5.8 GHz | centre of the 5.725–5.875 GHz ISM band, ~150 MHz wide |
| RFDC reference clock | 480 MHz | separate reference for each of ADC tiles 0/2 and DAC tiles 0/2, derived by LMK/LMX from the FS725 10 MHz |
| DAC sample rate | 9.6 GS/s | DAC tile 2 block 0 (`vout20`); IMR high-pass datapath, interpolation ×6 then ×2 (×12 total), NCO 3.8 GHz, mix-mode. 5.8 GHz is the 2nd-Nyquist-zone (4.8–9.6 GHz) image of 3.8 GHz, at about 0.604 fs; the 3.8 GHz component is removed by the TX BPF |
| ADC sample rate | 4.8 GS/s | ADC tile 2 block 0 (`vin2_01`); 5.8 GHz is undersampled in the 3rd Nyquist zone (4.8–7.2 GHz), folds to 1.0 GHz and is mixed to baseband by NCO −1.0 GHz; the RX BPF must reject anything else that would fold in |
| Complex baseband rate Fs | 800 MS/s | DAC interpolation ×12, ADC decimation ×6 |
| PRN chip rate | 100 Mchip/s | 8 samples per chip; one chip is exactly one tick |
| RRC roll-off β | 0.25 | occupied bandwidth 100 × 1.25 = 125 MHz, inside the ISM band |
| Fabric / raw tick clock | 100 MHz | exactly 8 complex samples per tick; tick = 10 ns |
| AXIS width | 256 bit | 8 × (16-bit I + 16-bit Q), same for DAC and ADC |

Every frequency above is an integer multiple of 10 MHz, so the mapping from sample index to raw tick is an exact integer relation.

**MTS tile requirements:** deterministic latency needs RFDC MTS. RFDC 2.6 (Vivado 2024.1) offers MTS only when at least two tiles of a kind take part and block 0 of every participating tile is enabled. The RFSoC 4x2 brings out ADC and DAC tiles 0 and 2, each with its own reference clock, so tile 0 joins MTS with the same datapath on its own PLL and its data is unused. On-chip clock distribution is not used (a distributing DAC tile is limited to 7.0 GS/s). Evidence: `plan/checklist.md` §1 and §3.

**Spectral inversion:** 5.8 GHz is a 2nd-Nyquist-zone image, so the transmitted RF spectrum is inverted relative to baseband; the ADC works in the 3rd (odd) zone and does not invert. The received baseband is therefore the conjugate of the transmitted one. BPSK PRN shaped by a real RRC is real, so this has no effect; a future complex waveform needs a conjugated correlation template.
### 3.1 RRC pulse-shaping parameters (used by ARM waveform generation)

| Parameter | Value | Notes |
| --- | --- | --- |
| `RRC_CONFIG_ID` | 2134958710 | identifies the filter: a hash of the RRC parameters, whose coefficients are computed at run time (`rrc_config()`) |
| `RRC_SAMPLES_PER_SYMBOL` | 8 | 8 samples per chip |
| `RRC_SPAN_CHIPS` | 10 | filter span of 10 chips |
| `RRC_TAP_COUNT` | 81 | = 10 × 8 + 1 |
| `RRC_TAIL_SAMPLES` | 73 | = 81 − 8; burst length = chips × 8 + 73 |
| `RRC_BETA` | 0.25 | roll-off |

- The ARM convolves the upsampled BPSK chips with the float64 coefficients from `RRC_coeff`, quantizes the result to the DAC's 16-bit I/Q, packs 256-bit beats and writes them into the TX buffer;
- the receiver's local correlation template must be generated from the **same config_id**; `config_id` is stored in every measurement record, and a measurement whose two ends disagree is rejected;
- RRC is applied once, at the transmitter. In the first version the receiver correlates directly against the "PRN ⊛ RRC" template (equivalent to a matched filter) instead of running a separate receive RRC.

**Must verify:** ADC input bandwidth and performance at 5.8 GHz, DAC output power at about 0.604 fs in mix-mode, and whether the board clock chain accepts an external 10 MHz reference. Transmit power must comply with local 5.8 GHz ISM regulations.

## 4. Time model and TWTT equations

### 4.1 Local time

Each PL keeps a never-reset 64-bit `raw_tick` at 100 MHz and captures `pps_tick[k]` on each PPS edge. Local time is "second k plus offset from PPS":

~~~text
T_local = k + (tick - pps_tick[k]) * 10 ns + (offset within the samples)
~~~

A TX burst starts at `tx_tick`; an RX window starts at `rx_tick`; the ARM correlation gives the peak position τ̂ in samples, including a fractional part:

~~~text
T_tx = local time of tx_tick
T_rx = local time of rx_tick + τ̂ / Fs
~~~

### 4.2 Two-way equations

Let θ = T_B − T_A (B's reading minus A's reading at the same true instant), δt/δr the transmit/receive hardware delays of each node, and p the air path delay:

~~~text
m_AB = T_rx,B − T_tx,A = δt_A + p + δr_B + θ      (measured by B)
m_BA = T_rx,A − T_tx,B = δt_B + p + δr_A − θ      (measured by A, sent to B over Ethernet)

θ = (m_AB − m_BA)/2 − Δ/2,   Δ = (δt_A + δr_B) − (δt_B + δr_A)
~~~

- The air path p cancels (same frequency and antenna positions, assumed reciprocal);
- the non-reciprocity Δ must be calibrated with a zero-baseline setup (§8);
- the two slots are not simultaneous; the ARM uses the Kalman frequency estimate to bring both measurements to one epoch;
- `(m_AB + m_BA)/2` is the one-way delay, logged as a diagnostic.

### 4.3 Carrier offset

The relative frequency offset of two FS725s is about 1e-11, which gives a carrier offset of about 0.06 Hz at 5.8 GHz. That is negligible over a burst of tens to hundreds of microseconds, so the first version uses **non-coherent correlation** (magnitude of the complex correlation) and needs no carrier recovery. Carrier-phase measurement (more precise, but with cycle ambiguity) is a later extension.

## 5. Slot plan (TDD)

One measurement cycle per second, referenced to the local PPS (candidate values):

~~~text
PPS + 100 ms : A transmits, B's capture window is open
PPS + 600 ms : B transmits, A's capture window is open
~~~

- Burst length: a 4095-chip PRN × 8 + 73 = 32833 complex samples ≈ 41 µs; 65535 chips (524353 samples, ≈ 655 µs) if more processing gain is needed;
- RX window = burst length + clock-offset uncertainty + filter tails, about 100 µs while tracking (80000 complex samples, about 320 KB);
- a node never opens its RX window while it is transmitting;
- the cycle period and slot offsets are planned by the ARM; the PL only executes target ticks.

### 5.1 Initial acquisition

At start-up θ may be as large as a second, which a 100 µs window cannot cover. Two steps:

1. **Second labels and coarse time:** the two ARMs align to millisecond level over Ethernet (NTP/PTP) and agree on the second number of each PPS;
2. **Coarse search:** B covers the coarse-time uncertainty with one long capture window (for example a few to tens of milliseconds, written straight to DDR by P4), or sweeps several windows around the expected position, until a correlation peak passes the quality threshold; then it switches to the tracking window.

If PPS_B differs from PPS_A by far more than frequency steering can remove, a one-off coarse PPS alignment is needed (§10, item 4).

## 6. PL modules

All timing-relevant logic is in one clock domain: the RFDC DAC AXIS, ADC AXIS, raw tick, TX buffer and RX timestamping all run on the same 100 MHz fabric clock derived from the FS725, so there is no CDC in the sample path before timestamping. The only CDC is the asynchronous FIFO after the RX timestamp (into the DMA clock domain), which does not affect timing. The AXI bus enters the fabric domain through a clock converter in the block design.

### P0 clock_reset: clocks and reset

- External 10 MHz → board LMK/LMX → RFDC reference clock and fabric clock;
- monitor PLL lock and RFDC status; on loss of lock, latch FAULT and mark all timestamps invalid;
- increment `time_epoch` after every re-lock.

### P1 timebase_pps: raw tick and PPS capture

- 64-bit `raw_tick`, incremented every fabric clock, never reset at frame boundaries, saturating rather than wrapping;
- synchronize the FS725 1PPS input, capture `pps_tick`, and check that every period is exactly 100,000,000 ticks; report deviations and missing pulses;
- **Note:** 10 MHz and PPS are coherent, so after each power-up the PPS edge has a fixed but unknown phase relative to the fabric clock. If that edge sits next to the sampling edge, the capture can toggle between adjacent ticks. Detect this from the PPS period statistics and move it away with IDELAY if needed. The sub-tick phase is a per-power-up calibration constant (§8).

### P2 control_regs: AXI-Lite control and status

- Read-only: ID/version, atomic `raw_tick` snapshot, latest `pps_tick` and PPS count, `time_epoch`, state, fault bits;
- commands: `TX_TARGET_TICK`, `TX_LENGTH`, `RX_TARGET_TICK`, `RX_LENGTH`, written to shadow registers first and committed atomically by a doorbell;
- results: `tx_actual_tick`, `rx_actual_tick`, completion sequence, return code;
- reject a target_tick that is already stale (≤ current tick + safety margin), a length larger than the buffer, and a resubmission while ARMED/RUNNING.

### P3 tx_player: timed TX playback

- The TX waveform buffer is BRAM/URAM, written by the ARM through an AXI BRAM interface; a 4095-chip burst is about 128 KB; a 65535-chip burst is about 2 MB, close to the total URAM; an unchanged waveform is written only once;
- states: IDLE → ARMED → RUNNING → DONE / FAULT;
- starting on the cycle where `raw_tick == TX_TARGET_TICK`, output one beat (8 complex samples) per clock without a gap until the length is reached; output zeros outside the burst;
- an ARM write to the TX buffer while ARMED/RUNNING is an error: the waveform being transmitted cannot change;
- a drop of the DAC `tready` while RUNNING is a FAULT: no pause, no retry;
- record the actual start tick (it should equal the target).

### P4 rx_capture: timed RX capture

RX samples go to DDR and do not occupy large BRAM. Windows need not be contiguous with each other, but **no sample may be lost inside a window**: sample time is derived from "start tick + sample index", so one lost beat shifts the time of every later sample.

~~~text
ADC AXIS ─> window gate + timestamp ─> async FIFO ─> width conv. ─> AXI DMA S2MM ─> HP port ─> DDR
  (100 MHz fabric domain)                          (DMA domain, e.g. 250 MHz)
~~~

- Open the window at `raw_tick == RX_TARGET_TICK`, push every ADC AXIS beat into the FIFO, close the window after the length is reached and mark the last beat with `TLAST`;
- **timestamps are recorded on the ADC side, before the FIFO:** actual start tick, beat count, ADC overflow/over-range flags. DDR write latency and jitter do not affect them;
- the data rate is 800 MS/s × 4 B = 3.2 GB/s. HP ports are 128 bit, so the DMA clock must be ≥ 200 MHz; measure whether this rate is sustained while the ARM also accesses DDR;
- the FIFO only absorbs short DDR stalls (a few KB to tens of KB); it does not hold a whole window;
- before submitting the command the ARM must have an S2MM DDR buffer of sufficient length ready (PYNQ `allocate`). If the DMA is not ready, the PL rejects the command and does not open the window;
- **FIFO overflow inside a window → the window is discarded:** set `rx_window_invalid`, count it, drop that measurement; do not enter FAULT and do not affect TX. This differs from TX: losing an RX window only loses one measurement;
- after DMA completion the ARM invalidates the cache for that buffer before reading samples;
- the same path serves the tracking window (~100 µs) and the long coarse acquisition windows.

### P5 rfdc: RFDC IP

- DAC 2nd-Nyquist mix-mode, ADC decimation, NCOs set for the 5.8 GHz plan;
- **enable Multi-Tile Sync (MTS)/SYSREF** so the DAC/ADC digital-path latency is identical after every power-up; otherwise calibrated constants become invalid at the next boot;
- the ARM initializes RFDC with PYNQ `xrfdc` or the XRFdc driver; the PL only provides the IP and clocks.

### P6 status: status and faults

- Collect faults from P0–P5 into one latched status word with source and sequence number;
- clear faults only with an explicit CLEAR command or a new `time_epoch`;
- polling in the first version, no interrupts.

### 6.1 Not needed in the first version

- **No DDR-streamed TX:** the transmit waveform is fixed and short, so it is played from an on-chip buffer and continuity is guaranteed by construction; only RX uses S2MM DMA;
- no DSP in the PL;
- no scatter-gather DMA and no interrupts: one simple-mode transfer per window, completion by polling.

## 7. ARM software modules

| Module | Function |
| --- | --- |
| `waveform` | m-sequence PRN, BPSK, RRC, quantization, packing in the RFDC lane format, write to the TX buffer |
| `pl_client` | read/write P2 registers, submit TX/RX commands, read RX buffers and metadata |
| `scheduler` | compute each second's TX/RX target ticks from the latest `pps_tick` |
| `correlator` | FFT cross-correlation magnitude, peak quality (peak-to-sidelobe), parabolic or upsampled interpolation for fractional delay |
| `twtt` | pair the two directions by second number and sequence, compute θ and one-way delay |
| `link` | Ethernet messages between the two ARMs (measurements, second number, sequence, epoch) |
| `estimator` | Kalman filter with clock offset θ and relative frequency y, with outlier rejection |
| `fs725` | RS-232 driver: set frequency offset, read status, log every correction |
| `calibration` | store Δ, PPS sub-tick phase, RFDC latency, with version and date |
| `rfdc_mgr` | RFDC initialization, MTS, NCO settings and status readback |

Measurement and steering happen on the scale of seconds, so Python + NumPy is enough. Move a bottleneck to C/C++/Rust only when measurement shows it is needed.

### 7.1 FS725 steering policy

- Only B is steered; A free-runs;
- the first version adjusts only B's frequency offset over RS-232. Frequency error is corrected directly; PPS phase error is removed by temporarily adding a frequency offset so that the phase slews into place;
- the range, resolution and units of the steering command come from the FS725/PRS10 manual;
- the loop time constant is tens to hundreds of seconds, matched to the stability of the rubidium standard.

## 8. Calibration

| Quantity | Method | Redo when |
| --- | --- | --- |
| Non-reciprocity Δ | Zero baseline: both nodes share one FS725's 10 MHz and PPS (PPS distribution cable lengths known), so the true θ is known; solve Δ from (m_AB − m_BA)/2 = θ + Δ/2 | after changing cables, amplifiers, filters, antennas or the RFDC configuration |
| PPS sub-tick phase vs fabric clock | oscilloscope / time-interval counter, or feed PPS into a spare ADC channel and time it at GS/s resolution | every power-up (unless the board PLL phase is shown to be deterministic) |
| RFDC path latency | on-board DAC → attenuator → ADC cable loopback, compared across many reboots | after RFDC configuration changes; also proves MTS works |

Calibration values live in software with a version number. Never subtract a guessed constant in code.

## 9. Milestones and acceptance

| Stage | Content | Acceptance |
| --- | --- | --- |
| M0 | FS725 10 MHz into the board clock chain, PPS into a PL pin | board PLLs locked to the external reference; RFDC clocks correct |
| M1 | P0/P1/P2 | ARM reads exactly 100,000,000 ticks per second; removing the reference raises FAULT |
| M2 | P3 + P5, scope triggered on PPS looking at the DAC output | burst delay relative to PPS is fixed and unchanged across reboots (MTS works) |
| M3 | P4, on-board DAC → ADC cable loopback, correlation on the ARM | standard deviation of the loopback delay and its consistency across reboots; S2MM sustains its write rate without overflow while the ARM also accesses DDR |
| M4 | two boards, cabled with attenuators, sharing one FS725 | Δ from the zero baseline; noise level of θ̂ |
| M5 | each board on its own FS725, cabled, Kalman + FS725 steering loop closed | PPS_A − PPS_B on a time-interval counter converges over the long term |
| M6 | replace cables with the 5.8 GHz BPF, amplifier and antenna air link | same metrics as M5; record multipath and SNR effects |

Every piece of evidence must say whether it comes from simulation, synthesis or board measurement. The final metric is PPS_A − PPS_B measured by an external time-interval counter, not the θ̂ reported by the system itself.

## 10. Open questions

Item-by-item progress and evidence are tracked in `plan/checklist.md`.

1. **Board model and external reference input:** can the board clock chain take a 10 MHz external reference, and can both RFDC and fabric clocks be derived from it?
2. **Antennas:** separate TX/RX antennas, or one antenna with a T/R switch? If shared, the PL must drive the switch at target ticks and protect the LNA.
3. **Inter-node data channel:** Ethernet by default; without it, measurements must be modulated into the RF bursts.
4. **FS725 coarse PPS alignment:** can the PPS phase be jumped once to near the target (for example by syncing to the 1PPS input)? If not, another way to do the initial coarse alignment is needed.
5. **Target accuracy:** ns level is reachable with code correlation; 100 ps or better needs carrier-phase measurement and tighter handling of the PPS sub-tick phase.
6. **Regulations:** 5.8 GHz transmit power and occupied-bandwidth limits.
