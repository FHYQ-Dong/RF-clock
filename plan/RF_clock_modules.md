# RF-clock Module Implementation Plan and Functional Description

> This document is the learning and reproduction plan for `RF-clock`. It decomposes H00–H19 from the reference design into modules that can be understood and verified incrementally.
> **A module being listed here does not mean that it is already implemented in this repository.** The current implementation baseline is still only the three skeleton files under `FPGA/src/`.
>
> Reference documents:
>
> - `D:/Program/Research/keysight-bb84-testbed/plan/RFSoC/PL_hardware.md`
> - `D:/Program/Research/keysight-bb84-testbed/plan/RFSoC/RF_clock.md`
> - `D:/Program/Research/keysight-bb84-testbed/docs/RF_clock/README.zh.md`
> - `D:/Program/Research/keysight-bb84-testbed/docs/RF_clock/pl_time_control_v1.md`

## 1. What problem are we actually solving?

The two QKD endpoints are not synchronized merely because each one generates a 10 MHz signal. The goal is for two RFSoCs, A and B, to use comparable time schedules for the same quantum-experiment event. The RF link transmits a known PRN waveform; the receiver estimates its arrival time from the correlation peak. Measurements in both directions, calibrated hardware delays, and local clock status are then combined to estimate clock offset and frequency offset.

A one-way measurement contains at least:

```text
receive time - transmit time
    = propagation time + transmit/receive hardware delay + clock offset between the endpoints
```

The design must therefore solve four classes of problems:

1. Does the FPGA have a common time axis that cannot silently wrap?
2. How are transmitted PRN chips and received samples mapped onto that time axis?
3. How does the receiver distinguish a real correlation peak from noise or multipath?
4. How are software-computed corrections applied safely at deterministic hardware times?

H00–H19 split these four problems into modules. They are not 19 unrelated files; they form a dependency chain.

## 2. Terminology and time layers

| Term | Meaning | What must not be confused |
| --- | --- | --- |
| `clk` | An edge-driven RTL clock domain | It is not necessarily the RF sample clock and is not automatically safe across domains |
| `raw_tick` | A coarse counter in the common time domain, preferably 64 bits | It is a digital time coordinate, not UTC seconds and not a sub-nanosecond TDC |
| chip | A `+1/-1` symbol in the PRN sequence | One chip is normally expanded into multiple DAC samples |
| sample | One I/Q sample in an ADC/DAC stream | A sample number is not a `raw_tick`; H06 establishes the mapping |
| frame | One numbered PRN transmission unit with seed, length, and configuration identity | A frame number is sideband identity, not authentication by itself |
| event | A candidate or valid arrival record produced by one correlation search | Without passing quality thresholds it remains only a candidate |
| boot/epoch/config ID | The identity/version of a run, time mapping, or waveform configuration | It prevents stale data or stale commands from entering a new run |

## 3. Module dependency graph

```text
H00 platform shell
 └─ H01 reset/reference qualification
     ├─ H03 raw_timebase ── H04 PPS capture
     │                    └─ H05 future-tick scheduled commit
     └─ H02 AXI control registers

H03 + sample clock
 └─ H06 sample_time_bridge ── H07 capture/DMA

H08 PRN frame source
 └─ H09 BPSK/rectangular/RRC/RFDC transmit chain
                                      │ RF link
RFDC ADC ── H10 receive front end ── H11 carrier correction
                                  └─ H12 correlator
                                      └─ H13 peak quality
                                          └─ H14 fractional peak
                                              └─ H15 measurement FIFO

H15 + PS bidirectional protocol/calibration/filter
 └─ H16 servo safety gate ── H05 scheduled executor/physical-clock interface
```

## 4. P0: Platform, reset, and common time

### H00 `base_shell`: RFSoC platform top level

**Problem it solves:** Custom RTL cannot be used as a floating collection of modules. An RFSoC contains the PS, RF Data Converter, DDR, AXI interconnect, multiple clock domains, and board-level reset. Without a top-level shell, individually simulated units cannot form a synthesizable design.

