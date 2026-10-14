# RF-clock

Two RFSoC 4x2 nodes exchange 5.8 GHz PRN bursts (two-way time transfer); the follower steers its SRS FS725 so its 10 MHz and 1PPS follow the master. The ARM runs the algorithms; the PL provides deterministic timing.

The major 2 version code of the python package `rf-clock` must match the PL's major 2 version code. Otherwise it cannot be brought-up.

| Document                                                                                       | Content                                              |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| [AGENTS.md](AGENTS.md)                                                                          | Durable design constraints                           |
| [plan/RF_clock_modules_zh.md](plan/RF_clock_modules_zh.md) ([English](plan/RF_clock_modules.md)) | Design plan                                          |
| [plan/checklist.md](plan/checklist.md)                                                          | Open items, with evidence for each closed one        |
| [docs/ABI.md](docs/ABI.md)                                                                      | ARM ↔ PL interface (registers, timing, procedures)  |
| [plan/handoff_clock_config.md](plan/handoff_clock_config.md)                                    | Next step: LMK04828/LMX2594 register files in Python |
| [plan/handoff_software_interface.md](plan/handoff_software_interface.md)                        | Next step: ARM-side Python driver for the PL         |

## Layout

| Path                                      | Content                                                                                                               |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `PL/src/`                               | PL RTL (`p_top` and P0–P6); `p_regs.vh` / `p_defs.vh` define the ARM-visible interface                         |
| `PL/sim/`                               | iverilog testbenches; run`./PL/sim/run_pl_tests.ps1`                                                                |
| `PL/bd/create_rf_clock_bd.tcl`          | Vivado 2024.1 block design, synthesis and implementation                                                              |
| `PL/constraints/`                       | RFSoC 4x2 constraints                                                                                                 |
| `config/RRC_config.db`                  | RRC coefficients used by the ARM waveform generator                                                                   |
| `src/rf_clock/pl/`                      | ARM ↔ PL Python interface, beat codec, and waveform generator                                                        |
| `src/rf_clock/overlay/`                 | The bitstream (`.bit`/`.hwh`/`.xsa`, Git LFS) shipped with the package; written by `tools/package_overlay.py` |
| `examples/pl/`                          | Commented fake-PL and PYNQ bring-up examples                                                                          |
| `src/rf_clock/`, `tools/`, `utils/` | Python: coefficient generation, ARM waveform interface, and optional RRC diagnostics                                  |
