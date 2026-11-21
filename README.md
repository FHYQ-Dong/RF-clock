# RF-clock

Two RFSoC 4x2 nodes, each with an SRS FS725 rubidium standard, exchange 5.8 GHz PRN bursts (two-way time transfer, TWTT). The follower steers its FS725 so that its 10 MHz and 1PPS follow the master. The PL keeps time and runs the per-burst measurement chain; the ARM runs the slower algorithms.

New to the project, or to RFSoC and clock measurement: start with the technical report, [docs/technical_report.md](docs/technical_report.md) ([中文](docs/technical_report_zh.md)).

English documents are the reference; `*_zh.md` files are Chinese translations with the same content.

## How it works

- **TWTT.** Nodes A (master) and B (follower) send each other known PRN bursts in fixed slots after each PPS. Each node timestamps the other's burst; differencing the two directions cancels the propagation delay and leaves the clock offset, which drives B's FS725 through its RS-232 port.
- **All of a node's clocks from its FS725.** The FS725 10 MHz drives the board's LMK04828 and LMX2594, which clock the RFDC converters and the PL. The PL counts a raw tick (100 MHz, 10 ns) on that clock, so steering the FS725 moves all of the node's clocks.
- **ARM does algorithms, PL does time and the per-burst measurement chain.** In the PL: the raw tick, PPS capture, the 2 ms slot engine, gap-free TX bursts made from chip sequences through the RRC filter starting at an exact tick, correlation (tracking and search) in every RX window, the measurement packets that carry each node's results to the other inside its bursts, the output PPS, and since 2.5, when chosen, each step of the steering through FC with its command on B's FS725's serial line. On the ARM (Python): the PRN chips and RRC coefficients, clock and RFDC bring-up, acquisition on long windows in DDR, the two-way equations, the steering algorithms that steer B's FS725, and the coefficient tables when the PL steers.
- **Signal.** BPSK PRN at 100 Mchip/s with an RRC filter (β = 0.25), 8 samples per chip at 800 MS/s complex baseband. The DAC runs at 9.6 GS/s and puts the burst at 5.8 GHz (second Nyquist zone); the ADC samples at 4.8 GS/s. RFDC multi-tile sync makes the converter latency the same on every power-up.

The design is in [plan/RF_clock_modules.md](plan/RF_clock_modules.md); the durable constraints any change must respect are in [AGENTS.md](AGENTS.md).

## Status

| Milestone | Result |
| --- | --- |
| M1: raw tick against the FS725 1PPS | Passed: exactly 100,000,000 ticks per second; 3 hours with no PPS jitter or fault count |
| M2: MTS and PPS-timed TX | Passed: every burst starts at its target tick; on a scope the PPS → burst delay is 180.00 ns on every restart |
| M3: wired DAC → ADC loopback | Passed with a cable and attenuator: with both LMX2594 synchronized and their VCO bands forced, the loopback delay holds to 0.1 ps over clock writes and 2.5 hours |
| M4: two-way time transfer between two boards on one reference | θ measured 250 times a second, 0.8 ps per measurement; θ₀ per wiring in `links/` |
| M5: two clocks, B's FS725 steered | θ − θ₀ 5.3–5.6 ps rms per second, steering through FC (2.3.0.2); B's output PPS against A's 9.2 ps rms per second on the scope, the scope's own resolution |
| M6: air link across Long Island Sound | Not started |

Item-by-item results with their evidence: [plan/checklist.md](plan/checklist.md). Results, what limits them and what remains: [docs/technical_report.md](docs/technical_report.md) sections 6–8.

## Hardware

Per node:

