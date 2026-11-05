# RF-clock design plan: 5.8 GHz two-way time transfer steering an FS725

> This is a fresh design. It does not inherit the earlier H00-H19 module split or the existing RTL. Principle: **algorithms run on the ARM; the PL provides deterministic time and the per-burst measurement chain** — it keeps a raw tick, transmits and captures at exact ticks and anchors each sample block to its tick, and, because the nodes exchange their measurements inside the bursts of a 2 ms ping-pong (§5), it also shapes the bursts, correlates the windows and carries the measurement packets.
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
 ARM_A: monitoring ─ measurements ride in the bursts (no network) ─ ARM_B: TWTT/Kalman ┘
~~~

Key premise: **on each board, the DAC/ADC sample clocks, the PL fabric clock and the raw tick are all derived from that node's FS725 10 MHz through the board PLLs.** The raw tick is then the FS725's time scale; steering FS725_B moves node B's whole timebase and closes the loop.

### 2.1 Responsibilities

| Responsibility | ARM (Python first) | PL |
| --- | --- | --- |
| Chip sequences of fixed codes (PRN, acquisition code, packet spreading code) and RRC coefficients, loaded into the PL (the correlation reference is made from the same chips by the same FIR) | yes | no |
| Slot plan: period, role, offsets, expected delay | configures it | runs it every slot (slot engine) |
| Chip stream → RRC FIR → DAC, gap-free from the slot's start tick | no | yes |
| RX window starting at the slot's tick | no | yes |
| Correlation, peak quality, sub-sample delay, carrier phase | acquisition and verification only | tracker in every window; whole-window search on loss of lock or request |
| Measurement packet: assemble, spread, despread, CRC | decodes during acquisition | yes |
| Acquisition on long windows in DDR | yes | captures the windows |
| TWTT, Kalman filter, FS725 steering | yes | no |
| raw tick counting, PPS capture | no | yes |
| Output PPS from the tick, its validity line | says when the time scale is synchronized | yes (P10, since 2.1) |
| Actual start tick recording, fault detection | no | yes |
| RFDC configuration and MTS | via driver | IP and clocks |

The PL's DSP is limited to the per-burst chain: the RRC FIR, the correlator (tracker and whole-window search) with its estimator, and the packet's spreading and despreading. It runs in the PL because each measurement must be on air within 2 ms of the burst it measures (§5); everything else stays on the ARM.

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
### 3.1 RRC pulse-shaping parameters (the PL FIR, coefficients from the ARM)

| Parameter | Value | Notes |
| --- | --- | --- |
| `RRC_CONFIG_ID` | 2134958710 | identifies the filter: a hash of the RRC parameters, whose coefficients are computed at run time (`rrc_config()`) |
| `RRC_SAMPLES_PER_SYMBOL` | 8 | 8 samples per chip |
| `RRC_SPAN_CHIPS` | 10 | filter span of 10 chips |
| `RRC_TAP_COUNT` | 81 | = 10 × 8 + 1 |
| `RRC_TAIL_SAMPLES` | 73 | = 81 − 8; burst length = chips × 8 + 73 |
| `RRC_BETA` | 0.25 | roll-off |

- The ARM computes the float64 coefficients (`rrc_config()`), quantizes them and loads them into the PL FIR (P3); the PL shapes each burst's chip stream (PRN, guard, measurement packet) at 8 samples per chip into the DAC's 16-bit I/Q;
- the receiver's local correlation template must be generated from the **same config_id**; `config_id` travels in every measurement packet (§5.1), and a measurement whose two ends disagree is rejected;
- RRC is applied once, at the transmitter. In the first version the receiver correlates directly against the "PRN ⊛ RRC" template (equivalent to a matched filter) instead of running a separate receive RRC.

**Must verify:** ADC input bandwidth and performance at 5.8 GHz, DAC output power at about 0.604 fs in mix-mode, and whether the board clock chain accepts an external 10 MHz reference. Transmit power must comply with local 5.8 GHz ISM regulations.

## 4. Time model and TWTT equations

### 4.1 Local time

Each PL keeps a never-reset 64-bit `raw_tick` at 100 MHz and captures `pps_tick[k]` on each PPS edge. Local time is "second k plus offset from PPS":

~~~text
T_local = k + (tick - pps_tick[k]) * 10 ns + (offset within the samples)
~~~

A TX burst starts at `tx_tick`; an RX window starts at `rx_tick`; the PL correlator gives the peak position τ̂ in samples, including a fractional part:

~~~text
T_tx = local time of tx_tick
T_rx = local time of rx_tick + τ̂ / Fs
~~~

### 4.2 Two-way equations

Let θ = T_B − T_A (B's reading minus A's reading at the same true instant), δt/δr the transmit/receive hardware delays of each node, and p the air path delay:

