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
| Clock status   | AXI GPIO (`pl_clk0` domain)                      | Whether the fabric MMCM is locked        | 32-bit MMIO read (§4.7)                      |

### 1.2 Addresses

The base addresses are fixed in `PL/bd/create_rf_clock_bd.tcl`. Software reads them from the HWH (PYNQ `overlay.ip_dict` / `overlay.mem_dict`) rather than hard-coding them.

| Address space      | HWH name                       | Base        | Size                                          | Clock domain |
| ------------------ | ------------------------------ | ----------- | --------------------------------------------- | ------------ |
| P2 registers       | `p_top_0`                      | 0xA000_0000 | 4 KiB (first 256 B used)                      | fabric       |
| AXI DMA registers  | `rx_dma`                       | 0xA001_0000 | 64 KiB                                        | pl_clk0      |
| Clock-status GPIO  | `clk_status_gpio`              | 0xA002_0000 | 4 KiB                                         | pl_clk0      |
| RFDC registers     | `rfdc`                         | 0xA004_0000 | 256 KiB                                       | pl_clk0      |
| TX waveform buffer | `tx_bram_ctrl` (`mem_dict`)    | 0xA040_0000 | 4 MiB window; the first 69632 beats (2.125 MiB) have storage | fabric |

> **Warning: bus hang.** The fabric clock comes from the LMK04828 PL_CLK
> through an MMCM. An access to P2 or the TX buffer while the fabric clock is
> not running never completes and hangs the whole system until a reset
> (remotely: `tools/jtag_reset_board.tcl`). **Before every access to these two
> address spaces, check that bit 0 of the clock-status GPIO is 1** (§4.7). The
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
| 0x04    | RO     | [31:16] major, [15:0] minor | 0x00010003 (1.3) |

```text
R32 0x04  → 0x00010003
```

Refuse to run when major differs from the one the software supports. `PPS_INPUTS` and `PPS_CONFIG` need minor ≥ 1, `BUILD_VERSION` needs minor ≥ 2, `TX_REPEAT` needs minor ≥ 3 (§14). Python: `PLClient.validate()`, `DeviceInfo.version`.

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

Handled only when the TX state is IDLE or DONE. Checks, in order: `time_valid` → length and repeat count → stale target. The first failing check gives its reject code and the state does not change. Only the shadow values at the doorbell cycle count; changing the shadows afterwards does not affect an accepted command. With a near target, the command may already be RUNNING or even DONE when `TX_RESULT` is read (tell by whether `TX_SEQ` advanced). Python: `PLClient.arm_tx(target, beats, *, repeat=1)`.

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

Handled only when the RX state is IDLE or DONE. Checks, in order: `time_valid` → length → stale target → DMA ready. Python: `pl.ctrl.arm_rx(target, beats)`, after `pl.rx_dma.prepare(beats)` (§11.8).

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
    phase to the FS725 10 MHz and PPS after every power-up. Each LMX2594 whose VCO band the
    board config forces (§11.1) is then rewritten with R8 VCO_CAPCTRL_FORCE = 1 and the band
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
 7. MTS on DAC, then ADC (tiles 0 and 2) with fixed target latencies DAC 1896 and ADC 1296, so
    the data-path delay does not depend on the bitstream; stop if the status is not OK or a
    tile does not land on the target. Each tile captures SYSREF in the window of its
    capture delay (DTC) nearest the code the board config records for it (since rf-clock
    1.3.0.8; without a record the window nearest code 64, which can change by a whole sample
    from one power-up to the next where two windows lie about as far from 64). Then restart
    the NCO phases of all four tiles at one SYSREF edge (PG269 "Use Case 1"), so the carrier
    phase is the same after every power-up (since rf-clock 1.3.0.5; before, it took one of
    three values 120° apart)
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
| 0x04   | `VERSION`         | RO     | 0x00010003 | [31:16] major, [15:0] minor | §4.1.2 |
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
- Both boards' system clocks follow RF-clock-PC by NTP (chrony there serves 192.168.2.0/24; on the boards `/etc/systemd/timesyncd.conf.d/rf-clock.conf` sets `NTP=192.168.2.1`). `rf_clock.twtt` needs it to pair the two boards' seconds (§11.12); the boards have no clock of their own across a power cycle.
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
| `rf_clock.twtt`        | two-way time transfer between two boards (§11.12) |
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
| `board`      | `BoardSetup`         | `serial`: the board's AT24MAC402 serial number; `config`: its board config or `None`; `lmx_vco`: per `"adc"`/`"dac"` the LMX2594's VCO read back (`band`, `forced`, `locked`, `capctrl`, `vco_sel`, `daciset`, `ld_vtune`), empty after `write_clocks=False` |

#### `PL.open(bitstream=DEFAULT_BITSTREAM, *, write_clocks=True, mts=True, pps_source_trig=False, pps_invert=False, ref_tile=0, dac_target_latency=DAC_TARGET_LATENCY, adc_target_latency=ADC_TARGET_LATENCY, check_version=True, board_config=None, progress=None)`