**How it solves the problem:** It places the PS, RFDC, DDR, AXI interconnect, clock/reset IP, and custom H modules in one synthesizable top level. Clock domains, reset domains, board pins, versions, and health signals are made explicit at that boundary.

**It is responsible for (following that solution path):**

- Instantiating the PS, RFDC, DDR, AXI Interconnect, clock, and reset IP;
- Defining which clock drives time control and which clocks drive DAC/ADC data;
- Connecting H01–H16 to AXI, RFDC, DMA, and external pins;
- Propagating boot ID, configuration ID, health status, and fault reset.

**Acceptance focus:** The block design is generated; the HWH matches the bitstream; clock/reset connections, constraints, and CDC checks pass. Top-level simulation connectivity is not the same as board deployment readiness.

### H01 `clock_reset_guard`: Reset and reference qualification

**Problem it solves:** External lock signals and reset can arrive asynchronously. Sending an asynchronous signal directly into logic can create metastability; releasing reset without synchronization can make registers start on different cycles, producing false events and partially initialized state.

**How it solves the problem:** It synchronizes the asynchronous lock indication, releases internal reset through a controlled synchronous pipeline, and requires the lock to remain stable for several cycles before granting time and capture qualification. If the reference fails, qualification is revoked without destroying the historical time axis.

**It is responsible for (following that solution path):**

- Asserting reset asynchronously and releasing it synchronously to the local clock;
- Sampling external `reference_locked_async` with a two-stage synchronizer;
- Requiring the lock to remain asserted for several cycles before producing `reference_ok`;
- Revoking “time valid / capture allowed / transmit allowed” qualification when the reference fails.

**Suggested interface:**

```text
Inputs:  clk, arst_n, reference_locked_async
Outputs: rst, reference_ok
```

**Important:** Reference loss must not silently clear H03's raw counter. It invalidates time labels, but historical ticks must remain monotonically increasing.

### H02 `control_regs`: AXI-Lite control entry between PS and PL

**Problem it solves:** AXI-Lite address and data channels may arrive independently, while software also needs identity, status, snapshots, and error codes. A few bare registers can lead to partially written configurations, torn 64-bit reads, and invalid commands being accepted.

**How it solves the problem:** It decouples the AXI-Lite handshakes from internal updates, stages multiword writes in shadow registers, and only publishes a complete, legal configuration. Snapshot and versioned readback paths provide a coherent software view.

**It is responsible for (following that solution path):**

- AXI4-Lite AW/W/B/AR/R handshakes;
- Four-byte alignment and `WSTRB` byte enables;
- Design ID, ABI version, feature bits, and build ID;
- Shadow registers, integrity bitmaps, and atomic snapshots;
- Returning `SLVERR` for illegal addresses, writes to read-only locations, and partial command writes.

### H03 `raw_timebase` / `raw_tick`: Common coarse time axis

**Problem it solves:** Without a common counter, transmit time, receive time, PPS, and software commands have no shared coordinate, so events cannot be compared in time.

**How it solves the problem:** A single unsigned 64-bit counter increments on every valid time-clock edge and saturates at all ones. Exhaustion and boot/epoch/config identities are reported separately, so every event shares a non-wrapping coarse coordinate.

**It is responsible for (following that solution path):**

- Incrementing one 64-bit unsigned counter on every valid clock edge;
- Starting from zero on reset;
- Holding at all ones after exhaustion and reporting `exhausted`;
- Recording the nominal tick frequency, such as 100 MHz in the prototype;
- Keeping boot/epoch/config as separate identities instead of embedding them in the tick.

**Suggested interface:**

```text
Inputs:  clk, rst
Outputs: raw_tick[63:0], exhausted
```

### H04 `pps_capture`: PPS coarse capture and health monitoring

**Problem it solves:** External PPS is asynchronous to the FPGA clock, and a pulse can be missing, too short, or have an abnormal period. Software must not declare the system locked from one edge alone.

