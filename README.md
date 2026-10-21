# RF-clock

Two RFSoC 4x2 nodes, each with an SRS FS725 rubidium standard, exchange 5.8 GHz PRN bursts (two-way time transfer, TWTT). The follower steers its FS725 so that its 10 MHz and 1PPS follow the master. The ARM runs every algorithm; the PL only provides deterministic time.

English documents are the reference; `*_zh.md` files are Chinese translations with the same content.

## How it works

- **TWTT.** Nodes A (master) and B (follower) send each other known PRN bursts in fixed slots after each PPS. Each node timestamps the other's burst; differencing the two directions cancels the propagation delay and leaves the clock offset, which drives B's FS725 through its RS-232 port.
- **One timebase per node.** The FS725 10 MHz drives the board's LMK04828 and LMX2594, which clock the RFDC converters and the PL. The PL counts a raw tick (100 MHz, 10 ns) on that clock, so steering the FS725 moves the whole timebase of the node.
- **ARM does algorithms, PL does time.** On the ARM (Python): PRN generation, RRC shaping, correlation and delay estimation, TWTT, Kalman filtering, FS725 steering. In the PL: the raw tick, PPS capture, gap-free TX bursts from an on-chip buffer starting at an exact tick, and lossless RX windows into DDR starting at an exact tick, each with its start-tick timestamp.
- **Signal.** BPSK PRN at 100 Mchip/s with an RRC filter (β = 0.25), 8 samples per chip at 800 MS/s complex baseband. The DAC runs at 9.6 GS/s and puts the burst at 5.8 GHz (second Nyquist zone); the ADC samples at 4.8 GS/s. RFDC multi-tile sync makes the converter latency the same on every power-up.

The design is in [plan/RF_clock_modules.md](plan/RF_clock_modules.md); the durable constraints any change must respect are in [AGENTS.md](AGENTS.md).

## Status

| Milestone | Result |
| --- | --- |
| M1: raw tick against the FS725 1PPS | Passed: exactly 100,000,000 ticks per second; 3 hours with no PPS jitter or fault count |
| M2: MTS and PPS-timed TX | Passed: every burst starts at its target tick; on a scope the PPS → burst delay is 180.00 ns on every restart |
| M3: wired DAC → ADC loopback | RX path, DMA bandwidth and correlation verified over on-board crosstalk; the measurement with a cable and attenuator is still to do |
| TWTT between two nodes, FS725 steering | Not started |

Item-by-item results with their evidence: [plan/checklist.md](plan/checklist.md).

## Hardware

Per node:

