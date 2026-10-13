# AGENTS.md

Durable design constraints for the RF-clock RFSoC FPGA project.

## Architecture constraints

- **H08 PRN throughput:** the PRN generator advances its 16-bit LFSR with one shift/XOR operation and is capable of producing exactly one PRN chip per clock cycle. Do not introduce a multi-cycle PRN-generation assumption or an unnecessary chip-rate throttle.

- **Continuous synchronization waveform:** the PRN chips and the H09 pulse-shaped waveform are the timing-synchronization signal. Once a PRN frame starts, its chip/sample stream must not contain a gap or bubble. Active-frame backpressure is not a legal operating mode.

- **Admission versus fault:** downstream readiness must be established before a frame starts (or the frame must remain in a pre-start state). After the frame starts, H08 → H09 → RFDC must advance at the fixed one-chip/continuous-sample rate. If the downstream cannot sustain that rate, enter `FAULT` and report it; do not pause the current chip and resume later.

- **Handshake terminology:** a `ready` signal may be used for pre-frame admission and command-channel handshakes, but do not present `chip_ready = 0`/sample backpressure during an active frame as a normal buffering strategy. Any proposed FIFO or elastic buffering must preserve the fixed active-frame timing and be justified explicitly.

## Review rule

Future H08/H09 and TX-chain design advice must be checked against the constraints above. Suggestions that rely on active-frame backpressure, inserted bubbles, or a PRN generator slower than one chip per clock are invalid for this project.

## Remote development and synchronization

- **Remote development machine:** `RF-clock-ai`.
- **Vivado environment:** source `/tools/Xilinx/Vivado/2024.1/settings64.sh` before invoking Vivado on `RF-clock-ai`.
- **Syncthing:** except for Git metadata, Python caches, `.venv`, and similar excluded directories, the other source code and documentation are synchronized automatically with `~/Program/RF-clock` on `RF-clock-ai`.
- Do not assume that generated build products or excluded cache directories are synchronized; verify their location on the machine where they are needed.