| Item | Connection |
| --- | --- |
| Real Digital RFSoC 4x2, PYNQ 3.1.1 image | the board's Linux runs the Python package as root |
| SRS FS725 10 MHz | external reference SMA of the 4x2 (LMK04828 CLKin0) |
| SRS FS725 1PPS | PPS SMA J7 (the board's comparator input) |
| B's FS725 RS-232, for the steering in the PL (2.5) | a TTL-to-RS-232 module (MAX3232, DB-9 male) on J1, the Pmod header, lower row at the PMODB end ([docs/board_setup.md](docs/board_setup.md) section 5.1) |
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
uv run pyright                        # static type check of src, tests, experiments and tools
uv run python -m rf_clock.examples.pl.01_fake_tx
./PL/sim/run_pl_tests.ps1             # RTL testbenches (PowerShell, iverilog)
```

### Building and releasing

1. Set the version in `pyproject.toml` (rules below).
2. On the build machine: `vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl -tclargs <new build dir> <RFSoC4x2-BSP>/board_files impl`
3. `python3 tools/package_overlay.py <build dir>` copies the bitstream and a manifest into `src/rf_clock/overlay/` (refused unless the build completed with timing met for this version).
4. `uv run pytest` and `uv run pyright` pass; commit, then push a tag `vA.B.C.D`: GitHub Actions, and Gitea Actions where the tag is pushed to Gitea, build the wheel and publish a release with notes.

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
| [docs/technical_report.md](docs/technical_report.md) ([中文](docs/technical_report_zh.md)) | Technical report and handoff, for readers new to RFSoC and clock measurement: background, design, bench, running and maintaining it, results, the problems found, open work. `pwsh -NoProfile -File tools/build_report.ps1` makes its PDF in `build/` |
| [AGENTS.md](AGENTS.md) | Durable design constraints |
| [plan/RF_clock_modules.md](plan/RF_clock_modules.md) ([中文](plan/RF_clock_modules_zh.md)) | Design plan |
| [plan/checklist.md](plan/checklist.md) ([中文](plan/checklist_zh.md)) | Open items, with evidence for each closed one |
| [docs/ABI.md](docs/ABI.md) ([中文](docs/ABI_zh.md)) | ARM ↔ PL programming manual: registers, timing, power-up, the Python library, board setup, releases |
| [docs/handoff_2x.md](docs/handoff_2x.md) ([中文](docs/handoff_2x_zh.md)) | Moving from 1.x to 2.x: what changed, how to run the link, the metrics before and after and what improved them |
| [docs/board_setup.md](docs/board_setup.md) ([中文](docs/board_setup_zh.md)) | From a new board to one in service: system, installation, wiring, the checks and per-board calibrations in order, what to redo after a change |
| [docs/debug_overview.md](docs/debug_overview.md) ([中文](docs/debug_overview_zh.md)) | Every problem found on the bench, along the 10 MHz distribution: a diagram of each module marking where the problems sat; what was seen, the cause, the change and the version, with links to the reports |
| [src/rf_clock/clocks/files/README.md](src/rf_clock/clocks/files/README.md) | Clock plan and the LMK/LMX register files |

## Layout

| Path | Content |
| --- | --- |
| `PL/src/` | PL RTL (`p_top` and P0–P13); `p_regs.vh` / `p_defs.vh` define the ARM-visible interface |
| `PL/sim/` | iverilog testbenches; run `./PL/sim/run_pl_tests.ps1` |
| `PL/bd/create_rf_clock_bd.tcl` | Vivado 2024.1 block design, synthesis and implementation |
| `PL/constraints/` | RFSoC 4x2 constraints |
| `src/rf_clock/pl/` | ARM ↔ PL interface: power-up, registers, the board config, the slot engine, bursts from chips, the correlator's tracking and search, the measurement queue, acquisition, the output PPS, the FS725's serial line, the steering in the PL; TX buffer, RX DMA, beat codec, waveform generator |
| `src/rf_clock/clocks/` | LMK04828/LMX2594 register files and their decoders |
| `src/rf_clock/overlay/` | The bitstream (`.bit`/`.hwh`/`.xsa`, Git LFS) shipped with the package |
| `src/rf_clock/checks/` | Board checks for milestones M1–M3, one node of the rf-clock 2 link (`m4c_link`), the per-board calibrations (`vco_bands`, `mts_dtc`, `fs725_loop`, `pps_pp_scan`) and the output PPS on its own (`pps_out`) |
| `src/rf_clock/examples/` | Annotated examples: fake PL, board bring-up, one node of the rf-clock 2 link, the two-way equations and the steering on a development machine, raw PYNQ and C++ access |
| `src/rf_clock/correlator.py` | Cross-correlation delay estimation |
| `src/rf_clock/plmodel/` | Bit-exact models of the PL's measurement chain and of its steering through FC, for the RTL testbenches and to recompute the PL's results on the ARM |
| `src/rf_clock/twtt/` | Two-way time transfer between two boards: rf-clock 2's two-way equations from a node's queue (`pairs`), the node's FS725 and SYNCED (`control`), the steering algorithms that steer B's FS725 (`steer`), the node's log read back (`logs`); the 1.x exchange over Ethernet (`timing`, `node`, `link`) |
| `src/rf_clock/fs725.py` | The FS725 rubidium standard over RS-232: status, frequency offset (SF), loop gain (GA), the crystal's tuning (FC), 1 pps time tags and placement |
| `tests/` | Unit tests (pytest, no hardware) |
| `tools/` | ABI generator, overlay packaging, release notes, version snapshots, results backup, the technical report's PDF, JTAG board reset |
| `boards/` | A copy of each board's config (`/etc/rf-clock/board.json`): reference and backup |
| `links/` | θ₀, B's steering target, per pair of boards and wiring ([links/README.md](links/README.md)) |
| `experiments/` | Bench experiments, run on RF-clock-PC: `m<N>_*.py` is a sub-experiment of milestone M<N> (plan section 9), one question each; `m3_common.py` holds what the M3 loopback series share; `epg1.py`, `lnb.py`, `sa_sweep.py` are the bench tools they use (EPG1 PPS distribution, scope .lnb files, SA124B) |
| `.github/workflows/release.yml` | Builds and publishes a GitHub release when a `vA.B.C.D` tag is pushed |
| `.gitea/workflows/release.yml` | The same for a Gitea release, plus an upload to the Gitea PyPI registry (Gitea Actions) |

The bitstream and other binaries are stored with Git LFS: after cloning, run `git lfs install` and `git lfs pull`.