**How it solves the problem:** It synchronizes PPS, detects edges in the `raw_tick` domain, records event sequence and inter-arrival intervals, and applies period tolerance, timeout, and recovery rules. A newly valid reference must first be observed low so a startup-high level is not mistaken for an event.

**It is responsible for (following that solution path):**

- Synchronizing the asynchronous PPS input;
- Detecting synchronized rising edges;
- Recording the latest PPS tick and event sequence in `raw_tick` coordinates;
- Computing adjacent PPS intervals and checking `PERIOD ± TOLERANCE`;
- Reporting PPS timeout, bad intervals, and recovery;
- Requiring a low observation after reference requalification.

### H05 `scheduled_commit`: Deterministic execution at a future tick

**Problem it solves:** Linux thread scheduling is nondeterministic. A register write made “now” is not the same as changing time or waveform state at a deterministic hardware instant; immediate execution would inject OS scheduling jitter into QKD slots.

**How it solves the problem:** Command fields are first latched in shadow registers. After handshake and range checks, an immutable commit is formed. Runtime logic compares `raw_tick` with the target tick, emits a one-cycle action at the target edge, and reports accepted, waiting, executed, missed, cancelled, or faulted outcomes.

**It is responsible for (following that solution path):**

- Receiving command ID, `target_tick`, and opaque control value;
- Providing atomic commit through shadow registers;
- Rejecting zero IDs, duplicate/out-of-order IDs, past targets, and insufficient lead time;
- Executing on the hardware edge where `raw_tick == target_tick`;
- Supporting cancellation, invalidation, and missed-target completion codes;
- Reporting command acceptance separately from actual execution.

**Key semantic:** `apply_now` must be sampled by the consumer on the target edge. A one-cycle-late `executed` pulse must not be mistaken for the physical execution edge.

## 5. P0: Sample time and capture chain

### H06 `sample_time_bridge`: Bridge from sample numbers to common time

**Problem it solves:** ADC/DAC data normally runs in an independent clock domain. `raw_tick` gives a common time coordinate, but it does not say which tick corresponds to sample 12345 on an ADC lane.

**How it solves the problem:** At a known reference edge it captures the sample counter and `raw_tick` together, then transfers that versioned anchor as an atomic CDC/FIFO message. The mapping is represented by a sample range and uncertainty rather than by pretending to have unlimited precision; stopped clocks, backpressure, and stale replies invalidate the anchor.

**It is responsible for (following that solution path):**

- Maintaining a sample number in the sample domain;
- Requesting a time anchor at a known reference edge;
- Transferring “sample number – raw tick – configuration version” as one CDC/FIFO message;
- Recording sample ranges before and after the anchor;
- Reporting invalidity on stopped clocks, backpressure, reference loss, or stale replies.

### H07 `capture_dma`: Bounded-window capture and transport

**Problem it solves:** RFDC is a continuous stream, while PS/DMA needs bounded blocks. Without explicit windows, headers, and fault markers, software cannot know whether data is complete or replay it offline.

**How it solves the problem:** It cuts the stream into bounded windows with explicit start/stop conditions, attaches timing and integrity metadata, and uses double banking, CRC, and DMA backpressure to deliver verifiable blocks.

**It is responsible for (following that solution path):**

- Capturing finite I/Q windows on command;
- Using double-bank or snapshot storage to avoid read/write overwrite;
- Adding first-sample number, time anchor, sample rate, I/Q format, configuration ID, gap, and saturation flags;
- Generating CRC/AXIS packets for DMA;
- Handling backpressure, short transfers, overflow, and reset recovery.

## 6. P1: PRN transmit chain

### H08 `prn_frame_source`: Repeatable PRN chip-frame source

**Problem it solves:** Endpoints A and B must generate exactly the same reproducible reference sequence so the receiver can find arrival delay by correlation. PRN is a timing marker, not a QKD key and not an authentication mechanism by itself.