~~~text
m_AB = T_rx,B − T_tx,A = δt_A + p + δr_B + θ      (measured by B, sent to A in B's next burst)
m_BA = T_rx,A − T_tx,B = δt_B + p + δr_A − θ      (measured by A, sent to B in A's next burst)

θ = (m_AB − m_BA)/2 − Δ/2,   Δ = (δt_A + δr_B) − (δt_B + δr_A)
~~~

- The air path p cancels (same frequency and antenna positions, assumed reciprocal);
- the non-reciprocity Δ must be calibrated with a zero-baseline setup (§8);
- the two bursts of a pair are 2 ms apart (§5), so the air path is the same in both directions; the clocks drift by y × 2 ms in between (0.02 ps at y = 1e-11), which the ARM removes with the Kalman frequency estimate;
- `(m_AB + m_BA)/2` is the one-way delay, logged as a diagnostic.

### 4.3 Carrier offset

The relative frequency offset of two FS725s is about 1e-11, which gives a carrier offset of about 0.06 Hz at 5.8 GHz: negligible over a burst and over the 4 ms between two bursts of one node, so no carrier recovery is needed. The correlator reports the envelope peak (magnitude of the complex correlation, with parabolic interpolation) and the carrier phase at the peak; the envelope resolves the carrier's whole cycles. The same arrival time and phase serve to despread the measurement packet coherently (§5.1).

## 5. Slot plan (TDD ping-pong)

The two nodes take turns every 2 ms, referenced to the local PPS (candidate values):

~~~text
slot k = 0 … 499 starts at PPS + k × 2 ms
k even : A transmits, B's window is open
k odd  : B transmits, A's window is open
pair j = slots 2j and 2j + 1: one θ
~~~

- A burst is the PRN (4096 chips, 41 µs), a guard of at least the RRC span (10 chips), then the measurement packet (§5.1), all one chip stream through the RRC FIR (P3);
- each burst's packet carries the sender's measurement of the peer's burst in the previous slot, 2 ms earlier;
- why 2 ms: the tracker's result is ready shortly after its window and the packet is assembled after that, and the propagation delay must fit as well (133 µs at 40 km); 2 ms is short enough that the air path is the same in both directions of a pair;
- RX window: opens before the expected arrival; its length sets the lag range a whole-window search covers and the time the search takes (today's 43848 samples: 11008 lags, 13.8 µs), and is open (§10); the window goes to the PL correlator (P8), the samples behind it to the packet decoder (P9), and a copy to DDR only when the ARM asks (acquisition, verification);
- a node never opens its RX window while it is transmitting;
- the ARM configures the slot plan once (period, role, offsets, expected delay); the PL slot engine (P7) runs it every slot without per-burst commands;
- both nodes open their windows from start-up, even before the peer transmits: an empty window gives no lock and is reported as no measurement.

### 5.1 Measurement packet

| Field | Type | Content |
| --- | --- | --- |
| format version, `config_id` | u8, u32 | packet layout and RRC configuration (§3.1) |
| `PPS_COUNT`, slot | u32, u16 | the sender's second and slot of this burst |
| `TIME_EPOCH` | u8 | changes when the sender's clocks relock: data across a change is not comparable |
| T_tx | i64, fs after the sender's PPS | start of this burst |
| T_rx | i64, fs after the sender's PPS | arrival of the peer's burst in the previous slot (P8's tracked peak) |
| carrier phase | i32, 2^-32 turn | at that peak, P8's phase as it is |
| quality | u8, 0.25 dB | the tracker's peak power over the window's noise power (the peak-to-sidelobe ratio needs a whole-window search, P8) |
| status | u8 | locked, measurement present, window valid, FIFO overflow, PPS locked, clocks locked |
| CRC-16 | u16 | |

- 36 bytes (288 bits). The phase keeps P8's full 2^-32 turn: 16 bits (2π/65536, 0.0026 ps at 5.8 GHz) would round it more coarsely than the 0.001 ps P8 is held to. Times are in time units, not ticks or samples, so the packet survives a change of sample rate; T_rx must not pass through float32, which resolves only about 0.15 ps near sample 1000.
- Modulation: BPSK spread at 100 Mchip/s with a code different from the PRN, L chips per bit. L follows from the link budget, measured on the link rather than assumed: about 10 dB Eb/N0 per bit gives a 1e-5 bit error rate before the CRC, and Eb/N0 = Es/N0 + 10·lg L, with Es/N0 the chip's signal-to-noise ratio at the receiver. The tracker's quality (§5.1 table; the peak power over the window's noise power) is Es/N0 + 36.1 dB for the 4096-chip PRN (10·lg 4096), so it gives Es/N0 on the link itself; L is the smallest power of two that leaves a margin above 10 dB, measured on the cabled link (M4b) and again on the air link (M6). Until then L = 64, which makes the packet 0.18 ms long (288 × 64 chips); P3 and P9 take L as a parameter.
- The receiving PL despreads coherently with the arrival time and carrier phase of the peer's previous burst, which move far less than a chip and a degree in 4 ms, and checks the CRC; a packet that fails is dropped, not repeated.
- The PL queues its own measurements and the decoded packets; the ARM reads the queue in batches.
- Pairing needs no NTP: each node learns the offset between the peer's `PPS_COUNT` and its own from every packet; a change of the offset (a missed PPS edge) discards that second.

### 5.2 Initial acquisition

- Until acquisition is done, bursts use the acquisition code, a fixed public code (today's PRN). Keyed codes (later) start only after both nodes decode each other's packets; a node that loses the peer falls back to the acquisition code.
- B listens with a long window to DDR (up to about 21 ms). A transmits every 4 ms, so one window holds several of A's bursts. The ARM correlates it, decodes A's packet, and from A's T_tx and `PPS_COUNT` finds the offset of its PPS from A's, to within the unknown propagation delay.
- B moves its 1 pps output by that offset less the steering target (the target holds the calibrated constant of the PPS paths, a few tens of ns on the bench), rounded to whole ticks, with the FS725's PP command (`place_pulse()` in `rf_clock.fs725`, 1 ns steps; `rf_clock.pl.acquire.place_pulse_ns`) and starts its slots. Whole ticks, because the PL latches the PPS on its tick: a move by whole ticks moves PPS_TICK by exactly that many and keeps the edge's place within its tick (its capture margin and the sub-tick phase of §8) to within the PP's own error, while a fraction of a tick would move that place by nanoseconds unseen. The FS725's PP misses its move by up to 0.23 ns (151 ps rms, and 0.5 ns over one sequence; measured against the unit's own 10 MHz on 2026-11-04, `results/m5-v2-20261103/REPORT.md` section 9.6), so a calibration of the PPS constant holds only until the next PP; the remainder, at most half a tick, stays in θ for the steering (§7.1). Tested on the bench (2026-11-03, `results/m5-v2-20261103/REPORT.md` section 2): an 11 ms window held three of A's bursts, all three packets decoded, and the offset agreed with the two-way θ that followed to 0.13 ns once the one-way delay was known; acquisition takes about 25 s on the ARM. A's whole-window search then finds B's bursts if they arrive within its lag range, that is, if the delay is known to within a quarter of it (about ±3.4 µs, 1 km, for today's 13.8 µs); otherwise A searches a long window as B did.
- With both directions measured, B steers the FS725's frequency (§7.1): the same loop takes out the rest of the offset, under half a tick plus the drift, and then holds θ. Not with PP: θ counts each node's time from the tick that latched its PPS (T = P + d, the edge P and its lead d on that tick), so steering to the target makes T_B = T_A and leaves the PPS edges d_A − d_B apart. Frequency moves P_B and T_B together and cannot change d_B; a PP of a fraction of a tick changes it unseen. Kept by whole-tick moves, d_A − d_B is a constant, calibrated once (§8, PPS sub-tick phase) and included in the target.
- After losing the peer, the PL searches the window (P8); it re-locks by itself only if the peak did not move (within ±1 sample) and is as strong as the tracker requires of a held lock, otherwise the ARM decides after checking the one-way delay; a peak outside the lag range goes back to long-window acquisition.

### 5.3 Timing of one pair of slots (P7)

~~~text
        slot 2j (A transmits)                     slot 2j+1 (B transmits)                   slot 2j+2 (A)
        |<---------------- 2 ms ---------------->|<---------------- 2 ms ---------------->|
A  TX   |-o-|PRN|g|  packet: m_BA of 2j-1  |    |                                        |-o-|PRN|g| packet: m_BA of 2j+1 |
B  RX   |-o-|-d-|[ window: P8 ][ packet span: P9 ]                                        |
B  TX   |                                        |-o-|PRN|g|  packet: m_AB of 2j    |    |
A  RX   |                                        |-o-|-d-|[ window: P8 ][ packet span: P9 ]
                    tracker result ──> B's next packet           tracker result ──> A's next packet
~~~

| Interval | Value | Set by |
| --- | --- | --- |
| slot | 200 000 ticks (2 ms) from PPS + k × 2 ms, k = 0 … 499 | P7, fixed |
| o: burst start in its slot | a few µs, the same on both nodes | P7 register |
| PRN | 4096 chips, 40.96 µs | P3 (chip memory) |
| g: guard | 10 chips, 0.1 µs | P3 |
| packet | 288 × L chips: 0.18 ms at L = 64 | P3, P9 (L an RTL parameter) |
| RRC tail | 73 samples after the last chip | P3 |
| d: expected arrival after the peer's burst start | path plus hardware delay | P7 register, from the ARM |
| window | opens 1.25 µs before the expected arrival; 43848 samples (54.8 µs) to start, an RTL parameter | P7, P8 |
| packet span | from the window's end to the packet's last chip, captured for P9 only | P7, P9 |

- B measures A's burst of slot 2j (its window and P8's tracker give T_rx and the phase) and sends that measurement, m_AB, in its own packet of slot 2j + 1; A does the same with B's burst of slot 2j + 1 in slot 2j + 2. A has both halves of pair j (slots 2j and 2j + 1) once it has decoded B's packet of slot 2j + 1, B once it has decoded A's packet of slot 2j + 2.
- Each node's own next burst leaves 2 ms after the peer's burst it measures: the tracker (well under 0.2 ms) and the packet's assembly fit with a wide margin, and a whole-window search (2.4 ms) runs on a frozen window while the next window of the same node, 4 ms later, goes to the other buffer.
- A node's window and packet span end long before its own burst of the next slot: at 40 km the path adds 133 µs, and window, packet span and path together stay under 0.5 ms.
- The despreader takes the arrival time and carrier phase from the same peer's burst of two slots before (4 ms earlier), so it decodes the packet while its samples arrive; the first packet after lock is the first one it can decode.

## 6. PL modules

All timing-relevant logic is in one clock domain: the RFDC DAC AXIS, ADC AXIS, raw tick, TX buffer and RX timestamping all run on the same 100 MHz fabric clock derived from the FS725, so there is no CDC in the sample path before timestamping. After the RX timestamp, samples cross into the DMA clock domain through an asynchronous FIFO and into the correlator's faster clock domain (300–350 MHz, also derived from the FS725) through P8's window buffer; neither affects timing. The AXI bus enters the fabric domain through a clock converter in the block design.

### P0 clock_reset: clocks and reset

- External 10 MHz → board LMK/LMX → RFDC reference clock and fabric clock;
- monitor PLL lock and RFDC status; on loss of lock, latch FAULT and mark all timestamps invalid;
- increment `time_epoch` after every re-lock.

### P1 timebase_pps: raw tick and PPS capture

- 64-bit `raw_tick`, incremented every fabric clock, never reset at frame boundaries, saturating rather than wrapping;
- synchronize the FS725 1PPS input, capture `pps_tick`, and check that every period is exactly 100,000,000 ticks; report deviations and missing pulses;
- **Note:** 10 MHz and PPS are coherent, so after each power-up the PPS edge has a fixed but unknown phase relative to the fabric clock. If that edge sits next to the sampling edge, the capture can toggle between adjacent ticks. Detect this from the PPS period statistics and move it away with IDELAY if needed. The sub-tick phase is a per-power-up calibration constant (§8).
- **Proposal (2026-11-03, not built):** report where the PPS edge falls within its tick, every second, from an oversampled copy of the PPS input (an ISERDES at the 800 MHz sample clock or faster: 1.25 ns or finer), so that each node sees its own capture margin and sub-tick phase. Today only a node that controls its FS725 can find them, by stepping PP until PPS_TICK moves (`rf_clock.checks.pps_pp_scan`; B's edge was 5.33 ns before its sampling edge, `results/m5-v2-20261103/REPORT.md` section 3), and a marginal edge on the other node shows only as a 10 ns step of θ.

### P2 control_regs: AXI-Lite control and status

- Read-only: ID/version, atomic `raw_tick` snapshot, latest `pps_tick` and PPS count, `time_epoch`, state, fault bits;
- commands: `TX_TARGET_TICK`, `TX_LENGTH`, `RX_TARGET_TICK`, `RX_LENGTH`, written to shadow registers first and committed atomically by a doorbell;
- results: `tx_actual_tick`, `rx_actual_tick`, completion sequence, return code;
- reject a target_tick that is already stale (≤ current tick + safety margin), a length larger than the buffer, and a resubmission while ARMED/RUNNING;
- these single commands remain for tests (M2, M3); the protocol runs from the slot engine's configuration (P7).

### P3 tx_burst: chip stream, RRC FIR and timed playback

- Chip memories (BRAM) hold the fixed codes the ARM writes (PRN or acquisition code, packet spreading code); the packet's bits come from P9; a later keyed-code generator feeds the same stream;
- the chip stream (PRN, a guard of zeros, packet chips) passes a polyphase RRC FIR (81 taps, 8 samples per chip; the chips are ±1, so it needs adders only) into 16-bit I/Q beats;
- a test mode plays a sample waveform the ARM preloads into the BRAM/URAM buffer (CW, scope and spectrum measurements), as the first version did, including `TX_REPEAT`'s back-to-back passes for a continuous signal; it serves development tests only, by P2's single commands, which the slot engine refuses while it runs;
- states: IDLE → ARMED → RUNNING → DONE / FAULT;
- starting on the cycle where `raw_tick` equals the slot's start tick (or `TX_TARGET_TICK` in test mode), output one beat (8 complex samples) per clock without a gap until the burst ends; output zeros outside the burst;
- an ARM write to the chip or sample memories while ARMED/RUNNING is an error: the burst being transmitted cannot change;
- a drop of the DAC `tready` while RUNNING is a FAULT: no pause, no retry;
- record the actual start tick (it should equal the target).

### P4 rx_capture: timed RX capture

Each window goes to the correlator's window buffer (P8) and the packet decoder (P9), and, when the ARM asks for it, also to DDR. Windows need not be contiguous with each other, but **no sample may be lost inside a window**, on either path: sample time is derived from "start tick + sample index", so one lost beat shifts the time of every later sample.

~~~text
ADC AXIS ─> window gate + timestamp ─┬─> P8 window buffer, P9 despreader
  (100 MHz fabric domain)             └─> async FIFO ─> width conv. ─> AXI DMA S2MM ─> HP port ─> DDR
                                                       (DMA domain, e.g. 250 MHz)
~~~

- Open the window at `raw_tick == RX_TARGET_TICK`, push every ADC AXIS beat into the FIFO, close the window after the length is reached and mark the last beat with `TLAST`;
- **timestamps are recorded on the ADC side, before the FIFO:** actual start tick, beat count, ADC overflow/over-range flags. DDR write latency and jitter do not affect them;
- the data rate is 800 MS/s × 4 B = 3.2 GB/s. HP ports are 128 bit, so the DMA clock must be ≥ 200 MHz; measure whether this rate is sustained while the ARM also accesses DDR;
- the FIFO only absorbs short DDR stalls (a few KB to tens of KB); it does not hold a whole window;
- before submitting the command the ARM must have an S2MM DDR buffer of sufficient length ready (PYNQ `allocate`). If the DMA is not ready, the PL rejects the command and does not open the window;
- **FIFO overflow inside a window → the window is discarded:** set `rx_window_invalid`, count it, drop that measurement; do not enter FAULT and do not affect TX. This differs from TX: losing an RX window only loses one measurement;
- after DMA completion the ARM invalidates the cache for that buffer before reading samples;
- the DDR path serves the long acquisition windows (§5.2) and the copies the ARM checks P8 against; every protocol window goes to P8 and P9.

### P5 rfdc: RFDC IP

- DAC 2nd-Nyquist mix-mode, ADC decimation, NCOs set for the 5.8 GHz plan;
- **enable Multi-Tile Sync (MTS)/SYSREF** so the DAC/ADC digital-path latency is identical after every power-up; otherwise calibrated constants become invalid at the next boot;
- the ARM initializes RFDC with PYNQ `xrfdc` or the XRFdc driver; the PL only provides the IP and clocks.

### P6 status: status and faults

- Collect faults from P0–P5 and P7–P9 into one latched status word with source and sequence number;
- clear faults only with an explicit CLEAR command or a new `time_epoch`;
- polling in the first version, no interrupts.

### P7 slot_engine: the ping-pong schedule

- Runs the slot plan (§5) from the PPS: in each slot it starts the burst (P3) or opens the window (P4) at its tick;
- the ARM writes the plan once; changes (expected delay, role, codes) take effect at the next PPS;
- a slot the engine cannot start on time is skipped and counted, never shifted.

### P8 correlator: tracking every slot, searching on demand

- Two window buffers (BRAM, written losslessly at 800 MS/s; their length is a parameter of the RTL, about 39 BRAM36 each for today's 43848 samples): one keeps receiving while the other can be frozen for a search;
- the reference is made from the burst's chips by the RRC FIR, so it equals what was transmitted, and keyed codes later need no extra step;
- tracker: in every window, a few lags around the predicted peak, three-point parabolic interpolation, carrier phase by CORDIC; ready shortly after the window, long before the node's next burst; its quality figure is the peak power over the window's noise power;
- whole-window search: parallel time-domain correlators, a systolic array in which the reference is broadcast to every lane and the window shifts through them, one lag per lane, passes repeated until every lag is done; integer multiply-accumulate (16 × 16 bits, 47-bit sums in the DSP48E2's 48-bit accumulator), so its correlation equals the ARM's float64 exactly; 11008 lags × 32841 samples ≈ 7.2e8 multiplications: 2.4 ms with 1024 DSP at 300 MHz, 1.2 ms with 2048 (the device has 4272, the tracker uses about 150);
- a search starts when the tracker has no lock (lost, or not yet found after start-up; then again on fresh windows until it locks) or the ARM asks; the ARM asks from time to time while locked, so that a lock on a wrong peak (a delayed copy, multipath) is found; one search at a time (busy), requests while busy are refused and counted;
- a search reports, with the window's `PPS_COUNT`, slot and start tick: the strongest peak, the earliest peak within a set threshold of it (the direct path arrives first), the peak-to-sidelobe ratio and the window status;
- re-lock: by the PL itself only if the new peak lies within ±1 sample of the old one and passes the tracker's magnitude test against the last lock (a noise peak near the old lag took the lock on the bench, 2026-11-03); otherwise the ARM decides after checking the one-way delay; slots without lock carry invalid measurements, while a search the ARM requests during lock leaves the tracker's measurements valid; a peak outside the lag range goes to the ARM's long-window acquisition;
- clock: the first RTL version (rf-clock 2.0) runs the search on the 100 MHz fabric clock, with 1024 lanes (2048 DSP) and about 3.9 ms per search, so that it needs no clock-domain crossing; a 300 MHz clock derived from the FS725 would do the same with half the DSP, if timing allows;
- the estimator must equal the ARM's (`rf_clock.correlator.estimate_delay`) so that calibrated constants carry over; acceptance against the ARM's float64 on the same window: envelope within 0.01 ps, phase within 0.001 ps;
- results (window tick, peak, sub-sample position, phase, quality) go to P9 and to the measurement queue;
- the alternative studied was a 65536-point FFT correlator on every window (2026-11-01: about 550–620 BRAM36 and 250 DSP, 0.7–0.9 ms per window, accurate enough with 27-bit data and 18-bit twiddles); the time-domain search needs no reference spectrum and little memory, and runs only when needed.

### P9 packet: measurement packet

- Assembles the packet of §5.1 from P8's latest result, the slot's T_tx and the status, adds the CRC, and gives its bits to P3, which spreads them with the packet code behind the PRN;
- despreads the peer's packet in each window coherently (§5.1), checks the CRC, and queues the decoded fields;
- the queue holds at least a second of both nodes' measurements, so the ARM reads it in batches.

### P10 pps_out: the output PPS (since 2.1)

The node's PPS for other equipment, made from its time scale rather than taken from the FS725's 1PPS (design and reasons: `plan/pps_output.md`):

```text
FS725 1PPS ──> P1: PPS_TICK(k) ──> P10: phase counter (anchored by locked edges, flywheel between)
                                     │ at PPS_TICK(k) + OFFSET: a step in DAC tile 0's stream
                                     v
                     DAC tile 0 (baseband, mixer bypassed) ──> 100 nF ──> balun ──> 100 nF ──> J20 (DAC_B)
                     validity line ──> NVT2008BQ ──> Pmod J1 pin 1 (high = not valid); LED W_LED_0
```

- Counts the ticks of each second from the PPS_TICK of the locked edge that started it, and on by itself when an edge is missing (flywheel, counted);
- in each second, the beat presented at PPS_TICK + OFFSET carries a step from sample SAMPLE on (1.25 ns steps), WIDTH beats long, from an idle level IDLE (since 2.2; −AMPLITUDE doubles the step); the edge on J20 lies at the second's start + OFFSET × 10 ns + SAMPLE × 1.25 ns + D_board (§8); its rise, 0.95 ns, is set by tile 0's 800 MS/s stream, which cannot be raised without losing tile 2's MTS (the IP keeps a DAC tile's MTS only with equal interpolation; `results/pps-out-20261104/REPORT.md` section 9);
- the output follows the time scale that θ aligns, so neither a PP's error nor the FS725 1PPS jitter enters it, and it is timed by the DAC's sample clock;
- the validity line is high unless the PPS input and the clocks are locked, the second was started by a locked edge, the DAC takes the stream, and the ARM has said the time scale is synchronized; high also without a bitstream (the translator's pull-up);
- registers `docs/ABI.md` §4.13; simulation `PL/sim/tb_p10_pps_out.v`.

### 6.1 Not needed in the first version

- **No DDR-streamed TX:** bursts are made from on-chip data (P3), so continuity is guaranteed by construction; only RX uses S2MM DMA;
- no DSP in the PL beyond the per-burst chain (P3's FIR, P8, P9);
- no scatter-gather DMA and no interrupts: one simple-mode transfer per window, completion by polling.

## 7. ARM software modules

| Module | Function |
| --- | --- |
| `waveform` | chip sequences of fixed codes (PRN or acquisition code, packet spreading code), RRC coefficients quantized for P3's FIR; test sample waveforms (CW) in the RFDC lane format |
| `pl_client` | read/write P2 registers, configure the slot engine, read the measurement queue, request DDR windows |
| `scheduler` | the slot plan: period, role, offsets, expected delay |
| `correlator` | float64 correlation on DDR windows: acquisition, and the reference that P8 is checked against |
| `twtt` | pair the two directions by `PPS_COUNT` and slot, compute θ and one-way delay |
| `link` | decode measurement packets from DDR during acquisition, parse the decoded packets from the queue; Ethernet only for management and logs |
| `estimator` | Kalman filter with clock offset θ and relative frequency y, with outlier rejection |
| `fs725` | RS-232 driver: set frequency offset, read status, log every correction |
| `calibration` | store Δ, PPS sub-tick phase, RFDC latency, with version and date |
| `rfdc_mgr` | RFDC initialization, MTS, NCO settings and status readback |

The ARM reads the PL's measurement queue in batches; estimation and steering happen on the scale of seconds, so Python + NumPy is enough. Move a bottleneck to C/C++/Rust only when measurement shows it is needed.

### 7.1 FS725 steering policy

- Only B is steered; A free-runs;
- the first version adjusts only B's frequency offset over RS-232. Frequency error is corrected directly; PPS phase error is removed by temporarily adding a frequency offset so that the phase slews into place;
- the steering command is SF (PRS10 manual): the frequency offset in parts in 1e12, −2000 to +2000, relative to the EEPROM calibration. The first SF disables the calibration pot and SF is ignored while the unit phase-locks to a 1 pps input, so steering starts from the value SF? reads, with that PLL off (`rf_clock.fs725`). Measured on B's unit (2026-11-03, `results/m5-v2-20261103/REPORT.md` section 5): SF +100 raised θ's rate by 101.5 ps/s within one to two seconds, and one step of the magnetic-field DAC behind SF is 3.4 SF units, so finer SF is realized only on average;
- the loop time constant is tens to hundreds of seconds, matched to the stability of the rubidium standard. With white frequency noise the residual of θ goes as σ_y(1 s)·√(τ/2), so the shortest τ the FS725's response allows is best; `rf_clock.twtt.steer` defaults to 50 s (`experiments/m5_steer_sim.py`). The two FS725s on the bench, measured through θ and on a scope (2026-11-03, report section 4): Allan deviation 1.4 × 10⁻¹¹ at 1 s, falling about as τ^−0.8 to 4.3 × 10⁻¹³ at 256 s, and an oscillation of the pair's frequency of about 5.3 s period; θ wanders by some 70 ps rms about its line, mostly over tens of seconds. Replaying that record through the loop gives 55 ps rms for τ = 50 s with a correction every 10 s, 33 ps for τ = 5 s every 2 s and 25 ps for τ = 2 s every second; on the bench the first held θ to 66 ps rms and the second to 39 ps (report section 6). With the FS725's 1.9 s response in the replay, 5 s every 2 s gives 42 ps and nothing faster does better (2 s every second: 63 ps): about 40 ps is the floor for this pair (of the 1.9 s, the FS725's 1.1-1.6 s cost about 15 ps: theta from the last 0.2 s of each second instead of the whole second would give 38.5 ps, an FS725 answering at once 25 ps; report section 6.5);
- in rf-clock 2 the node steers its own FS725: `rf_clock.checks.m4c_link --steer-target-ns` feeds each second's θ, from B's own measurement queue, to `rf_clock.twtt.steer` and sets SF over B's USB-RS232 adapter;
- the FS725's frequency follows an SF command about 1.1-1.6 s later, so the filter counts a change of SF from 1.9 s after the second whose θ asked for it, and widens its outlier gate while the change is new; without that, a large first step of a fast loop (τ = 5 s) made the filter reject ten θ in a row and start again on the bench (2026-11-03). A large error at the start runs SF at the edge of its allowed range rather than stopping the steering; only a control law that asks beyond it for two minutes stops it.

## 8. Calibration

| Quantity | Method | Redo when |
| --- | --- | --- |
| Non-reciprocity Δ | Zero baseline: both nodes share one FS725's 10 MHz and PPS (PPS distribution cable lengths known), so the true θ is known; solve Δ from (m_AB − m_BA)/2 = θ + Δ/2 | after changing cables, amplifiers, filters, antennas or the RFDC configuration |
| PPS sub-tick phase vs fabric clock | oscilloscope / time-interval counter, or feed PPS into a spare ADC channel and time it at GS/s resolution; on a node that controls its FS725, PP in 1 ns steps until PPS_TICK moves (`rf_clock.checks.pps_pp_scan`: where the edge lies before its sampling edge, to 1 ns; 2026-11-03) | every power-up (unless the board PLL phase is shown to be deterministic) |
| RFDC path latency | on-board DAC → attenuator → ADC cable loopback, compared across many reboots | after RFDC configuration changes; also proves MTS works |
| Output PPS delay D_board (P10) | J20's edge on the scope against the node's FS725 1PPS, less OFFSET × 10 ns + SAMPLE × 1.25 ns and the PPS capture margin (row 2); compared across power-ups | after RFDC configuration changes or changes to the DAC_B path |

Calibration values live in software with a version number. Never subtract a guessed constant in code.

## 9. Milestones and acceptance

| Stage | Content | Acceptance |
| --- | --- | --- |
| M0 | FS725 10 MHz into the board clock chain, PPS into a PL pin | board PLLs locked to the external reference; RFDC clocks correct |
| M1 | P0/P1/P2 | ARM reads exactly 100,000,000 ticks per second; removing the reference raises FAULT |
| M2 | P3 + P5, scope triggered on PPS looking at the DAC output | burst delay relative to PPS is fixed and unchanged across reboots (MTS works) |
| M3 | P4, on-board DAC → ADC cable loopback, correlation on the ARM | standard deviation of the loopback delay and its consistency across reboots; S2MM sustains its write rate without overflow while the ARM also accesses DDR |
| M4 | two boards, cabled with attenuators, sharing one FS725 | Δ from the zero baseline; noise level of θ̂ |
| M4b | P7–P9 and P3's FIR, on the M3 loopback, then the cabled pair | P8 equals the ARM's float64 on the same windows (envelope within 0.01 ps, phase within 0.001 ps); packets decoded with their CRC over hours; θ and the loopback delay unchanged with and without packets; pairing by `PPS_COUNT` without NTP; searches triggered by loss of lock and by the ARM, with the re-lock rules of P8 |
| M5 | each board on its own FS725, cabled, acquisition (§5.2), Kalman + FS725 steering loop closed | PPS_A − PPS_B on a time-interval counter converges over the long term; since 2.1 the nodes' PPS is the output PPS (P10), whose difference must also hold over re-acquisitions and power-ups |
| M6 | replace cables with the 5.8 GHz BPF, amplifier and antenna air link | same metrics as M5; record multipath and SNR effects |

Every piece of evidence must say whether it comes from simulation, synthesis or board measurement. The final metric is PPS_A − PPS_B measured by an external time-interval counter, not the θ̂ reported by the system itself.

## 10. Open questions

Item-by-item progress and evidence are tracked in `plan/checklist.md`.

1. **Board model and external reference input:** can the board clock chain take a 10 MHz external reference, and can both RFDC and fabric clocks be derived from it?
2. **Antennas:** separate TX/RX antennas, or one antenna with a T/R switch? If shared, the PL must drive the switch at target ticks and protect the LNA.
3. **Measurement packet:** measurements ride in the bursts (§5.1). Open: the spreading factor L, measured as §5.1 describes (L = 64 until then; on the cabled pair, 2026-11-03, the tracker's quality was 45.25 dB at TX amplitude 16000, Es/N0 ≈ 9.2 dB, so L = 64 gives Eb/N0 ≈ 27 dB, and with the amplitude cut to 4000 every packet still decoded: by §5.1's rule L = 8 would do there, but the attenuators set that SNR, so L stays 64 until the air link, M6), and the packet's fields and registers (registers only added keep `VERSION`'s major; changing existing ones bumps it, `docs/ABI.md` §14).
4. **FS725 coarse PPS alignment:** the PRS10 moves its 1 pps output by PP in 1 ns steps; §5.2 uses that with the offset found by RF acquisition (`place_pulse()`), in whole ticks; tested on the bench on 2026-11-03 (10 PP steps make one tick, 9.90 ns measured). A PP misses its move by up to 0.23 ns, and the 1PPS itself jitters by 22-34 ps against the unit's 10 MHz (2026-11-04). **Since 2.1** the node's output PPS is made by the PL from its tick (P10, `plan/pps_output.md`): a pulse on DAC_B (J20) at a programmable delay after PPS_TICK. It follows the time scale that θ aligns, with no PP and no FS725 PPS jitter between them, and the FS725's 1PPS serves only as the PPS input; the delay from the tick to J20 is a board constant to calibrate once (§8). On board B (2026-11-04, `results/pps-out-20261104/REPORT.md`) it held against B's 10 MHz to the scope's 7 ps per second, moved by exactly whole ticks through ten PP moves (3.1 ps rms) while the 1PPS missed them by 40-260 ps, and stayed within 8.7 ps over six PL.opens. With both boards' output PPS on the scope while B was steered, their difference held to 0.9 ps over a re-acquisition with PP and a PL.open of both boards (report section 8), where the FS725 1PPS lost 145 ps; power cycles are open. Time-tagging the peer's PPS on the rear 1 pps input (TT?, 1 ns, `rf_clock.fs725 align`) remains a bench alternative.
5. **Target accuracy:** ns level is reachable with code correlation; 100 ps or better needs carrier-phase measurement and tighter handling of the PPS sub-tick phase.
6. **Regulations:** 5.8 GHz transmit power and occupied-bandwidth limits.
7. **Security:** monitoring the one-way delay; an innovation gate in the Kalman filter; the threshold for the earliest peak (P8); a message authentication code on the packet; keyed codes (§5.2) and their keys.
8. **Polarization in rain:** with the two directions on orthogonal polarizations, rain's differential phase makes the link non-reciprocal (estimated at 10–20 ps over 40 km in heavy rain, not measured); both directions on one polarization would avoid it.
9. **RX window length:** today's 43848 samples give 11008 lags (13.8 µs) and a 2.4 ms search with 1024 DSP; a longer window widens the range a search and A's first catch of B (§5.2) cover, and lengthens the search in proportion. The window length is a parameter of the RTL. It starts at 43848 samples, which this design did not derive: it is the window of the M3/M4 node (the burst's 32841 samples, 1.25 µs before the expected arrival and 10 µs after it), kept so that the RX windows saved in M4 serve as test vectors. Its final value follows from how well the link distance is known (A's first catch of B needs the delay to within a quarter of the lag range) and from how far after the direct peak the multipath reaches.