| Item | Connection |
| --- | --- |
| Real Digital RFSoC 4x2, PYNQ 3.1.1 image | the board's Linux runs the Python package as root |
| SRS FS725 10 MHz | external reference SMA of the 4x2 (LMK04828 CLKin0) |
| SRS FS725 1PPS | PPS SMA J7 (the board's comparator input) |
| RF transmit | DAC_A SMA (`vout20`, DAC tile 2) |
| RF receive | ADC_B SMA (`vin2_01`, ADC tile 2) |
| Loopback test (M3) | DAC_A → SMA cable → ≥ 20 dB attenuator → ADC_B |

Building the bitstream needs Vivado 2024.1 and the [RealDigitalOrg RFSoC4x2-BSP](https://github.com/RealDigitalOrg/RFSoC4x2-BSP) board files.

## Getting started

### On the board

Releases (on GitHub and on the group's Gitea) carry a wheel that contains the library, the bitstream, the clock register files, the examples and the board checks; nothing else from the repository is needed. Install the wheel into PYNQ's own environment as described in [docs/ABI.md §9.1](docs/ABI.md), then, as root:

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -m rf_clock.checks.vco_bands scan --write  # once per board: the board config,
$run -m rf_clock.checks.mts_dtc record --write   # then its MTS capture windows
$run -m rf_clock.checks.m1_check 60             # tick against the PPS
$run -m rf_clock.checks.m2_tx_timing 10         # MTS and timed bursts
$run -m rf_clock.checks.m3_loopback 20          # loopback delay by correlation
$run -m rf_clock.examples.pl.03_pynq_bringup    # one burst and one RX window
```

The board config, `/etc/rf-clock/board.json`, names its board by serial number and fixes the VCO band of each LMX2594 and the SYSREF capture window of each MTS tile, which otherwise can change from one power-up to the next and move a converter by 8-10 ps or by a whole sample; without them `PL.open()` warns ([docs/ABI.md §11.1](docs/ABI.md)).

A new board, from the SD card to the two-way time transfer, step by step with what each step must show: [docs/board_setup.md](docs/board_setup.md).

In your own code:

```python
from rf_clock.pl import PL, WaveformGenerator

pl = PL.open()                                  # overlay, clocks, MTS, PPS lock
beats = WaveformGenerator().generate_beats()
pl.tx_buffer.write_beats(beats)
_, pps = pl.ctrl.wait_next_pps()
target = pps + 100_000_000 + 100_000            # 1 ms after the next PPS
pl.ctrl.arm_tx(target, len(beats))
pl.ctrl.wait_tx(timeout=2.5)                    # returns TX_ACTUAL == target
```

### On a development machine

```bash
uv sync                               # Python environment (pynq is a dev dependency, for completion only)
uv run pytest                         # unit tests against a fake PL, no hardware
uv run python -m rf_clock.examples.pl.01_fake_tx
./PL/sim/run_pl_tests.ps1             # RTL testbenches (PowerShell, iverilog)
```

### Building and releasing

1. Set the version in `pyproject.toml` (rules below).
2. On the build machine: `vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl -tclargs <new build dir> <RFSoC4x2-BSP>/board_files impl`
3. `python3 tools/package_overlay.py <build dir>` copies the bitstream and a manifest into `src/rf_clock/overlay/` (refused unless the build completed with timing met for this version).
4. Commit, then push a tag `vA.B.C.D`: GitHub Actions, and Gitea Actions where the tag is pushed to Gitea, build the wheel and publish a release with notes.

Details in [docs/ABI.md §14.1](docs/ABI.md).

### Versions

The package version `A.B.C.D` binds the Python code to its bitstream:

| Field | Meaning |
| --- | --- |
| A.B | register protocol version (`VERSION` in the PL) |
| C | which bitstream for that protocol |
| D | Python-only releases |

The build writes the version into the bitstream (`BUILD_VERSION`), and `PL.open()` refuses a bitstream whose A.B.C differs from the installed package. A rebuild increments C; a Python-only change increments D.

## Documents

| Document | Content |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Durable design constraints |
| [plan/RF_clock_modules.md](plan/RF_clock_modules.md) ([中文](plan/RF_clock_modules_zh.md)) | Design plan |
| [plan/checklist.md](plan/checklist.md) ([中文](plan/checklist_zh.md)) | Open items, with evidence for each closed one |
| [docs/ABI.md](docs/ABI.md) ([中文](docs/ABI_zh.md)) | ARM ↔ PL programming manual: registers, timing, power-up, the Python library, board setup, releases |
| [docs/board_setup.md](docs/board_setup.md) ([中文](docs/board_setup_zh.md)) | From a new board to one in service: system, installation, wiring, the checks and per-board calibrations in order, what to redo after a change |
| [src/rf_clock/clocks/files/README.md](src/rf_clock/clocks/files/README.md) | Clock plan and the LMK/LMX register files |

## Layout

| Path | Content |
| --- | --- |
| `PL/src/` | PL RTL (`p_top` and P0–P6); `p_regs.vh` / `p_defs.vh` define the ARM-visible interface |
| `PL/sim/` | iverilog testbenches; run `./PL/sim/run_pl_tests.ps1` |
| `PL/bd/create_rf_clock_bd.tcl` | Vivado 2024.1 block design, synthesis and implementation |
| `PL/constraints/` | RFSoC 4x2 constraints |
| `src/rf_clock/pl/` | ARM ↔ PL interface: power-up, registers, TX buffer, RX DMA, beat codec, waveform generator |
| `src/rf_clock/clocks/` | LMK04828/LMX2594 register files and their decoders |
| `src/rf_clock/overlay/` | The bitstream (`.bit`/`.hwh`/`.xsa`, Git LFS) shipped with the package |
| `src/rf_clock/checks/` | Board checks for milestones M1–M3 |
| `src/rf_clock/examples/` | Annotated examples: fake PL, board bring-up, raw PYNQ and C++ access |
| `src/rf_clock/correlator.py` | Cross-correlation delay estimation |
| `src/rf_clock/twtt/` | Two-way time transfer between two boards (M4): slots, the PL loop, the link between the boards |
| `tests/` | Unit tests (pytest, no hardware) |
| `tools/` | ABI generator, overlay packaging, release notes, JTAG board reset |
| `boards/` | A copy of each board's config (`/etc/rf-clock/board.json`): reference and backup |
| `experiments/` | Bench experiments, run on RF-clock-PC: `m<N>_*.py` is a sub-experiment of milestone M<N> (plan section 9), one question each; `m3_common.py` holds what the M3 loopback series share; `epg1.py`, `lnb.py`, `sa_sweep.py` are the bench tools they use (EPG1 PPS distribution, scope .lnb files, SA124B) |
| `.github/workflows/release.yml` | Builds and publishes a GitHub release when a `vA.B.C.D` tag is pushed |
| `.gitea/workflows/release.yml` | The same for a Gitea release, plus an upload to the Gitea PyPI registry (Gitea Actions) |

The bitstream and other binaries are stored with Git LFS: after cloning, run `git lfs install` and `git lfs pull`.