**How it solves the problem:** A fixed LFSR, seed, and frame descriptor generate a repeatable chip sequence. The sequence advances only when downstream `ready` accepts a chip, while frame boundaries, identity changes, aborts, and faults are latched into status.

**It is responsible for (following that solution path):**

- Receiving a frame descriptor: frame number, nonzero seed, chip count, and boot/config ID;
- Generating the LFSR sequence from the selected seed;
- Outputting chip bit, chip index, start/end markers;
- Supporting `valid/ready` backpressure so the sequence advances only on a successful handshake;
- Detecting disable, abort, or identity changes during an active frame and latching a fault;
- Reporting how many chips have been accepted downstream.

**H08 default rules:** A 16-bit right-shift LFSR, with feedback from old-state `bit0 ^ bit2 ^ bit3 ^ bit5`, outputting old-state bit0, and producing at most eight chips per cycle in the prototype. `FPGA/src/prn_generator.v` is only the core functional skeleton; framing, handshake, and fault semantics are still missing. The 32-bit polynomial in the current comments must be reconciled with the project specification.

### H09 `tx_shape_rfdc`: Convert chips into safe DAC samples

**Problem it solves:** PRN bits are not I/Q samples that RFDC can send directly. The design must define samples per chip, amplitude, packing, and how to become electrically silent during a fault.

**How it solves the problem:** The transmit chain is built in layers—bit mapping, chip expansion, RRC coefficient/FIR processing, frame scheduling, and RFDC adaptation. A rectangular baseline is verified first; fixed-point parameters are frozen next. On loss of lock or underrun, the actual DAC data—not only `valid`—is forced to zero.

**It is responsible for (following that solution path):**

- Mapping chips to signed I/Q amplitudes;
- Performing shaping, scheduling, and RFDC packing at the selected sample rate;
- Producing deterministic silent data on fault or loss of qualification.

**Implementation layers:**

1. `bpsk_mapper`: map 0/1 to `-A/+A`;
2. `tx_rect_samples`: rectangular expansion to verify chip order, sample count, and frame boundaries;
3. `tx_rrc_coeff_bank`: store RRC coefficients and coefficient version;
4. `tx_rrc_fir`: fixed-point FIR, rounding, and saturation;
5. `tx_frame_scheduler`: prefill and release at future tick/sample targets;
6. `rfdc_tx_adapter`: adapt DAC I/Q packing, DUC, and interpolation interfaces.

### H10 `rx_frontend`: ADC/RFDC receive front end

**Problem it solves:** ADC output is usually multi-lane I/Q data with a particular width and clock relationship; the RF carrier also needs digital downconversion and decimation. Unnormalized data cannot be sent directly to the correlator.

**How it solves the problem:** It first normalizes lane ordering, I/Q alignment, valid semantics, and clock relationships, then performs DDC/NCO/decimation/filtering. Gaps, saturation, and warm-up are encoded in the stream before it is handed to H11/H12.

**It is responsible for (following that solution path):**

- RFDC ADC run-time configuration and status checks;
- DDC, NCO, decimation, and required low-pass filtering;
- `rfadc_iq_unpack`: lane reorder, I/Q alignment, and valid checks;
- Detecting continuity gaps, saturation, reset warm-up, and invalid samples;
- Passing a unified data format to H11/H12.

### H11 `carrier_corrector`: Carrier frequency-offset and phase correction

**Problem it solves:** Local oscillators and the RF link introduce carrier frequency offset, phase rotation, and time-varying phase. Without correction, the correlation peak is reduced, broadened, or misplaced.

**How it solves the problem:** A versioned frequency/phase estimate drives an NCO or phase accumulator that rotates complex I/Q samples. Frame phase continuity and parameter update boundaries are checked; the first version can be bypassed while the correlator is validated independently.

**It is responsible for (following that solution path):**

- Rotating complex I/Q with an NCO/phase accumulator;
- Accepting frequency and phase parameters estimated by PS;
- Maintaining phase continuity within a frame;
- Checking sign conventions, parameter versions, and update boundaries;
- Supporting a bypass path for the first implementation.

