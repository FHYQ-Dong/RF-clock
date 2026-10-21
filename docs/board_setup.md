# Setting up a new RFSoC 4x2 for RF-clock

Chinese translation: [board_setup_zh.md](board_setup_zh.md).

This is the procedure from a new Real Digital RFSoC 4x2 to a board that takes part in the two-way time transfer: system, installation, wiring, then the checks and per-board calibrations in the order they depend on each other, each with the command and what it must show. The reference for every interface it uses is [ABI.md](ABI.md); the reasons behind the calibrations are in `results/` (each step names its report).

## 0. What is needed

- The board, a micro-SD card of at least 16 GB, its USB cable (JTAG and serial console) and Ethernet.
- The bench host RF-clock-PC: Ethernet `eno1` = 192.168.2.1/24 with the boards on it, NTP for that network (chrony, `/etc/chrony/conf.d/rf-clock.conf`), the repository in `~/Program/RF-clock` with its `.venv`, and Vivado 2024.1 for the JTAG reset.
- The references: the FS725 10 MHz through the Thorlabs EPG1 (REF IN → REF OUT) and a resistive splitter to every board, and the FS725 1PPS through the EPG1 (TRIG IN → one output per board).
- RF cables, 20 dB attenuators and 5725-5875 MHz band-pass filters: one set for the board's own loopback (DAC_A → ADC_B), and one per direction between two boards.
- The release wheel `rf_clock-<version>-py3-none-any.whl` from the GitHub or the group's Gitea release.

