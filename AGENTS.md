# AGENTS.md

Durable design constraints for the RF-clock RFSoC FPGA project.

The current design is in `plan/RF_clock_modules.md` (Chinese: `plan/RF_clock_modules_zh.md`). Two nodes exchange 5.8 GHz PRN bursts (two-way time transfer); the follower steers its FS725 so its 10 MHz and 1PPS follow the master.

## Architecture constraints

- **ARM does algorithms, PL does time:** PRN generation, RRC shaping, correlation, delay estimation, TWTT, Kalman filtering and FS725 steering run on the ARM. The PL keeps the raw tick, captures PPS, plays TX bursts and opens RX capture windows at exact ticks, and records timestamps. Do not move DSP into the PL without a measured reason.

- **One coherent timebase per node:** RFDC sample clocks, the PL fabric clock and `raw_tick` are all derived from that node's FS725 10 MHz. Sample rates are integer multiples of the tick rate so sample index ↔ tick is exact. RFDC Multi-Tile Sync is required so datapath latency is identical across power-ups. With RFDC 2.6 on the RFSoC 4x2 this means: ADC and DAC tiles 0 and 2 both enabled, each on its own PLL, block 0 of every tile enabled. Tile 2 carries the signal; tile 0 exists only to satisfy MTS — do not remove it. Do not switch to on-chip clock distribution: a distributing DAC tile is limited to 7.0 GS/s.

- **Gap-free TX bursts from on-chip buffers:** a TX burst is preloaded into PL BRAM/URAM before it is armed and played one beat per clock from `TX_TARGET_TICK` to the end, with no gap or bubble. Active-burst backpressure is not a legal operating mode: if the DAC drops `tready`, enter `FAULT`; never pause and resume. Do not stream TX samples from DDR in the first version.

- **Lossless RX windows to DDR:** RX capture starts at `RX_TARGET_TICK`, is timestamped on the ADC side before any FIFO, and goes through an async FIFO and S2MM DMA to DDR. Windows need not be contiguous, but no beat may be lost inside a window. A FIFO overflow inside a window invalidates that window (one lost measurement), not the whole system: flag and count it, do not enter `FAULT`.

- **Timestamps are tick anchors:** each burst/window has one start tick; per-sample time is `start_tick` plus sample index / Fs. Do not attach per-sample timestamps. Sub-sample timing comes from ARM correlation.

- **Calibrated constants are explicit:** non-reciprocity Δ, PPS sub-tick phase and RFDC latency are measured and stored with a version. Never subtract a guessed constant.

- **What belongs to one board lives in its board config:** `/etc/rf-clock/board.json` (`rf_clock.pl.board_config`, docs/ABI.md §11.1), named by the board's AT24MAC402 serial number; `PL.open()` refuses a config for another board. Each LMX2594's VCO band is forced from it, because the calibrated band can change at every clock write and move a converter by 8-10 ps, and MTS picks its SYSREF capture windows by the codes it records, because the window nearest code 64 can change by a whole sample. Never put one board's value in the package.

- **One register map:** `PL/src/p_regs.vh` (addresses, bit fields) and `PL/src/p_defs.vh` (states, result codes, flags) are the RTL source for the ARM-visible interface; `docs/ABI.md` documents it for software. RTL and testbenches use these names, never raw offsets. Any change updates all three and bumps `VERSION` per `docs/ABI.md` §14.

- **English documents are the reference:** each Chinese translation (`*_zh.md`) is updated in the same change; docstrings and code comments are English.

- **The overlay ships with the package:** `src/rf_clock/overlay/` holds `rf_clock.bit`/`.hwh` and `rf_clock_hw.xsa` (Git LFS) and `manifest.json`, copied from a build directory by `tools/package_overlay.py`. The package version is `A.B.C.D`: A.B equals the register map `VERSION`, C counts bitstreams built for it, D counts Python-only releases. The build script writes it into P2 `BUILD_VERSION`, and `PL.open()` refuses a bitstream whose A.B.C differs from the installed package. Bump C (reset D) before building a new bitstream, and A.B together with `VERSION`; Python-only changes bump D (`docs/ABI.md` §14.1).

## Documents

- **Mechanisms are explained as a complete chain:** a report's phenomenon and mechanism go from the cause to the measured quantity link by link, without skipping a step the reader needs to follow it (for example: VCO band → tuning voltage → LMX2594 output phase → tile sample clock against SYSREF → MTS capture codes and the loopback time). Each link starts from the previous link's effect as its cause ("that output moved, so the tile PLLs locked to it move the sample clocks …"), rather than listing facts side by side. Say which links were measured and which are inferred.
- **A verified finding is written in one order, so that it reads straight through:** the phenomenon; the background knowledge needed to follow it; the complete causal chain that produces it; the experiments that test that chain; their results.
- **Markdown is not hard-wrapped:** each paragraph and each list item is one line, broken only where it ends; the renderer wraps. Tables, code blocks and headings are written as usual.

## Review rule

Design advice must be checked against the constraints above. Suggestions that rely on active-burst backpressure, inserted bubbles, dropped beats inside an RX window treated as valid data, DDR-streamed TX in the first version, or clocks not derived from the node's FS725 are invalid for this project.

## Remote development and synchronization

- **Remote development machine:** RF-clock-PC, reached from the development machine as the ssh alias `RF-clock-PC-ai` (an SSH key for AI tools). Documents call it RF-clock-PC.
- **Vivado environment:** source `/tools/Xilinx/Vivado/2024.1/settings64.sh` before invoking Vivado on `RF-clock-PC`.
- **Syncthing:** except for Git metadata, Python caches, `.venv`, and similar excluded directories, the other source code and documentation are synchronized automatically with `~/Program/RF-clock` on `RF-clock-PC`.
- Do not assume that generated build products or excluded cache directories are synchronized; verify their location on the machine where they are needed.