### H12 `correlator_windowed`: Finite-window matched correlator

**Problem it solves:** The receiver does not know the exact sample position of the PRN arrival, so it must compare the received waveform with a local template at candidate delays within a search window.

**How it solves the problem:** A stored or streaming local template is correlated over an explicitly bounded window. The correlator outputs integer-sample delay and complex accumulation, with controlled width and boundary rules; resource-driven folding, reuse, or FFT options are considered only after the bounded algorithm is verified.

**It is responsible for (following that solution path):**

- Storing or streaming the local PRN/shaped template;
- Computing complex correlation over the finite search window;
- Outputting integer delay and complex accumulation;
- Managing accumulator width to avoid overflow for long codes or high amplitude;
- Rejecting incomplete templates at window boundaries.

### H13 `peak_quality`: Decide whether a correlation peak is trustworthy

**Problem it solves:** The maximum correlation value is not necessarily the real signal. Noise spikes, multipath, saturation, and a peak at the window boundary can all produce false peaks. Without quality assessment, a bad time measurement can enter the servo loop.

**How it solves the problem:** It evaluates the main peak, the second peak outside a guard region, background energy, peak width, and boundary position. Multipath, low SNR, saturation, and other conditions become quality levels and reason codes; only measurements passing the threshold proceed to the servo.

**It is responsible for (following that solution path):**

- Finding the main peak, its location, and its amplitude;
- Computing second-peak, background, peak-to-sidelobe, and width metrics;
- Detecting multipath, broad or boundary peaks, low SNR, and saturation;
- Producing `valid/invalid/candidate` and reason codes;
- Supplying measurement covariance or quality level to later filters.

### H14 `fractional_peak`: Fractional-sample peak estimation

**Problem it solves:** The ADC sample interval can be several nanoseconds, so an integer peak only identifies one sample grid point. Neighboring samples must be used to estimate a fractional position for finer timing resolution.

**How it solves the problem:** It interpolates several samples around the peak, handles zero denominators, boundaries, clipping, and multipath explicitly, and calibrates fixed bias with known-delay scans. The number of fractional bits is kept separate from actual timing accuracy.

**It is responsible for (following that solution path):**

- Applying a three-point parabolic model or a later, more accurate interpolator;
- Outputting fixed-point fractional delay;
- Handling zero denominator, window boundary, clipping, and multipath cases;
- Calibrating systematic bias using known delays.

### H15 `measurement_fifo`: Timing-event records and cross-domain delivery

**Problem it solves:** PL correlation results must be delivered safely to PS. If fields update in different cycles, software may read a torn record—for example, a peak position from one event with quality from the previous event.

**How it solves the problem:** It assembles all fields into one atomic event before crossing into a FIFO. Sequence numbers, overflow/drop reporting, and candidate/confirmed flags make event ownership and validity explicit.

**It is responsible for (following that solution path):**

- Atomically assembling one timing event;
- Recording local event sequence, direction, candidate frame number, integer/fractional peak, quality, and status;
- Carrying boot/epoch/config/calibration IDs;
- Handling FIFO full, dropped events, and duplicate consumption;
- Distinguishing candidate events from confirmed events.

## 8. P1: Bidirectional synchronization and servo execution

### H16 `servo_output_guard`: Safely apply synchronization corrections

**Problem it solves:** A correction computed by software must not immediately rewrite a counter or clock arbitrarily. It must execute at a deterministic future tick, and invalid measurements must not continue driving the control loop.

**How it solves the problem:** Only corrections that pass version, range, and quality checks are converted into H05 future commands. LOCKED, HOLDOVER, INVALID, and REACQUIRE states, rate limits, timeouts, and a watchdog prevent unsafe control values from being applied.

**It is responsible for (following that solution path):**

- Receiving validated phase/frequency corrections;
- Converting them into future commands executable by H05;
- Managing LOCKED, HOLDOVER, INVALID, and REACQUIRE states;
- Applying control slew limits, timeouts, and watchdog protection;
- Outputting logical-time corrections, test PPS, or physical-actuator control;
- Returning execution result and actual tick to PS.