The commands below use, on the board:

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
```

## 1. System

1. Write the PYNQ 3.1.1 image for the RFSoC 4x2 to the micro-SD card and boot the board from it.
2. Open its serial console from RF-clock-PC: `/dev/serial/by-id/usb-Xilinx_JTAG+Serial_<JTAG serial>-if01-port0`, 115200 baud, login `xilinx`/`xilinx`. Use `if01`; `if00` is the JTAG channel, never write to it.
3. Change the address before the board joins the shared network: every PYNQ image starts at the static 192.168.2.99, which is taken. Edit `/etc/network/interfaces.d/eth0` and reboot. In use: 192.168.2.99 = 887329260072A, 192.168.2.101 = 887329260101, 192.168.2.100 = another group's board.
4. From RF-clock-PC: `ssh-copy-id xilinx@<address>`, which adds RF-clock-PC's SSH public key to the board's `~/.ssh/authorized_keys` (it asks for the board's password once). From then on ssh needs no password, which the scripts in `experiments/` rely on.
5. NTP from RF-clock-PC, needed to pair the two boards' seconds (ABI.md §9.1):

   ```bash
   sudo mkdir -p /etc/systemd/timesyncd.conf.d
   printf '[Time]\nNTP=192.168.2.1\n' | sudo tee /etc/systemd/timesyncd.conf.d/rf-clock.conf
   sudo systemctl restart systemd-timesyncd
   timedatectl show -p NTPSynchronized --value     # must print yes
   ```

Pass: `ssh xilinx@<address>` works without a password, and NTPSynchronized is yes.

## 2. Installation

rf-clock goes into PYNQ's own environment, where PYNQ's Jupyter and everyone else on the board find it, and takes its only dependency, numpy, from there (ABI.md §9.1):

```bash
sudo /usr/local/share/pynq-venv/bin/python -m pip install --no-deps --no-index rf_clock-<version>-py3-none-any.whl
$run -c "import rf_clock, importlib.metadata as m; print(m.version('rf-clock'))"
```

Pass: it prints the version installed. Install a new wheel the same way; never install rf-clock editable there.

## 3. Wiring

| Signal | From | To the board |
| --- | --- | --- |
| 10 MHz reference | EPG1 REF OUT (square wave, locked to the FS725) → resistive splitter | external reference SMA (LMK04828 CLKin0) |
| 1PPS | an EPG1 output: 10 µs positive pulses, 2.5 V into 50 Ω | J7 |
| RF out | DAC_A (`vout20`) | → 20 dB → band-pass → ADC_B of the receiving board |
| RF in | ADC_B (`vin2_01`) | from DAC_A of the transmitting board, the same way |

The FS725's own 10 MHz sine through the splitter is too weak (the LMK04828 fell into holdover); use the EPG1 square wave. For sections 4.6 and 4.7 the board's own DAC_A goes to its own ADC_B; for section 5 each board's DAC_A goes to the other's ADC_B.

## 4. Checks and calibrations of one board

In this order: each step needs the ones before it. The board config, `/etc/rf-clock/board.json`, collects everything that belongs to this board (ABI.md §11.1); `PL.open()` refuses a config written for another board, so an SD card moved to another board is refused there until section 4 is done on that board. The config lives only on the SD card: after section 4 copy it into the repository as `boards/<S/N>/board.json` and commit it ([boards/README.md](../boards/README.md)).

1. **Time base (M1).** `$run -m rf_clock.checks.m1_check 60`. Pass: every PPS period 100 000 000 ticks, jitter count 0, `pps_locked` at the end, M1 PASS. A constant other period means the board runs without its 10 MHz (99 992 670 ticks, 73 ppm slow, was a disconnected reference); no PPS edges mean nothing at J7.
2. **PPS capture margin.** From RF-clock-PC: `.venv/bin/python experiments/m1_pps_phase_scan.py --pair <address>:<EPG1 output>` (it steps that output's delay). Pass: the PPS edge lies more than about 1 ns from a 10 ns sampling edge at the delay in use. Otherwise set a delay for that output in `experiments/epg1.py pps` (887329260072A's output 2 has 6.6 ns); it takes effect at the EPG1's next RUN restart, which interrupts all its outputs. Report: `results/pps-phase-scan-20261018_203329`.
3. **VCO bands.** `$run -m rf_clock.checks.vco_bands scan --write --label <S/N>` (no cable needed). It writes the clocks 20 times, takes the band each LMX2594 calibrates to most often, finds the bands that lock and writes the board config with the board's serial number. Pass: both suggested bands locked. Every clock write would otherwise let a chip pick another band and move its converter by 8-10 ps (`results/lmx-sync-loopback-20261019`, section 5).
4. **MTS capture windows.** `$run -m rf_clock.checks.mts_dtc record --write`, then `$run -m rf_clock.checks.mts_dtc check`. It powers up six times, takes the SYSREF capture window each tile picks most often and stores its codes. Pass: check reports a largest distance of at most 6 codes. Without the codes a tile whose two windows lie about as far from code 64 can move by a whole sample between power-ups (887329260101's DAC: 104 ps; ABI.md §7.1 step 7).
5. **Timed transmission (M2).** `$run -m rf_clock.checks.m2_tx_timing 10`. Pass: M2 PASS, every TX_ACTUAL equal to its target, both MTS OK with latencies DAC 1896 and ADC 1296.
6. **Loopback (M3)**, with the board's own loopback cable. `$run -m rf_clock.checks.m3_loopback 20`. Pass: M3 PASS (PSL at least 10 dB; about 21 dB with 20 dB of attenuation and the band-pass filter), the delay the same as before for the same cable set (288.06 ns on 887329260072A), standard deviation a few ps.
7. **Band table**, with the loopback cable. `$run -m rf_clock.checks.vco_bands delays --write` (about 20 minutes). It measures the loopback delay of each band within two of the configured ones and stores it in the band table: what a band change would cost if a forced band ever stopped locking. Pass: every band measured, three power-ups within about 0.5 ps.

`PL.open()` warns (Python `UserWarning`) when the config does not set a band or a capture window, when a picked capture code has moved more than 6 codes from its record, or when the codes were recorded with other bands. Each such warning means: redo the step it names.

## 5. Two boards (M4)

With both boards through section 4, both on one FS725, and A's DAC_A → B's ADC_B and B's DAC_A → A's ADC_B, from RF-clock-PC:

```bash
.venv/bin/python experiments/m4_twtt.py pair --seconds 600
```

It starts `rf_clock.twtt.node A` on 192.168.2.99 and `node B` on 192.168.2.101 (edit `BOARDS` in the script for other boards), collects both logs into `results/m4-twtt-pair-<time>/` and summarizes theta and the one-way delay (ABI.md §11.12). Pass: every second paired and valid, theta's standard deviation a few ps, and the same theta after the boards are powered up again. With one FS725 the true clock offset is zero, so this theta is the zero-baseline constant: the non-reciprocity Delta/2 plus the difference of the two boards' PPS paths (an EPG1 delay that moves a PPS edge into the next tick adds 10 ns). Where Delta is stored is not decided yet (plan/RF_clock_modules.md section 8).

## 6. What to redo when something changes

| Change | Redo |
| --- | --- |
| New rf-clock wheel, same A.B.C | nothing (install it, section 2) |
| New bitstream (C or A.B changes) | sections 4.4-4.7 and 5 |
| LMK04828 or LMX2594 register files | sections 4.3-4.7 and 5 |
| A forced VCO band changed by hand | sections 4.4, 4.7 and 5 |
| PPS cable or EPG1 delay | sections 4.1, 4.2 and 5 |
| RF cables, attenuators or filters | sections 4.6 and 5 (4.7 if on the loopback) |
| A `PL.open()` warning about the bands or capture codes | the step it names |
| SD card rewritten (a new PYNQ image) | sections 1 and 2, then put back `boards/<S/N>/board.json` ([boards/README.md](../boards/README.md)), or redo section 4 |

## 7. When something goes wrong

- **The board stops answering** (an access to the PL while its clock is off hangs the AXI bus): from RF-clock-PC, `source /tools/Xilinx/Vivado/2024.1/settings64.sh; xsdb tools/jtag_reset_board.tcl <JTAG serial>`; ssh is back after about 40 s, with PYNQ's base overlay in the PL (ABI.md §9.1).
- **`BoardConfigError`**: the config is for another board (serial number), malformed, or a named file is missing; redo section 4.3 on this board.
- **`BringupError: LMX2594 not locked`**: a forced band does not lock, perhaps at another temperature; redo sections 4.3, 4.4 and 4.7.
- **`BringupError: PL pps_locked not set`**: no PPS at J7, or no 10 MHz (M1 shows which, section 4.1).
- **M3 or M4 with a weak peak** (PSL below 10 dB, results null): cable, attenuator or filter; on M4, which direction says which cable.