Runs every power-up step of §7.1. The fixed hardware choices are defaults: the packaged overlay `rf_clock/overlay/rf_clock.bit` (§14.1); the clock files `LMK04828_100.0.txt` and `LMX2594_480.0.txt` from `rf_clock/clocks/files/`, written through xrfclk (no copies in xrfclk's directory needed); `pl_resetn0`; RFDC tiles 0 and 2 started and synchronized.

| Parameter                         | Use |
| --------------------------------- | --- |
| `write_clocks=False`              | skip LMK/LMX when they were written since boot |
| `mts=False`                       | no RFDC start-up and MTS (time base only, as for M1) |
| `pps_source_trig`, `pps_invert`   | written to `PPS_CONFIG` (§4.4.5) |
| `ref_tile`, `*_target_latency`    | MTS parameters (§11.6); targets default to DAC 1896 and ADC 1296 (§7.1); -1 aligns to the largest measured latency |
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
| `mts_dtc` | written by `rf_clock.checks.mts_dtc` (§13): the SYSREF capture codes MTS picks its windows by (§7.1 step 7), per converter (`dac`, `adc`) and clock (`t1`; `pll` on the ADC), one per tile 0-3 (-1 unused), and `recorded` (time, power-ups, the VCO bands they belong to). `PL.open()` warns when a picked code is more than 6 codes (about 32 ps) from its record, when the record was made with other VCO bands, or when there is none |
| `band_table` | written by `rf_clock.checks.vco_bands` (§13): per chip how often the calibration picked each band (`calibrated`), the bands that lock (`locked`) and the DAC → ADC loopback delay of each band in ps (`loopback_ps`), relative to both chips at `measured.reference`; with the date and die temperature of the scan (`scanned`) and of the measurement (`measured`) |

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

#### `PLClient(regs, clock_status, *, clock_status_offset=0, clock_locked_mask=1, expected_major=1)`

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
| `pps_inputs()` | R32 `PPS_INPUTS` | `PPSInputs`; `IdentityError` if ABI minor < 1 |
| `set_pps_config(*, source_trig=False, invert=False)` | W32 `PPS_CONFIG` | same |
| `build_version()` | R32 `BUILD_VERSION` | `"A.B.C.D"`; `IdentityError` if ABI minor < 2 |

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

#### `setup_rfdc_and_mts(overlay, *, rfdc_name="rfdc", tiles=(0, 2), signal_tile=2, ref_tile=0, dac_target_latency=-1, adc_target_latency=-1)`

Steps 6–7 of §7.1: waits for the tiles, runs MTS on the DAC and then the ADC, restarts the NCO phases at a SYSREF edge (`sync_nco_phase`, only if both MTS succeeded), and reads back the mixer settings of the signal tile. Usable directly as the `rfdc_setup` of `Bringup`.

- Returns `RFDCSetupResult(dac, adc, started_after_startup_call, mixers, nco_synced)`.
- Raises `RFDCError` if either MTS status is not OK; its `.result` still holds the result, so the latencies can be logged.
- `*_target_latency = -1` aligns to the largest measured latency, which changes with the bitstream; `PL.open()` passes the fixed `board.DAC_TARGET_LATENCY` = 1896 and `board.ADC_TARGET_LATENCY` = 1296. A target below a tile's measured latency makes MTS report `TARGET_LOW`; then raise the target and recalibrate the loopback delay.

| Other functions | Description |
| --------------- | ----------- |
| `run_mts(rfdc, kind, tiles=(0, 2), *, ref_tile=0, target_latency=-1)` | `kind` is `"DAC"` or `"ADC"`; returns `MTSResult` |
| `sync_nco_phase(rfdc, tiles=(0, 2))` | PG269 "Use Case 1": with the SYSREF receivers off, sets each block-0 mixer's event source to SYSREF and arms ResetNCOPhase; turns the receivers on for one SYSREF edge (200 ns, whole NCO periods) and off again; sets the event sources back so later `UpdateEvent` calls work. Raises `RFDCError` if `XRFdc_MTS_Sysref_Config` fails |
| `start_tiles(rfdc, tiles=(0, 2), *, settle=1.0, timeout=5.0)` | waits for TileState 0xF; calls StartUp if not started after `settle` s; returns whether StartUp was called |
| `tile_states(rfdc)` | `{"ADC": [4 TileStates], "DAC": [...]}` |
| `rfdc_driver(overlay, name="rfdc")` | the `xrfdc.RFdc` driver; built from the HWH if `xrfdc` was not imported before the overlay loaded |
| `decode_mts_status(status)` | splits an MTS status into names, for example `["TARGET_LOW"]` |

`MTSResult` fields: `kind`, `status`, `tiles`, `ref_tile`, `target_latency`, `latency` (tiles 0–3), `offset` (tiles 0–3), `dtc` (the SYSREF capture delay codes the driver picked, 0–127, and the number of capture windows it found, per tile, for the PLL and the T1 clock: `pll_code`, `pll_windows`, `t1_code`, `t1_windows`; they follow the tile clock's phase to SYSREF, which `latency` and `offset` do not); `describe()` gives one line. `ok` requires status 0 and every tile on the target (`on_target`): the driver adds delay in whole steps (6 for both DAC and ADC), so a target that is not a whole number of steps away would miss without an error.

Measured on the board (2026-10-14): without targets, DAC tiles 0/2 have latency 1848 and the ADC 1236 (run4) or 1248 (run6), unchanged across restarts of one bitstream. With targets DAC 1896 and ADC 1296, the crosstalk loopback delay on run4 and run6 is 226.05 and 226.02 samples: 0.03 samples apart, within the standard deviation (0.13–0.16 samples).

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

| Module | Content |
| ------ | ------- |
| `timing` | the slots (`SLOTS`: AB 100 ms and BA 600 ms after the PPS edge; an RX window opens `PRE_TICKS` = 1.25 µs before its slot), the record format, `two_way()` (m_AB, m_BA, θ = (m_AB − m_BA)/2, one-way delay (m_AB + m_BA)/2) and `Pairer` (A's and B's records of one PPS edge, matched by system time) |
| `node` | the PL loop and the command: `PL.open()`, then every second, right after the PPS edge, the slots of the node (A transmits in AB and receives in BA, B the reverse; AB does both in both, one board through its own loopback cable), the correlation of each window, one record per second |
| `link` | the second process, started by `node` and never touching the hardware: TCP to the other board (A listens on port 50725, B connects to `--peer` and reconnects), pairing, the log |

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -m rf_clock.twtt.node A --seconds 600                       # first board
$run -m rf_clock.twtt.node B --peer 192.168.2.99 --seconds 600   # second board
$run -m rf_clock.twtt.node AB --seconds 60    # one board through its own loopback cable
```

The log (`twtt_<node>.jsonl`, or `--log`) holds this board's `info` (serial number, VCO bands, MTS T1 codes), the other board's `hello`, both boards' records (`local`, `peer`), a `result` for every paired second (`m_ab_ns`, `m_ba_ns`, `theta_ns`, `delay_ns`; null when a slot failed or its correlation peak is below 10 dB PSL) and `event`s (connections, errors). θ is B's clock reading minus A's, without the non-reciprocity Δ. The two boards' records are paired by the system time of their PPS edges, so A and B need NTP-synchronized clocks (§9.1); the command refuses to start otherwise (`--no-ntp-check`). If the link process ends, the PL loop stops. `experiments/m4_twtt.py` runs the nodes on the boards from RF-clock-PC and summarizes the logs.

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
| `mts_dtc`      | board config (§11.1) | `record [--write] [--runs N]` powers up N times (default 6) without the recorded codes and, per converter, clock and tile, takes the window picked most often; `--write` stores its codes in the board config. `check [--runs N]` powers up with them and reports how far each picked code lies from its record. Run it after `vco_bands scan --write`, which drops the codes (they depend on the bands) |
| `vco_bands`    | board config (§11.1) | `scan [--write] [--label S/N] [--calibrations N]` writes the clocks N times (default 20) and suggests the band each LMX2594's calibration picks most often, so that the chip stays at the calibration's own operating point; it then forces the bands below and above it until one does not lock, to show the margin; `--write` saves the suggestion, the counts, the bands that lock and this board's serial number in the board config. `delays [--write] [--span N] [--runs N] [--captures N]`, with the loopback cable DAC_A → ADC_B: the loopback delay of each band within N (default 2) of the configured one from the carrier phase, over power-ups in separate processes; `--write` saves the band table; records go to `vco_bands_runs.jsonl` |

M2 and M3 also record the board serial number and both LMX2594's VCO bands. The M1-M3 checks take `--bitstream PATH` (default: the packaged overlay), `--no-version-check` (bitstreams before 1.2), `--no-clocks` (skip the clocks when they were written since boot), `--pps-trig` and `--pps-invert`.

For a scope without the bandwidth for the RF, use `m2_tx_timing --scope-nco`: the DAC is switched to IMR low-pass in Nyquist zone 1 for the run; `--scope-nco 100` moves the output to 100 MHz and `--scope-nco 0` bypasses the mixer so the DAC outputs the I samples; the settings are restored at the end. To measure PPS → burst start, use `--scope-nco 0 --waveform square --offset-ticks 0`: the first edge of the square wave does not depend on any NCO phase (180.00 ns on 2026-10-14, unchanged over five restarts; DAC_A is inverted relative to I, so the first edge falls).

---

# Part III Versions and open items

## 14. Version rules

- `VERSION` minor + 1: registers or bits added only; existing offsets and meanings unchanged, so older software still works.
- `VERSION` major + 1: any change to existing offsets, bit fields, encodings or timing semantics.
- Software checks the major at start-up and refuses to run on a mismatch; calls that use a 1.x addition check the minor.

| Version | Change |
| ------- | ------ |
| 1.0     | first version |
| 1.1     | added `PPS_INPUTS` (0x3C), `PPS_CONFIG` (0x90) |
| 1.2     | added `BUILD_VERSION` (0x94) |
| 1.3     | added `TX_REPEAT` (0x5C) |

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