**Logical time and physical clocks must be reported separately:** Modifying an FPGA time accumulator does not mean that the RFSoC physical 10 MHz, ADC/DAC sampling clock, or node-local WR output has been corrected.

## 9. P2: QKD application extensions

### H17 `sagnac_awg`: QKD waveform and slot triggering

**Problem it solves:** The synchronization result must eventually drive a Sagnac loop, lasers, polarization controllers, and detector gates. Every QKD frame/slot needs the correct waveform from a traceable time schedule.

**How it solves the problem:** A finite set of waveforms is preloaded and selected by frame, slot, and coding state. H16/H05 releases playback at the requested time, while version and actual first/last sample times are recorded.

**It is responsible for (following that solution path):**

- Preloading a finite waveform set;
- Selecting a waveform by frame number, slot number, and coding state;
- Starting playback at the time specified by H16/H05;
- Outputting digital triggers to optical equipment and instruments;
- Recording waveform version, amplitude, and actual first/last sample time.

### H18 `tdc_coarse`: Coarse timestamps for photon events

**Problem it solves:** An SNSPD produces discrete electrical pulses, and QKD must record every photon event rather than only PRN correlation peaks.

**How it solves the problem:** Level-shifted SNSPD/NIM/LVDS/LVCMOS inputs are synchronized and edge-detected. Channel, edge, coarse tick, sequence, dead-time, and overflow information are recorded and delivered through an event FIFO for comparison with an external Swabian TimeTagger.

**It is responsible for (following that solution path):**

- Receiving level-shifted SNSPD/NIM/LVDS/LVCMOS inputs;
- Synchronizing inputs and detecting rising/falling edges;
- Recording channel, edge, coarse tick, and event sequence;
- Managing dead time, overflow, and event FIFO;
- Comparing counts with an external Swabian TimeTagger.

### H19 `tdc_fine`: Carry-chain fine-time TDC

**Problem it solves:** H18 quantizes time to the PL clock period. If QKD needs finer arrival timing, the phase of a pulse between clock edges must be measured.

**How it solves the problem:** After input level, placement, temperature, and reference equipment are frozen, a CARRY8 delay line and sampling registers produce a thermometer code. Bubble correction, coarse/fine stitching, code-density calibration, temperature drift, channel skew, DNL/INL, and dead time are evaluated before a fine-time result is claimed.

**It is responsible for (following that solution path):**

- Building a delay line with FPGA `CARRY8`;
- Capturing the thermometer code with sampling registers;
- Performing bubble correction, encoding, and coarse/fine time stitching;
- Obtaining actual bin widths through code-density calibration;
- Evaluating temperature drift, channel skew, DNL/INL, and dead time.

## 10. Required support modules outside the H numbering

### 10.1 Clock-domain and reset support

**Problem they solve:** Messages and reset state cross time, ADC, and DAC domains, while clocks can stop and FIFOs can retain stale state.

**How they solve it:** CDC FIFOs and explicit ownership rules transfer versioned atomic messages. Reset guards wait for the domain to be ready, flush old messages, and allocate a new boot ID after recovery.

**Responsibilities:**

- `anchor_cdc_fifo`: transfer versioned atomic messages between time, ADC, and DAC domains;
- `capture_reset_guard`: prevent recapture while clocks are stopped, FIFOs are not ready, or DMA has not drained;
- `capture_source_guard`: check RFDC health, simultaneous I/Q validity, IRQ, and saturation;
- Synchronizers, handshakes, timeout logic, and stale-message discard in each domain.

This is not “CDC completed by adding one FIFO.” The design must define message ownership, reset flushing, and boot-ID allocation after recovery.

### 10.2 Data buffering, packetization, and DMA

**Problem they solve:** Samples, headers, fault context, and ownership must reach PS as complete packets with a clear lifetime.

