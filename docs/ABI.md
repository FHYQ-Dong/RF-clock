# RF-clock PL Programming Manual (ABI 1.3)

This is the reference version; `docs/ABI_zh.md` is the Chinese translation.

This manual describes how ARM software controls the RF-clock PL. Part I is the register-level protocol (the instrument's command set); Part II is the Python library `rf_clock.pl` that wraps it.

- The single RTL source of the protocol is `PL/src/p_regs.vh` (addresses, bit fields) and `PL/src/p_defs.vh` (states, result codes, flags). Python's `rf_clock.pl.abi` is generated from these two files by `tools/generate_abi.py`. Any change to the three updates this manual and bumps `VERSION` per §14.
- Design background: `plan/RF_clock_modules.md`; open items: `plan/checklist.md`.

---

# Part I Protocol

## 1. Overview

### 1.1 Paths

| Path           | PL hardware                                      | What it carries                          | How the ARM accesses it                      |
| -------------- | ------------------------------------------------ | ---------------------------------------- | -------------------------------------------- |
| Control/status | P2, AXI4-Lite registers                          | Commands, timestamps, status             | 32-bit MMIO reads and writes (§4.1–§4.6)     |
| TX waveform    | P3 on-chip buffer, through an AXI BRAM Controller | The transmit waveform (written once, reused) | Memory writes (§4.5.8)                    |
| RX samples     | P4 → async FIFO → AXI DMA S2MM → DDR             | The samples of each capture window       | DMA buffer + DMA registers (§4.6.9)          |
| Burst memory (2.0) | P3/P8/P9 code memories and FIR taps, through an AXI BRAM Controller | The chip sequences and the RRC taps | Memory writes while the slot engine is off (§4.9) |
| Measurement queue (2.0) | P8/P9 entries in a BRAM ring, through an AXI BRAM Controller | This node's measurements and the peer's decoded packets | Memory reads, `QUEUE_HEAD`/`QUEUE_TAIL` (§4.12) |
| Output PPS (2.1) | P10 → RFDC DAC tile 0 → DAC_B (J20); validity line on Pmod J1 pin 1 | One pulse a second, timed by the PL's 100 MHz clock | Registers (§4.13) |
| 10 MHz phase (2.4) | ADC_D → RFDC ADC tile 0 → P11 | The phase of a copy of the node's 10 MHz against the PL's 100 MHz clock, once a second | Registers (§4.14) |
| Clock status   | AXI GPIO (`pl_clk0` domain)                      | Whether the fabric MMCM is locked        | 32-bit MMIO read (§4.7)                      |

### 1.2 Addresses

The base addresses are fixed in `PL/bd/create_rf_clock_bd.tcl`. Software reads them from the HWH (PYNQ `overlay.ip_dict` / `overlay.mem_dict`) rather than hard-coding them.

| Address space      | HWH name                       | Base        | Size                                          | Clock domain |
| ------------------ | ------------------------------ | ----------- | --------------------------------------------- | ------------ |
| P2 registers       | `p_top_0`                      | 0xA000_0000 | 4 KiB (first 1.25 KiB used since 2.5)         | fabric       |
| AXI DMA registers  | `rx_dma`                       | 0xA001_0000 | 64 KiB                                        | pl_clk0      |
| Clock-status GPIO  | `clk_status_gpio`              | 0xA002_0000 | 4 KiB                                         | pl_clk0      |
| RFDC registers     | `rfdc`                         | 0xA004_0000 | 256 KiB                                       | pl_clk0      |
| TX waveform buffer | `tx_bram_ctrl` (`mem_dict`)    | 0xA040_0000 | 4 MiB window; the first 69632 beats (2.125 MiB) have storage | fabric |
| Burst memory (2.0) | `burst_bram_ctrl` (`mem_dict`) | 0xA080_0000 | 64 KiB (§4.9)                                 | fabric       |
| Measurement queue (2.0) | `queue_bram_ctrl` (`mem_dict`) | 0xA081_0000 | 64 KiB (§4.12)                           | fabric       |

> **Warning: bus hang.** The fabric clock comes from the LMK04828 PL_CLK
> through an MMCM. An access to P2 or the TX buffer while the fabric clock is
> not running never completes and hangs the whole system until a reset
> (remotely: `tools/jtag_reset_board.tcl`). **Before every access to these address
> spaces (and to the burst memory and the measurement queue, 2.0), check that
> bit 0 of the clock-status GPIO is 1** (§4.7). The
> `pl_clk0` addresses are not affected.

## 2. Conventions

| Item             | Convention |
| ---------------- | ---------- |
| Byte order       | Little-endian |
| Access width     | P2 accepts aligned 32-bit accesses only; writes must be whole words (`wstrb = 0xF`) |
| Error response   | Partial writes, writes to read-only registers and accesses to unmapped addresses return SLVERR and change nothing. Linux may report a bus error; software must not rely on it |
| 64-bit values    | LO at +0, HI at +4. For values that change (`TICK`, `PPS_TICK`), **read LO before HI**: the PL latches HI in the cycle LO is read, so both halves belong to the same instant and cannot tear |
| Commands         | Write the shadow registers, then the doorbell bit of `CONTROL`, then read `*_RESULT` to confirm (§5.2) |
| Interrupts       | None; everything is polled |

**Units**

| Quantity       | Value |
| -------------- | ----- |
| tick           | 10 ns (100 MHz fabric clock, derived from this node's FS725 10 MHz) |
| beat           | 32 bytes = 256 bits = 8 complex samples |
| complex sample | 16-bit I + 16-bit Q, 800 MS/s, 1.25 ns apart |
| 1 tick         | exactly 1 beat = 8 samples |

**Which times can be used for measurements.** raw_tick counts from PL reset, never wraps, and is not cleared on loss of lock or a new epoch. A register read by the ARM takes from a few hundred ns to milliseconds, so a `TICK` read by software is only used to schedule future targets. Times used in a measurement are always values the PL latched in the cycle of the event: `PPS_TICK`, `TX_ACTUAL`, `RX_ACTUAL`.

## 3. Command entry format

Each register ("command") in section 4 is described with these fields:

| Field   | Content |
| ------- | ------- |
| Address | Byte offset from the P2 base; 64-bit values give the LO and HI offsets |
| Access  | RO read-only; RW read/write; WO write-only (reads as 0); shadow: takes effect at the doorbell |
| Format  | u32 / u64 / bit field |
| Reset   | Value after PL reset; "—" means it changes during operation |
| Syntax  | The access sequence, in the notation below |
| Python  | The corresponding call in `rf_clock.pl` (Part II) |

Notation:

```text
R32 <off>              read 32 bits; the value read follows →
W32 <off> <value>      write 32 bits
R64 <off>              R32 <off> (LO), then R32 <off+4> (HI); value = (HI << 32) | LO
```

## 4. Command reference

### 4.1 System

#### 4.1.1 `ID` — design identifier

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x00    | RO     | u32    | 0x52434C4B ("RCLK") |

```text
R32 0x00  → 0x52434C4B
```

Confirms that the RF-clock bitstream is loaded. On any other value, stop and do not access other registers. Python: `PLClient.validate()`.

#### 4.1.2 `VERSION` — interface version

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x04    | RO     | [31:16] major, [15:0] minor | 0x00020005 (2.5) |

```text
R32 0x04  → 0x00020005
```

Refuse to run when major differs from the one the software supports. In 1.x, `PPS_INPUTS` and `PPS_CONFIG` came with minor 1, `BUILD_VERSION` with 2 and `TX_REPEAT` with 3; 2.0 keeps all of them and adds the slot engine, bursts from chips, the correlator, the packet and the queue (§4.8–§4.12, §14); 2.1 adds the output PPS (§4.13), 2.2 its idle level `PPS_OUT_IDLE`, 2.3 its fractional edge delay `PPS_OUT_FRAC`, 2.4 the phase of the 10 MHz reference (§4.14), 2.5 the FS725's serial line and the steering through FC in the PL (§4.15, §4.16). Python: `PLClient.validate()`, `DeviceInfo.version`.

#### 4.1.3 `CONTROL` — command pulses

| Address | Access | Format    | Reset |
| ------- | ------ | --------- | ----- |
| 0x08    | WO     | bit field | reads as 0 |

```text
W32 0x08 <bits>
```

Each bit written as 1 makes a one-cycle pulse; several bits may be written at once (for example `0x5` starts TX and RX together).

| Bit | Name          | Effect | See |
| --- | ------------- | ------ | --- |
| 0   | `TX_DOORBELL` | Takes `TX_TARGET`/`TX_LENGTH` and checks them; if accepted, TX becomes ARMED | §4.5.3 |
| 1   | `TX_CANCEL`   | Cancels an ARMED TX, back to IDLE | §4.5.4 |
| 2   | `RX_DOORBELL` | Takes `RX_TARGET`/`RX_LENGTH` and checks them | §4.6.3 |
| 3   | `RX_CANCEL`   | Cancels an ARMED RX before its window opens | §4.6.4 |
| 4   | `CLEAR_FAULT` | Clears faults; TX goes from FAULT back to IDLE | §4.2.4 |

#### 4.1.4 `TIME_EPOCH` — time epoch

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x10    | RO     | u32    | 0     |

```text
R32 0x10  → epoch
```

Incremented each time the clocks lock again; 0 means time has never been valid. A change means lock was lost in between: timestamps before and after it are not comparable, and measurements spanning epochs are discarded (§6.4). Python: `PLClient.time_epoch()`, `remember_epoch()`, `require_same_epoch()`.

#### 4.1.5 Build parameters `TX_DEPTH`, `RX_MAX_BEATS`, `MIN_LEAD_TICKS`

| Address | Name             | Access | Value   | Meaning |
| ------- | ---------------- | ------ | ------- | ------- |
| 0x84    | `TX_DEPTH`       | RO     | 69632   | TX buffer capacity (beats) |
| 0x88    | `RX_MAX_BEATS`   | RO     | 2097151 | Longest RX window (beats), limited by the 26-bit DMA length register |
| 0x8C    | `MIN_LEAD_TICKS` | RO     | 64      | How much later than the tick at the doorbell a target must be |

Read once at start-up and use them to check parameters. Python: `PLClient.validate()` → `DeviceInfo`. `TX_DEPTH` = 69632 = 17 × 4096: a 65535-chip burst with its RRC tails (65545 beats) fits.

#### 4.1.6 `BUILD_VERSION` — package version at build time (since 1.2)

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x94    | RO     | [31:24] A, [23:16] B, [15:8] C, [7:0] D | the `pyproject.toml` version at build time |

```text
R32 0x94  → 0x01030000 (rf-clock 1.3.0.0)
```

The build script `PL/bd/create_rf_clock_bd.tcl` reads `version = "A.B.C.D"` from `pyproject.toml` and makes it the constant `0xAABBCCDD`; A.B is `VERSION`, and the build fails if they differ. Software compares it with its installed rf-clock version and refuses to run when A.B.C differs (§14.1). Python: `PLClient.build_version()` → `"A.B.C.D"`.

### 4.2 Status and faults

#### 4.2.1 `STATUS` — status word

| Address | Access | Format    | Reset |
| ------- | ------ | --------- | ----- |
| 0x0C    | RO     | bit field | —     |

```text
R32 0x0C  → status
```

| Bit   | Name             | Meaning |
| ----- | ---------------- | ------- |
| 0     | `time_valid`     | Clocks locked and stable; timestamps are valid |
| 1     | `clocks_locked`  | The current clock-lock signal (before the stability delay) |
| 2     | `pps_locked`     | `PPS_LOCK_COUNT` (4) consecutive PPS periods were correct |
| 3     | `tick_exhausted` | raw_tick has saturated (never in normal operation) |
| 4     | `any_fault`      | `FAULT_BITS` is not 0 |
| 5     | `dma_ready`      | The S2MM is accepting data (`tready` high) |
| 6     | `slots_running`  | The slot engine runs (2.0, §4.8) |
| 7     | `locked`         | P8's tracker is locked (2.0, §4.10) |
| 10:8  | `tx_state`       | TX state, encoded as in §5.1 |
| 14:12 | `rx_state`       | RX state, encoded as in §5.1 |

The state fields are the authority on TX/RX progress (`*_RESULT` can be overwritten by `REJ_BUSY`, §5.2). Python: `PLClient.status()` → `Status`.

#### 4.2.2 `FAULT_BITS` — sticky fault bits

| Address | Access | Format          | Reset |
| ------- | ------ | --------------- | ----- |
| 0x14    | RO     | [3:0] bit field | 0     |

```text
R32 0x14  → bits
```

Encoding in §5.4. Cleared only by `CLEAR_FAULT` or a new `TIME_EPOCH`. Python: `abi.FaultBit`.

#### 4.2.3 `FAULT_INFO` — fault information

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x18    | RO     | [3:0] first fault source, [31:16] total fault events | 0 |

```text
R32 0x18  → info
```

- [3:0]: bit number + 1 of the first fault since the last clear; 0 = none.
- [31:16]: a count of fault events that is never cleared. Comparing two polls shows whether a new fault happened in between.

#### 4.2.4 `CONTROL.CLEAR_FAULT` — clear faults

```text
W32 0x08 0x10
```

Clears `FAULT_BITS` and `FAULT_INFO[3:0]`; a TX in FAULT returns to IDLE and `TX_RESULT` becomes NONE. A new fault in the same cycle as the clear is kept. Read and record `FAULT_BITS`/`FAULT_INFO` before clearing. Python: `PLClient.clear_fault()`.

### 4.3 Time

#### 4.3.1 `TICK` — current raw_tick

| Address            | Access | Format     | Reset |
| ------------------ | ------ | ---------- | ----- |
| 0x20 (LO), 0x24 (HI) | RO   | u64, ticks | —     |

```text
R64 0x20  → tick          reading LO latches HI
```

Only for scheduling future targets, never for measurements (§2). Python: `PLClient.read64(ABI.REG.TICK_LO)`.

### 4.4 PPS

#### 4.4.1 `PPS_TICK`, `PPS_COUNT` — the latest PPS

| Address              | Name        | Access | Format     | Reset |
| -------------------- | ----------- | ------ | ---------- | ----- |
| 0x28 (LO), 0x2C (HI) | `PPS_TICK`  | RO     | u64, ticks | 0     |
| 0x30                 | `PPS_COUNT` | RO     | u32        | 0     |

```text
R64 0x28  → pps_tick      reading LO latches PPS_TICK_HI and PPS_COUNT
R32 0x30  → pps_count     belongs to the same PPS edge as pps_tick above
```

`PPS_TICK` is the tick in which the timed edge of the selected PPS signal (§4.4.5) reached the pin, with the fixed synchronizer delay removed. To use the tick and the count as a pair, read `PPS_TICK_LO` first. Timing semantics in §6.3.

#### 4.4.2 `PPS_LAST_PERIOD` — the latest period

| Address | Access | Format     | Reset |
| ------- | ------ | ---------- | ----- |
| 0x34    | RO     | u32, ticks | 0     |

```text
R32 0x34  → 100000000     normal value
```

Ticks between the last two PPS edges. A constant 100000000 shows that the fabric clock and the FS725 share their reference.

#### 4.4.3 `PPS_JITTER_CNT` — jitter count

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x38    | RO     | u32    | 0     |

Number of periods off by ±1 tick (within tolerance but not 0). P1 checks every period in hardware, so this covers periods software did not sample. A steady increase means the PPS edge falls close to the clock sampling edge (§6.3).

#### 4.4.4 `PPS_INPUTS` — the board's two PPS copies (since 1.1)

| Address | Access | Format    | Reset |
| ------- | ------ | --------- | ----- |
| 0x3C    | RO     | bit field | —     |

```text
R32 0x3C  → inputs
```

| Bit   | Name         | Meaning |
| ----- | ------------ | ------- |
| 0     | `COMP_LEVEL` | Current level of `IRIG_COMP_OUT` (comparator), synchronized |
| 1     | `TRIG_LEVEL` | Current level of `IRIG_TRIG_OUT` (Schmitt trigger) |
| 15:8  | `COMP_EDGES` | Rising edges on `IRIG_COMP_OUT`, 8 bits, wraps |
| 23:16 | `TRIG_EDGES` | Rising edges on `IRIG_TRIG_OUT`, 8 bits, wraps |

Shows without a scope whether the PPS arrives and its polarity: a PPS pulse is only 10–30 µs wide, so the level read is almost always the idle level. Idle 0 means positive pulses; idle 1 means negative pulses (needs `INVERT = 1`). Read twice a few seconds apart: the counts advance by one per second; if neither moves, the PPS does not reach the board's PPS connector (J7). Python: `PLClient.pps_inputs()` → `PPSInputs`.

#### 4.4.5 `PPS_CONFIG` — PPS source and polarity (since 1.1)

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x90    | RW     | [1:0] bit field; other bits are ignored on write and read as 0 | 0 |

```text
W32 0x90 <cfg>
R32 0x90  → cfg
```

| Bit | Name       | Meaning |
| --- | ---------- | ------- |
| 0   | `SRC_TRIG` | 0 = `IRIG_COMP_OUT` (AJ13, default), 1 = `IRIG_TRIG_OUT` (AH13) |
| 1   | `INVERT`   | 1 = time the falling edge of the selected signal |

Board circuit: the PPS SMA (J7) has a 49.9 Ω termination to ground and is AC-coupled through 100 Ω and 10 nF to a node biased at 1.65 V, which feeds a comparator (LMV7235, about ±0.17 V hysteresis) and a Schmitt trigger (SN74AUP1G17). Both copies reach 1.8 V through an NVT2008 (the FPGA's internal pull-up is used). A positive FS725 pulse is a rising edge on both copies; use the default 0.

Writing a different value restarts PPS capture in P1: `pps_locked` clears and lock returns after one reference edge and 4 correct periods; no fault is recorded. Set it only at start-up. Python: `PLClient.set_pps_config(source_trig=..., invert=...)`.

### 4.5 TX (transmit)

#### 4.5.1 `TX_TARGET` — start time (shadow)

| Address              | Access    | Format     | Reset |
| -------------------- | --------- | ---------- | ----- |
| 0x40 (LO), 0x44 (HI) | RW shadow | u64, ticks | 0     |

```text
W32 0x40 <target[31:0]>
W32 0x44 <target[63:32]>
```

The tick in which the DAC accepts beat 0 (§6.1). At the doorbell, `target ≥ tick at that moment + MIN_LEAD_TICKS`, or the command is rejected with `REJ_STALE`.

#### 4.5.2 `TX_LENGTH` — burst length (shadow)

| Address | Access    | Format     | Reset |
| ------- | --------- | ---------- | ----- |
| 0x48    | RW shadow | u32, beats | 0     |

```text
W32 0x48 <beats>          1 … TX_DEPTH
```

Sends this many beats in a row, starting from beat 0 of the buffer. 0 or more than `TX_DEPTH` is rejected with `REJ_LENGTH`. Repeated playback: `TX_REPEAT` (§4.5.9).

#### 4.5.3 `CONTROL.TX_DOORBELL` — submit a TX command

```text
W32 0x40 <target_lo>
W32 0x44 <target_hi>
W32 0x48 <beats>
W32 0x5C <passes>         since 1.3; if not written, the previous value is used (reset 1)
W32 0x08 0x01
R32 0x4C  → 0x01 (ARMED)  otherwise see §5.2
```

Refused with `REJ_BUSY` while `SLOT_CONTROL.ENABLE` is set (2.0): the slot engine owns the DAC then (§4.8). Handled only when the TX state is IDLE or DONE. Checks, in order: `time_valid` → length and repeat count → stale target. The first failing check gives its reject code and the state does not change. Only the shadow values at the doorbell cycle count; changing the shadows afterwards does not affect an accepted command. With a near target, the command may already be RUNNING or even DONE when `TX_RESULT` is read (tell by whether `TX_SEQ` advanced). Python: `PLClient.arm_tx(target, beats, *, repeat=1)`.

#### 4.5.4 `CONTROL.TX_CANCEL` — cancel

```text
W32 0x08 0x02
```

Only in ARMED: back to IDLE, `TX_RESULT = CANCELLED`. Once RUNNING, a burst cannot be cancelled; it finishes or enters FAULT. Python: `PLClient.cancel_tx()`.

#### 4.5.5 `TX_RESULT` — result code

| Address | Access | Format             | Reset |
| ------- | ------ | ------------------ | ----- |
| 0x4C    | RO     | [7:0] result code  | 0     |

Encoding in §5.2.

#### 4.5.6 `TX_SEQ` — completion count

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x50    | RO     | u32    | 0     |

Number of bursts that completed (DONE). Compare it before and after a submit to know that a result belongs to that command.

#### 4.5.7 `TX_ACTUAL` — actual start time

| Address              | Access | Format     | Reset |
| -------------------- | ------ | ---------- | ----- |
| 0x54 (LO), 0x58 (HI) | RO     | u64, ticks | 0     |

```text
R64 0x54  → actual        read only after tx_state = DONE
```

The tick in which the DAC accepted beat 0. Normally equal to the target; software checks it. Python: `PLClient.tx_actual()`.

#### 4.5.8 TX waveform buffer (memory)

| Address                          | Access | Format |
| -------------------------------- | ------ | ------ |
| `tx_bram_ctrl` base + i × 32     | write  | beat i, 32 bytes, little-endian |

```text
W32 base+i*32+0  <beat i, bytes 0..3>
...
W32 base+i*32+28 <beat i, bytes 28..31>
```

- The buffer is in UltraRAM, in 17 banks of 4096 beats; capacity `TX_DEPTH` = 69632 beats. The address window is 4 MiB (2^17 beats); writes at or above `TX_DEPTH` are dropped and reads there are meaningless.
- The PL passes each beat unchanged to the RFDC DAC. The DAC reads **I and Q interleaved per word**: sample n (n = 0…7, earliest first) is 32-bit word n, `I = [32n+15:32n]`, `Q = [32n+31:32n+16]` (`TX_LAYOUT`). Evidence: the M3 lane check (2026-10-14, §13); with planar packing the impulse delay of lane k became D − ⌈k/2⌉, which is exactly what interleaved reading gives. The impulses were on I only, so which of the two 16-bit halves is I and which is Q is not verified separately (no effect on real BPSK signals).
- Write a waveform once while it does not change; each burst then only needs a TX command.
- **Do not** write the buffer while TX is ARMED or RUNNING; TX would enter FAULT with `FAULT_BUF_WRITE`.
- Fabric clock domain: check the clock-status GPIO before writing (§1.2).

Python: `TXBuffer.write_beats(beats)`.

#### 4.5.9 `TX_REPEAT` — repeated playback (shadow, since 1.3)

**Development tests only** (rf-clock 2): continuous signals for spectrum and scope measurements. It is not part of the protocol: the protocol's bursts come from the slot engine (P7), which refuses single commands, this one included, while it runs.

| Address | Access    | Format          | Reset |
| ------- | --------- | --------------- | ----- |
| 0x5C    | RW shadow | u32, 1 … 65535 | 1     |

```text
W32 0x5C <passes>         1 … TX_REPEAT_MAX (65535)
```

One TX command plays buffer beats 0 … `TX_LENGTH`−1 `TX_REPEAT` times back to back: beat 0 of each pass follows the last beat of the previous pass in the next cycle, with no gap. `TX_ACTUAL` is the start of the first pass; DONE comes after the last pass and `TX_SEQ` advances by one. 0 or more than 65535 is rejected with `REJ_LENGTH`. The total is `TX_LENGTH × TX_REPEAT` cycles, at most about 45 s; a RUNNING burst cannot be cancelled.

The shadow is taken at the doorbell; if not written, the previous value is used. `PLClient.arm_tx` writes it on every command on 1.3 and later bitstreams (default 1); on older bitstreams this address does not exist (writes return SLVERR), so it is not written and `repeat` other than 1 raises `IdentityError`.

Seamless repetition needs a periodic waveform (`WaveformGenerator(periodic=True)`, §11.9): an ordinary burst has an RRC tail at each end and would not join up. Python: `PLClient.arm_tx(target, beats, repeat=n)`.

### 4.6 RX (receive)

> The RX path is verified on the board (2026-10-14): timed windows, DMA
> writes, `RX_ACTUAL` and byte-count checks; 64 MB (20 ms) windows did not
> overflow under about 4 GB/s of ARM memory traffic
> (`rf_clock.checks.m3_loopback --stress`).

#### 4.6.1 `RX_TARGET` — window start (shadow)

| Address              | Access    | Format     | Reset |
| -------------------- | --------- | ---------- | ----- |
| 0x60 (LO), 0x64 (HI) | RW shadow | u64, ticks | 0     |

Beat 0 of the window is the data on the ADC bus in the cycle where `raw_tick == RX_TARGET` (§6.2). Stale targets are rejected as for TX.

#### 4.6.2 `RX_LENGTH` — window length (shadow)

| Address | Access    | Format     | Reset |
| ------- | --------- | ---------- | ----- |
| 0x68    | RW shadow | u32, beats | 0     |

1 … `RX_MAX_BEATS`; otherwise rejected with `REJ_LENGTH`.

#### 4.6.3 `CONTROL.RX_DOORBELL` — submit an RX command

```text
(set up the S2MM first, §4.6.9)
W32 0x60 <target_lo>
W32 0x64 <target_hi>
W32 0x68 <beats>
W32 0x08 0x04
R32 0x6C  → 0x01 (ARMED)  0x14 means the S2MM is not ready yet
```

Refused with `REJ_BUSY` while `SLOT_CONTROL.ENABLE` is set (2.0); a slot window reaches DDR by `SLOT_COPY` then (§4.8). Handled only when the RX state is IDLE or DONE. Checks, in order: `time_valid` → length → stale target → DMA ready. Python: `pl.ctrl.arm_rx(target, beats)`, after `pl.rx_dma.prepare(beats)` (§11.8).

#### 4.6.4 `CONTROL.RX_CANCEL` — cancel

```text
W32 0x08 0x08
```

Only while ARMED and before the window opens: back to IDLE, `RX_RESULT = CANCELLED`. An S2MM transfer already started does not end by itself; reset the DMA (`PynqS2MMDMA.abort()`). Python: `PLClient.cancel_rx()`.

#### 4.6.5 `RX_RESULT` — result code and window flags

| Address | Access | Format | Reset |
| ------- | ------ | ------ | ----- |
| 0x6C    | RO     | [7:0] result code, [11:8] window flags | 0 |

Result codes in §5.2, flags in §5.3. A window is valid only if it ends with `DONE`; `RX_INVALID` means this measurement must be discarded. Python: `PLClient.rx_result()` → `(ResultCode, RxFlag)`.

#### 4.6.6 `RX_SEQ`, `RX_INVALID_CNT` — counters

| Address | Name             | Access | Format | Meaning |
| ------- | ---------------- | ------ | ------ | ------- |
| 0x70    | `RX_SEQ`         | RO     | u32    | Windows ended (valid and invalid) |
| 0x80    | `RX_INVALID_CNT` | RO     | u32    | Invalid windows in total |

#### 4.6.7 `RX_BEATS` — beats sent to the DMA

| Address | Access | Format     | Reset |
| ------- | ------ | ---------- | ----- |
| 0x74    | RO     | u32, beats | 0     |

Beats of this window sent into the DMA stream, including the tail beat added to an invalid window. A valid window has `RX_BEATS = RX_LENGTH`, and the S2MM has received exactly `RX_BEATS × 32` bytes. Python: `PLClient.rx_beats()`.

#### 4.6.8 `RX_ACTUAL` — actual window start

| Address              | Access | Format     | Reset |
| -------------------- | ------ | ---------- | ----- |
| 0x78 (LO), 0x7C (HI) | RO     | u64, ticks | 0     |

The tick in which beat 0 of the window was on the ADC bus; read only after `rx_state = DONE`; equal to the target. Python: `PLClient.rx_actual()`.

#### 4.6.9 RX sample buffer and AXI DMA S2MM

The sample buffer is in DDR:

- Software allocates a physically contiguous, DMA-accessible buffer (PYNQ `allocate`) of at least `RX_LENGTH × 32` bytes.
- Beat n is at buffer + n × 32. Within a beat the layout is **planar**: the low 128 bits are 8 I samples and the high 128 bits 8 Q samples, lane 0 (the earliest sample) in the lowest 16 bits of each (`RX_LAYOUT`, formed in the block design by concatenating the I and Q streams). The lane order is confirmed by the M3 lane check; which half is I and which Q is, as in §4.5.8, not verified separately.
- An invalid window may miss beats and may end with an extra all-zero beat (which carries TLAST); its contents must not be used.
- DMA writes bypass the CPU cache; invalidate before reading (PYNQ `buffer.invalidate()`).

The DMA is a Xilinx AXI DMA in simple mode; offsets per AMD PG021, address space `rx_dma`:

| Offset | Name          | Use |
| ------ | ------------- | --- |
| 0x30   | `S2MM_DMACR`  | bit 0 = RS (run), set at initialization; bit 2 = soft reset (self-clearing) |
| 0x34   | `S2MM_DMASR`  | bit 1 = Idle; bit 12 = IOC (complete); bits 4–6 = errors |
| 0x48   | `S2MM_DA`     | Destination physical address, low 32 bits |
| 0x4C   | `S2MM_DA_MSB` | Destination physical address, high 32 bits |
| 0x58   | `S2MM_LENGTH` | Writing it starts the transfer; afterwards it reads the bytes actually received (26 bits) |

### 4.7 Clock-status GPIO

| Address                                 | Access | Format |
| --------------------------------------- | ------ | ------ |
| `clk_status_gpio` + 0x0 (`GPIO_DATA`)   | RO     | bit 0  |

```text
R32 0xA002_0000  → 1     fabric MMCM locked; P2 and the TX buffer may be accessed
```

A Xilinx AXI GPIO (one input bit) in the `pl_clk0` domain, readable at any time. Bit 0 = 1 means PL_CLK is present and the MMCM is locked; it **does not mean that the LMK04828 is locked to the FS725**: without its external reference the LMK keeps running from the on-board VCXO. The evidence that the LMK follows the FS725 is `PPS_LAST_PERIOD` staying at 100000000; the PLL1/PLL2 LEDs on the board (this project's LMK file makes Status_LD1/2 lock indicators) are only a visual hint, and the LMK is not read back over SPI. Python: `PLClient.fabric_clock_locked()`, `require_fabric_clock()`.

### 4.8 Slot engine, P7 (since 2.0)

The slot engine runs the ping-pong of plan §5 from each PPS edge, with no command per slot. Slot k = 0 … `SLOTS_PER_SECOND` − 1 starts at `PPS_TICK` + k × `SLOT_TICKS` (200 000 ticks, 2 ms). In each of its own slots (even ones for node A, odd ones for node B, `SLOT_CONTROL.ROLE_B`) it starts a burst `SLOT_TX_OFFSET` ticks after the slot start; in each of the peer's slots it opens a window `SLOT_RX_OFFSET` ticks after the slot start, `RX_SPAN_BEATS` beats long. The first `RX_WINDOW_BEATS` beats of the window go to the correlator (P8, §4.10), the whole span to the packet decoder (P9, §4.11).

```text
slot start = PPS_TICK + k × SLOT_TICKS
own slot:   burst beat 0 on the DAC AXIS when raw_tick = slot start + SLOT_TX_OFFSET   (as TX_ACTUAL)
peer slot:  window beat 0 = the ADC beat of raw_tick = slot start + SLOT_RX_OFFSET     (as RX_ACTUAL)
```

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x100 | `SLOT_CONTROL` | RW | 0 | bit 0 `ENABLE`, bit 1 `ROLE_B` (0: node A, bursts in even slots; 1: node B, in odd slots), bit 2 `PACKETS` (a measurement packet behind every burst). Taken at the next PPS edge |
| 0x104 | `SLOT_TX_OFFSET` | RW | 1000 | ticks from the slot start to the burst. Taken at the next PPS edge |
| 0x108 | `SLOT_RX_OFFSET` | RW | 0 | ticks from the slot start to the window: the peer's `SLOT_TX_OFFSET` plus the expected arrival, less 125 ticks (1.25 µs). Taken at the next PPS edge |
| 0x10C | `SLOT_STATUS` | RO | 0 | bit 0 `RUNNING` (from the PPS edge after `ENABLE`), [24:16] the current slot |
| 0x110 | `SLOT_SKIPPED_CNT` | RO | 0 | slots not started (below) |
| 0x114 | `SLOT_COPY` | RW | 0 | write 1: the next window (its whole span) also goes to DDR through the S2MM, set up for `RX_SPAN_BEATS` beats first (§4.6.9); reads 1 until that window opens |
| 0x118 | `SLOT_TICKS` | RO | 200000 | ticks per slot |
| 0x11C | `SLOTS_PER_SECOND` | RO | 500 | |
| 0x120 | `RX_WINDOW_BEATS` | RO | 5481 | the correlator's window, an RTL parameter (43848 samples) |
| 0x124 | `RX_SPAN_BEATS` | RO | — | `RX_WINDOW_BEATS` + `BURST_GUARD_CHIPS` + `PACKET_BITS` × `PACKET_SPREAD` + 1: the window and the latest packet it can hold |

- The engine schedules slots only while `time_valid` and `pps_locked`; without them it idles (`RUNNING` 0) and starts again at the next PPS edge.
- A slot is skipped and counted when its start has passed by the time the engine schedules it, when `SLOT_TX_OFFSET` + `BURST_BEATS` > `SLOT_TICKS` (the burst would run into the next slot), or when `SLOT_RX_OFFSET` + `RX_SPAN_BEATS` > `SLOT_TICKS` + `SLOT_TX_OFFSET` (the window would still be open at the node's next burst).
- While `ENABLE` is set, P2's TX and RX commands are refused with `REJ_BUSY` (§4.5.3, §4.6.3) and burst memory writes are refused (§4.9). `TX_REPEAT` and the TX waveform buffer serve development tests only, with the engine off.
- A low DAC `tready` during a burst is a fault (`FB_TX`, §5.4): the engine stops until `CONTROL.CLEAR_FAULT` and the next PPS edge.

Python: `pl.slots.configure(...)`, `pl.slots.status()` (§11.14).

### 4.9 Bursts from chips and the burst memory, P3 (since 2.0)

A burst is one chip stream through the RRC FIR, one chip per tick (`rf_clock.plmodel.fir`, plan §3.1):

```text
BURST_PRN_CHIPS chips of the PRN memory, BURST_GUARD_CHIPS zero chips,
with SLOT_CONTROL.PACKETS: PACKET_BITS × PACKET_SPREAD packet chips, chip m = code[m] for a 0 bit, −code[m] for a 1 bit
(bit b of the packet, most significant first, covers chips b·L … b·L + L − 1), then 10 zero chips that flush the FIR.
sample 8n + k += chip[n] × tap[k], k = 0 … 80: the 16-bit I sample, Q = 0; no rounding.
```

`BURST_BEATS` is the number of chips (the 10 flush chips included): each chip is one beat. The ARM chooses taps (round(h[k] × amplitude)) whose largest phase sum, Σ\|tap[p + 8j]\|, fits 16 bits; the PL does not saturate. The correlator's reference (§4.10) and the packet decoder's code reference (§4.11) come from the same memories through the same FIR, so they equal what the peer's PL transmits.

The burst memory window (0xA080_0000, 32-bit words, read latency 1):

| Byte offset | Content |
| ----------- | ------- |
| 0x0000 … 0x1FFF | PRN or the fixed code used before acquisition: chip n is bit n % 32 of word n / 32, 1 for +1 and 0 for −1 (65536 chips) |
| 0x2000 … 0x3FFF | packet code, packed alike (65536 chips) |
| 0x4000 … 0x4140 | taps: tap k is a signed 16-bit value in bits 15:0 of word k, k = 0 … 80 (read back sign-extended) |

Writes are refused while the slot engine is enabled: the memory keeps its contents and `BURST_MEM_REJ_CNT` counts them. Reads are always allowed.

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x180 | `BURST_PRN_CHIPS` | RW | 4096 | chips of the PRN, 1 … `CHIP_MEM_CHIPS` (other values: SLVERR). Taken at the next PPS edge |
| 0x184 | `BURST_GUARD_CHIPS` | RW | 10 | zero chips before the packet, at least the RRC span. Taken at the next PPS edge |
| 0x188 | `BURST_BEATS` | RO | — | beats of a burst as configured |
| 0x18C | `BURST_CNT` | RO | 0 | bursts the engine sent |
| 0x190 | `PACKET_SPREAD` | RO | 64 | L: chips per packet bit, an RTL parameter |
| 0x194 | `PACKET_BITS` | RO | 288 | |
| 0x198 | `CHIP_MEM_CHIPS` | RO | 65536 | capacity of each code memory |
| 0x19C | `BURST_MEM_REJ_CNT` | RO | 0 | burst memory writes refused |

Python: `pl.burst.load(prn, taps, packet_code=None)` (§11.14).

### 4.10 Correlator, P8 (since 2.0)

The correlator keeps two window buffers of `RX_WINDOW_BEATS` beats. With R = 8 × `BURST_PRN_CHIPS` + 73 reference samples, a window has K = 8 × `RX_WINDOW_BEATS` − R + 1 lags (`P8_SEARCH_LAGS`); lag k means the reference starts at window sample k. Every estimate is the one `rf_clock.plmodel.correlator` defines to the bit: exact integer correlation of the 16-bit I and Q with the 16-bit reference, magnitude ⌊√(I² + Q²)⌋, the sub-sample position of the parabola through the peak and its neighbours in 2^-32 sample (rounded half away from zero, kept in i32: half a sample after the peak reads 2^31 − 1), the carrier phase by CORDIC in 2^-32 turn.

- **Tracker**, every window: lags L₀ − H … L₀ + H, H = `P8_TRACK_HALF` (4), L₀ = `P8_LOCK_LAG`, samples beyond the window taken as zero. The lock holds while the largest power is at neither end and its magnitude × 2^16 ≥ `P8_CONFIG.LOCK_MIN` × the last lock's magnitude; L₀ then follows the peak's integer lag and the measurement goes to the queue (`QEF_LOCKED`, §4.12). Otherwise the lock is lost (`P8_LOST_CNT`) and the window is searched.
- **Search**, on the newest window when the tracker is not locked (lost, or not yet locked after start-up; then again on fresh windows until it locks), or on `P8_CONTROL.SEARCH`. One at a time: a request while `SEARCH_BUSY`, or for a PRN that leaves fewer than 3 lags (`P8_SEARCH_LAGS` then reads 0), is refused (`P8_REFUSED_CNT`). The searched window's buffer stays frozen until the search ends; later windows go to the other buffer. It takes `BURST_PRN_CHIPS` + 18 ticks to make the reference, ⌈K / `P8_SEARCH_LANES`⌉ × (R + `P8_SEARCH_LANES`) ticks in the lanes, K ticks to scan their results and about 300 for the estimates: about 3.9 ms for 11008 lags and 1024 lanes, within the 4 ms between two windows.
- **Report** (`SR_*`): the strongest peak (largest power, the first lag on a tie); the earliest peak, the first lag that is a local maximum of power with power × 2^32 ≥ `THRESHOLD`² × the strongest's power; the sidelobe ⌊√(largest power more than 16 lags from the strongest)⌋; the window's `PPS_COUNT`, slot, start tick and RX flags. `SR_SEQ` advances after the other fields: read it first and last, and read again if it changed.
- **Re-lock**: not locked, the PL locks on a search's strongest peak by itself only if it lies within ±1 lag of `P8_LOCK_LAG`, is as strong as the tracker requires (its magnitude × 2^16 ≥ `P8_CONFIG.LOCK_MIN` × the last lock's magnitude; since rf-clock 2.0.1.0) and a lock existed before (since the engine started or the last `UNLOCK`; `P8_RELOCK_CNT`). Without the magnitude test a noise peak that fell within one lag by chance took the lock while the peer was silent (one search in about 3700 does). Otherwise `P8ST_WAITING` is set until the ARM, after checking the one-way delay, writes `ACCEPT_STRONGEST` or `ACCEPT_EARLIEST` (lock on that peak's lag, magnitude and correlation; ignored before the first report) or `SEARCH`. `UNLOCK` drops the lock. A search the ARM requests while locked leaves the tracker's measurements valid. When the engine stops (`SLOT_STATUS.RUNNING` 0) the lock and any pending request are dropped.
- **Quality** of a measurement: ⌊(Λ(P) − Λ(S) + `QUALITY_OFFSET`) × 771 / 1024⌋, clamped to 0 … 255, in 0.25 dB: P = I² + Q² at the peak, S = Σ(I² + Q²) over the window, Λ(v) = 16 × ⌊log2 v⌋ plus the 4 bits after v's leading one. With `QUALITY_OFFSET` = 16 × log2(window samples / Σ ref²), which the ARM computes from the reference, it approximates 4 × 10 lg(peak power / (reference energy × mean sample power)), within about 0.3 dB.

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x200 | `P8_CONTROL` | WO | — | write-1 pulses: bit 0 `SEARCH`, bit 1 `ACCEPT_STRONGEST`, bit 2 `ACCEPT_EARLIEST`, bit 3 `UNLOCK` |
| 0x204 | `P8_CONFIG` | RW | 0x80008000 | [15:0] `THRESHOLD` for the earliest peak, [31:16] `LOCK_MIN`, both in 2^-16 |
| 0x208 | `P8_STATUS` | RO | 0 | bit 0 `LOCKED`, bit 1 `SEARCH_BUSY`, bit 2 `WAITING` |
| 0x20C | `P8_LOCK_LAG` | RO | 0 | L₀ |
| 0x210 / 0x214 | `P8_LOCK_MAG_LO` / `_HI` | RO | 0 | the last lock's magnitude |
| 0x218 | `P8_LOST_CNT` | RO | 0 | locks lost |
| 0x21C | `P8_RELOCK_CNT` | RO | 0 | locks the PL took by itself |
| 0x220 | `P8_SEARCH_CNT` | RO | 0 | searches finished |
| 0x224 | `P8_REFUSED_CNT` | RO | 0 | search requests refused while busy |
| 0x228 | `P8_TRACK_HALF` | RO | 4 | H |
| 0x22C | `P8_SEARCH_LANES` | RO | 1024 | an RTL parameter |
| 0x230 | `P8_SEARCH_LAGS` | RO | — | K |
| 0x234 | `QUALITY_OFFSET` | RW | 0 | signed, 1/16 log2 unit |
| 0x240 | `SR_SEQ` | RO | 0 | search reports written |
| 0x244 | `SR_PPS_COUNT` | RO | 0 | the window's second |
| 0x248 | `SR_SLOT` | RO | 0 | the window's slot |
| 0x24C | `SR_FLAGS` | RO | 0 | the window's RX flags (§5.3) |
| 0x250 / 0x254 | `SR_TICK_LO` / `_HI` | RO | 0 | the window's start tick |
| 0x258 … 0x268 | `SR_STRONG_LAG`, `_FRAC` (i32, 2^-32 sample), `_PHASE` (i32, 2^-32 turn), `_MAG_LO`, `_MAG_HI` | RO | 0 | the strongest peak |
| 0x26C … 0x27C | `SR_EARLY_LAG`, `_FRAC`, `_PHASE`, `_MAG_LO`, `_MAG_HI` | RO | 0 | the earliest peak |
| 0x280 / 0x284 | `SR_SIDELOBE_LO` / `_HI` | RO | 0 | the sidelobe magnitude |

Python: `pl.correlator` (§11.14).

### 4.11 Measurement packet, P9 (since 2.0)

The packet is the 36-byte word of plan §5.1 (`rf_clock.plmodel.packet`): the fields in order, each big-endian, sent most significant bit first; the last two bytes are the CRC-16/CCITT-FALSE of the others (polynomial 0x1021, initial 0xFFFF, no reflection, no final XOR).

| Field | Bytes | The PL fills it with |
| ----- | ----- | -------------------- |
| version | 1 | `PACKET_FORMAT_VERSION` (1) |
| config_id | 4 | `PACKET_CONFIG_ID` |
| PPS_COUNT, slot | 4, 2 | this burst's second and slot |
| TIME_EPOCH | 1 | its low 8 bits |
| T_tx | 8 | (the burst's tick − `PPS_TICK`) × 10^7 fs |
| T_rx | 8 | the latest measurement of the peer's burst: (its window tick − the `PPS_TICK` of that second) × 10^7 + lag × 1 250 000 + ⌊(fraction × 1 250 000 + 2^31) / 2^32⌋ fs |
| phase | 4 | that measurement's phase, 2^-32 turn |
| quality | 1 | that measurement's quality, 0.25 dB |
| status | 1 | bits `PKST_LOCKED`, `PKST_MEASUREMENT` (T_rx, phase and quality are a measurement), `PKST_WINDOW_VALID`, `PKST_OVERFLOW`, `PKST_PPS_LOCKED`, `PKST_CLOCKS_LOCKED` |
| CRC | 2 | |

While locked, with `SLOT_CONTROL.PACKETS`, the decoder despreads the peer's packet in every window, from window sample `P8_LOCK_LAG` + 8 × (`BURST_PRN_CHIPS` + `BURST_GUARD_CHIPS`), with the lock's correlation as the carrier phase reference (both as they were when the window was armed: from the peer's previous burst) (`rf_clock.plmodel.packet.despread`): bit b correlates its 8 L samples, from 8 L b + 36 after the packet start, with the code reference, and is 1 when the projection on the lock's correlation is negative. A good CRC puts a `QE_TYPE_PEER` entry in the queue; a bad one only counts.

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x300 | `PACKET_CONFIG_ID` | RW | 0 | config_id in every packet: the RRC configuration (plan §3.1) |
| 0x304 | `PACKET_VERSION` | RO | 1 | `PACKET_FORMAT_VERSION` |
| 0x308 | `PACKET_TX_CNT` | RO | 0 | packets sent |
| 0x30C | `PACKET_RX_CNT` | RO | 0 | packets decoded with a good CRC |
| 0x310 | `PACKET_CRC_CNT` | RO | 0 | packets with a bad CRC |

### 4.12 Measurement queue (since 2.0)

The queue window (0xA081_0000) holds a ring of `QUEUE_ENTRIES` (1024) entries of 16 words (64 bytes); entry i starts at byte 64 i. The PL writes an entry at `QUEUE_HEAD` and then advances `QUEUE_HEAD` modulo `QUEUE_ENTRIES`; the ARM reads the entries from `QUEUE_TAIL` up to `QUEUE_HEAD` and then writes `QUEUE_TAIL`. When the ring is full (the next head would equal the tail) a new entry is dropped and counted in `QUEUE_DROPPED_CNT`. The window is read-only for the ARM; writes are ignored. At 250 measurements and 250 packets per second the ring holds about 2 s.

| Word | `QE_TYPE_OWN`: a measurement of the peer's burst | `QE_TYPE_PEER`: a decoded peer packet |
| ---- | ------------------------------------------------ | ------------------------------------- |
| 0 | [7:0] type 1, [15:8] flags (`QEF_LOCKED`, `QEF_WINDOW_BAD`, `QEF_SEARCHED`), [31:16] entry sequence (mod 2^16; dropped entries take a number too, so a gap shows a drop) | [7:0] type 2, [31:16] entry sequence |
| 1 | this node's `PPS_COUNT` of the window | the same |
| 2 | the window's slot | the same |
| 3, 4 | the window's start tick, LO, HI | the same |
| 5 | lag | [31:24] version, [23:16] TIME_EPOCH, [15:8] quality, [7:0] status |
| 6 | fraction, i32, 2^-32 sample | config_id |
| 7 | phase, i32, 2^-32 turn | the peer's `PPS_COUNT` |
| 8 | magnitude LO | the peer's slot |
| 9 | magnitude HI | T_tx LO |
| 10 | T_rx LO: i64 fs after this node's PPS | T_tx HI |
| 11 | T_rx HI | T_rx LO |
| 12 | quality, [7:0], 0.25 dB | T_rx HI |
| 13 | the window's Σ(I² + Q²), LO | phase, i32 |
| 14 | the window's Σ(I² + Q²), HI | 0 |
| 15 | 0 | 0 |

Since 2.5, while P13 steers through FC with `STEER_CONTROL.LOG`, a third type, `QE_TYPE_STEER`, logs each of its steps (§4.16). The word indices are `QE_*` in `p_defs.vh`. An entry of any type is written only after all its words. Python: `pl.queue.read()` (§11.14).

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x380 | `QUEUE_HEAD` | RO | 0 | the entry the PL writes next |
| 0x384 | `QUEUE_TAIL` | RW | 0 | the entry the ARM reads next; 0 … `QUEUE_ENTRIES` − 1 (other values: SLVERR) |
| 0x388 | `QUEUE_ENTRIES` | RO | 1024 | |
| 0x38C | `QUEUE_DROPPED_CNT` | RO | 0 | entries lost to a full ring |

### 4.13 Output PPS, P10 (since 2.1)

P10 sends one pulse a second on the stream of DAC tile 0, whose output is DAC_B (J20); the design is `plan/pps_output.md`. It counts the ticks of each second from the `PPS_TICK` of the locked PPS edge that started the second, and between edges it counts on by itself, in seconds of 100 000 000 ticks, so that it carries on from the last locked edge when an edge is missing or the PPS is not locked (the flywheel).

```text
pulse of second k: the beat on DAC tile 0's stream when raw_tick = PPS_TICK(k) + PPS_OUT_OFFSET:
                   I samples PPS_OUT_SAMPLE … 7 = PPS_OUT_AMPLITUDE, the earlier ones PPS_OUT_IDLE, Q = 0;
                   with FRAC (2.3) the first of them = AMPLITUDE + (IDLE − AMPLITUDE) × PPS_OUT_FRAC / 2^16;
                   then PPS_OUT_WIDTH − 1 beats of PPS_OUT_AMPLITUDE in all eight I samples; then PPS_OUT_IDLE
edge on J20 = start of second k + PPS_OUT_OFFSET × 10 ns + PPS_OUT_SAMPLE × 1.25 ns + D_board
              (with FRAC: later by 0 … 1.25 ns as PPS_OUT_FRAC goes 0 … 65535, an S-shaped curve; below)
```

D_board is the delay from the stream to J20: the DAC's latency, which MTS fixes at every power-up, and the analog path. It is a board constant, still to be measured.

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x140 | `PPS_OUT_CONTROL` | RW | 0 | bit 0 `ENABLE` (taken at the next second), bit 1 `SYNCED` (the ARM vouches that the PL's 100 MHz clock is synchronized; acts at once), bit 2 `FRAC` (delay the edge by `PPS_OUT_FRAC`, 2.3; taken at the next second) |
| 0x144 | `PPS_OUT_OFFSET` | RW | 8 | ticks from the second's start to the pulse, `PPSOUT_MIN_OFFSET` (8) … 99 999 999 (other values: SLVERR). Taken at the next second |
| 0x148 | `PPS_OUT_SAMPLE` | RW | 0 | the step's first I sample in its beat, 0 … 7, 1.25 ns each (other values: SLVERR). Taken at the next second |
| 0x14C | `PPS_OUT_WIDTH` | RW | 4 | beats of the pulse, 1 … 65535 (other values: SLVERR). Taken at the next second |
| 0x150 | `PPS_OUT_AMPLITUDE` | RW | 0 | [15:0] signed: the pulse's DAC code; [31:16] ignored, read 0. Taken at the next second |
| 0x154 | `PPS_OUT_STATUS` | RO | 0x8 | bit 0 `HAVE_REF` (a locked edge has set the count), bit 1 `RUNNING` (enabled for the current second), bit 2 `FLYWHEEL` (the last pulse came from the flywheel), bit 3 `INVALID` (the validity line, below) |
| 0x158 | `PPS_OUT_CNT` | RO | 0 | pulses sent |
| 0x15C | `PPS_OUT_FLYWHEEL_CNT` | RO | 0 | of them, pulses in seconds that no locked edge started |
| 0x160 | `PPS_OUT_TICK_LO` | RO | 0 | the tick of the last pulse's first beat; reading it latches HI |
| 0x164 | `PPS_OUT_TICK_HI` | RO | 0 | latched high 32 bits |
| 0x168 | `PPS_OUT_IDLE` | RW | 0 | [15:0] signed: the I samples between pulses (2.2); [31:16] ignored, read 0. Taken at the next second |
| 0x16C | `PPS_OUT_FRAC` | RW | 0 | [15:0] unsigned: with `FRAC`, the edge's delay in 2^-16 of a sample (2.3); [31:16] ignored, read 0. Taken at the next second |

- The stream's TVALID is high from reset on, so the DAC reads `PPS_OUT_IDLE` between pulses. The path to J20 is AC-coupled, so `PPS_OUT_IDLE` sets no level at J20; `PPS_OUT_IDLE` = −`PPS_OUT_AMPLITUDE` doubles the step (2.2). A new `PPS_OUT_IDLE` steps the output once, at the start of the second it is taken.
- The fractional delay (2.3): with `FRAC`, the step's first sample lies `PPS_OUT_FRAC` / 2^16 of the way from `PPS_OUT_AMPLITUDE` back to `PPS_OUT_IDLE`, so the step reaches its full height that much of a sample later; after the DAC's interpolation filter its 50 % crossing moves by up to one 1.25 ns sample, along an S-shaped curve, steepest in the middle. Measured on board 8873292600AB (`experiments/m5_pps_frac.py`, `results/m5-pps-frac-20261105_214333`): 0 ps at 0, 102 ps at 8192, 636 ps at 32768, 1158 ps at 57344, 1254 ps at 65535; 0.0275 ps per unit within 4096 of 32768, where a straight line is good to 2 ps, against 0.0191 on average. Node B uses it for the theta-compensated output PPS (`m4c_link --pps-out-comp`, §13; `plan/pps_output.md` section 7): it moves its edge by θ − θ₀ before each second, so that its output PPS follows the 100 MHz clock on A's PL to the measurement. Without `FRAC` the first sample is `PPS_OUT_AMPLITUDE`, as before 2.3. In the compensated mode B's output edge no longer keeps a fixed place against B's 10 MHz: it moves against it by θ − θ₀ (17-19 ps rms, within ±53 ps after the lock on the bench, `results/precision-20261105/REPORT.md` section 7) and sits the base's delay (636 ps at 32768) after the plain edge. Equipment that takes its fine phase from the 10 MHz and uses the PPS only to pick the cycle is unaffected but gets the 10 MHz's accuracy, not the PPS's; equipment that times the PPS against the 10 MHz sees θ − θ₀. The plain mode stays the default for that reason (`plan/pps_output.md` section 7).
- `PL.open()` puts DAC tile 0 block 0 at baseband with the mixer bypassed (§7.1 step 7), so the DAC outputs the stream's I samples. The path to J20 is AC-coupled (100 nF, balun, 100 nF; RFSoC 4x2 schematic, sheet 28 "RF DAC"): the start of the pulse gives one edge and its end an edge of the other sign. DAC_A's output is inverted against I (§13); a negative `PPS_OUT_AMPLITUDE` inverts the pulse.
- A pulse is never sent within half a second of the previous one. When a locked edge sets the count later than the flywheel had it (the PPS moved later, as after a PP), the pulse already sent in that second is not repeated; when the PPS moved earlier, that second's pulse follows the new edge.
- `PPSOUT_MIN_OFFSET`: the edge that starts a second reaches P10 three ticks after the tick that latched it (P1's two synchronizer stages and its register), so every pulse is scheduled from its own second's edge.
- The validity line, Pmod J1 pin 1 (PMOD0_0, FPGA pin AF16; it passes the NVT2008BQ level translator, which has 1.15 kΩ pull-ups to 3.3 V on the header side), is high while the output PPS is not valid, that is unless all of these hold: `ENABLE` in force for the current second, a locked edge started the current second (counted from `PPSOUT_MIN_OFFSET` ticks after the second's start, when that edge is in), `pps_locked`, `clocks_locked`, DAC tile 0's TREADY, and `SYNCED`. High means not valid because the pull-up also reads high without a bitstream or with the board off. The LED W_LED_0 (FPGA pin AR11) is lit while the line is low.
- Nothing in the PL sets or clears `SYNCED`: the ARM sets it once the link is locked and the steering has settled.

Python: `pl.pps_out` (§11.16); board check `rf_clock.checks.pps_out` (§13).

### 4.14 Phase of the 10 MHz reference, P11 (since 2.4)

The node's tick and its converters are clocked from its FS725's 10 MHz through the LMK04828, the two LMX2594 and each RFDC tile's PLL (§7.1). They lock again at every `PL.open()` and do not come back to the same phase, so the 10 MHz the FS725 sends out moves against the PL's 100 MHz clock by a few ps at each start and drifts by a few ps an hour, while the output PPS, made from the tick (§4.13), does not move with it (`experiments/m5_10mhz_restarts.py`). P11 measures that phase, τ_ref: a copy of the 10 MHz goes into ADC_D, and P11 correlates the samples with a 10 MHz cosine and sine laid on the tick grid.

```text
FS725 10 MHz output ──► (splitter) ──────────────────────────────────────► White Rabbit, scope
(not the one into the LMK04828)  └──────────────► ADC_D ─► balun ─► ADC tile 0 block 0:
                                                                     4.8 GS/s, NCO 0 Hz, ÷6
                                                                          │ I stream, 800 MS/s,
                                                                          ▼ 8 samples a beat
raw_tick mod 10 ─► k = 8 × (raw_tick mod 10) + lane ─► cos, sin (80-entry table) ─► x·cos, x·sin
                                                                          │ summed until the next PPS event
PPS event (P1) ───────────────────────────────────────────────────────────► the second kept: REF_PHASE_*
```

```text
k         = 8 × (raw_tick mod 10) + n         lane n of the beat at raw_tick; one period = 80 samples = 10 ticks
I         = Σ x × round(32767 cos(2π k / 80)),   Q = Σ x × round(32767 sin(2π k / 80))
            over the beats from one PPS event to the next
τ_ref     = atan2(Q, I) / (2π × 10 MHz) − 10 ns × (PPS_TICK mod 10)   mod 100 ns,
            PPS_TICK of the edge that started the second
amplitude = 2 √(I² + Q²) / (32767 × 8 × REF_PHASE_BEATS)   in ADC codes
```

For a reference x = A cos(2π × 10 MHz × (t − τ)), with t the sample's place on the tick grid (raw_tick × 10 ns + n × 1.25 ns), atan2(Q, I) / (2π × 10 MHz) is τ: the delay of the reference's crest after a tick that is a multiple of 10, as the stream sees it. raw_tick starts from 0 at reset, at no fixed place against the 10 MHz, so that grid moves by whole ticks at every start (by 10-50 ns between `PL.open()`s on 887329260072A, 2026-11-09); the second's PPS_TICK fixes it, so τ_ref is the crest's delay after the tick that starts the second. It is the FS725's 10 MHz against the PL's 100 MHz clock, plus the delay from the splitter to the stream (cable, the ADC's latency, which MTS fixes at every start, and the decimation filter). Between two linked nodes τ_ref of B less τ_ref of A is then, while the link holds the 100 MHz clock on B's PL on A's, B's 10 MHz against A's, plus a constant of the two boards and their wiring, still to be calibrated. The sums cancel the input's DC and harmonics only over whole periods: a second of 100 000 000 beats holds 10^7 of them.

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x1C0 | `REF_PHASE_SEQ` | RO | 0 | seconds kept; read it before and after the others |
| 0x1C4 | `REF_PHASE_COUNT` | RO | 0 | the `PPS_COUNT` of the second summed: P1 raises `PPS_COUNT` with the edge that starts a second, so the second from the edge with count k to the next has k |
| 0x1C8 | `REF_PHASE_BEATS` | RO | 0 | beats summed (beats without TVALID are left out) |
| 0x1CC | `REF_PHASE_PEAK` | RO | 0 | [15:0] unsigned: the second's largest \|x\| |
| 0x1D0 | `REF_PHASE_I_LO` | RO | 0 | I, signed 64 bits |
| 0x1D4 | `REF_PHASE_I_HI` | RO | 0 | |
| 0x1D8 | `REF_PHASE_Q_LO` | RO | 0 | Q, signed 64 bits |
| 0x1DC | `REF_PHASE_Q_HI` | RO | 0 | |

- At each PPS event P11 keeps the sums of the second just ended, which stay until the next event; the beat that comes with the event starts the next second. Read `REF_PHASE_SEQ`, the others, the latest edge (`PPS_TICK`, `PPS_COUNT`, `PPS_LAST_PERIOD`), then `REF_PHASE_SEQ` again: another value, or a `PPS_COUNT` other than `REF_PHASE_COUNT` + 1, means a new second or edge came in between; read again. The second's own PPS_TICK is the latest edge's less `PPS_LAST_PERIOD`. The 64-bit sums need no latch: they hold for a second.
- Use a second only when `REF_PHASE_BEATS` = 100 000 000. The first second after reset, a second around a missing PPS edge and the second in which the PPS was moved (§11.15) are shorter or longer, and a part period leaks into the sums. Without a PPS edge for more than about 10 s the sums can wrap (64 bits hold 10 s of full-scale samples).
- `REF_PHASE_PEAK` near 32767 means the ADC clips, which bends τ_ref. The FS725's output straight into ADC_D gives about 7000 codes (−13 dBFS) on both boards (887329260072A 6955, 8873292600AB 7227; 2026-11-09), so no attenuator is needed; behind a splitter's 3.3 dB about 4800 (inferred). Without an input, τ_ref is noise.
- Measured on both boards (2026-11-09, `rf_clock.checks.ref_phase`, `experiments/m5_ref_phase_restarts.py`, `results/m5-ref-phase-restarts-20261109_114652`): τ_ref scatters by 0.2-0.6 ps from second to second, the FS725s free. Across `PL.open()`s it kept its whole ticks every time (37 opens) and moved by 0.64 and 0.42 ps rms (A, B; 10 restarts) when the clocks were not written again, by 3.4 and 8.2 ps rms (9 restarts) when they were; ADC tile 0's MTS offset (6 or 14 on B at the same latency) did not move it.
- Against the scope (2026-11-09, `experiments/m5_10mhz_restarts.py`, `results/m5-10mhz-restarts-20261109_134943`): the FS725 outputs into ADC_D go on through tees into the scope's C1/C2 at 1 MΩ; six steering runs, each with a new `PL.open()` that writes the clocks again. At the five restarts the scope's B 10 MHz less A's, less (θ − θ₀), jumped by 9.1 ps rms; τ_ref of B less τ_ref of A jumped with it, slope −1.04 (the two are counted with opposite signs), and 2.5 ps rms was left. The scope alone repeats to 0.9 ps over restarts (`results/m5-10mhz-restarts-20261108_140003`), so about 2 ps of a restart's jump lies where P11 does not see it, presumably in the signal tiles' PLLs or in the link (inferred, five restarts; restarts without new clock writes left 2.2 ps on the scope, `results/m5-10mhz-restarts-20261108_163524`, and 0.7 ps in τ_ref).
- Over hours (`results/m5-10mhz-restarts-20261109_145922`, three steering runs of 88 minutes with two restarts, plot `p11_vs_scope.png`) the scope's quantity drifted by 8 to 10 ps within each run, and τ_ref of B less τ_ref of A followed it with slopes of −1.09 to −1.32 in 60 s means. What was left had a standard deviation of 0.7 to 1.1 ps within a run. Once, about two hours in and without a restart, the scope moved about 3 ps more than τ_ref within a few minutes, and no event in the logs explains it. Over all seven restarts with new clock writes the slope was −0.97 and 2.4 ps rms was left.
- `PL.open()` sets ADC tile 0 block 0's NCO to 0 Hz with phase 0 (§7.1 step 7), so its I stream is the samples. The tile runs for MTS with the signal tile's datapath, whose NCO at −1.0 GHz would move the 10 MHz out of the band. Its Q stream is not used.
- τ_ref is measured on ADC tile 0's sample clock, while the link measures on tile 2's. MTS aligns the two tiles to SYSREF in whole samples; a tile PLL that locked a few ps differently at each start would enter τ_ref and not θ, or the other way round. Tile 0's comes back to within 0.4-0.6 ps without new clock writes (above); tile 2's is not measured alone. Comparing τ_ref with the scope's 10 MHz across restarts tests it.
- Nothing in the PL or the link uses τ_ref yet; `m4c_link` logs each second it keeps (`ref_phase` lines, §13).

Python: `pl.ref_phase` (§11.17).

### 4.15 The FS725's serial line, P12 (since 2.5)

Up to 2.4 the ARM sent every FC command to node B's FS725 through a USB serial adapter. The command left 22-151 ms after the end of its step with 2.4.0.0 and 22-58 ms from 2.4.0.1 on, at a time that varied from step to step, and the steering's model of the FS725 had to allow for it ([FC response report](../results/fc-response-20261112/REPORT.md) section 1). Since 2.5 the PL can drive the FS725's RS-232 line itself, so that P13 can start each command at a tick it chooses. A TTL-to-RS-232 module (MAX3232, DB-9 male, the same pin-out as a PC's port) plugs into J1, and its DB-9 plugs straight into the FS725's port in place of the USB serial adapter. The USB serial adapter and `--steer-actuator fc` keep working as before, so the module is needed only for the steering in the PL. `PL/sim/tb_p12_serial.v` checks P12 in simulation. On the bench (8873292600AB, 2026-11-16), in the four runs with P13's commands ([results/pl-steering-20261116](../results/pl-steering-20261116/REPORT.md)), P12 sent 38 542 FC commands and 38 614 messages of the ARM and received 270 114 bytes of replies, with no framing error and no byte lost. In this section the serial line is the wire pair to the FS725, and an ARM message is the up to 32 bytes the ARM hands P12 in one go.

J1 is the board's 2 × 15 right-angle header. It holds two Pmod connectors side by side, PMOD0 and PMOD1, and the silkscreen marks the PMOD1 end "PMODB". Every Pmod line passes the board's NVT2008 level translators, between the FPGA's 1.8 V bank and the 3.3 V header, with 1.15 kΩ pull-ups to 3.3 V on the header side, so an empty hole reads as a high level.

```text
P13's FC command (start tick) ─┐
                               ├─► P12 transmitter ─► PMOD1_6 and PMOD1_2 ─► NVT2008 ─► module RXD ─► MAX3232 ─► DB-9 pin 3 ─► FS725
the ARM's message (32 B) ───────┘
the ARM ◄─ 256-byte FIFO ◄─ P12 receiver ◄─ AND of PMOD1_7 and PMOD1_3 ◄─ NVT2008 ◄─ module TXD ◄─ MAX3232 ◄─ DB-9 pin 2 ◄─ FS725
```

**Which pins.** On 8873292600AB the module sits on the lower row of J1, the one nearer the board, at the PMODB end. Its VCC is in the hole at the very end, its GND next to it, and a meter read 3.3 V between those two holes (2026-11-16). The schematic (page 5, "PMOD HEADERS") puts PMOD1 on J1 pins 10-15 and 16-21, with 3.3 V on pin 16 and GND on pin 17. That the lower row is pins 16-21 was inferred from the order of the symbol's pins and then confirmed on the bench, where the FS725's replies came in on PMOD1_7 and none on PMOD1_3 (2026-11-16). The module's pins land as the table shows.

| Module pin | J1 pin | Line | FPGA pin | In P12 |
| ---------- | ------ | ---- | -------- | ------ |
| VCC | 16 | 3.3 V | | |
| GND | 17 | GND | | |
| TXD, the FS725's replies | 18 | PMOD1_7 | AU14 | receive pin A |
| RXD, to the FS725 | 19 | PMOD1_6 | AT15 | transmit |
| CTS | 20 | PMOD1_5 | AP14 | not used |
| RTS | 21 | PMOD1_4 | AU15 | not used |

If the lower row is pins 10-15 instead, TXD and RXD are on PMOD1_3 (AV13, receive pin B) and PMOD1_2 (AU13). So that either works, P12 sends on both PMOD1_6 and PMOD1_2, receives the AND of PMOD1_7 and PMOD1_3, and counts the falling edges on each (`SERIAL_RX_EDGES`). The empty pin's count stays 0, because its pull-up gives no edges, and the pin whose count is not 0 is the one the module is on. The counts wrap at 256, so their values say nothing more than that. If both stay 0 while the FS725 should answer, nothing came from it. `m4c_link --fs725 pl` logs both counts at its start and end, and also when the FS725 does not answer at the start (`serial` log lines, §13). Driving the other row does no harm, since its holes are empty. RTS and CTS stay unconnected in the PL, because the FS725 uses no handshake lines (PRS10 manual p. 20).

**The frame.** Each byte goes out as a start bit, 8 data bits from the least significant, no parity and one stop bit, each bit `SERIAL_CONFIG.BIT_TICKS` ticks long. The reset value 10417 gives $100\ \mathrm{MHz} / 10417 = 9599.7$ baud, 0.003 % slower than the FS725's fixed 9600 baud, far inside what a UART tolerates. A 12-byte command such as `FC1675,2048\r` takes 12.5 ms on the line. The FS725 sends XON/XOFF characters for flow control, and P12 does not act on them. One short command a step and the ARM's queries between them keep the FS725's input buffer far from full (inferred).

**P13's commands.** P13 hands over each command with its bytes and its start tick T. If the transmitter is idle at T, the start bit begins in the tick after T, and `STEER_CMD_TICK` (§4.16) reads T. If a frame is still on the line at T, the command starts as soon as the line is free, and it is counted as late (`STEER_LATE_CNT` and `STEER_STATUS.LATE` in §4.16).

**The ARM's messages.** The ARM writes up to 32 bytes into `SERIAL_LINE` and then `SERIAL_CONTROL.SEND`. P12 starts the message only while no P13 command waits and, while P13 runs, only if the message ends at least one bit time before P13's reservation, the tick of its next command, which is all P12 knows of that command until P13 hands it over. Since 2.5.1.0 the reservation stays on a step's command from the step's end until P13 has computed it, handed it over and logged the step, and only then moves on to the next step's, so while P13 runs a message never delays a command, apart from one already on the line when P13 starts. With the 2.5.0 bitstream (rf-clock 2.5.0.0 and 2.5.0.1) P13 moved the reservation on at the step's end, while it still spent 10-40 µs computing the step's command, and a message that P12 had held back for that command could start in those µs and delay it by up to the message's length. On steps of 0.1 s that never happened on the bench, since the ARM's query fits before the next command as soon as it comes. On steps of 0.02 s it delayed 3.4 % of the commands, about 800 in each run of 8 minutes on 2026-11-17, each one after a step whose query came in about the last 4 ms before the next step's end, too late to fit ([results/pl-best-20261117](../results/pl-best-20261117/REPORT.md) section 4.9). `PL/sim/tb_p13_steer.v` checks the reservation, and on the bench with 2.5.1.0 (2026-11-17 16:54 EST, 0.02 s steps, 480 s) none of 23 064 commands started late. A message of up to 32 bytes takes at most 33 ms, so with steps of 0.1 s it waits at most one step. With steps of 0.02 s the gap after each command of 12.5 ms is about 7.5 ms, so a message of more than about 7 bytes never fits while P13 runs, which holds for the ARM's 4-byte queries but not for longer messages. While a message waits or is being sent, a write to `SERIAL_LINE` is refused (SLVERR), and so is a 33rd byte.

**Receiving.** Each byte with a good stop bit goes into a FIFO of 256 bytes. A read of `SERIAL_RX` takes the oldest byte out in the cycle after the read. A byte with a bad stop bit, or one that finds the FIFO full, is counted and dropped.

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x400 | `SERIAL_CONTROL` | WO | — | write-1 pulses, bit 0 `SEND` sends the ARM's message, bit 1 `CLEAR` drops the bytes written for a message not yet sent, bit 2 `RX_FLUSH` drops the received bytes |
| 0x404 | `SERIAL_CONFIG` | RW | 10417 | [15:0] `BIT_TICKS`, at least 16, [16] `ENABLE`. Without it the serial line idles high, a pending command is dropped and nothing is received |
| 0x408 | `SERIAL_STATUS` | RO | — | bit 0 `LINE_QUEUED` (an ARM message waits or is being sent), bit 1 `TX_ACTIVE`, bit 2 `CMD_PENDING`, [13:8] bytes written for the ARM's message, bit 16 level on PMOD1_7, bit 17 on PMOD1_3, [28:20] bytes in the FIFO |
| 0x40C | `SERIAL_LINE` | WO | — | [7:0] a byte appended to the ARM's message |
| 0x410 | `SERIAL_RX` | RO | — | [7:0] the oldest byte, bit 8 `VALID`, [24:16] bytes after it. A read takes it out |
| 0x414 | `SERIAL_LINES_CNT` | RO | 0 | the ARM's messages sent |
| 0x418 | `SERIAL_CMDS_CNT` | RO | 0 | P13's commands sent |
| 0x41C | `SERIAL_RX_CNT` | RO | 0 | bytes received |
| 0x420 | `SERIAL_ERR_CNT` | RO | 0 | [15:0] bytes with a bad stop bit, [31:16] bytes lost to a full FIFO |
| 0x424 | `SERIAL_RX_EDGES` | RO | 0 | [7:0] falling edges on PMOD1_7 (receive pin A), [15:8] on PMOD1_3 (receive pin B), both wrapping at 256 |
| 0x428 / 0x42C | `SERIAL_LINE_TICK_LO` / `_HI` | RO | 0 | the tick the last ARM message started. Reading LO latches HI |

In Python, `pl.serial` and `PLPort` (§11.18) drive P12, and every `FS725` method works on the PL's line through `FS725.open("pl")` (§11.13).

### 4.16 Steering through FC in the PL, P13 (since 2.5)

P13 can run the predictive steering algorithm through FC (`PredictiveSteering` built by `predictive_fc`, §11.12) in the PL, one step at a time, and P12 then sends each step's FC to the FS725 at a fixed tick after the step's end. The ARM computes the algorithm's coefficients and loads them once, starts and stops P13, reads one queue entry for each step and checks the FS725 between the PL's commands. `rf_clock.plmodel.steer` defines every step to the bit, and `PL/sim/tb_p13_steer.v` compares the RTL with it step by step. Steering through FC on the ARM (`--steer-actuator fc`) stays as it was. On the bench (2026-11-16, [results/pl-steering-20261116](../results/pl-steering-20261116/REPORT.md)) its θ and number of pairs equalled what the ARM computed from the same queue entries in all 2923 steps checked, and its FC equalled that of `rf_clock.plmodel.steer` fed with P13's θ in all 26 689 steps of the three steering runs in the PL. No command of P13 was late.

```text
P8 OWN entry (T_rx) ─┐  a pair's 2θ, fs
P9 PEER entry ───────┴─► summed over the step's last STEER_STEP.LATE slots ─► at the step's end tick, θ_k (2^-16 fs)
                                                                                │
coefficient tables (the ARM loads them) ─► one 48 × 48 multiplier, 104-bit sum ─► S_k ─► round, clip ─► FC_k
                                                                                                          │
           ┌──────────────────────────────────────────────────────────────────────────────────────────────┤
           ▼                                                                                              ▼
"FC<high>,<low>\r" at the step's end + STEER_WRITE_DELAY ─► P12 ─► FS725              QE_TYPE_STEER entry ─► queue ─► ARM
```

**Pairs and steps.** A locked OWN entry and the PEER entry of the same window (its `PPS_COUNT` and slot) whose packet carries a measurement make a pair, as `rf_clock.twtt.pairs.pair` forms it, with $2\theta = (T_\mathrm{rx} - T_\mathrm{tx}^\mathrm{peer}) - (T_\mathrm{rx}^\mathrm{peer} - T_\mathrm{tx}^\mathrm{own})$ on node B (`STEER_CONTROL.ROLE_B`) and the opposite sign on A, in fs. $T_\mathrm{tx}^\mathrm{own}$ is this node's burst in the slot before, from `SLOT_TX_OFFSET`. The second is cut into steps of `STEER_STEP.SLOTS` slots from this node's PPS edge (50 slots for 0.1 s), and a step's θ is the mean of the pairs whose slot lies in its last `STEER_STEP.LATE` slots, rounded to $2^{-16}$ fs. A step without such a pair has no θ. Node B has a window only in A's slots, the even ones (§4.8), and a step of 50 slots starts on an even slot, so with the reset values the last 25 slots of a step hold at most 12 pairs. P13 takes each step at its end tick. A window ends `SLOT_RX_OFFSET` + `RX_SPAN_BEATS` ticks after its slot starts (one beat a tick), about 0.25 ms plus the one-way delay, so even a pair from the step's last slot has reached the queue well before the step ends. A pair whose entries came after its step's end tick is dropped, since that step's θ has been taken and the next step's sum starts from zero.

**The algorithm.** With its settings fixed, the predictive steering algorithm is one linear filter followed by a rounding and two clips. Let $U_k$ be the change of FC decided in step $k$, in fine steps (one unit of FC's fine DAC), $g(d)$ the algorithm's model of θ's response to one change $d$ steps later, $h(d) = g(d) - g(d-1)$, and $\Delta F_k$ the change from step $k-1$ to step $k$ of θ less B's own corrections. Then

$$\Delta F_k = \theta_k - \theta_{k-1} - \sum_{d \ge 1} h(d)\, U_{k-d}$$

$$S_k = R_0 (\theta_k - \theta_0) + \sum_{j=0}^{p-1} \alpha_j \Delta F_{k-j} + \frac{\gamma}{n_e} W_k + \sum_{d \ge 1} K(d)\, U_{k-d}$$

where $p$ is the AR model's order (`STEER_P`), $W_k$ is the sum of the last $n_e = \min(n, k)$ values of $\Delta F$, and $n$ (`STEER_N`, 600 steps or 60 s at 0.1 s steps) is the number of steps over which the algorithm takes the mean that its AR model predicts deviations from. The new FC is $\mathrm{FC}_k = \mathrm{FC}_{k-1} + U_k$, where $U_k$ is $-S_k$ plus the remainder carried from the step before, rounded half up, held to `STEER_MAX_STEP` and to `STEER_FC_LIMITS`. $R_0$, $\alpha_j$, $\gamma$ and $K(d)$ follow from the first row of the algorithm's least-squares solution, its AR model and $g$ (`rf_clock.plmodel.steer.tables`). Apart from $R_0 (\theta_k - \theta_0)$, which the loop holds near zero, only differences of θ and of θ less B's own corrections enter, so no quantity grows with the clocks' drift. Beyond the tables ($d >$ `STEER_L`) $h$ and $K$ are constant, so older changes enter through their sum alone. A step without θ changes nothing and takes the algorithm's own prediction one step ahead,

$$\Delta F_k = \sum_{i=0}^{p-1} \mathrm{ar}_i\, \Delta F_{k-1-i} + \frac{1 - \sum_i \mathrm{ar}_i}{n_e} W_{k-1}$$

with $\mathrm{ar}_i$ the AR coefficients (tables `AR` and `MEAN2`). The algorithm waits `STEER_SETTLE` steps before its first change, and stops when the wanted FC has lain outside the limits for `STEER_MAX_SATURATED` steps in a row (`STOPPED`, no more commands). This computes what `PredictiveSteering` computes, in fixed point. In closed-loop simulations on synthetic clocks (four runs of 6000 steps), $S_k$ agreed with the float algorithm's within $2.4 \times 10^{-6}$ fine steps, the rounded changes were the same except, rarely, by one fine step where $S_k$ lay that close to a rounding boundary, and the rms of θ − θ₀ agreed within 0.1 %.

**The command.** From the first step with θ on, every step sends `STEER_PREFIX` (the ARM writes `FC<high>,`), FC's fine DAC value in four digits and a carriage return, starting `STEER_WRITE_DELAY` ticks after the step's end. It does so also in steps that change nothing, while the algorithm settles and in steps without θ. At GA 1 the FS725's own frequency lock loop still integrates and slowly pulls FC away from the value written, and the next write puts it back (§11.12). The ARM builds the algorithm's model with each command leaving `STEER_WRITE_DELAY` after its step's end. The 12.5 ms the command takes on the line and the FS725's own 11 ms need no allowance of their own, because the dead time measured from the command's start (23.5 ms) already holds both. That dead time depends on `STEER_WRITE_DELAY` in steps, though. In FC steps with the write delay set from 0.2 to 80 ms (2026-11-17), θ began to respond at the first point of a cycle of about 14.5 ms that lay at least 4 ms after the command's last stop bit, so the dead time from the command's start came out between 14.5 and 31 ms, 27.5 ms at the default 0.2 ms ([results/pl-best-20261117](../results/pl-best-20261117/REPORT.md) section 4.5). A step takes about $2L + 2p + 150$ clock cycles of 10 ns to compute, about 10 µs with the tables for 0.1 s steps (inferred from the RTL). A `STEER_WRITE_DELAY` shorter than that makes the command late, and the default 20000 ticks (0.2 ms) leaves ample room.

**FC steps.** With `STEER_CONTROL.STEPS` P13 runs FC steps (`rf_clock.twtt.steer.FCSteps`, §13 under `m4c_link`) instead of the algorithm, through the same path, to measure the FS725's response with each command on its tick. They too start at the first step with θ, after which θ is ignored. After `STEER_SETTLE` steps, FC goes up by `STEER_STEPS.SIZE` fine steps, back, down by as much and back, holding each level for `STEER_STEPS.HOLD` steps of `STEER_STEP.SLOTS` slots, and the cycle repeats until P13 is stopped. `STEER_STEPS`, the FC steps, and `STEER_STEP`, the length of a step, differ by one letter.

**Starting and stopping.** Writing `ENABLE` from 0 to 1 starts afresh, with FC from `STEER_FC_START` and from the next step with θ, which becomes step 0. Writing 0 stops at once. Load the tables and settings while `ENABLE` is 0. P13 runs on by itself when the program that started it ends without stopping it. While the link runs it keeps steering on its own pairs, and without θ it keeps writing the same FC. Besides its own stop (`STOPPED`), only `ENABLE` 0 or a new `PL.open()`, which loads the bitstream again, stops it. `m4c_link` writes `ENABLE` 0 at its end, also after an error, Ctrl-C or SIGTERM, but not when it is killed outright (§13).

| Address | Name | Access | Reset | Meaning |
| ------- | ---- | ------ | ----- | ------- |
| 0x440 | `STEER_CONTROL` | RW | 0xA | bit 0 `ENABLE`, bit 1 `ROLE_B`, bit 2 `STEPS`, bit 3 `LOG` (a queue entry each step) |
| 0x444 | `STEER_STATUS` | RO | 0 | bit 0 `ENABLED` (`ENABLE` read back), bit 1 `STARTED` (a step with θ came since `ENABLE`), bit 2 `STOPPED`, bit 3 `SETTLING`, bit 4 `LATE` (the last command started after its tick, not to be confused with `STEER_STEP.LATE`), bit 5 `BUSY` (a step is being computed) |
| 0x448 | `STEER_STEP` | RW | 0x190032 | [8:0] `SLOTS`, slots a step, dividing `SLOTS_PER_SECOND`, [24:16] `LATE`, the last slots of a step that θ comes from. The reset value is 50 and 25, for steps of 0.1 s |
| 0x44C | `STEER_WRITE_DELAY` | RW | 20000 | ticks from a step's end to its command, 0.2 ms by default |
| 0x450 … 0x458 | `STEER_L`, `STEER_N`, `STEER_P` | RW | 400, 600, 40 | L, n and p, the change tables' length, the mean's window and the AR order, at most `STEER_LMAX` − 2, `STEER_NMAX` − 2, `STEER_PMAX` |
| 0x45C | `STEER_SETTLE` | RW | 300 | steps before the first change |
| 0x460 | `STEER_MAX_STEP` | RW | 1000 | fine steps a change at most |
| 0x464 | `STEER_FC_LIMITS` | RW | 0x0C000400 | [11:0] lowest, [27:16] highest fine DAC value. The reset value is 1024 and 3072 |
| 0x468 | `STEER_FC_START` | RW | 2048 | [11:0] the fine DAC value at the start |
| 0x46C | `STEER_MAX_SATURATED` | RW | 1200 | steps in a row outside the limits before P13 stops |
| 0x470 | `STEER_STEPS` | RW | 0x10000 | FC steps, [15:0] `SIZE` in fine steps, [31:16] `HOLD`, steps a level |
| 0x474 / 0x478 / 0x47C | `STEER_PREFIX_LO`, `_HI`, `_LEN` | RW | 0 | the command's first bytes, byte 0 in [7:0] of LO, and how many (0-8) |
| 0x480 | `STEER_COEF_ADDR` | RW | 0 | [15:13] the table, [12:0] the entry index of the next write |
| 0x484 | `STEER_COEF_HI` | RW | 0 | [15:0] bits 47:32 of the entries written next. It holds until written again |
| 0x488 | `STEER_COEF_LO` | WO | — | bits 31:0. A write stores `STEER_COEF_HI` and these bits as the entry and moves to the next index |
| 0x490 | `STEER_INDEX` | RO | 0 | steps since the first with θ |
| 0x494 | `STEER_FC` | RO | 0 | [11:0] FC's fine DAC value, [31:16] the last change, i16 |
| 0x498 / 0x49C | `STEER_THETA_LO` / `_HI` | RO | 0 | the last step's θ in $2^{-16}$ fs (its prediction without pairs). Reading LO latches HI |
| 0x4A0 | `STEER_MISSING_CNT` | RO | 0 | steps without θ |
| 0x4A4 | `STEER_LATE_CNT` | RO | 0 | commands that started after their tick |
| 0x4A8 / 0x4AC | `STEER_CMD_TICK_LO` / `_HI` | RO | 0 | the tick the last command was to start, T of §4.15. Reading LO latches HI |
| 0x4B0 | `STEER_PAIRS_CNT` | RO | 0 | pairs formed |
| 0x4B4 | `STEER_SATURATED_CNT` | RO | 0 | steps whose wanted FC lay outside the limits |
| 0x4B8 … 0x4C0 | `STEER_LMAX`, `_NMAX`, `_PMAX` | RO | 4096, 4096, 64 | the tables' entries in this build |

Each entry of the coefficient tables is a signed 48-bit number, written at the index the table lists.

| Table | Entries | Value |
| ----- | ------- | ----- |
| 0 `H` | 1 … L | $h(d)$, fs per fine step, times $2^{32}$ |
| 1 `K` | 1 … L | $K(d)$, fine steps per fine step, times $2^{40}$ |
| 2 `ALPHA` | 0 … p − 1 | $\alpha_j$, fine steps per fs, times $2^{40}$ |
| 3 `AR` | 0 … p − 1 | $\mathrm{ar}_i$, times $2^{40}$ |
| 4 `GAMMA` | 1 … n | $\gamma / n_e$ for $n_e = 1, \dots, n$, fine steps per fs, times $2^{40}$ |
| 5 `MEAN2` | 1 … n | $(1 - \sum_i \mathrm{ar}_i) / n_e$, times $2^{40}$ |
| 6 `SCALAR` | 0 … 3 | ρ ($h$ beyond the table, as table 0), $K_\infty$ (as table 1), $R_0$ (as table 2), θ₀ in $2^{-16}$ fs |

With `LOG`, each step from the first with θ on writes a `QE_TYPE_STEER` entry into the queue after its command. P13 computes $S$ and the remainder in $2^{-56}$ fine steps, and the entry carries them shifted right by 32 bits, which floors them to $2^{-24}$ fine steps.

| Word | `QE_TYPE_STEER` |
| ---- | --------------- |
| 0 | [7:0] type 3, [15:8] flags, [31:16] entry sequence. Flag i is word bit 8 + i, with `QESF_MISSING` = 0 (no θ), `QESF_SETTLING` = 1, `QESF_SATURATED` = 2 (the wanted FC lay outside the limits), `QESF_STOPPED` = 3, `QESF_FIRST` = 4 (the first step with θ), `QESF_LATE` = 5 (the command of the step before started after its tick) and `QESF_STEPS` = 6 (FC steps) |
| 1 | the step's `PPS_COUNT` |
| 2 | [8:0] the step in its second, [25:16] its pairs |
| 3, 4 | the tick its command was to start, LO, HI. A late command's actual start is not recorded anywhere |
| 5 | the step's index since the first with θ |
| 6, 7 | θ, i64, $2^{-16}$ fs (the prediction in a step without pairs, and in FC steps the first step's θ throughout, since FC steps do not use θ) |
| 8, 9 | $\Delta F$, i64, $2^{-16}$ fs |
| 10, 11 | $S$, i64, $2^{-24}$ fine steps (0 while settling, without θ and in FC steps) |
| 12 | [11:0] FC's fine DAC value after the step, [31:16] the change, i16 |
| 13 | the remainder, i32, $2^{-24}$ fine steps |
| 14 | the changes older than `STEER_L` steps, summed, i32 |

In Python, `pl.steering` (§11.19) and `rf_clock.plmodel.steer` (§11.12) drive and model P13, and `m4c_link --steer-actuator pl` (§13) runs it.

## 5. Status and error reporting

### 5.1 TX/RX states (`STATUS[10:8]`, `STATUS[14:12]`)

| Value | Name    | TX | RX |
| ----- | ------- | -- | -- |
| 0     | IDLE    | idle | idle |
| 1     | ARMED   | accepted, waiting for the target | accepted, waiting for the target |
| 2     | RUNNING | transmitting | window open |
| 3     | DRAIN   | — | window closed, waiting for the DMA to take TLAST |
| 4     | DONE    | finished; the next command can be submitted | ended; check `RX_RESULT` for validity |
| 5     | FAULT   | fault; needs `CLEAR_FAULT` | — (RX never enters FAULT) |

```text
TX:  IDLE/DONE ─doorbell (accepted)→ ARMED ─target→ RUNNING ─last beat→ DONE
                                     ARMED ─cancel→ IDLE
                           ARMED/RUNNING ─fault→ FAULT ─CLEAR_FAULT→ IDLE
RX:  IDLE/DONE ─doorbell (accepted)→ ARMED ─target→ RUNNING ─last beat→ DRAIN ─TLAST→ DONE
                                     ARMED ─cancel (before the window)→ IDLE
```

TX and RX are independent: both can be ARMED at once, and one `CONTROL` write can start both.

### 5.2 Result codes (`TX_RESULT` / `RX_RESULT` [7:0])

| Value | Name              | Class  | Meaning |
| ----- | ----------------- | ------ | ------- |
| 0x00  | NONE              | state  | after reset or `CLEAR_FAULT` |
| 0x01  | ARMED             | state  | command accepted |
| 0x02  | RUNNING           | state  | started |
| 0x03  | DONE              | state  | completed successfully |
| 0x04  | CANCELLED         | state  | cancelled while ARMED |
| 0x10  | REJ_BUSY          | reject | doorbell while the state is not IDLE/DONE |
| 0x11  | REJ_STALE         | reject | target < current tick + `MIN_LEAD_TICKS` |
| 0x12  | REJ_LENGTH        | reject | length 0 or above the limit |
| 0x13  | REJ_TIME_INVALID  | reject | `time_valid = 0` |
| 0x14  | REJ_DMA_NOT_READY | reject | RX: S2MM not ready (set up the DMA before the doorbell) |
| 0x20  | FAULT_TREADY      | fault  | TX: DAC `tready` low during a burst; the waveform has a hole |
| 0x21  | FAULT_BUF_WRITE   | fault  | TX: TX buffer written while ARMED/RUNNING |
| 0x22  | FAULT_TIME        | fault  | TX: `time_valid` dropped while ARMED/RUNNING |
| 0x30  | RX_INVALID        | result | RX: the window ended with a flag set (§5.3); discard the measurement |

A rejection does not change the state and does not affect the command in progress. But a doorbell rejected while ARMED/RUNNING (`REJ_BUSY`) overwrites the value shown in `*_RESULT` until the next state change writes a new code. So track progress with the state fields of `STATUS`, and judge whether a submit was accepted from the `*_RESULT` read right after it.

### 5.3 RX window flags (`RX_RESULT[11:8]`)

Any flag set makes the window `RX_INVALID`; only that measurement is lost and the system keeps running.

| Bit | Name      | Meaning |
| --- | --------- | ------- |
| 8   | OVERFLOW  | The FIFO was full inside the window and beats were lost (DDR writes could not keep up) |
| 9   | GAP       | ADC `tvalid` was 0 inside the window |
| 10  | TIME      | `time_valid` was 0 while ARMED or during the window |
| 11  | OVERRANGE | The ADC went over range inside the window |

### 5.4 Fault bits (`FAULT_BITS`)

| Bit | Name           | Meaning |
| --- | -------------- | ------- |
| 0   | LOCK_LOST      | Clock lock lost (`time_valid` 1→0) |
| 1   | PPS            | A PPS period out of tolerance, or the PPS missing (`pps_locked` clears as well) |
| 2   | TICK_EXHAUSTED | raw_tick saturated |
| 3   | TX             | TX entered FAULT |

## 6. Timing semantics

### 6.1 TX

The DAC AXIS accepts beat 0 in the cycle where `raw_tick == TX_TARGET`, then one beat per cycle with no gap (also between passes when `TX_REPEAT` > 1); outside a burst the DAC input is 0. Sample n of the burst (with repetition, n counts on from the first pass):

```text
t_tx(n) = TX_TARGET × 10 ns + n × 1.25 ns
```

### 6.2 RX

Beat 0 of the window is the data on the ADC AXIS in the cycle where `raw_tick == RX_TARGET`; the window covers `RX_LENGTH` consecutive cycles. Sample n of the window:

```text
t_rx(n) = RX_TARGET × 10 ns + n × 1.25 ns
```

Both are **times at the RFDC digital interfaces**. The delays of the DAC/ADC internal data paths are fixed by MTS (§7.1 step 7); cables, amplifiers and antennas are not included and are calibrated as in section 8 of the plan. Without a loopback cable, the crosstalk from DAC tile 2 to ADC tile 2 on the board gives about 226.0 samples (282.5 ns, 2026-10-14) between the two digital interfaces. That is the value with the MTS target latencies fixed (§7.1 step 7), the same on run4 and run6; without fixed targets it changes with the bitstream (212.07, 214.08).

### 6.3 PPS

`PPS_TICK` is quantized to 10 ns. `PPS_LAST_PERIOD` staying at 100000000 shows that the fabric clock and the FS725 share their reference; when the LMK runs from the on-board VCXO, a second is off by tens to hundreds of ticks and PPS does not lock. P1 decides:

| Period minus 100000000              | Action |
| ----------------------------------- | ------ |
| 0                                   | correct period; after 4 in a row `pps_locked = 1` |
| ±1                                  | counted in `PPS_JITTER_CNT`; lock kept |
| larger, or no PPS for over a period | `FAULT_BITS.PPS`; `pps_locked` cleared |

A steadily increasing `PPS_JITTER_CNT` means the PPS edge sits on the sampling edge, and the PL input delay needs adjusting. The sub-tick phase of the PPS edge relative to the tick is calibrated after each power-up.

### 6.4 Epoch

A change of `TIME_EPOCH` means the clocks lost lock and locked again. raw_tick is not cleared, but timestamps and measurements before and after the change are not comparable; data spanning epochs is discarded.

## 7. Programming sequences

### 7.1 Power-up and start

The order matters:

- At boot PYNQ loads its base overlay, part of which also runs on LMK clocks. Replace it with this design first, then change the clocks (seen on the board: changing the clocks first once hung the whole system).
- When the clocks change, the fabric MMCM input changes; reset it with `pl_resetn0` so that it locks again (seen on the board: without the reset the MMCM may not lock after the first load).
- RFDC tile start-up needs the LMX references running; P2 and the TX buffer need the fabric clock running.

```text
Overlay
 0. After boot, wait for the system to finish starting (systemctl is-system-running --wait):
    PYNQ's bootpy.service loads the base overlay about a minute after the kernel starts and
    would replace a design loaded earlier
 1. Load rf_clock.bit + rf_clock.hwh (PYNQ Overlay); do not touch P2 or the TX buffer yet.
    For the packaged overlay, check manifest.json first (§14.1)
Clocks
 2. Write this project's LMK04828 / LMX2594 register files (rf_clock/clocks/files/) to the
    clock chips through xrfclk (lmk_freq = 100.0, lmx_freq = 480.0). The LMK runs in nested
    zero-delay mode (PLL1 feedback from DCLKout8 = PL_CLK), so the 100 MHz tick has the same
    phase to the FS725 10 MHz and PPS after every power-up. The register file releases the LMK
    dividers before PLL2 is programmed, so they then get the data sheet's SYNC (TI SNAS605AS
    9.3.2.1.1, Table 1): SYSREF_MUX 0 (the SYNC, not the continuous SYSREF, on the dividers'
    SYNC line), SYNC_POL 0, every SYNC_DIS cleared, SYNC_POL 1 then 0, SYNC_DIS and SYSREF_MUX
    as in the file, then 2 s for PLL1 (since rf-clock 1.3.0.9; without it a few power-ups in a
    thousand put one board's converters 4 or 5 LMK VCO periods, 416.7 ps each, late against
    its PPS tick). Each LMX2594 whose
    VCO band the board config forces (§11.1) is then rewritten with R8 VCO_CAPCTRL_FORCE = 1 and the band
    in R19, and both get their phase SYNC (R0 VCO_PHASE_SYNC 0 -> 1). After 1 s read back
    each LMX2594's VCO (R110-R112) and stop if one is not locked (since rf-clock 1.3.0.6).
    A band left to the calibration can differ at the next power-up and moves its converter
    by 8-10 ps
 3. On site: the PLL1, PLL2 and both LMX (ADC/DAC) LEDs are lit
 4. Hold pl_resetn0 (EMIO GPIO 95) low for about 10 ms and release it: resets the fabric MMCM
    and every proc_sys_reset
 5. Poll bit 0 of the clock-status GPIO; it becomes 1 within 1 s. While it is 0, do not touch
    P2 or the TX buffer
RFDC
 6. Wait until ADC and DAC tiles 0 and 2 are started (TileState 0xF); after pl_resetn0 the IP
    restarts by itself, and StartUp is called only on a timeout
 7. MTS on DAC, then ADC (tiles 0 and 2) with fixed target latencies DAC 1944 and ADC 1296, so
    the data-path delay does not depend on the bitstream; stop if the status is not OK or a
    tile does not land on the target. Each tile captures SYSREF in the window of its
    capture delay (DTC) nearest the code the board config records for it (since rf-clock
    1.3.0.8; without a record the window nearest code 64, which can change by a whole sample
    from one power-up to the next where two windows lie about as far from 64). Then restart
    the NCO phases of all four tiles at one SYSREF edge (PG269 "Use Case 1"), so the carrier
    phase is the same after every power-up (since rf-clock 1.3.0.5; before, it took one of
    three values 120° apart). Then put DAC tile 0 block 0 at baseband, mixer bypassed
    (rfdc.baseband_dac, since rf-clock 2.1.0.0): its stream is the output PPS (§4.13);
    and ADC tile 0 block 0's NCO at 0 Hz, phase 0 (rfdc.baseband_adc, since rf-clock
    2.4.0.0): its I stream is the 10 MHz reference on ADC_D (§4.14)
PL
 8. Read ID == 0x52434C4B and VERSION major == 1; BUILD_VERSION A.B.C equals the installed
    rf-clock (§14.1)
 9. Read TX_DEPTH, RX_MAX_BEATS, MIN_LEAD_TICKS
10. Write PPS_CONFIG (default 0 on this board)
11. Wait for STATUS.time_valid = 1 and STATUS.pps_locked = 1 (about 5 s); record TIME_EPOCH.
    If pps_locked does not come, read PPS_INPUTS to see whether the PPS arrives
12. If STATUS.any_fault: read and record FAULT_BITS/FAULT_INFO, write CONTROL.CLEAR_FAULT
13. Generate the waveform and write it to the TX buffer
14. Allocate the RX DDR buffer; S2MM_DMACR.RS = 1
```

### 7.2 Scheduling a TX

```text
1. Check that tx_state is IDLE or DONE
2. R64 0x28 → pps_tick (the latest PPS)
3. target = pps_tick + 100000000 + slot offset; after the next PPS, so certainly
   ≥ current tick + MIN_LEAD_TICKS
4. W32 0x40 / 0x44 / 0x48 (from 1.3 also 0x5C, the repeat count), then W32 0x08 0x01
5. R32 0x4C and check 0x01 (ARMED)
6. Poll STATUS until tx_state = DONE or FAULT
7. DONE: R64 0x54 and check TX_ACTUAL == target
```

### 7.3 Scheduling an RX

```text
1. Check that rx_state is IDLE or DONE
2. Set up the S2MM: write S2MM_DA / S2MM_DA_MSB, then S2MM_LENGTH = RX_LENGTH × 32
3. W32 0x60 / 0x64 / 0x68, then W32 0x08 0x04
4. R32 0x6C and check 0x01; 0x14 means the S2MM is not ready; on a rejection, reset the DMA
5. Wait for the S2MM to finish, then poll until rx_state = DONE
6. Valid only if RX_RESULT[7:0] == 0x03; on 0x30 discard the measurement and record the
   [11:8] flags
7. Check RX_ACTUAL == target and S2MM_LENGTH (bytes received) == RX_BEATS × 32
8. Invalidate the buffer and read the samples
```

### 7.4 Monitoring during operation

In each measurement cycle check that bit 0 of the clock-status GPIO is still 1, `TIME_EPOCH` has not changed and `STATUS.pps_locked` is still 1 (when the LMK loses the FS725 reference, the PPS period drifts out of tolerance). If any of them fails, discard the measurements of that interval.

## 8. Register summary

Offsets from the P2 base.

| Offset | Name              | Access | Reset      | Meaning | See |
| ------ | ----------------- | ------ | ---------- | ------- | --- |
| 0x00   | `ID`              | RO     | 0x52434C4B | design identifier "RCLK" | §4.1.1 |
| 0x04   | `VERSION`         | RO     | 0x00020005 | [31:16] major, [15:0] minor | §4.1.2 |
| 0x08   | `CONTROL`         | WO     | —          | command pulses | §4.1.3 |
| 0x0C   | `STATUS`          | RO     | —          | status word | §4.2.1 |
| 0x10   | `TIME_EPOCH`      | RO     | 0          | number of clock relocks | §4.1.4 |
| 0x14   | `FAULT_BITS`      | RO     | 0          | sticky fault bits | §4.2.2 |
| 0x18   | `FAULT_INFO`      | RO     | 0          | first fault source, total fault events | §4.2.3 |
| 0x20   | `TICK_LO`         | RO     | —          | current raw_tick, low 32 bits; reading it latches HI | §4.3.1 |
| 0x24   | `TICK_HI`         | RO     | —          | latched high 32 bits | §4.3.1 |
| 0x28   | `PPS_TICK_LO`     | RO     | 0          | tick of the latest PPS; reading it latches HI and `PPS_COUNT` | §4.4.1 |
| 0x2C   | `PPS_TICK_HI`     | RO     | 0          | latched high 32 bits | §4.4.1 |
| 0x30   | `PPS_COUNT`       | RO     | 0          | latched PPS edge count | §4.4.1 |
| 0x34   | `PPS_LAST_PERIOD` | RO     | 0          | latest PPS period (ticks) | §4.4.2 |
| 0x38   | `PPS_JITTER_CNT`  | RO     | 0          | periods off by ±1 tick | §4.4.3 |
| 0x3C   | `PPS_INPUTS`      | RO     | —          | levels and edge counts of the two PPS copies (1.1) | §4.4.4 |
| 0x40   | `TX_TARGET_LO`    | RW     | 0          | TX start tick, low 32 bits (shadow) | §4.5.1 |
| 0x44   | `TX_TARGET_HI`    | RW     | 0          | TX start tick, high 32 bits (shadow) | §4.5.1 |
| 0x48   | `TX_LENGTH`       | RW     | 0          | TX length (beats, shadow) | §4.5.2 |
| 0x4C   | `TX_RESULT`       | RO     | 0          | TX result code | §4.5.5 |
| 0x50   | `TX_SEQ`          | RO     | 0          | TX bursts completed | §4.5.6 |
| 0x54   | `TX_ACTUAL_LO`    | RO     | 0          | actual start tick, low 32 bits | §4.5.7 |
| 0x58   | `TX_ACTUAL_HI`    | RO     | 0          | actual start tick, high 32 bits | §4.5.7 |
| 0x5C   | `TX_REPEAT`       | RW     | 1          | passes over the burst (shadow, 1.3) | §4.5.9 |
| 0x60   | `RX_TARGET_LO`    | RW     | 0          | RX window tick, low 32 bits (shadow) | §4.6.1 |
| 0x64   | `RX_TARGET_HI`    | RW     | 0          | RX window tick, high 32 bits (shadow) | §4.6.1 |
| 0x68   | `RX_LENGTH`       | RW     | 0          | RX window length (beats, shadow) | §4.6.2 |
| 0x6C   | `RX_RESULT`       | RO     | 0          | [7:0] result code, [11:8] window flags | §4.6.5 |
| 0x70   | `RX_SEQ`          | RO     | 0          | windows ended | §4.6.6 |
| 0x74   | `RX_BEATS`        | RO     | 0          | beats sent to the DMA | §4.6.7 |
| 0x78   | `RX_ACTUAL_LO`    | RO     | 0          | actual window tick, low 32 bits | §4.6.8 |
| 0x7C   | `RX_ACTUAL_HI`    | RO     | 0          | actual window tick, high 32 bits | §4.6.8 |
| 0x80   | `RX_INVALID_CNT`  | RO     | 0          | invalid windows in total | §4.6.6 |
| 0x84   | `TX_DEPTH`        | RO     | 69632      | TX buffer capacity (beats) | §4.1.5 |
| 0x88   | `RX_MAX_BEATS`    | RO     | 2097151    | RX window limit (beats) | §4.1.5 |
| 0x8C   | `MIN_LEAD_TICKS`  | RO     | 64         | minimum target lead (ticks) | §4.1.5 |
| 0x90   | `PPS_CONFIG`      | RW     | 0          | PPS source and polarity (1.1) | §4.4.5 |
| 0x94   | `BUILD_VERSION`   | RO     | set at build | rf-clock version at build time, 0xAABBCCDD (1.2) | §4.1.6 |
| 0x100 … 0x124 | `SLOT_*`, `RX_WINDOW_BEATS`, `RX_SPAN_BEATS` | | | slot engine (2.0) | §4.8 |
| 0x140 … 0x16C | `PPS_OUT_*` | | | output PPS (2.1; `PPS_OUT_IDLE` 2.2, `PPS_OUT_FRAC` 2.3) | §4.13 |
| 0x180 … 0x19C | `BURST_*`, `PACKET_SPREAD`, `PACKET_BITS`, `CHIP_MEM_CHIPS` | | | bursts from chips (2.0) | §4.9 |
| 0x1C0 … 0x1DC | `REF_PHASE_*` | RO | 0 | phase of the 10 MHz reference (2.4) | §4.14 |
| 0x200 … 0x234 | `P8_*`, `QUALITY_OFFSET` | | | correlator (2.0) | §4.10 |
| 0x240 … 0x284 | `SR_*` | RO | 0 | search report (2.0) | §4.10 |
| 0x300 … 0x310 | `PACKET_*` | | | measurement packet (2.0) | §4.11 |
| 0x380 … 0x38C | `QUEUE_*` | | | measurement queue (2.0) | §4.12 |
| 0x400 … 0x42C | `SERIAL_*` | | | the FS725's serial line (2.5) | §4.15 |
| 0x440 … 0x4C0 | `STEER_*` | | | steering through FC in the PL (2.5) | §4.16 |

---

# Part II Python library `rf_clock.pl`

## 9. Environment

| Where | How |
| ----- | --- |
| Board (PYNQ 3.1.1) | PYNQ's environment `/usr/local/share/pynq-venv` (§9.1), run as root: `sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m <module>`. `xrfclk` needs `BOARD`, PYNQ needs `XILINX_XRT` |
| Development machine | Test the protocol logic against fake registers (`MemoryMMIO`, `tests/`). `uv sync` installs the board's `pynq==3.1.1` as a dev dependency, for IDE completion and offline HWH parsing only; it cannot drive hardware and is not a runtime dependency (the board uses its own copy) |

### 9.1 Board environment

The board has no internet access. rf-clock is installed into PYNQ's own environment `/usr/local/share/pynq-venv` (Python 3.10, pynq 3.1.1, numpy 1.21.5), where PYNQ's Jupyter and everyone else on the board find it. Its only dependency, numpy, is PYNQ's own (`pyproject.toml` pins the same 1.21.5), so it is installed without dependencies and nothing else in that environment changes.

Install or update, as root (the environment belongs to root):

```bash
sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index rf_clock-<version>-py3-none-any.whl
```

The wheel is a release's (§14.1) or, for a development version, built on the board from the repository: `~/.local/bin/uv build --wheel --offline -o /tmp/rfw` (offline, because uv_build is in the board's uv cache).

Notes:

- `--no-deps --no-index`: never install or upgrade anything else in PYNQ's environment; the board is offline as well. Do not install rf-clock there editable: everyone on the board would run whatever the working tree holds. Do not run `uv sync` on the board either.
- The clock register files are inside the package (`rf_clock/clocks/files/`); `PL.open()` hands them to xrfclk, so nothing is copied into xrfclk's directory.
- The `BOARD` variable (`RFSoC4x2`) is set by `/etc/profile.d/boardname.sh`, in login shells only, and `xrfclk` reads it at import. `/dev/mem`, bitstream download and the SPI clock chips need root, and `sudo` clears the environment, hence `sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python ...`.
- Jupyter: PYNQ's Jupyter runs in that environment and imports rf_clock as installed.
- `~/RF-clock/.venv`, the separate venv used before 1.3.0.6 (it sees PYNQ's packages through a `.pth` file), holds no rf-clock; scripts started with it, such as the board side of `experiments/`, import PYNQ's copy.
- After a PYNQ image upgrade, install the wheel again.
- Both boards' system clocks follow RF-clock-PC by NTP (chrony there serves 192.168.2.0/24; on the boards `/etc/systemd/timesyncd.conf.d/rf-clock.conf` sets `NTP=192.168.2.1`). rf-clock 2 needs it to start the link: both nodes get the same `--start-at` and `--stop-at` (Unix times, half a second into a second), so their slot engines start and stop at the same PPS edge only if the two system clocks agree to well within half a second (§11.14; the measurements themselves are paired by `PPS_COUNT` and slot). The 1.x exchange, `rf_clock.twtt`, pairs the two boards' seconds by it (§11.12). The boards have no clock of their own across a power cycle.
- Remote recovery: an access to the fabric domain while its clock is not running hangs the AXI bus and the board stops answering ping. Reset it from the machine at the other end of the JTAG cable: `source /tools/Xilinx/Vivado/2024.1/settings64.sh; xsdb tools/jtag_reset_board.tcl <JTAG cable serial>`; ssh is back after about 40 s, with PYNQ's base overlay in the PL.

Modules:

| Module                 | Content |
| ---------------------- | ------- |
| `rf_clock.pl.board`    | `PL`: power-up, and the hardware blocks (ctrl, tx_buffer, rx_dma, rfdc, clock_gpio) |
| `rf_clock.pl.board_config` | the per-board config file and the board serial number (§11.1) |
| `rf_clock.pl.abi`      | addresses, bit numbers and codes generated from the RTL |
| `rf_clock.pl.overlay`  | path of the packaged overlay, its manifest and the version checks (§14.1) |
| `rf_clock.pl.mmio`     | MMIO protocol and implementations (PYNQ / in-memory fake) |
| `rf_clock.pl.client`   | `PLClient`: the whole P2 register protocol |
| `rf_clock.pl.bringup`  | `Bringup`: the power-up sequence of §7.1, and PYNQ helpers |
| `rf_clock.pl.rfdc`     | RFDC tile start-up and MTS |
| `rf_clock.pl.txbuf`    | `TXBuffer`: writes the TX waveform buffer |
| `rf_clock.pl.rxdma`    | `RXDMA`: the S2MM side of an RX window (buffer, DMA set-up and completion checks) |
| `rf_clock.pl.waveform` | `WaveformGenerator`: PRN + RRC transmit waveform |
| `rf_clock.pl.beats`    | packing and unpacking of 256-bit beats |
| `rf_clock.correlator`  | cross-correlation delay estimation (independent of the PL) |
| `rf_clock.twtt`        | two-way time transfer between two boards, and steering B's FS725 (§11.12) |
| `rf_clock.fs725`       | the FS725 rubidium standard over RS-232 (§11.13) |
| `rf_clock.checks`      | board checks for milestones M1–M3 (§13) |
| `rf_clock.examples`    | annotated examples |

## 10. Quick start

On the board: power up, send one burst after a PPS, capture a window from the same tick (`python -m rf_clock.examples.pl.03_pynq_bringup`, run on the board 2026-10-14).

```python
from rf_clock.pl import PL, WaveformGenerator

pl = PL.open()                                 # every step of §7.1; the packaged overlay

beats = WaveformGenerator().generate_beats()   # TX_LAYOUT
rx_beats = len(beats) + 1250
pl.tx_buffer.write_beats(beats)                # on-chip TX buffer
pl.rx_dma.prepare(rx_beats)                    # S2MM address and length, before the RX doorbell

_, pps = pl.ctrl.wait_next_pps()               # just after a PPS
target = pps + 100_000_000 + 100_000           # 1 ms after the next PPS
pl.ctrl.arm_tx(target, len(beats))             # TX_TARGET, TX_LENGTH, doorbell
pl.ctrl.arm_rx(target, rx_beats)               # RX_TARGET, RX_LENGTH, doorbell
t_tx = pl.ctrl.wait_tx(timeout=2.5)            # TX_ACTUAL, checked == target
window = pl.ctrl.wait_rx(timeout=2.5)          # RX_ACTUAL (checked), RX_BEATS, window flags

x = pl.rx_dma.wait(window.beats).samples       # complex64, 800 MS/s, from window.actual
```

Commands, timestamps and status are all in `pl.ctrl`; `tx_buffer` and `rx_dma` only handle the data paths (writing the waveform, setting up the DMA). The order of the two waits does not affect the data: once the window opens, the hardware moves it P4 → FIFO → DMA → DDR.

On a development machine: check the protocol logic against fake registers.

```python
from rf_clock.pl import abi
from rf_clock.pl.client import PLClient
from rf_clock.pl.mmio import MemoryMMIO

regs = MemoryMMIO(initial={abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
                           abi.ABI.REG.VERSION: abi.ABI.VERSION})
client = PLClient(regs, MemoryMMIO(initial={0: 1}))     # GPIO bit 0 = 1
client.validate()
```

## 11. API reference

### 11.1 `rf_clock.pl.PL` (power-up and hardware blocks)

`PL.open()` powers up; the object it returns only holds the hardware blocks, one per address space of §1.1, and work is done through each block's methods:

| Attribute    | Type                 | Hardware |
| ------------ | -------------------- | -------- |
| `ctrl`       | `PLClient` (§11.4)   | P2 registers: every command (arm, wait, cancel), timestamps, PPS, status |
| `tx_buffer`  | `TXBuffer` (§11.7)   | on-chip TX waveform buffer |
| `rx_dma`     | `RXDMA` (§11.8)      | AXI DMA S2MM: RX windows into DDR; the buffer is allocated and reused automatically |
| `rfdc`       | `RFDC`               | `rfdc.driver`: the xrfdc driver (`driver.dac_tiles[2].blocks[0]` and so on); `rfdc.mts`: the MTS result (§11.6), raises `RuntimeError` after `mts=False` |
| `clock_gpio` | MMIO                 | clock-status GPIO (`pl_clk0`), bit 0 = fabric MMCM locked |
| `overlay`    | PYNQ `Overlay`       | |
| `bringup`    | `BringupResult`      | faults found and cleared at power-up (`initial_fault_bits`, `initial_fault_info`) |
| `pps_out`    | `PPSOut` (§11.16)    | P10: the output PPS on DAC_B and its validity line (2.1) |
| `ref_phase`  | `RefPhase` (§11.17)  | P11: the phase of the 10 MHz reference on ADC_D (2.4) |
| `serial`     | `SerialLine` (§11.18) | P12, the FS725's serial line on PMOD1 (2.5) |
| `steering`   | `PLSteering` (§11.19) | P13, steering through FC in the PL (2.5) |
| `board`      | `BoardSetup`         | `serial`: the board's AT24MAC402 serial number; `config`: its board config or `None`; `lmx_vco`: per `"adc"`/`"dac"` the LMX2594's VCO read back (`band`, `forced`, `locked`, `capctrl`, `vco_sel`, `daciset`, `ld_vtune`), empty after `write_clocks=False` |

#### `PL.open(bitstream=DEFAULT_BITSTREAM, *, write_clocks=True, mts=True, pps_source_trig=False, pps_invert=False, ref_tile=0, dac_target_latency=DAC_TARGET_LATENCY, adc_target_latency=ADC_TARGET_LATENCY, check_version=True, board_config=None, progress=None)`

Runs every power-up step of §7.1. The fixed hardware choices are defaults: the packaged overlay `rf_clock/overlay/rf_clock.bit` (§14.1); the clock files `LMK04828_100.0.txt` and `LMX2594_480.0.txt` from `rf_clock/clocks/files/`, written through xrfclk (no copies in xrfclk's directory needed); `pl_resetn0`; RFDC tiles 0 and 2 started and synchronized.

| Parameter                         | Use |
| --------------------------------- | --- |
| `write_clocks=False`              | skip LMK/LMX when they were written since boot |
| `mts=False`                       | no RFDC start-up and MTS (only the tick and PPS, as for M1) |
| `pps_source_trig`, `pps_invert`   | written to `PPS_CONFIG` (§4.4.5) |
| `ref_tile`, `*_target_latency`    | MTS parameters (§11.6); targets default to DAC 1944 and ADC 1296 (§7.1); -1 aligns to the largest measured latency |
| `check_version=False`             | skip the manifest and `BUILD_VERSION` checks of §14.1 (bitstreams before 1.2) |
| `board_config`                    | a `BoardConfig`, or the path of a board config file, instead of the default file (below) |
| `progress`                        | called with one line per step, for example `print` |

On failure the original exception (`BringupError`, `BoardConfigError`, `RFDCError`, `VersionMismatch`, ...) propagates, carrying `.overlay` and, once MTS ran, `.mts`. On a development machine, `PL(ctrl=..., tx_buffer=..., ...)` builds one from fakes.

**Board config** (`rf_clock.pl.board_config`, since 1.3.0.6). What belongs to one board rather than to the design is in a JSON file on that board, `/etc/rf-clock/board.json`, or the file named by the environment variable `RF_CLOCK_BOARD_CONFIG`. `PL.open()` reads it before loading the overlay:

| Key | Content |
| --- | ------- |
| `serial` | the 128-bit serial number of the board's AT24MAC402 EEPROM (PS I2C1, address 0x58, offset 0x80), 32 hex digits; `PL.open()` raises `BoardConfigError` if it is not this board's |
| `label` | free text, for example the board's S/N |
| `lmx_vco_band` | the VCO band (VCO_CAPCTRL, 0-183) to force on each LMX2594, `{"adc": 117, "dac": 122}` (§7.1 step 2) |
| `mts_dtc` | written by `rf_clock.checks.mts_dtc` (§13): the SYSREF capture codes MTS picks its windows by (§7.1 step 7), per converter (`dac`, `adc`) and clock (`t1`; `pll` on the ADC), one per tile 0-3 (-1 unused); per converter also `offset`, each tile's usual MTS offset; and `recorded` (time, power-ups, the VCO bands they belong to). `PL.open()` warns when a picked code is more than 6 codes (about 32 ps) from its record, when the record was made with other VCO bands, or when there is none; and when a tile's offset differs from its record, as it did when one board's converters came up 4 or 5 LMK VCO periods (416.7 ps each) late against its PPS tick (open the PL again), or when no offsets are recorded |
| `band_table` | written by `rf_clock.checks.vco_bands` (§13): per chip how often the calibration picked each band (`calibrated`), the bands that lock (`locked`) and the DAC → ADC loopback delay of each band in ps (`loopback_ps`), relative to both chips at `measured.reference`; with the date and die temperature of the scan (`scanned`) and of the measurement (`measured`) |
| `fs725` | written by `rf_clock.checks.fs725_loop` (§13, since 2.2.0.8): the FS725 that clocks this board, `serial` (its PRS10's serial number, from `ID?`) and `identity`; `port`, the serial device it answers on (since 2.2.0.9); `gain`, the frequency lock loop's gain GA that the link sets at its start; `loops`, per GA the loop measured from FC's answer to SF steps (`k_per_s`, `lag_s`, `dead_s`, and `measured`: when, the steps and their fits), which the predictive steering algorithm steers by; `fc` (optional, since 2.3.0.2), the crystal's answer to FC with the unit at GA 1 (`lag_s`, `dead_s`, `ps_per_step`: theta's rate per fine step of FC, and `measured`), which the algorithm steers by when it sets FC (`m4c_link --steer-actuator fc`), measured with `experiments/m5_fll_off.py` (B's unit: 1.12 s, 0 s, 0.86 ps/s per step) and since 2026-11-12 with `experiments/m5_fc_response.py` (B's unit: 0.994 s, 23.5 ms after the write, 0.849 ps/s per step); releases before 2.3.0.2 refuse a config that has it. `m4c_link --fs725` checks the unit against it (`rf_clock.fs725.check_config`, §11.13) |

Without the file, or for a chip the file leaves out, the LMX2594 keeps the band its calibration picks, `PL.open()` warns (`UserWarning`) and `pl.board.lmx_vco` records the band: where the board's frequency lies in reach of two bands, the calibration picks one or the other from one power-up to the next, and one band higher puts that converter's clock 8-10 ps later (results/lmx-sync-loopback-20261019 §5). A named file that does not exist, a malformed file or one with an unknown key raises `BoardConfigError`. Either way a chip that does not lock raises `BringupError`. The band table is not applied by `PL.open()`; it records what each band costs.

### 11.2 `rf_clock.pl.abi`

Generated by `python tools/generate_abi.py --write` from `p_regs.vh` and `p_defs.vh`; never edit it by hand. `--check` reports whether it is stale (`tests/test_abi_sync.py`).

| Name                         | Content |
| ---------------------------- | ------- |
| `ABI.REG.<name>`             | register offsets, for example `ABI.REG.PPS_TICK_LO == 0x28` |
| `ABI.CTRL.<name>`            | `CONTROL` bit numbers: `TX_DOORBELL`, `TX_CANCEL`, `RX_DOORBELL`, `RX_CANCEL`, `CLEAR_FAULT` |
| `ABI.STAT.<name>`            | `STATUS` bit numbers |
| `ABI.PPSCFG`, `ABI.PPSIN`    | `PPS_CONFIG` and `PPS_INPUTS` bit numbers |
| `ABI.DESIGN_ID`, `ABI.VERSION` | the expected `ID` and the `VERSION` this library implements |
| `TxState`, `RxState`         | the states of §5.1 (IntEnum; identical) |
| `ResultCode`                 | the result codes of §5.2 (IntEnum) |
| `RxFlag`                     | the window flags of §5.3 (IntFlag) |
| `FaultBit`                   | the fault bits of §5.4 (IntFlag) |

### 11.3 `rf_clock.pl.mmio`

| Name                                       | Description |
| ------------------------------------------ | ----------- |
| `RegisterIO`                               | protocol: `read(offset) -> int`, `write(offset, value)`; 32 bits, word-aligned offsets |
| `PynqMMIO(base, length=0x1000)`            | board implementation, wraps `pynq.MMIO` |
| `MemoryMMIO(initial=None, on_write=None)`  | in-memory fake: `values` dict, `read_log`, `write_log`; `on_write(offset, value)` models side effects such as doorbells |

### 11.4 `rf_clock.pl.client`

#### `PLClient(regs, clock_status, *, clock_status_offset=0, clock_locked_mask=1, expected_major=2)`

The only entry to the P2 register protocol. The constructor touches no register. **Every P2 access first reads the clock-status GPIO** and raises `ClockUnavailable` if the clock is not locked, so the bus never hangs.

| Method | Protocol | Returns / raises |
| ------ | -------- | ---------------- |
| `validate()` | reads `ID`, `VERSION`, `TX_DEPTH`, `RX_MAX_BEATS`, `MIN_LEAD_TICKS`, `TIME_EPOCH` | `DeviceInfo`, afterwards also the `info` property (before `validate()` it raises `IdentityError`); `IdentityError` on a wrong ID or major |
| `status()` | R32 `STATUS` | `Status` |
| `read32(off)` / `write32(off, v)` | R32 / W32 | raw access |
| `read64(lo_off)` | R64 (LO before HI) | int |
| `fabric_clock_locked()` | reads GPIO bit 0 | bool |
| `require_fabric_clock()` | same | `ClockUnavailable` if not locked |
| `time_epoch()` | R32 `TIME_EPOCH` | int |
| `remember_epoch()` | records the current epoch | int |
| `require_same_epoch(expected=None)` | compares with the recorded epoch | `EpochChanged` on a change |
| `arm_tx(target, beats, *, repeat=1)` | §4.5.3, §4.5.9 | returns when accepted; `CommandRejected` on a rejection; `PLClientError` on an immediate FAULT |
| `arm_rx(target, beats)` | §4.6.3 | same |
| `wait_tx(timeout=1.0, poll_interval=0.001)` | polls until `tx_state = DONE`, reads `TX_ACTUAL` | `TX_ACTUAL`; `TargetMissed` if ≠ target; `PLClientError` on FAULT; `WaitTimeout` |
| `wait_rx(timeout=1.0, poll_interval=0.001)` | polls until `rx_state = DONE`, reads `RX_RESULT`, `RX_ACTUAL`, `RX_BEATS` (§7.3 steps 5–7) | `RXWindow(target, actual, beats)`; `RXWindowInvalid` for an invalid window; `TargetMissed` if ≠ target; `WaitTimeout` |
| `tx_actual()` / `rx_actual()` | R64 `TX_ACTUAL` / `RX_ACTUAL` | int |
| `rx_beats()` | R32 `RX_BEATS` | int |
| `rx_result()` | R32 `RX_RESULT` | `(ResultCode, RxFlag)` |
| `cancel_tx()` / `cancel_rx()` | W32 `CONTROL` | — |
| `clear_fault()` | W32 `CONTROL` 0x10 | — |
| `latest_pps()` | R64 `PPS_TICK`, then R32 `PPS_COUNT` | `(count, tick)` of the same edge |
| `wait_next_pps(timeout=2.5, poll_interval=0.005)` | polls until `PPS_COUNT` changes | `(count, tick)` of the new edge; `WaitTimeout` |
| `pps_inputs()` | R32 `PPS_INPUTS` | `PPSInputs`; `IdentityError` before ABI 1.1 |
| `set_pps_config(*, source_trig=False, invert=False)` | W32 `PPS_CONFIG` | same |
| `build_version()` | R32 `BUILD_VERSION` | `"A.B.C.D"`; `IdentityError` before ABI 1.2 |

How `arm_tx`/`arm_rx` decide that a command was accepted: ARMED or RUNNING read after the submit means accepted; DONE (and for RX also RX_INVALID) counts only if `*_SEQ` advanced since before the submit, so the previous command's result is never taken for this one. If an epoch was recorded, it is checked before the submit.

Data classes:

| Class        | Fields |
| ------------ | ------ |
| `Status`     | `raw`, `time_valid`, `clocks_locked`, `pps_locked`, `tick_exhausted`, `any_fault`, `dma_ready`, `tx_state`, `rx_state` |
| `DeviceInfo` | `design_id`, `version`, `tx_depth`, `rx_max_beats`, `min_lead_ticks` |
| `PPSInputs`  | `comp_level`, `trig_level`, `comp_edges`, `trig_edges` |

Scheduling a slot after a PPS:

```python
count, pps_tick = client.wait_next_pps()            # just after a PPS; about 1 s to the next
target = pps_tick + 100_000_000 + offset_ticks      # offset after the next PPS
```

### 11.5 `rf_clock.pl.bringup`

#### `Bringup(*, overlay_loader, clock_setup, register_factory, clock_status_factory, rfdc_setup=None, pl_reset=None, pps_source_trig=False, pps_invert=False, package_version=None, fabric_lock_timeout=1.0, time_valid_timeout=1.0, pps_lock_timeout=10.0, poll_interval=0.1)`

Calls the injected board operations in the fixed order of §7.1. `run(bitstream)`:

| Step | Call | On failure |
| ---- | ---- | ---------- |
| 1 | `overlay_loader(path)` → overlay | propagates |
| 2 | `clock_setup()` | propagates |
| 3 | `pl_reset()` (if given) | propagates |
| 4 | poll `clock_status_factory(overlay)` bit 0 | `BringupError` if not locked within `fabric_lock_timeout` s |
| 5 | `rfdc_setup(overlay)` (if given) | propagates (for example `RFDCError`) |
| 6 | `PLClient.validate()`; with `package_version`, compares `build_version()` | `IdentityError`; `VersionMismatch` if A.B.C differs |
| 7 | `set_pps_config(pps_source_trig, pps_invert)` | — |
| 8 | waits for `time_valid` and `pps_locked` | `BringupError` on a timeout |
| 9 | on a fault, reads `FAULT_BITS`/`FAULT_INFO`, then `clear_fault()`; records the epoch | — |

Returns `BringupResult(overlay, client, info, initial_fault_bits, initial_fault_info)`.

Helpers:

| Function | Description |
| -------- | ----------- |
| `wait_for_boot(timeout=300.0)` | waits for `systemctl is-system-running --wait` and returns the final state (`degraded` on PYNQ, usually) |
| `pynq_pl_reset(*, emio_index=95, pulse=0.01)` | holds `pl_resetn0` low for about 10 ms |
| `pynq_register_factories(*, p2_ip_name="p_top_0", gpio_ip_name="clk_status_gpio")` | returns the `(registers, clock_status)` factories used by `Bringup` |
| `mmio_from_overlay(overlay, name, *, default_length=0x1000)` | a `PynqMMIO` for an HWH name, looked up in `ip_dict` and `mem_dict` |
| `overlay_address(overlay, name, *, default_length=0x1000)` | returns `(base, length)` |

### 11.6 `rf_clock.pl.rfdc`

PYNQ 3.1.1's `xrfdc` does not wrap MTS. This module calls `libxrfdc.so` directly through cffi, with declarations from `xrfdc_mts.h` of embeddedsw `xilinx-v2020.2`; they must match that version (later versions of `XRFdc_MultiConverter_Init` take one more argument).

#### `setup_rfdc_and_mts(overlay, *, rfdc_name="rfdc", tiles=(0, 2), signal_tile=2, ref_tile=0, dac_target_latency=-1, adc_target_latency=-1, dtc_target=None)`

Steps 6–7 of §7.1: waits for the tiles, runs MTS on the DAC and then the ADC, restarts the NCO phases at a SYSREF edge (`sync_nco_phase`, only if both MTS succeeded), and reads back the mixer settings of the signal tile. Usable directly as the `rfdc_setup` of `Bringup`.

- Returns `RFDCSetupResult(dac, adc, started_after_startup_call, mixers, nco_synced)`.
- Raises `RFDCError` if either MTS status is not OK; its `.result` still holds the result, so the latencies can be logged.
- `*_target_latency = -1` aligns to the largest measured latency, which changes with the bitstream; `PL.open()` passes the fixed `board.DAC_TARGET_LATENCY` = 1944 (1896 before rf-clock 1.3.0.10) and `board.ADC_TARGET_LATENCY` = 1296. A target below a tile's measured latency makes MTS report `TARGET_LOW`; then raise the target and recalibrate the loopback delay.

| Other functions | Description |
| --------------- | ----------- |
| `run_mts(rfdc, kind, tiles=(0, 2), *, ref_tile=0, target_latency=-1)` | `kind` is `"DAC"` or `"ADC"`; returns `MTSResult` |
| `baseband_dac(rfdc, tile, nco_mhz=0.0)` | moves DAC `tile` block 0 to IMR low-pass in Nyquist zone 1 with the NCO at `nco_mhz` (0: coarse mixer bypassed, the DAC outputs the I samples; with an NCO the fine-mixer scale is 1.0); returns a function that restores the settings. `PL.open()` calls it for `PPS_OUT_TILE` = 0 after MTS (since 2.1.0.0); `m2_tx_timing --scope-nco` for tile 2 |
| `baseband_adc(rfdc, tile)` | sets ADC `tile` block 0's NCO to 0 Hz with phase 0 and resets its accumulator at once (at 0 Hz the instant does not matter; a frozen accumulator would scale the samples by its cosine, or invert them), so the I stream is the samples. `PL.open()` calls it for `REF_PHASE_TILE` = 0 after `baseband_dac` (since 2.4.0.0, §4.14) |
| `sync_nco_phase(rfdc, tiles=(0, 2))` | PG269 "Use Case 1": with the SYSREF receivers off, sets each block-0 mixer's event source to SYSREF and arms ResetNCOPhase; turns the receivers on for one SYSREF edge (200 ns, whole NCO periods) and off again; sets the event sources back so later `UpdateEvent` calls work. Raises `RFDCError` if `XRFdc_MTS_Sysref_Config` fails |
| `start_tiles(rfdc, tiles=(0, 2), *, settle=1.0, timeout=5.0)` | waits for TileState 0xF; calls StartUp if not started after `settle` s; returns whether StartUp was called |
| `tile_states(rfdc)` | `{"ADC": [4 TileStates], "DAC": [...]}` |
| `rfdc_driver(overlay, name="rfdc")` | the `xrfdc.RFdc` driver; built from the HWH if `xrfdc` was not imported before the overlay loaded |
| `decode_mts_status(status)` | splits an MTS status into names, for example `["TARGET_LOW"]` |

`MTSResult` fields: `kind`, `status`, `tiles`, `ref_tile`, `target_latency`, `latency` (tiles 0–3), `offset` (tiles 0–3), `dtc` (the SYSREF capture delay codes the driver picked, 0–127, and the number of capture windows it found, per tile, for the PLL and the T1 clock: `pll_code`, `pll_windows`, `t1_code`, `t1_windows`; they follow the tile clock's phase to SYSREF, which `latency` and `offset` do not); `describe()` gives one line. `ok` requires status 0 and every tile on the target (`on_target`): the driver adds delay in whole steps (6 for both DAC and ADC), so a target that is not a whole number of steps away would miss without an error.

Measured on the board (2026-10-14): without targets, DAC tiles 0/2 have latency 1848 and the ADC 1236 (run4) or 1248 (run6), unchanged across restarts of one bitstream. With targets DAC 1896 and ADC 1296, the crosstalk loopback delay on run4 and run6 is 226.05 and 226.02 samples: 0.03 samples apart, within the standard deviation (0.13–0.16 samples).  With the LMK divider SYNC of rf-clock 1.3.0.9 the DAC tiles measure 1896 before alignment (offset 0), which left no room for a tile that measures more (`TARGET_LOW`); rf-clock 1.3.0.10 raises the DAC target by 8 steps to 1944, which delays the DAC output by 48 samples (5 ns) against 1.3.0.9: delays measured against the PPS or through a loopback are to be measured again.

### 11.7 `rf_clock.pl.txbuf`

#### `TXBuffer(memory, client)`

`memory` is the `RegisterIO` of `tx_bram_ctrl` (`mmio_from_overlay(overlay, "tx_bram_ctrl")`).

| Method | Description |
| ------ | ----------- |
| `write_beats(beats)` | writes from beat 0 and returns the number of beats; one block copy (appendix A.2), with one check of the TX state and the clock GPIO for the whole load |
| `write_beat(index, beat)` | writes one beat |

A beat is an int below 2^256 or 32 bytes. Beyond `TX_DEPTH`, or while TX is ARMED/RUNNING, `TXBufferError` is raised and nothing is written (so `FAULT_BUF_WRITE` cannot happen). Loading the default 4106-beat waveform takes about 17 ms (about 0.5 ms of it the copy); word by word it would take 0.3–0.5 s.

### 11.8 `rf_clock.pl.rxdma`

> Verified on the board (2026-10-14, `rf_clock.checks.m3_loopback`, including
> the 64 MB stress windows); `PynqS2MMDMA.abort()` has not been exercised on
> the board yet.

#### `RXDMA(dma, *, buffer_factory=None)`

`pl.rx_dma`: the S2MM side of an RX window. Commands and window checks are in `pl.ctrl` (§11.4); one window, following §7.3:

```python
pl.rx_dma.prepare(n)                     # 1. S2MM: buffer address and length
pl.ctrl.arm_rx(target, n)                # 2. RX_TARGET, RX_LENGTH, doorbell
window = pl.ctrl.wait_rx(timeout=2.5)    # 3. DONE, flags, RX_ACTUAL == target
data = pl.rx_dma.wait(window.beats)      # 4. S2MM done, bytes == RX_BEATS * 32, invalidate
x = data.samples                         # complex64, from window.actual
```

| Method | Effect |
| ------ | ------ |
| `prepare(beats, *, buffer=None)` | starts the S2MM and returns the buffer. Without `buffer`, `buffer_factory` allocates one, reused by later windows (reallocated only when a window is larger) |
| `wait(beats, *, timeout=1.0)` | `beats` from `RXWindow.beats`; returns `RXData(buffer, beats, received_bytes)` |
| `abort()` | drops a started transfer |

`RXData.samples` decodes the window with `RX_LAYOUT` into complex64 on first access. The next window reuses the buffer, so take the `samples` before the next `prepare`.

`prepare` comes before the RX doorbell: the PL rejects the doorbell (0x14) while the S2MM is not ready. If the doorbell is rejected or the window cancelled, the started transfer never sees TLAST; the next `prepare` finds the channel busy and resets the DMA first, or call `abort()` at once. An invalid window (`RXWindowInvalid`) still ends the DMA normally (P4 reports DONE only after the DMA took TLAST), so nothing needs doing.

| Exception    | When |
| ------------ | ---- |
| `RXDMAError` | DMA bytes ≠ `RX_BEATS × 32`; `wait` without `prepare` |

`PynqS2MMDMA(dma, *, length_reader=None)` adapts a PYNQ `DMA` object to the `prepare`/`wait`/`abort`/`received_bytes` that `RXDMA` needs. `prepare` starts a transfer of exactly the window's bytes, so one large buffer can serve shorter windows. `abort()` clears a transfer that will never end with the soft-reset bit of `S2MM_DMACR`. `pynq_rx_dma(overlay, name="rx_dma")` returns the board adapter, building the driver from the HWH if `pynq.lib.dma` was not imported before the overlay loaded.

### 11.9 `rf_clock.pl.waveform`

#### `WaveformGenerator(amplitude=512, chip_count=4096, lfsr_seed=0xACE1, periodic=False, beta=0.25, span_chips=10, samples_per_symbol=8)`

Generates the BPSK PRN waveform on the ARM (Q = 0). The RRC coefficients are computed from `beta`, `span_chips` and `samples_per_symbol` (`rrc_config()`, about 2 ms on the board); no data file is stored.

| Member | Description |
| ------ | ----------- |
| `generate_beats(chips=None, *, count=None, seed=None, layout=None)` | TX beats packed with `TX_LAYOUT`, ready for `TXBuffer` |
| `generate(...)` | int16 real samples |
| `generate_iq(...)` | `(N, 2)` int16, I and Q columns |
| `chips(count=None, seed=None)` | PRN chips |
| `tap_count`, `tail_samples`, `expected_sample_count`, `config_id` | derived values |

Defaults: β = 0.25, a span of 10 chips, 8 samples per chip (81 taps), 4096 chips, 32841 samples, 4106 beats (about 41 µs). `config_id` is a hash of the RRC parameters (2134958710 for the defaults), recorded with each measurement so that both ends can confirm they use the same filter; `tests/test_waveform.py` holds golden coefficients against implementation drift. `rrc_config(beta, span_chips, samples_per_symbol)` returns `(config dict, coefficient list)`.

On the board `generate_beats()` takes about 50 ms for 4096 chips and about 0.7 s for 65535 chips, mostly computing the samples; the packing works on the whole array at once.

`periodic=True` gives one period of the chip sequence repeated forever: the RRC tail wraps onto the start (circular convolution, `simulate_periodic_convolution`), exactly `chip_count × 8` samples = `chip_count` beats, with no tails. Played with `arm_tx(..., repeat=n)` it gives one continuous signal; one period of a 65535-chip m-sequence is 65535 beats and fits.

```python
gen = WaveformGenerator(chip_count=65535, periodic=True)
beats = gen.generate_beats()                  # 65535 beats
pl.tx_buffer.write_beats(beats)
pl.ctrl.arm_tx(target, len(beats), repeat=10)  # 10 passes back to back, 6.55 ms
```

### 11.10 `rf_clock.pl.beats`

| Name | Description |
| ---- | ----------- |
| `TX_LAYOUT` | DAC format: I and Q interleaved per word (§4.5.8) |
| `RX_LAYOUT` | ADC format: planar `{Q[255:128], I[127:0]}` (§4.6.9); also the default of `BeatLayout()` |
| `BeatLayout(...)` | bit positions of the samples in a beat; `lane_order`, `i_offsets` and `q_offsets` describe other formats |
| `BeatPacker(layout=None)` | `pack_beat`, `pack_samples`, `pack_bytes`; `unpack_beat`, `unpack_beats`, `unpack_bytes`, `unpack_numpy`, `unpack_complex`. Without a layout it uses `RX_LAYOUT`; pass `TX_LAYOUT` to pack TX beats. `pack_samples` packs an integer NumPy array (`(N,)` I or `(N, 2)` I/Q) with array operations, giving the same beats as a list |
| `unpack_complex(data, *, sample_count=None)` | vectorized decoding of a DMA buffer into complex64 `I + jQ`; use it for large windows |
| `pack_tx_beats(samples, layout=None)` | samples → beats, default `TX_LAYOUT` |
| `unpack_rx_beats(...)` | beats → samples, default `RX_LAYOUT` |

### 11.11 `rf_clock.correlator`

Non-coherent delay estimation (the `correlator` of plan section 7), pure NumPy, independent of the board.

| Function | Description |
| -------- | ----------- |
| `correlate(rx, ref)` | complex cross-correlation `c[k] = Σ rx[k+n]·conj(ref[n])`, k = 0 … len(rx) − len(ref), by zero-padded FFT |
| `find_peak(corr, *, guard=16)` | the largest magnitude, refined by a three-point parabola to a fractional position; `guard` samples on each side count as the main lobe, the largest value outside is the sidelobe |
| `estimate_delay(rx, ref, *, guard=16)` | both steps; returns `Peak` |

`Peak` fields: `index` (fractional samples; where the reference starts in rx), `integer_index`, `magnitude`, `peak_to_sidelobe` (and `peak_to_sidelobe_db`), `phase`. For the default RRC waveform the parabolic bias stays below 0.002 samples over the whole fractional range (noise-free simulation).

### 11.12 `rf_clock.twtt` (two-way time transfer, since 1.3.0.7)

One board's side of the two-way time transfer of plan section 4, for M4 (two boards on one FS725, cabled with attenuators: A's DAC_A → B's ADC_B, B's DAC_A → A's ADC_B).

Deprecated: rf-clock 2 (branch `v2-rf-data-transfer`) moves this exchange into the PL: its slot engine runs a 2 ms ping-pong, the PL correlates every window, and the measurements ride in the bursts' measurement packets, paired by `PPS_COUNT` and slot without NTP. This once-a-second exchange, driven by the ARM and carried over Ethernet, stays on the 1.x line for M4 and as the reference v2 is checked against (M4b); it gets fixes, not features.

rf-clock 2's side of it, which `rf_clock.checks.m4c_link` runs (in modules of their own since 2.2.0.9; before, in `m4c_link`):

| Module | Content |
| ------ | ------- |
| `pairs` | the two-way equations of one window from one node's queue (plan section 4.2): `SlotTiming` (`slots_per_second`, `slot_ticks`, `tx_offset`; `tx_fs(slot)`), `pair()` and `Pair` (`m_ab_ns`, `m_ba_ns`, `theta_ns`, `delay_ns`), `Pairer` (a window's OWN entry with the peer packet of the same `PPS_COUNT` and slot, from queue entries or log lines), `SecondTheta` (the node's `second` lines), `StepTheta` (theta per step of an algorithm faster than a second) |
| `control` | `configure_fs725(config, port, emit)`: the node's FS725 checked against the board config and its GA set (on the config's port when `port` is None; `BoardConfigError`, `FS725Error`); `FrequencyControl`: the unit for a run (the 1 pps PLL off, SF handed over from the pot, each second's or step's theta into the algorithm, SF back at the end; with `actuator="fc"`, since 2.3.0.2, the algorithm sets FC instead: the unit at GA 1 for the run, where it stays locked and keeps its 1PPS (GA 0 and LO 0 stop it), FC written every step, its fine DAC brought near the middle by a carry first when short of room, SF untouched, GA back when the steering stops and at the end; with `actuator="pl"`, since 2.5.0.0, the PL takes the steps, below); `SyncedRule`: when SYNCED is set |
| `logs` | an `m4c_link` log read back: `read(path, types)`, `pairs(path)` (line by line, for logs of queue entries), `seconds`, `fc`, and `steering_error(entries, settle_s=300)`, theta less the target from `settle_s` on, by which the reports compare steering runs |

`steer` (below) serves both. θ₀, the steering target, is the shared-reference theta of a pair of boards and a wiring (Δ/2 and the two PPS paths of that reference): it lives in `links/<A's S/N>_<B's S/N>.json`, one entry per wiring with how and when it was measured (links/README.md), and is given as `--steer-target-ns` (`experiments/m4c_v2_link.py` reads it). Examples: `rf_clock.examples.twtt` (development machine) and `rf_clock.examples.pl.04_link_node` (board).

The 1.x modules:

| Module | Content |
| ------ | ------- |
| `timing` | the slots (`SLOTS`: AB 100 ms and BA 600 ms after the PPS edge; an RX window opens `PRE_TICKS` = 1.25 µs before its slot), the record format, `two_way()` (m_AB, m_BA, θ = (m_AB − m_BA)/2, one-way delay (m_AB + m_BA)/2) and `Pairer` (A's and B's records of one PPS edge, matched by system time) |
| `node` | the PL loop and the command: `PL.open()`, then every second, right after the PPS edge, the slots of the node (A transmits in AB and receives in BA, B the reverse; AB does both in both, one board through its own loopback cable), the correlation of each window, one record per second |
| `link` | the second process, started by `node` and never touching the hardware: TCP to the other board (A listens on port 50725, B connects to `--peer` and reconnects), pairing, the log |
| `steer` | steering B's FS725 from B's log (M5): a Kalman filter for θ and the clocks' relative frequency, and the frequency offset SF that brings θ to its target |

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -m rf_clock.twtt.node A --seconds 600                       # first board
$run -m rf_clock.twtt.node B --peer 192.168.2.99 --seconds 600   # second board
$run -m rf_clock.twtt.node AB --seconds 60    # one board through its own loopback cable
```

The log (`twtt_<node>.jsonl`, or `--log`) holds this board's `info` (serial number, VCO bands, MTS T1 capture codes), the other board's `hello`, both boards' records (`local`, `peer`), a `result` for every paired second (`m_ab_ns`, `m_ba_ns`, `theta_ns`, `delay_ns`; null when a slot failed or its correlation peak is below 10 dB PSL) and `event`s (connections, errors). θ is B's clock reading minus A's, without the non-reciprocity Δ. The two boards' records are paired by the system time of their PPS edges, so A and B need NTP-synchronized clocks (§9.1); the command refuses to start otherwise (`--no-ntp-check`). If the link process ends, the PL loop stops. Each record also carries the board's die temperature (`die_c`, the hottest AMS sensor, `rf_clock.pl.board.die_c`). `--save-every N` keeps every Nth second's RX windows as complex64 `<slot>_<count>.npy` in `--save-dir` (default `rx_<node>`), and the record names the file. Threads of their own write them, so that a slow disk cannot hold up the loop (a write to a board's SD card held it for up to 5 s on the night of 2026-10-26/27, and it missed those PPS edges); while 8 writes are still under way a window is dropped and the record names it under `not_saved`. `experiments/m4_twtt.py` runs the nodes on the boards from RF-clock-PC and summarizes the logs; `experiments/m4_channel.py` estimates each direction's channel response from the kept windows.

`rf_clock.twtt.steer` runs on B beside node B, follows its log and steers the FS725 on `--port` (M5: each board on its own reference, only B steered):

```bash
$run -m rf_clock.twtt.steer --target-ns T --log twtt_B.jsonl --port /dev/ttyUSB0
```

Each paired second's θ updates a Kalman filter for θ and the two clocks' relative frequency (`--clock-adev`: the pair's Allan deviation at 1 s, default 2e-11, the FS725's specification; `--theta-sd`: θ's scatter, default 3 ps; a θ more than 5 standard deviations from the prediction is rejected). Every `--interval` seconds (default 10), after `--settle` seconds (default 60), SF is set so that θ's error decays with time constant `--tau` (default 50 s). `--target-ns` is θ's zero-baseline value for this wiring (plan section 8) and has no default. SF is an integer: each correction carries the rounding remainder to the next. It changes by at most `--max-step` (default 100) per correction and is held within `--max-offset` (default 500) of its starting value; when the steering algorithm asks for more for `max_saturated_s` (120 s) in a row, `SteeringError` and SF stays (since 2.0.1.2; before, the first such correction raised it). A change of SF counts in the filter `response_delay_s` after the second that asked for it (`Steering`, default 0, the 1.x model; `m4c_link --steer-response-delay`, default 1.9 s, measured on B's FS725 on 2026-11-03); with `response_lag_s` (`m4c_link --steer-response-lag`, default 0, since 2.2.0.1) the frequency then follows the change with that first-order time constant, as the FS725's frequency lock loop makes it, and the change counts whole once eight time constants have passed; and while it is new the outlier gate widens by the change times `response_sd_s` (0.5 s). `fit_sf_response(counts, theta_ps, at_count)` measures a unit's answer from a step of SF: from per-second thetas around the command (each the mean over its second), the change of theta's rate and the delay after the command at which it acts (a quadratic for the clocks' variation plus a delayed ramp, the delay searched in 0.02 s steps); `m4c_link --sf-step` fits each step and writes `sf_response` lines (B's FS725 on 2026-11-03: 1.34 and 1.42 s after the command, 1.10 and 0.99 ps/s per SF unit). That bound does not show that the loop closes: with both boards on one FS725 (M4) θ does not follow SF and the filter takes the missing response for clock noise; check first that a step of SF changes θ's rate by as much. With the 1 pps PLL on, the command turns it off (SF is ignored otherwise). Before each correction it checks that the PLL is still off and SF still the value it last set (`check_unit`): a restart brings back the pot's frequency and the PLL (with a PPS on the rear input the unit would then lock to it), and another program may have changed SF; then, or if the FS725 stops answering (`FS725Error`), steering stops. `--dry-run` computes without writing, `--replay` runs a recorded log without an FS725. The steering log (`steer_B.jsonl`, or `--steer-log`) has one line per second and one per correction; stopping leaves SF at its last value. The FS725 is shared through the EPG1: the log's `stop` line and the last line printed give the SF at the start, which `rf_clock.fs725 sf` sets back after an experiment. `experiments/m5_steer_sim.py` simulates the loop: θ's residual goes as σ_y(1 s)·√(τ/2) and hardly depends on θ's measurement noise, about 100 ps rms at τ = 50 s for the FS725's specification and 26 ps for a pair at 5e-12.

`PredictiveSteering` (since 2.2.0.2; `predictive_for(target_ps, sf0, loop, late_s=…, step_s=…)`, `m4c_link --steer-mode predictive`) is the other steering algorithm, with the same `step(count, theta_ps)` interface and SF limits. It models the FS725's answer to SF by its frequency lock loop (`loop`: the integrator's gain, the crystal's lag and the dead time of the unit at its GA, from the board config's `fs725` since 2.2.0.8, before that `LOOPS[ga]`, B's unit; `loop_response`, `LoopModel`) and takes its own corrections out of each θ, which leaves θ less B's own corrections (`free_ps` in the steering log). It predicts their frequency about its 60 s mean with an AR model (`AR4`, fitted on a free record; `fit_frequency_ar`), and each second plans the next `moves` changes of SF (default 2) by least squares, holding the predicted θ on the target over `horizon` seconds (default 4) at a cost `lam` (0.05 ps² per unit²) per change. It sends the first change. `m4c_link` refuses to steer when the board config has no loop of the unit at its GA: a model for another gain makes the loop oscillate. With θ over the last 100 slots of each second (`m4c_link --steer-late-slots 100`, `theta_late_ns` on the second lines) it held θ to 27.2-27.9 ps rms on the bench at GA 8, against 33-36 ps for `Steering` at τ 5 s, every 2 s ([predictive steering report](../results/predictive-steering-20261105/REPORT.md)). Since 2.2.0.6 the algorithm and `LoopModel` take a step shorter than a second (`step_s`; `STEP_SETTINGS` holds each step's AR model, horizon, moves, cost and command time: at 0.2 s `AR20_STEP_02`, 20 steps, 2, 0.2, 0.03 s), and `m4c_link --steer-step 0.2` forms θ over the last N slots of each 100-slot step (`StepTheta`, `step` lines), writes SF without a read-back (`FS725.write_frequency`) and checks the unit once a second; `LoopModel` interpolates the loop's phase (rounding to its 20 ms grid made the table jagged, which the 0.2 s algorithm followed) ([two-loops report](../results/two-loops-20261105/REPORT.md)). The loop of another unit differs (A's integrator gain at GA 7 is 1.7 times B's), so each unit's is measured and kept in its board's config: `rf_clock.checks.fs725_loop` (§13) fits it from FC's answer to SF steps (`fit_loop`; FC alone fixes it, without the other board). Since 2.3.0.2 the algorithm can set FC, the crystal's tuning, instead of SF (`predictive_fc(target_ps, fc0, plant, late_s=…, step_s=0.1)`, `LoopModel.crystal`, `lag_response`): with the unit at GA 1 the crystal follows FC through a 1 s RC alone, without the loop's integrator and dead time, so the algorithm has to predict the clocks over about a second less. `FC_STEP_SETTINGS` holds its settings at 0.1 s steps (`AR40_STEP_01`, fitted on B's record at GA 1; 30 steps, 4 changes, cost 0.08 per (ps/s)², scaled by the FS725's gain squared; changes of up to 1000 fine steps), its model is slower than the FS725's response measured (lag × `FC_MODEL_LAG` 1.25, dead time + `FC_MODEL_DEAD_S` 0.1 s: in replay it oscillates once the FS725's dead time exceeds the model's by 0.1 s) ([precision report](../results/precision-20261105/REPORT.md) section 8). Up to 2.4.0.0 `check_fc` checked the unit once a second and waited for each reply, which held the next step's write back by about 100 ms and the one after by about 35 ms. Since 2.4.0.1 `FCWatch` sends one of the same queries right after each step's write and reads its reply right after the next write, without waiting. The node also reads its tick right after each write, and the algorithm counts each change from the moment it left rather than from the model's `command_s` (`PredictiveSteering.written`, `LoopModel.answer_late`, [FC steering report](../results/fc-steering-20261112/REPORT.md)). Since 2.4.0.1 B's board config holds the FS725's answer to FC as measured again on 1183 steps with θ pair by pair, a lag of 0.994 s after a dead time of 23.5 ms from the write, and `FC_STEP_SETTINGS` plans 3 changes at a cost of 0.01 per (ps/s)² with the model's lag 1.1 times the measured one (`FC_MODEL_LAG`) and its dead time 0.02 s longer (`FC_MODEL_DEAD_S`). In replay that holds θ − θ₀ to 2.73 ps rms and to 3.53 ps with the FS725 30 ms later, 10 % faster or slower or 15 % harder or softer, and on the bench to 2.77-2.88 ps against 4.44-4.94 ps for the settings before ([FC response report](../results/fc-response-20261112/REPORT.md)). Since 2.4.0.2 `FC_STEP_SETTINGS` sets the cost of a change to 0.0003 ps² per (ps/s)² instead of 0.01, where ps/s is the change of θ's rate that a change of FC finally gives (0.849 ps/s per fine step on B's unit). `FrequencyControl` keeps FC within the fine DAC's room on either side of FC's start, at least 500 fine steps. With FC kept in that room, the replays on B's GA 1 record neither oscillated nor ran away at any cost down to 0.00001, with the FS725's answer as measured and in each of the six departures from it (dead time, lag and gain changed, some together). At 0.0003, θ − θ₀ over the record's second half was 1.93 ps rms, and 2.50 ps in the worst departure, against 2.73 and 3.53 ps at 0.01. Lower costs gained little more and pushed FC to the edges of its room. On the bench, in six alternating runs of 20 minutes with rf-clock 2.4.0.1 and the cost passed as `--steer-lambda`, θ − θ₀ was 1.88 ps rms in both runs at 0.0003, 1.73-1.84 ps in the two at 0.0001 and 2.81-2.92 ps in the two at 0.01. From 300 s on, FC stayed within −442 and +493 fine steps of its start at 0.0003, but needed up to 686 at 0.0001, more than the 500 the package guarantees, so 0.0003 was chosen (the same report, sections 8 and 9).

Since 2.5.0.0 the predictive steering algorithm through FC can also run in the PL (P13, §4.16), with the FS725's serial line driven from the PL too (P12, §4.15). With `actuator="pl"` (`m4c_link --steer-actuator pl --fs725 pl`) `FrequencyControl` starts as it does for `"fc"`, with the unit at GA 1 and FC read, and its fine DAC brought near the middle first when fewer than 500 fine steps of room are left. It then computes the coefficient tables from the algorithm, loads them into P13 with the step's slots, the late slots, `STEER_WRITE_DELAY` and the prefix `FC<high>,`, and enables P13. From then on the PL takes every step and sends every command itself. The ARM takes each step's `SteerStep` queue entry in `pl_step(entry)`, writes from it the `steer` and `fc_set` lines that its own steps write, and sends one `FCWatch` query a step through P12 in the gaps between commands. If that check finds the unit changed, or P13 reports `STOPPED`, `FrequencyControl` disables P13, writes `steer_stopped` and puts GA back, and the link runs on, as with `"fc"`. In this mode the `fc_set` lines' `written_count` and `late_s` are not read after a write but follow from the PL's fixed timing, the step's end plus `STEER_WRITE_DELAY`. A command that started later than that is flagged `late` in the next step's `steer` line, and its actual start is not logged. `predictive_fc(..., command_s=S)` builds the algorithm's model with each command leaving S seconds after its step's end, which for P13 is `STEER_WRITE_DELAY` rather than the 0.03 s of `FC_STEP_SETTINGS`. On the ARM the command left 22-58 ms after its step's end (2.4.0.1), at a time that varied from step to step. In the PL it leaves on its tick, so the model needs no margin for that variation. On the bench (2026-11-16, [results/pl-steering-20261116](../results/pl-steering-20261116/REPORT.md)) two runs of 20 minutes with the steering in the PL alternated with two of the same algorithm on the ARM, each with the write time it has, the ARM's 18-57 ms after the step's end (1st to 99th percentile) and the PL's 0.2 ms. B's FS725 was on P12 in all four, so the ARM wrote through P12 too. θ − θ₀, over each second's mean θ from 300 s on, was 1.69 and 1.63 ps rms in the PL against 1.94 and 2.05 ps on the ARM. On 2026-11-13 the ARM's steering through the USB serial adapter had left 1.88 ps in both of two runs at the same cost, so the day's clocks may differ as well, which these runs do not separate. Since 2.5.0.1 `FC_STEP_SETTINGS` also holds steps of 0.02 s, for the steering in the PL only (`FC_PL_ONLY_STEPS`; `m4c_link` refuses them with `--steer-actuator fc`, since the ARM's writes leave 17-61 ms after their step's end). θ comes from the last 5 slots (10 ms) of each 10-slot step (`--steer-step 0.02 --steer-late-slots 5`, scenario `steer-pl-20ms` of `experiments/m4c_v2_link.py`). The AR model `AR40_STEP_002` is fitted on 0.02 s steps of B's GA 1 record, the algorithm plans 3 changes over 50 steps (1 s) at a cost of $3 \times 10^{-6}$ ps² per (ps/s)², 100 times less than at 0.1 s, and its model keeps the lag 1.1 times the board config's but adds no dead time (`model_dead_s` 0 in the step's settings, which stands in for `FC_MODEL_DEAD_S`). P13's tables then hold 2000 entries of changes (40 s) and 3000 of the mean (60 s), within `STEER_LMAX` and `STEER_NMAX`, so the bitstream of 2.5.0.0 takes these steps unchanged. `FCWatch` queries the unit after every 5th write only (`every`), 10 queries a second as on 0.1 s steps. On these steps the time at which the ARM reads a step's queue entry varies from step to step and is often later than the next step's end, so P12 often sends its query only after the next step's command, and the FS725 applies each command only at points of a cycle of its own. So `FCWatch` accepts a reply to `FC?` that shows the FC written before the query or any FC written from the query until the reply is read. Without this the first bench run on 0.02 s steps stopped after about a second (2026-11-17). In replay on B's GA 1 record and on R1, with B's response as measured from the PL, these settings left θ − θ₀ at 0.65 and 0.55 ps rms against 1.69 and 1.52 ps for those of 0.1 s steps, and at most 0.84 and 0.71 ps with the FS725's response 30 ms later, 10 % faster or slower or 15 % larger or smaller. They swing FC by up to about 570 fine steps from one step to the next and over its whole room of ±500 fine steps. On the bench on 2026-11-17 they left θ − θ₀ at 0.59 and 0.64 ps rms against 1.64 and 1.59 ps for the settings of 0.1 s steps, in alternating runs of 8 minutes ([results/pl-best-20261117](../results/pl-best-20261117/REPORT.md) section 4.9).

`rf_clock.plmodel.steer` is the model of P13 that defines every step to the bit. `pair_2theta_fs(role_b, slot, t_rx_fs, peer_t_tx_fs, peer_t_rx_fs, *, slot_ticks, slots, tx_offset)` gives a pair's 2θ in fs as P13 forms it, and `step_theta_q16(sum, pairs)` a step's θ in $2^{-16}$ fs, rounded half up. `tables(law, *, table_s=40.0, low0, lo, hi)` turns a `PredictiveSteering` from `predictive_fc` into a `Tables` of P13's coefficients and settings in the PL's fixed point, with change tables of `table_s` seconds (`STEER_L`), and refuses a law whose numbers do not fit 48 bits. `Engine(tables, steps=None).step(theta_q16)` takes one step as P13 does, with `None` for a step without θ, and returns its numbers (`low`, `change`, `S_q56` and `rem_q56` in $2^{-56}$ fine steps, of which the queue entry carries the top bits in $2^{-24}$, `dF_q16` and the flags). `PL/sim/gen_steer_vectors.py` drives `tb_p13_steer.v` with it.

### 11.13 `rf_clock.fs725` (the FS725 rubidium standard)

The FS725 passes every command on its rear DB9 (DCE, a straight-through cable; 9600 baud, 8N1, XON/XOFF, no handshake lines) to the PRS10 inside it and returns its replies; the commands are the PRS10 manual's "RS-232 Instruction Set". The module uses the standard library's `termios`, so it needs no package beyond PYNQ's environment. Since 2.5 the unit can also be connected to the PL's own serial line (P12, §4.15), through the TTL-to-RS-232 module on PMOD1. Then `FS725.open("pl")` opens it through `rf_clock.pl.serial.PLPort`, and every method below works the same (on the bench since 2026-11-16). Every check that opens the PL through `rf_clock.checks.board_common.open_pl`, such as `m4c_link` and `pps_pp_scan`, registers its own PL's line under that name. A command without a PL of its own, such as `rf_clock.fs725 ... --port pl` or `fs725_loop --port pl`, opens the PL first. Either way the bitstream is loaded, which stops whatever ran in the PL, P13 included, so none of them may run while a link runs on the board. Opening the PL needs the environment of §9.1 (`BOARD`, `XILINX_XRT`).

| Method of `FS725` | Command | Meaning |
| ----------------- | ------- | ------- |
| `FS725.open(port)` | `VB0` | open the serial device (default `/dev/ttyUSB0`), or a port registered under that name (`register_port(name, make)`, since 2.5, such as `"pl"`); `log` receives every setting written |
| `query(cmd)` | any `…?` | the reply of one query; anything else is refused |
| `identify()` | `ID?` | model, firmware and serial number |
| `serial()` | `ID?` | the PRS10's serial number (since 2.2.0.8) |
| `status()` | `ST?` | the six status bytes as `Status`: `conditions()` names every set bit, `rb_locked`, `pps_locked`, `command_errors`; reading clears the latched bits |
| `frequency()`, `set_frequency(sf)` | `SF?`, `SF` | the frequency offset in parts in 1e12 (−2000 to +2000, relative to the EEPROM calibration; lost at power-off); setting reads it back and raises `FS725Error` if it did not take |
| `write_frequency(sf)` | `SF` | the same without the read-back or a log entry, for an algorithm that changes SF several times a second (since 2.2.0.6) |
| `write_frequency_control(high, low)` | `FC` | set FC without a read-back, for an algorithm that sets it several times a second at GA 1 (since 2.3.0.2); the fine DAC within 1024 … 3072 (`FC_LOW`), else `ValueError` |
| `phase_lock()`, `set_phase_lock(on)` | `PL?`, `PL` | whether the unit phase-locks to a 1 pps input on its rear BNC; off until the next restart |
| `time_tag()` | `TT?` | when the latest 1 pps input arrived after the 1 pps output, 0 to 999 999 999 ns (None if no new one) |
| `place_pulse(ns)` | `PP` | move the 1 pps output earlier by 1 to 999 999 999 ns |
| `magnetic_dac()` | `MR?` | the 12-bit DAC of the magnetic field, which SF sets |
| `case_temperature()` | `AD10?` | the case temperature, °C |
| `frequency_control()` | `FC?` | the crystal's tuning as the frequency lock loop sets it, `(high, low)`: two 12-bit DACs summed 1000:1, the low step about 1.5e-12 (since 2.2.0.1) |
| `gain()`, `set_gain(ga)` | `GA?`, `GA` | the frequency lock loop's gain, 0 (loop stopped) to 10; 7 is normal (time constant about 2 s), each step up halves it; setting reads it back and holds until the next restart (since 2.2.0.1) |
| `check_config(unit, entry, source)` | `ID?`, `GA?`, `GA` | a function: checks that the unit is the FS725 of a board config's `fs725` entry (§11.1) and sets its GA to the entry's, with a `UserWarning` that names the old and the new gain; returns the loop at that gain, or None; `FS725Error` when the entry is empty or names another unit (since 2.2.0.8) |

The first SF disables the calibration pot until the unit restarts, so SF 0 is not "unchanged": steering starts from what `frequency()` reads. Until then SF reads the pot, which can flicker by a unit (A's read 3 and 2); `m4c_link` therefore sets SF once to the value read at the start of any run that will change it (since 2.2.0.5). SF is ignored while the 1 pps PLL is locked to an input. SF acts through the magnetic field, whose frequency goes as the square of the DAC setting: near the nominal operating point one DAC step is about 4 SF units by the PRS10 manual, 3.4 measured on B's unit (2026-11-03, `magnetic_dac()`). Nothing writes the EEPROM (`!`), changes a calibration (MO, SP, SS, TS, PS, TO, FC, LO, PH, MS, SD) or restarts the unit (RS, RC); GA is set only by `set_gain`: by `check_config` to the board config's gain at the start of the link, by `rf_clock.checks.fs725_loop` while it measures, and by the `gain` command for experiments; a restart brings back the gain in EEPROM (7).

```bash
run="sudo /usr/local/share/pynq-venv/bin/python"
$run -m rf_clock.fs725 status --port /dev/ttyUSB0   # identity, status, SF, PLL, time tag, case temperature
$run -m rf_clock.fs725 align --port /dev/ttyUSB0    # the other node's PPS on the rear 1 pps input
$run -m rf_clock.fs725 sf 37 --port /dev/ttyUSB0    # set SF (read back), e.g. to where steering started
$run -m rf_clock.fs725 gain 8 --port /dev/ttyUSB0   # the frequency lock loop's gain until the next restart
sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.fs725 gain 8 --port pl
                                                    # the same on the PL's serial line (2.5), loading the bitstream first
```

`align` turns the 1 pps PLL off, takes ten time tags and moves the 1 pps output onto the input by `PP` (PP only moves earlier: later by d is earlier by one second minus d), to a few ns, so that the two-way time transfer's RX windows (1.25 µs before the slot) catch the other node's bursts. It moves the PPS against the 10 MHz: check the board's PPS capture margin afterwards (M1), and the margin of the EPG1, which retimes this PPS with a clock locked to the same 10 MHz (everything fed from the EPG1 moves too). The PLL stays off only until the unit restarts: take the other PPS off the rear input once aligned, or a restart locks the unit to it. Settings go to `fs725.jsonl` (`--log`).

### 11.14 The slot engine, the burst memory, the correlator and the queue (since 2.0)

`PL.open()` adds four blocks for §4.8–§4.12; each takes the `PLClient`, and the two memories their AXI BRAM windows (`burst_bram_ctrl`, `queue_bram_ctrl`).

**`pl.slots`: `rf_clock.pl.slots.Slots`** (§4.8)

| Method / property | Protocol | Returns / raises |
| ----------------- | -------- | ---------------- |
| `configure(*, role_b, rx_offset, tx_offset=1000, packets=True, enable=True)` | W32 `SLOT_CONTROL` (off), `SLOT_TX_OFFSET`, `SLOT_RX_OFFSET`, checks `BURST_BEATS` and `RX_SPAN_BEATS`, then `ENABLE` | `ValueError` when the burst would run into the next slot or the window would still be open at the next burst |
| `enable()` / `disable()` | sets / clears `ENABLE` | taken at the next PPS edge |
| `copy_next_window()` | W32 `SLOT_COPY` 1 | set up `pl.rx_dma` for `span_beats` first |
| `status()` | R32 `SLOT_STATUS`, `SLOT_SKIPPED_CNT`, `SLOT_COPY` | `SlotStatus(running, slot, skipped, copy_pending)` |
| `slot_ticks`, `slots_per_second`, `window_beats`, `span_beats` | R32 | int |

`rx_offset_for(peer_tx_offset, arrival_ticks)` is the peer's offset plus the expected arrival less `WINDOW_LEAD_TICKS` (125).

**`pl.burst`: `rf_clock.pl.burst.BurstMemory`** (§4.9)

| Method | Protocol | Returns / raises |
| ------ | -------- | ---------------- |
| `load(prn, taps, *, packet_code=None, guard=10)` | refuses while the engine is on; writes both codes and the taps, then `BURST_PRN_CHIPS`, `BURST_GUARD_CHIPS`; reads the taps back | `BurstMemoryError` when the engine is on or a write was refused; `ValueError` for chips other than ±1, a code too long, not 81 taps, or taps whose phase sum leaves int16 |
| `read_taps()`, `read_prn(count=None)` | memory reads | list / array |

`packet_code=None` loads `rf_clock.plmodel.packet.code_chips(288 × L)`; the taps come from `rf_clock.plmodel.fir.coefficients(amplitude)`. `pack_chips(chips)` / `unpack_chips(words, count)` convert between chips and memory words.

**`pl.correlator`: `rf_clock.pl.tracking.Correlator`** (§4.10)

| Method / property | Protocol | Returns / raises |
| ----------------- | -------- | ---------------- |
| `configure(*, threshold=0.5, lock_min=0.5, quality_offset=0)` | W32 `P8_CONFIG` (fractions in 2^-16), `QUALITY_OFFSET` | `ValueError` outside 0 … 1 − 2^-16 |
| `status()` | R32 `P8_STATUS` and the counters | `CorrelatorStatus(locked, search_busy, waiting, lock_lag, lock_magnitude, lost, relocks, searches, refused)` |
| `search()`, `accept(*, earliest=False)`, `unlock()` | W32 `P8_CONTROL` | — |
| `report()` | `SR_SEQ`, the fields, `SR_SEQ` again (read again on a change) | `SearchReport(seq, pps_count, slot, flags, tick, strongest, earliest, sidelobe)` or `None` before the first search |
| `wait_report(after=0, *, timeout=1.0)` | polls `report()` | the first report with `seq > after`; `WaitTimeout` |
| `lags` | R32 `P8_SEARCH_LAGS` | K |

`Peak(lag, fraction, phase, magnitude)` has `index` and `phase_rad` as `rf_clock.plmodel.correlator.Peak`. `quality_offset(window_samples, reference)` gives `QUALITY_OFFSET` for a reference.

**`pl.queue`: `rf_clock.pl.measurements.MeasurementQueue`** (§4.12)

| Method / property | Protocol | Returns |
| ----------------- | -------- | ------- |
| `read(max_entries=None)` | R32 `QUEUE_HEAD`, `QUEUE_TAIL`, the entries, W32 `QUEUE_TAIL` | list of `OwnMeasurement`, `PeerPacket` and, while P13 steers (2.5), `SteerStep`, oldest first |
| `pending()`, `dropped()`, `clear()` | head and tail, `QUEUE_DROPPED_CNT`, W32 `QUEUE_TAIL` = head | int |
| `entries` | R32 `QUEUE_ENTRIES` | int |

| Class | Fields |
| ----- | ------ |
| `OwnMeasurement` | `seq`, `pps_count`, `slot`, `tick`, `locked`, `window_bad`, `searched`, `lag`, `fraction`, `phase`, `magnitude`, `t_rx_fs`, `quality` (`quality_db`), `window_power` |
| `PeerPacket` | `seq`, `pps_count`, `slot`, `tick`, `version`, `time_epoch`, `quality`, `status` (`peer_locked`, `measurement`, `window_valid`, `overflow`, `pps_locked`, `clocks_locked`), `config_id`, `peer_pps_count`, `peer_slot`, `t_tx_fs`, `t_rx_fs`, `phase` |
| `SteerStep` | `seq`, `pps_count`, `step`, `pairs`, `command_tick`, `index`, `theta_q16` (`theta_ps`), `dF_q16` (`dF_ps`), `S_q24`, `low`, `change`, `remainder_q24`, `old_changes`, `missing`, `settling`, `saturated`, `stopped`, `first`, `late`, `steps` (2.5, §4.16) |

`decode_entry(words)` decodes one 16-word entry. Bringing up the link (§7, the first slots):

```python
pl.burst.load(prn, fir.coefficients(16000))          # the engine off
pl.correlator.configure(quality_offset=quality_offset(43848, reference))
pl.slots.configure(role_b=False, rx_offset=rx_offset_for(1000, arrival_ticks))
report = pl.correlator.wait_report(timeout=3.0)      # the first search, after the next PPS edge
# check report.strongest against the expected one-way delay, then:
pl.correlator.accept()
for entry in pl.queue.read(): ...
```


### 11.15 `rf_clock.pl.acquire` (initial acquisition, since 2.0.1.1)

Plan section 5.2: with its slot engine off, a node finds the peer's PPS on its own time axis from a long window in DDR.

| Function | Does | Returns |
| -------- | ---- | ------- |
| `acquire(pl, prn, taps, *, beats=1_100_000, arrival_ticks=29.0)` | `capture`, `correlation_power`, `find_peaks`, `decode_burst` for every peak, then `locate` | `Acquisition` |
| `capture(pl, beats, lead_ticks=2_000_000)` | one P2 RX window of `beats` through P4 and the S2MM, `lead_ticks` from now | `(start tick, I, Q)` |
| `correlation_power(rx_i, rx_q, ref, chunk=1 << 20)` | \|sum rx[k + n] ref[n]\|² for every lag, FFT blocks in float32 | array |
| `find_peaks(power, ref_size, *, floor_db=10.0, limit=8)` | the strongest peaks, at least a reference apart, down to `floor_db` under the strongest | sorted lags |
| `decode_burst(rx_i, rx_q, lag, ref, cref, prn_chips)` | P8's estimate within two lags of `lag` (exact integer correlation), then P9's despreader with that peak's phase | `Burst(lag, fraction, magnitude, c_i, c_q, packet)`; `packet` None when the CRC fails or the window ends first |
| `locate(window_tick, own_pps_tick, own_pps_count, bursts, arrival_ticks)` | arrival − T_tx − `arrival_ticks` for each decoded burst, folded into ±0.5 s of this node's PPS | `Acquisition(window_tick, own_pps_tick, own_pps_count, bursts, peer_pps_tick, offset_ticks, peer_pps_count, spread_ticks)`, `offset_ns` |
| `place_pulse_ns(offset_ns)` | the FS725 PP argument that moves this node's PPS by `offset_ns` (positive: later), rounded to whole ticks (since 2.0.1.2) | int, or None under half a tick |

The offset is known to within the error of `arrival_ticks`, the assumed one-way delay. Whole ticks, because the PL latches the PPS on its tick: a move by whole ticks moves PPS_TICK by exactly that many and keeps the edge's place within its tick; the rest stays in theta. One acquisition of 11 ms takes about 3 s on the board's ARM since 2.3.0.4, of which 2.2 s is the coarse correlation (single-precision FFT blocks over the four cores, `correlation_power`; 24 s before, of 25 s).

### 11.16 `rf_clock.pl.pps_out` (the output PPS, since 2.1.0.0)

`pl.pps_out` is a `PPSOut` on the registers of §4.13.

| Method | Does |
| ------ | ---- |
| `configure(*, amplitude, offset=MIN_OFFSET, sample=0, width=4, idle=0, enable=True)` | writes the pulse and `ENABLE`, taken at the next second (`idle`: `PPS_OUT_IDLE`, 2.2). `ValueError` for what P2 would refuse: `amplitude` or `idle` outside int16, `sample` outside 0 … 7, `width` outside 1 … 65535, `offset` outside `MIN_OFFSET` (8) … 99 999 999 |
| `enable()`, `disable()` | `ENABLE`, taken at the next second |
| `set_synced(synced)` | `SYNCED`, at once: the validity line may go low |
| `set_frac(frac)`, `frac` | 2.3: `frac` 0 … 65535 writes `PPS_OUT_FRAC` and sets `FRAC`, None clears `FRAC`; taken at the next second. `frac` reads None while `FRAC` is clear. `ValueError` outside 0 … 65535 |
| `status()` | `PPSOutStatus(have_ref, running, flywheel, invalid, pulses, flywheel_pulses, last_tick)` |
| `amplitude`, `offset`, `sample`, `width` | the registers read back (`amplitude` signed) |

```python
pl.pps_out.configure(amplitude=24000, offset=100)  # a pulse 1 us after each second's start
pl.pps_out.set_synced(True)                         # once the link is locked and steered
st = pl.pps_out.status()                            # st.last_tick - PPS_TICK == 100
```

### 11.17 `rf_clock.pl.ref_phase` (the phase of the 10 MHz reference, since 2.4.0.0)

`pl.ref_phase` is a `RefPhase` on the registers of §4.14.

| Method | Does |
| ------ | ---- |
| `read(*, attempts=5)` | the last second kept, `RefPhaseSecond(seq, pps_count, beats, peak, i, q, pps_tick)`, or None before the first; `pps_tick` is the second's PPS_TICK, the latest edge's less `PPS_LAST_PERIOD`; reads `REF_PHASE_SEQ` first and last and again when a second or an edge came in between (`WaitTimeout` after `attempts`) |
| `seq()` | `REF_PHASE_SEQ`: to see a new second without reading the rest |
| `RefPhaseSecond.delay_ns` | τ_ref in [0, 100) ns, against the second's PPS_TICK |
| `RefPhaseSecond.amplitude` | the reference's amplitude in ADC codes |

```python
second = pl.ref_phase.read()
if second is not None and second.beats == 100_000_000:
    print(second.pps_count, second.delay_ns, second.amplitude, second.peak)
```

### 11.18 `rf_clock.pl.serial` (the FS725's serial line, since 2.5.0.0)

`pl.serial` is a `SerialLine` on the registers of §4.15.

| Method | Does |
| ------ | ---- |
| `configure(*, enable=True, bit_ticks=10417)` | writes `SERIAL_CONFIG`. The default is 9600 baud, and `bit_ticks` below 16 gives `ValueError` |
| `enabled` | whether the serial line is on |
| `status()` | `SerialStatus` with the transmitter's state (`line_queued`, `tx_active`, `cmd_pending`, `line_len`), the levels on the two receive pins (`rxd_a` on PMOD1_7, `rxd_b` on PMOD1_3), `rx_level`, the counters (`lines`, `commands`, `received`, `framing_errors`, `overruns`) and the falling edges on each receive pin (`rxd_a_edges`, `rxd_b_edges`, of which the one not 0 belongs to the pin the module is on) |
| `busy()` | whether an ARM message still waits or is being sent |
| `send(data, timeout=2.0)` | sends `data` as ARM messages of up to 32 bytes, each once the one before has left, and gives `TimeoutError` when a message has not left after `timeout` seconds |
| `receive()` | the bytes waiting in the FIFO, which reading takes out |
| `flush()` | drops the received bytes |
| `last_line_tick()` | the tick the last ARM message started |

`PLPort(pl.serial)` is the `rf_clock.fs725.Port` on this line, so `FS725(PLPort(pl.serial))` is the FS725 on the PL's line. It switches the line on and drops old received bytes when it opens. `read_line(timeout)` returns the bytes up to the next carriage return, or None when none came within `timeout`.

```python
from rf_clock.fs725 import FS725
from rf_clock.pl.serial import PLPort

unit = FS725(PLPort(pl.serial))
print(unit.identify(), unit.frequency_control())
print(pl.serial.status().rxd_a_edges, pl.serial.status().rxd_b_edges)  # the module's pin counts, the empty one stays 0
```

### 11.19 `rf_clock.pl.steering` (steering through FC in the PL, since 2.5.0.0)

`pl.steering` is a `PLSteering` on the registers of §4.16.

| Method | Does |
| ------ | ---- |
| `sizes` | `(STEER_LMAX, STEER_NMAX, STEER_PMAX)`, the table entries of this build |
| `load(t, *, step_slots, late_slots, write_delay_ticks, prefix, steps=None)` | writes the settings and the coefficient tables of `t` (`rf_clock.plmodel.steer.tables`, §11.12) and the command's prefix (`FC<high>,`, 1 to 8 bytes, where the register would also take an empty one). With `steps=(size, hold)` it also writes `STEER_STEPS` for FC steps. `ValueError` when the tables do not fit this build or a coefficient does not fit 48 bits, `RuntimeError` while P13 runs |
| `enable(*, role_b=True, steps=False, log=True)` | starts afresh, from the next step with θ and FC from `STEER_FC_START`. `steps` runs FC steps instead of the algorithm, and `log` writes a `QE_TYPE_STEER` entry each step |
| `disable()` | stops at once, so no further command leaves |
| `status()` | `SteeringStatus` with the flags of `STEER_STATUS`, `index`, `low`, `last_change`, `theta_ps`, `missing`, `late_commands`, `last_command_tick`, `pairs` and `saturated` |

`rf_clock.twtt.control.FrequencyControl(actuator="pl")` (§11.12) calls these, so a run needs no direct call.

By hand, with the link running and B's FS725 on the PL's line, it goes as below. `FrequencyControl` also brings the fine DAC near the middle first and puts GA back at the end.

```python
from rf_clock.fs725 import FS725
from rf_clock.pl.serial import PLPort
from rf_clock.plmodel import steer as pm
from rf_clock.twtt import steer

unit = FS725(PLPort(pl.serial))         # switches the serial line on (SERIAL_CONFIG.ENABLE)
unit.set_gain(1)                        # at GA 1 the FS725's own loop barely moves FC
high, low = unit.frequency_control()    # FC's coarse and fine DAC values
plant = pl.board.config.fs725["fc"]     # the FS725's response to FC, from the board config
law = steer.predictive_fc(-21.5, 1000 * high + low, plant, late_s=0.05, step_s=0.1,  # theta0 -21.5 ps
                          command_s=20000e-8)
t = pm.tables(law, low0=low, lo=max(low - 500, 1024), hi=min(low + 500, 3072))  # limits in fine DAC values
pl.steering.load(t, step_slots=50, late_slots=25, write_delay_ticks=20000, prefix=f"FC{high},".encode())
pl.steering.enable(role_b=True)
for entry in pl.queue.read(): ...       # SteerStep entries, one a step
```

## 12. Exceptions and result codes

| Exception | Base | Raised by |
| --------- | ---- | --------- |
| `PLClientError` | `RuntimeError` | base of every protocol error in `client` |
| `ClockUnavailable` | `PLClientError` | clock-status GPIO bit 0 = 0; P2 access refused |
| `IdentityError` | `PLClientError` | wrong `ID`, wrong major, or minor too low |
| `EpochChanged` | `PLClientError` | `TIME_EPOCH` changed |
| `CommandRejected` | `PLClientError` | doorbell rejected; `.operation`, `.result` (the 0x1x codes of §5.2) |
| `WaitTimeout` | `PLClientError` | waiting for DONE timed out |
| `TXBufferError` | `PLClientError` | buffer write out of range or at the wrong time |
| `RXWindowInvalid` | `PLClientError` | `wait_rx`: invalid window (`.flags`, `.result`); discard the measurement |
| `TargetMissed` | `PLClientError` | `wait_tx`/`wait_rx`: `*_ACTUAL` ≠ target (`.target`, `.actual`) |
| `RXDMAError` | `RuntimeError` | DMA bytes ≠ `RX_BEATS × 32` |
| `BringupError` | `RuntimeError` | a power-up step timed out, or an LMX2594 did not lock |
| `BoardConfigError` | `BringupError` | the board config is malformed, is missing where named, or is for another board (§11.1) |
| `RFDCError` | `RuntimeError` | a tile did not start, or an MTS status is not OK |
| `VersionMismatch` | `RuntimeError` | overlay or `BUILD_VERSION` A.B.C differs from the installed rf-clock, or a file does not match the manifest (§14.1) |
| `FS725Error` | `RuntimeError` | `rf_clock.fs725`: no reply, a malformed reply, or a setting that did not take (§11.13) |
| `SteeringError` | `RuntimeError` | `rf_clock.twtt.steer`: the corrections asked for SF beyond its allowed offset for `max_saturated_s` in a row, or the FS725 no longer holds the setting the steering made (§11.12). Since 2.5 also when P13 stopped because the wanted FC lay outside its limits too long (§4.16). With P13, `FrequencyControl.pl_step` raises it and catches it at once, and writes `steer_stopped` |

| Result code (§5.2) | In Python |
| ------------------ | --------- |
| ARMED, RUNNING     | `arm_*` returns |
| DONE, RX_INVALID   | `arm_*` returns (when `*_SEQ` advanced); `wait_rx` raises `RXWindowInvalid` for RX_INVALID |
| REJ_*              | `CommandRejected` |
| FAULT_*            | `arm_tx` or `wait_tx` raises `PLClientError` |

## 13. Board checks (`rf_clock.checks`)

Installed with the package; run on the board as root: `sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.<module> ...`; `--help` lists every option. Records are appended to `m2_runs.jsonl` and `m3_runs.jsonl` in the current directory. If the board hangs, reset it remotely with `tools/jtag_reset_board.tcl` (§9.1).

| Module         | Milestone | Usage |
| -------------- | --------- | ----- |
| `m1_check`     | M1: tick and PPS | `[seconds] [--no-clocks]`; one line per second, PASS/FAIL at the end |
| `m2_tx_timing` | M2: MTS and timed TX | `[bursts] [--offset-ticks N] [--amplitude A] [--waveform prn\|rect\|square] [--scope-nco MHZ]`; one burst after every PPS; the MTS latencies of each run go to `m2_runs.jsonl` |
| `m3_loopback`  | M3: loopback, RX, correlation | `[captures]` measures the loopback delay; `--lane-check` locates single-lane impulses to verify the beat layout; `--stress [--stress-beats N]` tests long windows under DDR load; results go to `m3_runs.jsonl` |
| `mts_dtc`      | board config (§11.1) | `record [--write] [--runs N]` powers up N times (default 6) without the recorded codes and, per converter, clock and tile, takes the window picked most often, and per tile the MTS offset seen most often; `--write` stores both in the board config. `check [--runs N]` powers up with them and reports how far each picked code lies from its record and how many power-ups had other offsets. Run it after `vco_bands scan --write`, which drops the codes (they depend on the bands) |
| `vco_bands`    | board config (§11.1) | `scan [--write] [--label S/N] [--calibrations N]` writes the clocks N times (default 20) and suggests the band each LMX2594's calibration picks most often, so that the chip stays at the calibration's own operating point; it then forces the bands below and above it until one does not lock, to show the margin; `--write` saves the suggestion, the counts, the bands that lock and this board's serial number in the board config. `delays [--write] [--span N] [--runs N] [--captures N]`, with the loopback cable DAC_A → ADC_B: the loopback delay of each band within N (default 2) of the configured one from the carrier phase, over power-ups in separate processes; `--write` saves the band table; records go to `vco_bands_runs.jsonl` |
| `fs725_loop`   | board config (§11.1) | `[--port PORT] --gain GA [--step 200] [--steps 4] [--hold 40] [--write]`: this board's FS725 (on the port the board config records, without `--port`) and its frequency lock loop at gain GA, from FC's answer to steps of SF, alternately up and back, one loop fitted to all steps together (`rf_clock.twtt.steer.fit_loop`; FC alone fixes the loop, so neither the other board nor the link is needed, but no link may use this board's clock meanwhile); `--write` records the unit, its port, GA and the loop in the board config (`fs725`); records go to `fs725_loop_runs.jsonl` (since 2.2.0.8) |
| `m4c_link`     | M4b, M5: the rf-clock 2 link | `--role A\|B [--start-at T --stop-at T \| --start-file PATH [--start-timeout S]]`: one node of the link (`--start-file`, since 2.3.0.3: once its PL is open and its FS725 checked it prints `ready: waiting for the start time in PATH` and takes both times from PATH, `{"start_at": T, "stop_at": T}`, when it appears; it refuses a start time already past; `experiments/m4c_v2_link.py` writes it 2-3 s ahead as soon as both nodes are ready, and stops both when one is not ready within 120 s); the queue entries and one `second` line per second (theta, one-way delay, their scatter) go to `--out` (`--no-entries`: the second lines only), and, since 2.4.0.0, one `ref_phase` line per second P11 keeps (§4.14); protocol tests (`--copy-every`, `--search-every`, `--silent-at`, `--refusal-checks`, `--pause-reads-at`); `--fs725 [PORT]` names this node's FS725, on the port the board config records without PORT (since 2.2.0.9), checked at the start against the board config (`rf_clock.twtt.control.configure_fs725`: the unit it names, GA set to its gain; refused otherwise, since 2.2.0.8); `--acquire [--fs725 PORT]` acquires the peer's PPS first (§11.15) and moves this node's PPS onto it; on B with `--fs725`, `--sf-step N` (each step fitted at the end: the FS725's rate change per SF unit and its delay, `sf_response` lines) and `--steer-target-ns T` (`rf_clock.twtt.steer` on the second lines; SF back to its start at the end unless `--keep-sf`), `--fc-every S` (the FS725's FC read every S seconds, `fc` lines with the time on this node's PPS count, since 2.2.0.1; on either node with `--fs725` since 2.2.0.4, as is `--sf-step`); `--steer-mode predictive [--steer-late-slots N --steer-step S --steer-ar A,B,… --steer-horizon H --steer-moves M --steer-lambda L]` (`PredictiveSteering` with the unit's loop at its GA from the board config, since 2.2.0.2; `--steer-step 0.2` since 2.2.0.6; `--steer-actuator fc [--fc-model-lag S --fc-model-dead S]` with `--steer-step 0.1` since 2.3.0.2: FC instead of SF, the unit at GA 1, the board config's `fs725.fc` made slower for the model, `fc_set` lines, since 2.4.0.1 with the count read right after the write, `written_count`, and how long after its step's end the change left, `late_s`); `--pps-out OFFSET [--pps-out-amplitude A] [--pps-out-idle I]` turns on the output PPS (§4.13, since 2.1.0.0; left on at the end) and, since 2.2.0.4, sets SYNCED by `SyncedRule` (A: link locked and B's packets coming in; B: also steering, with the 60 s rms of θ − θ₀ below `--synced-rms` (100 ps) and mean below `--synced-mean` (30 ps), cleared above `--synced-clear-rms` (200 ps) over 10 s), cleared at the end. `--pps-out-comp` (node B, with `--steer-target-ns`; since 2.3.0.0): the theta-compensated output PPS (`rf_clock.twtt.control.EdgeCompensation`): at `--pps-out-comp-at` of each second (0.94) theta of the newest pairs (`--pps-out-comp-window`, 0.02 s), carried to the next edge by its rate over the last second, sets `PPS_OUT_FRAC` to `--pps-out-base-frac` (32768) plus theta − target over `--pps-out-ps-per-frac` (the scope's calibration; 0.0275 ps by default since 2.3.0.1, board 8873292600AB's, §4.13); `pps_comp` lines; `FRAC` cleared at the end. Since 2.4.0.1 `--steer-mode steps --fc-step-size N --fc-step-hold S` with `--steer-actuator fc` steers nothing but steps FC by N fine steps up, back, down and back, each level held S seconds, through the same path as the steering (`steer.FCSteps`, `experiments/m5_fc_response.py`). Since 2.5.0.0 `--steer-actuator pl --fs725 pl [--steer-write-delay TICKS --steer-table-s S]` runs the same steering, or the FC steps, in the PL (P13, §4.16), with the FS725 on the PL's serial line (P12, §4.15). It needs the options that `fc` needs as well, which are `--steer-target-ns`, `--steer-mode predictive` or `steps`, `--steer-step 0.1` (or, since 2.5.0.1, `0.02`, which only the PL takes), and `--steer-late-slots` above 0. The PL takes every step and sends each command `--steer-write-delay` ticks after its step's end (default 20000, 0.2 ms), the change tables cover `--steer-table-s` seconds (default 40), and the `steer` and `fc_set` lines come from P13's queue entries, with the write times the PL's timing fixes (§11.12). Two `serial` log lines, at the start and at the end, and one when the FS725 does not answer at the start, give P12's counters, among them the falling edges on each receive pin, which show the row of PMOD1 the module is on (§4.15). In this mode `m4c_link` stops P13 and puts GA back also when it ends by an error, Ctrl-C or SIGTERM (`FrequencyControl.stop_pl`), which is how `experiments/m4c_v2_link.py` stops its nodes. A node killed outright leaves P13 steering, until the next `PL.open()`. `experiments/m4c_v2_link.py` runs both nodes |
| `pps_out`      | output PPS (2.1) | `[seconds] [--offset N] [--sample S] [--width W] [--amplitude A] [--idle I] [--synced] [--keep]`: the output PPS on DAC_B (§4.13) and one line per second (where the pulse was presented against its PPS_TICK, the pulse and flywheel counts, the validity line, the die temperature); PASS when every second from the first pulse on has its pulse at PPS_TICK + offset and none came from the flywheel (with `--synced`, the validity line low too); records to `pps_out_runs.jsonl`. The edge itself is measured on the scope |
| `ref_phase`    | 10 MHz phase (2.4) | `[seconds] [--min-amplitude A]`: P11 on ADC_D (§4.14), one `second` line per second kept (PPS_COUNT, PPS_TICK mod 10, beats, peak, amplitude, tau_ref, die temperature) and the run's `record` (MTS, VCO bands, tau_ref's mean, scatter and slope over the whole seconds). PASS when every second after the first is whole and the input is there without clipping. `experiments/m5_ref_phase_restarts.py` runs it on both boards, restart after restart (since 2.4.0.0) |
| `pps_pp_scan`  | PPS sub-tick phase | `--fs725 PORT [--steps 22] [--edges 8] [--no-center]`: PP in 1 ns steps until PPS_TICK moves, which gives where the PPS edge lies before its sampling edge; then PP to the middle of the tick; records to `pps_pp_scan_runs.jsonl` |

M2 and M3 also record the board serial number and both LMX2594's VCO bands. The M1-M3 checks take `--bitstream PATH` (default: the packaged overlay), `--no-version-check` (bitstreams before 1.2), `--no-clocks` (skip the clocks when they were written since boot), `--pps-trig` and `--pps-invert`.

For a scope without the bandwidth for the RF, use `m2_tx_timing --scope-nco`: the DAC is switched to IMR low-pass in Nyquist zone 1 for the run; `--scope-nco 100` moves the output to 100 MHz and `--scope-nco 0` bypasses the mixer so the DAC outputs the I samples; the settings are restored at the end. To measure PPS → burst start, use `--scope-nco 0 --waveform square --offset-ticks 0`: the first edge of the square wave does not depend on any NCO phase (180.00 ns on 2026-10-14, unchanged over five restarts; DAC_A is inverted relative to I, so the first edge falls).

---

# Part III Versions and open items

## 14. Version rules

- `VERSION` minor + 1: registers or bits added only; existing offsets and meanings unchanged, so older software still works.
- `VERSION` major + 1: any change to existing offsets, bit fields, encodings or timing semantics, or a change of the protocol between the nodes on air: a node of one major cannot measure a node of another.
- Software checks the major at start-up and refuses to run on a mismatch; calls that use a 1.x addition check that `VERSION` is at least that 1.x (every 2.x has them).

| Version | Change |
| ------- | ------ |
| 1.0     | first version |
| 1.1     | added `PPS_INPUTS` (0x3C), `PPS_CONFIG` (0x90) |
| 1.2     | added `BUILD_VERSION` (0x94) |
| 1.3     | added `TX_REPEAT` (0x5C) |
| 2.0     | the measurements ride in the bursts: slot engine (0x100), bursts from chips and the burst memory (0x180), correlator and search report (0x200, 0x240), measurement packet (0x300), measurement queue (0x380); register offsets widened to 12 bits; P2's commands refused while the slot engine runs. The 1.x registers keep their offsets and meanings |
| 2.1     | added the output PPS (0x140, P10): one pulse a second on DAC tile 0 (DAC_B), whose stream now comes from the PL, and its validity line on the Pmod header |
| 2.2     | added `PPS_OUT_IDLE` (0x168), the output PPS's level between pulses |
| 2.3     | added `PPS_OUT_FRAC` (0x16C) and `PPS_OUT_CONTROL.FRAC`: the output PPS's edge delayed by a fraction of a sample |
| 2.4     | added `REF_PHASE_*` (0x1C0, P11): the phase of a copy of the 10 MHz reference on ADC_D (ADC tile 0 block 0, whose I stream now goes into the PL) against the tick |
| 2.5     | added `SERIAL_*` (0x400, P12), the FS725's serial line driven from the PL through a module on PMOD1, and `STEER_*` (0x440, P13), the predictive steering algorithm through FC run in the PL, with the queue entry `QE_TYPE_STEER` |

### 14.1 Package and overlay

The overlay ships with the Python package in `src/rf_clock/overlay/`, copied from a build directory by `tools/package_overlay.py`:

| File | Content |
| ---- | ------- |
| `rf_clock.bit`, `rf_clock.hwh` | for PYNQ |
| `rf_clock_hw.xsa` | the hardware platform for Vitis/PetaLinux (includes the bitstream). Not named `rf_clock.xsa`: with an `.xsa` of the same name next to the `.bit`, PYNQ 3.1 takes its XSA branch and fails to load the `.bit` |
| `manifest.json` | `build_version`, the build directory, `build_status` (status.txt, with WNS/WHS), and the sha256 of the three files |

The three binaries are stored with Git LFS (`.gitattributes`). After cloning, run `git lfs install` and `git lfs pull`; otherwise they are pointer files, which the sha256 check of `PL.open()` refuses.

The rf-clock version has four fields, `A.B.C.D` (PEP 440 allows any number), edited by hand in `pyproject.toml`:

| Field | Meaning | Incremented when |
| ----- | ------- | ---------------- |
| A.B   | the register protocol, equal to `VERSION` | `VERSION` changes per §14; reset C and D |
| C     | which bitstream for this protocol | a new bitstream with the same registers; reset D |
| D     | Python-only changes | no rebuild |

Change the version before building; the build script writes it into `BUILD_VERSION`. If A.B differs from `P_VERSION` the build script fails, and `tests/test_abi_sync.py` checks it too.

`PL.open()` checks twice and raises `VersionMismatch` on a mismatch (`check_version=False` turns this off):

1. Before loading: if a `manifest.json` sits next to the bitstream, its `build_version` must have the A.B.C of `importlib.metadata.version("rf-clock")`, and the sha256 of the `.bit` and `.hwh` must match the manifest. Loading straight from a build directory (no manifest) skips this.
2. After loading: `BUILD_VERSION` has the A.B.C of the installed rf-clock.

Only D may differ: a Python-only release keeps using the existing bitstream.

An editable install records the version at install time; on the development machine `uv run` reinstalls it by itself after `pyproject.toml` changes.  On the board rf-clock is installed from a wheel into PYNQ's environment (§9.1), so a new version means a new wheel.

Releasing a new bitstream:

```text
1. pyproject.toml: increment C of the version and reset D (change A.B as in the table
   if the registers changed)
2. RF-clock-PC: vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl \
       -tclargs ~/build/<new directory> ~/ref/RFSoC4x2-BSP/board_files impl
3. python3 tools/package_overlay.py ~/build/<new directory>
   (requires impl complete, timing met, and the status.txt build_version equal to
   pyproject.toml)
4. Commit, then tag and push (below); on the board install the new wheel into PYNQ's
   environment (§9.1)
```

Release: pushing a tag `vA.B.C.D` runs the GitHub Action `.github/workflows/release.yml`. It checks with `tools/release_notes.py` (the tag is `v` + the `pyproject.toml` version; the overlay files are not LFS pointers, match the manifest and have the same A.B.C), runs `uv build`, and creates a GitHub Release with the wheel, the sdist and `manifest.json`. The release notes hold the tag message, the overlay build record, the commits since the previous tag and the install steps.

```text
git tag -a v1.3.0.1 -m "what changed"
git push origin v1.3.0.1
```

The group's Gitea server runs the same steps from `.gitea/workflows/release.yml` (Gitea reads `.gitea/workflows` and ignores `.github/workflows`) on its own runner and creates a Gitea release with the same files through the Gitea API. It also uploads the wheel and sdist to the repository owner's Gitea PyPI registry, which needs an Actions secret `PACKAGE_TOKEN` (an access token with package write access; the job's own token may not write packages). The server is reached at `http://kinabalu2.stdusr.yale.internal/gitea/`, and `actions/checkout` cannot clone from a server under a sub-path (it keeps only scheme, host and port), so the Gitea workflow checks out with git itself since 1.3.0.6; 1.3.0.3-1.3.0.5 have no Gitea release, only GitHub ones. To install, use a Gitea access token that may read packages:

```text
uv pip install --no-deps --index-url http://<user>:<token>@<gitea>/api/packages/<owner>/pypi/simple rf-clock==1.3.0.2
```

Push the tag to each remote that should get a release.

The wheel contains the library, the packaged overlay, the clock register files, `rf_clock.examples` and `rf_clock.checks`; installed on the board (§9.1) it works without the repository.

## 15. Open items

Tracked in `plan/checklist.md` and copied here once settled. The main ones:

- Which of the two 16-bit halves of a beat word is I and which Q (the lane order is confirmed, §4.5.8, §4.6.9), and whether the received baseband is the conjugate of the transmitted one; real BPSK signals are not affected.
- With a loopback cable (DAC → attenuator → ADC): the standard deviation of the loopback delay and its consistency over restarts (M3 acceptance); the behaviour of `PynqS2MMDMA.abort()` on the board.
- Calibrated MTS target latencies, and the sub-tick phase of the PPS edge relative to the tick.

---

# Appendix A Low-level access without `rf_clock.pl`

This appendix explains how the three access paths of Part I work on Linux, and how to use them with PYNQ's own API or C++. The examples are in `rf_clock/examples/native/` and ran on the board on 2026-10-14 (as root, with the overlay loaded and the fabric clock running).

## A.1 How it works

Each IP occupies a range of the PS physical address space (§1.2). CPU reads and writes to that range become AXI transactions to the IP through the PS `M_AXI_HPM0_FPD` port and the SmartConnect. A Linux user process cannot use physical addresses directly; it first `mmap`s that range of `/dev/mem` (needs root). Opened with `O_SYNC`, this gives an uncached device mapping, and each 32-bit read or write is one AXI transaction on the bus. PYNQ's `MMIO` is implemented this way (`/dev/mem` + `mmap` + a numpy uint32 view).

| Path | What is on the other side | How to access it |
| ---- | ------------------------- | ---------------- |
| 32-bit MMIO (P2, clock-status GPIO, DMA registers, RFDC) | registers whose accesses have side effects (doorbells, reading LO latches HI) | one aligned 32-bit word at a time, in protocol order; never cached or merged |
| Memory writes (TX buffer) | a RAM behind an AXI BRAM controller | the same kind of mapping, but without side effects; can be written as a block |
| DMA buffer + DMA registers (RX) | the PL's S2MM as a bus master, writing DDR through the HP0 port | allocate a physically contiguous DDR buffer → start the DMA by writing its registers through MMIO → handle the cache → read ordinary memory |

PYNQ's `allocate()` gets physically contiguous memory from CMA through XRT/zocl and returns a numpy array with `.physical_address`. It is uncached by default; with `cacheable=True` reads are faster, but each read needs an `invalidate()` first.

Measured on the board (2026-10-14):

| Operation | Time |
| --------- | ---- |
| C++ read of one P2 register | about 0.54 µs (one AXI round trip, including the SmartConnect clock crossing) |
| PYNQ `MMIO.read()` | about 7.7 µs (the rest is Python overhead) |
| writing 128 KB of TX buffer with `MMIO.write()` per word | 0.31 s |
| writing 128 KB of TX buffer as one numpy slice assignment | 0.5 ms, reads back correctly |

## A.2 PYNQ's own API (`rf_clock.examples.native.raw_pynq`)

```python
import numpy as np
from pynq import MMIO, allocate

gpio = MMIO(0xA002_0000, 0x1000)          # clock-status GPIO (pl_clk0, always readable)
p2   = MMIO(0xA000_0000, 0x1000)          # P2 registers (fabric)
txb  = MMIO(0xA040_0000, 4 << 20)         # TX buffer (4 MiB window)
dma  = MMIO(0xA001_0000, 0x10000)         # AXI DMA registers

assert gpio.read(0) & 1                   # the fabric clock must run, or P2 hangs the bus

# Registers: one 32-bit word at a time, in protocol order
lo = p2.read(0x20); hi = p2.read(0x24)    # TICK: LO first, which latches HI
tick = (hi << 32) | lo
p2.write(0x40, target & 0xFFFF_FFFF); p2.write(0x44, target >> 32)
p2.write(0x48, nbeats); p2.write(0x08, 0x1)   # shadows first, the doorbell last
assert p2.read(0x4C) & 0xFF == 0x01           # ARMED

# TX buffer: one block (beat_bytes are beats packed with TX_LAYOUT)
words = np.frombuffer(beat_bytes, dtype=np.uint32)
txb.array[:words.size] = words

# RX DMA: allocate contiguous memory → write the DMA registers → then the RX doorbell
buf = allocate(shape=(nbytes,), dtype=np.uint8)
dma.write(0x30, 0x1)                                # S2MM_DMACR.RS
dma.write(0x48, buf.physical_address & 0xFFFF_FFFF) # S2MM_DA
dma.write(0x4C, buf.physical_address >> 32)         # S2MM_DA_MSB
dma.write(0x58, nbytes)                             # S2MM_LENGTH: writing it starts the transfer
# ... write RX_TARGET, RX_LENGTH, then W32 CONTROL 0x4 ...
while not dma.read(0x34) & 0x2: pass                # S2MM_DMASR.Idle
received = dma.read(0x58)                           # bytes received
buf.invalidate()                                    # needed before reading a cacheable buffer
data = np.array(buf[:received])
```

Take the addresses from the HWH (`overlay.ip_dict["p_top_0"]["phys_addr"]` and so on); they are hard-coded here for clarity only.

## A.3 C++ (`rf_clock/examples/native/mmio_demo.cpp`)

```cpp
int fd = open("/dev/mem", O_RDWR | O_SYNC);
auto *gpio = (volatile uint32_t *)mmap(nullptr, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0xA0020000);
auto *p2   = (volatile uint32_t *)mmap(nullptr, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0xA0000000);
if (!(gpio[0] & 1)) return 1;                   // fabric clock not running: leave P2 alone
uint32_t lo = p2[0x20 / 4], hi = p2[0x24 / 4];  // LO before HI
p2[0x40 / 4] = target; p2[0x44 / 4] = target >> 32; p2[0x48 / 4] = nbeats;
p2[0x08 / 4] = 0x1;                              // doorbell
```

Build in the file's directory with `g++ -O2 -o mmio_demo mmio_demo.cpp` and run as root. `volatile` makes every access really reach the bus and keeps the compiler from reordering them.

A DMA buffer in C++ needs physically contiguous memory. Use XRT's C++ API (which PYNQ uses underneath: `xrt::bo`, `bo.address()` for the physical address, `bo.map()`, `bo.sync(XCL_BO_SYNC_BO_FROM_DEVICE)`; the board has XRT 2.17, **not yet tried on the board**), or allocate in Python, keep the control flow in Python and write only the heavy computation as a C++ extension.

## A.4 Notes

1. All of this needs root (`/dev/mem`).
2. Read the clock-status GPIO before touching the fabric domain (P2, TX buffer), in C++ too; otherwise the bus hangs and only a board reset helps.
3. Use only aligned 32-bit accesses on P2: 8/16-bit writes return SLVERR, and a 64-bit write may be split in two, which conflicts with the doorbell ordering.
4. Block writes to the TX buffer must be 4-byte aligned; do not use `memset` or unaligned accesses on the mapping (they may use instructions device memory does not allow, and the process gets SIGBUS). numpy slice assignment works.
5. In C++, `volatile` does not order ordinary memory (the DMA buffer) against device registers: put a barrier (`std::atomic_thread_fence` or `__sync_synchronize()`) between "data written" and "doorbell", and between "DMA finished" and "read the data".