**How they solve it:** Fixed packet formats, finite snapshot banks, shadow control commands, and DMA fences make buffer ownership and drain completion explicit.

**Responsibilities:**

- `capture_packetizer`: organize samples, headers, fault prefixes, and CRC into a fixed format;
- `capture_snapshot_bank`: finite-window storage in double-bank/URAM/BRAM;
- `capture_control`: AXI-Lite shadow commands and completion records;
- DMA/width conversion/write fences: use AMD IP with ownership and drain proofs;
- `capture_management`: PS-side start, stop, failure retention, and new-boot recovery.

### 10.3 Transmit-chain control support

**Problem they solve:** Waveform and filter configuration crosses from software/control clock domains into a running DAC data path.

**How they solve it:** Configuration is versioned and transferred atomically; a sample scheduler releases only committed data, while the control block owns coefficient banks, prefill, completion, and fault handling.

**Responsibilities:**

- `tx_rrc_command_regs`: PS writes frame, SPS, amplitude, target sample, and coefficient version;
- `tx_rrc_config_bridge`: atomically transfer configuration into the data domain;
- `tx_rrc_sample_scheduler`: release data at the DAC sample target;
- `tx_rrc_control`: manage coefficient banks, prefill, completion, and faults.

### 10.4 RFDC and board management

**Problem they solve:** RFDC, clock chips, MTS, and external 10 MHz/PPS/GPIO interfaces have board-specific startup and health requirements.

**How they solve it:** Vendor IP and official drivers perform configuration and status access; IRQs and faults are latched and acknowledged, while every MTS/startup operation records its delay and status.

**Responsibilities:**

- RFDC AXI configuration/status access;
- RFDC IRQ latch, mask, acknowledge, and fault recovery;
- Clock-chip SPI startup and read-only status checks;
- MTS (multi-tile synchronization) configuration and per-start delay records;
- External 10 MHz/PPS electrical interfaces and GPIO management.

These functions should preferentially use vendor IP and official drivers. Passing a custom-RTL simulation is not board-level completion.

## 11. PS software modules

PL owns deterministic edges; PS owns complex computation, networking, and records. At minimum, provide:

| Software module | Problem it solves |
| --- | --- |
| `pl_control_client` | Reads design ID/ABI, starts snapshots, submits/cancels commands, and validates completion records. |
| `rfdc_manager` | Configures RFDC, NCO, DDC/DUC, tiles, and MTS, and reads health status. |
| `capture_manager` | Manages DMA buffers, start/stop/drain/recovery, and rejects packets without integrity proof. |
| `prn_reference` | Generates PRN and shaping references consistent with RTL and provides golden vectors. |
| `measurement_pairer` | Pairs A→B/B→A measurements and checks frame IDs, sequence, and time windows. |
| `delay_calibrator` | Loads directional hardware delay, cable delay, and calibration versions. |
| `offset_frequency_filter` | Estimates clock offset/frequency; the first version may use Python/NumPy before moving to C++. |
| `sync_state_manager` | Manages lock, holdover, loss of lock, reacquisition, and QKD-data validity boundaries. |
| `network_protocol` | Exchanges measurements, versions, integrity, and authentication fields; network arrival time cannot replace hardware timestamps. |
| `run_manifest` | Records bitstream, software, clock configuration, calibration, board, and raw-data hashes. |

## 12. Current implementation order for `RF-clock`

The repository currently contains only:

```text
FPGA/src/raw_tick.v          # empty skeleton
FPGA/src/prn_generator.v     # incomplete LFSR skeleton
FPGA/src/rrc_pulse_shaper.v  # empty file
```

Do not start by copying every DMA, management, and RFDC file from the reference project. Follow these milestones:

### Milestone 0: Freeze the specification

- Fix the time-domain clock frequency and reset polarity;
- Define `raw_tick` as 64 bits;
- Decide whether PRN uses the reference H08 16-bit LFSR or a separately frozen 32-bit polynomial;
- Fix chip rate, sample rate, samples per chip, and frame length;
- Write fixed golden vectors.

### Milestone 1: Minimum digital time system

Suggested files:

```text
FPGA/src/clock_reset_guard.v
FPGA/src/raw_timebase.v
FPGA/src/pps_capture.v
FPGA/src/scheduled_commit.v
FPGA/sim/tb_raw_timebase.v
FPGA/sim/tb_time_control.v
```

Acceptance: reset, per-cycle increment, low-word carry, all-ones saturation, no rollback on reference loss, asynchronous PPS phase, and exact future-tick execution.

### Milestone 2: Digital PRN transmit loopback

Suggested files:

```text
FPGA/src/prn_frame_source.v
FPGA/src/bpsk_mapper.v
FPGA/src/tx_rect_samples.v
FPGA/src/tx_frame_scheduler.v
FPGA/sim/tb_prn_frame_source.v
FPGA/sim/tb_tx_chain.v
FPGA/data/golden/prn_vectors.csv
```

Acceptance: bit-for-bit agreement for different seeds and lengths, correct full-cycle/boundary behavior, no chip skipping under backpressure, an error on frame-identity changes, and actual zero DAC data during faults.

### Milestone 3: RRC shaping

First write a Python/NumPy reference model, then implement:

```text
FPGA/src/rrc_coeff_bank.v
FPGA/src/rrc_fir.v
FPGA/src/rrc_pulse_shaper.v
FPGA/sim/tb_rrc_pulse_shaper.v
```

Acceptance: RTL matches Python sample by sample, with explicit latency, rounding, saturation, coefficient version, and frame-tail behavior. Do not implement RRC before these parameters are frozen.

### Milestone 4: Receive and offline timing measurement

Start from files/simulation samples rather than real RFDC:

```text
FPGA/src/rx_frontend.v
FPGA/src/correlator_windowed.v
FPGA/src/peak_quality.v
FPGA/src/fractional_peak.v
FPGA/src/measurement_fifo.v
```

Acceptance: known delay, noise, frequency offset, multipath, no peak, double peak, window boundary, and saturation all produce correct status rather than a silently valid-looking time.

### Milestone 5: Bidirectional software loop

- Pair A→B/B→A events in Python;
- Add hardware-delay/asymmetry calibration;
- Inject known clock offset/frequency offset to verify filter signs;
- Send only quality-checked results to H05/H16;
- Validate logical-time correction before discussing a physical 10 MHz actuator.

### Milestone 6: RFSoC platform integration

Only then add RFDC, DMA, AXI, CDC, clock chips, XDC, Vivado Tcl, and board acceptance. Simulation pass, standalone synthesis pass, place-and-route pass, and real-board pass are four different conclusions and cannot replace one another.

## 13. Acceptance questions every module must answer

Before implementing any module, state clearly:

1. Which clock domain supplies each input?
2. What are the valid conditions and reset values for each input?
3. Does each output represent data, a timestamp, status, or a fault reason?
4. What happens under backpressure, dropped samples, timeout, reset, and configuration change?
5. Can the output version/ID prove that it belongs to the current run?
6. Which fixed golden vector or assertion verifies it?
7. Is the evidence RTL simulation, synthesis, place-and-route, or actual board measurement?

## 14. Key questions not yet frozen

Until these questions are answered, a digital prototype may be built, but completion of 44 km QKD synchronization must not be claimed:

- RF carrier, bandwidth, chip rate, sample rate, and frame period;
- PRN length, full-period ambiguity, and maximum search window;
- DAC/ADC digital reference plane and antenna-feed reference plane;
- Hardware delay and asymmetry calibration for both directions;
- Whether endpoint B corrects logical time, the on-board clock chip, or an externally controlled oscillator;
- Electrical level, impedance, pulse width, and pins for PPS, 10 MHz, and SNSPD inputs;
- Formal QKD limits for mean offset, RMS jitter, holdover time, and reacquisition time;
- Authentication, replay protection, and run numbering for measurement messages.
