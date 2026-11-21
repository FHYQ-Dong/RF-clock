# First over-the-air tests with the two panel antennas (M6, 2026-11-18)

**Question.** Do the two antennas that arrived on 2026-11-18 work at 5.8 GHz, and does the link between the two boards stay reciprocal over the air, so that θ does not change when only the air path between them changes?

**Background.** Two RFSoC 4x2 boards, A (S/N 887329260072A, 192.168.2.99) and B (S/N 8873292600AB, 192.168.2.100), each form a node. They take turns sending a radio burst at 5.8 GHz, each node for half of a 2 ms cycle, and compare their clocks from the bursts' arrival times (two-way time transfer). Up to now the boards are joined by cables, one per direction, each with a 20 dB attenuator and a band-pass filter ([board_setup.md](../docs/board_setup.md) section 3). M6 replaces the cables with an air link ([plan/RF_clock_modules.md](../plan/RF_clock_modules.md) section 9). The two-way transfer cancels the path between the boards only if both directions see the same path, and over the air the path also holds reflections from the room that a cable does not have. The steps below go from the simplest test to the full link, and each rules out one kind of fault before the next starts. The link script writes each run into `results/m4c-v2-<scenario>-<time>/`. For these tests the runs and the notes taken at the bench are collected in `results/m6-antennas-<date>/`, with a report once the tests are done.

## 1. Terms

- **θ** is the time of B's clock against A's, in ps. More exactly, it is the reading of the 100 MHz clock in B's programmable logic (the PL) less that of A's, as the link measures it from each pair of bursts, one burst each way, a pair every 4 ms.
- **θ₀** is the θ the link shows when both boards run on one FS725, so that their true offset is zero. It is half the non-reciprocity Δ plus the difference of the two boards' PPS cables from that FS725. On the cabled link it is −21.5 ps ([links/README.md](../links/README.md)).
- **Δ, the non-reciprocity,** is the difference between the delays of the two directions. Parts that both directions pass cancel in it, and parts that only one direction passes do not.
- **The one-way delay** is a burst's time of flight from one board to the other, the run summary's `delay_ns`.
- **The tracker quality** is the power of the correlation peak against the noise power in the receive window, in dB, per node, the run summary's `quality_db`.
- **The measurement packet** is the data each burst carries after its spreading code. A node sends back in it what it measured of the other's burst.
- **Steering** adjusts B's FS725 so that θ stays at θ₀. What it leaves is given as the rms of the one-second means of θ − θ₀ from 300 s after it starts.
- **DAC_A and ADC_B** are the names of two connectors on every board, the transmitter's output and the receiver's input. They have nothing to do with the nodes A and B.

## 2. The antennas

The label reads L-com, model HG4958-19DP, a flat panel antenna with two N-type female ports, H POL and V POL, one for each of two crossed polarizations. Its band is 4.9-5.85 GHz and its nominal gain 19 dBi. The value used below, 17.2 dBi at 5.85 GHz, was read off the measured curve of L-com's datasheet, which is not yet in `hw/ref/` and is to be downloaded there. The model, the gain, the beamwidth, the return loss, the isolation between the two ports of one panel, the cross-polarization rejection between two panels and the panel's size are all to be checked against it. The panels measure about 0.3 m on a side.

## 3. What to expect

**The signal.** The burst is spread at 100 Mchip/s with a root-raised-cosine filter of roll-off 0.25, so it occupies 125 MHz, 5.7375-5.8625 GHz. The antenna's band ends at 5.85 GHz, so the top 12.5 MHz of the burst's edge lies just above it, which costs little.

**Power and licensing.** The DAC puts out at most about −14 dBm at 5.8 GHz, a continuous tone at full scale ([DAC spectrum report](../results/dac-spectrum-20261017/REPORT.md)), and the link's bursts less. With the antenna's gain the equivalent isotropic radiated power is at most about 0 dBm, no concern for exposure. The DANGER label on the panels is meant for high-power use. The US unlicensed band ends at 5.850 GHz, though, and our burst reaches 5.8625 GHz. The plan lists the regulations as open ([plan](../plan/RF_clock_modules.md) section 10, item 6). Before the first step that radiates, ask the university's office for radio licensing whether these tests need an experimental license.

**Distance.** The far field starts at about $2D^2/\lambda$, with D the antenna's largest dimension and λ = 5.17 cm at 5.8 GHz. The panel's diagonal of about 0.42 m gives about 7 m. Closer than that the antennas' gain is not fully formed, and the received power can come out a few dB off the budget below.

**Loss from one board's DAC_A to the other's ADC_B.** The cabled link loses about 21.5 dB, the 20 dB attenuator, 0.8 dB in the ZVBP-5800-S+ filter ([BPF report](../results/bpf-compare-20261022/REPORT.md)) and some cable. The air link of section 4 loses two passes through splitters, two filters, about 1 dB in each antenna cable, and the free-space loss $20\lg(4\pi d/\lambda) = 20\lg d + 47.7$ dB (d in m) less the two antennas' 34.4 dB of gain.

| Splitter in each node | 4 m | 7 m | 10 m |
| --- | --- | --- | --- |
| Wilkinson, about 3.5 dB per pass | 36 dB | 41 dB | 44 dB |
| Resistive, 6 dB per pass | 41 dB | 46 dB | 49 dB |

**Tracker quality.** On 2026-11-03 the cabled link gave a tracker quality of 45 dB at the link's default amplitude of 16000 ([plan](../plan/RF_clock_modules.md) section 10, item 3). The quality includes the gain of correlating over the 4096 chips of the spreading code, 36.1 dB, so the signal-to-noise ratio of one chip, $E_s/N_0$, is the quality less 36.1 dB. One bit of the measurement packet spans 64 chips, which adds 18.1 dB, so the ratio of energy per bit to noise, $E_b/N_0$, is the quality less 18 dB. The plan asks for $E_b/N_0$ of about 10 dB, a bit error rate of about 1e-5 before the packet's CRC check, so a quality of at least 28 dB. At amplitude 4000, 12 dB lower and so about 33 dB of quality, the cabled link still decoded every packet. Each dB of extra loss takes one dB off the quality. The link's amplitude scales the taps of its root-raised-cosine filter (`--node-args "--amplitude 32767"`, 16000 by default), and the full 32767 adds 6.2 dB, if the burst stays undistorted there, which the quality rising by about 6 dB would show.

| Expected quality, amplitude 16000 / 32767 | 4 m | 7 m | 10 m |
| --- | --- | --- | --- |
| Wilkinson splitters | 31 / 37 dB | 26 / 32 dB | 23 / 29 dB |
| Resistive splitters | 26 / 32 dB | 21 / 27 dB | 18 / 24 dB |

With Wilkinson splitters the link works at 4 m at either amplitude and at 7 m at full amplitude, and at 10 m only just. With resistive splitters it works only at 4 m and full amplitude.

**Wi-Fi.** 5.8 GHz Wi-Fi uses 5.735-5.835 GHz, inside our band. Step 0 measures how much of the band the building's Wi-Fi already fills.

## 4. How each node connects to its antenna

```
 board A (node A)                                                                   board B (node B)
 DAC_A ──┐                                                                        ┌── DAC_A
         ├─ splitter ─ band-pass ─ cable ─ antenna V ~~~ air ~~~ antenna V ─ cable ─ band-pass ─ splitter ─┤
 ADC_B ──┘                               (H port to 50 Ω)    (H port to 50 Ω)                         └── ADC_B
```

Each board transmits on DAC_A and receives on ADC_B, while each node has only one antenna. A splitter joins the two connectors, and the band-pass filter sits between the splitter and the antenna.

- **Both directions then pass the same parts.** The antenna cables, the filters, the antennas and the air path are passed by both directions and cancel in Δ. Only the splitters' two arms and the short jumpers between each board and its splitter are passed by one direction only, and they enter θ₀ as constants.
- **The filter must stay in front of the antenna.** The DAC runs at 9.6 GS/s with its digital mixer at 3.8 GHz and gives 5.8 GHz as the image of 3.8 GHz in its second Nyquist zone (mix-mode), so 3.8 GHz comes out about as strongly. The filter takes 94 dB off it ([BPF report](../results/bpf-compare-20261022/REPORT.md)). Without the filter the board would radiate at 3.8 GHz, in the band licensed for 5G in the US, and the other board's ADC, running at 4.8 GS/s, would map both 3.8 and 5.8 GHz onto 1.0 GHz, where the 3.8 GHz copy spoils the correlation.
- **The filter on the antenna side also takes it out of Δ.** Each ZVBP-5800-S+ delays the burst by 3.5-4.1 ns across the burst's band, and two units of the same type need not agree to the ps or drift alike with temperature. In the cabled link each direction has its own filter, so their difference enters Δ. Here both directions pass both filters.
- **Both nodes use the same polarization,** V here, and close the other port with a 50 Ω load. Transmitting on one polarization and receiving on the other would send the two directions through different antenna ports and different reflections of the room, and the difference between them would enter Δ.
- **A circulator is an option, not a need.** The Pasternack PE8402 at hand (4-8 GHz, about 0.5 dB loss, 18 dB isolation by distributors' listings) could replace the splitter at one node, DAC_A on port 1, the antenna on port 2 and ADC_B on port 3 in the direction of the arrow on its body. It saves about 3 dB in each direction. Its transmit path (port 1 to 2) and receive path (port 2 to 3) both run through a magnetized ferrite whose properties change with temperature, and the difference between the two paths enters θ₀, so it may drift more than the difference between a splitter's two passive arms (inferred, not measured). The splitters come first, and the circulator only if the link needs the 3 dB, with θ₀ then checked at more than one temperature.
- **A node's own burst reaches its own ADC too.** It leaves the DAC at well under −14 dBm and loses at least 6 dB between the splitter's two arms (more with a Wilkinson splitter), far below anything that could harm the ADC. The 2 ms cycle never opens a node's receive window while that node transmits.

## 5. What is needed

- Two splitters with isolation between their two arms, the two Mini-Circuits ZFSC-2-10G at hand (2-10 GHz, about 0.5 dB above the 3 dB split, about 20 dB of isolation by distributors' listings, so the Wilkinson row of section 3). Their datasheets go into `hw/ref/` with the antenna's.
- The two ZVBP-5800-S+ filters now in the cabled link's two directions.
- Two low-loss SMA cables of the length the distance needs, four short SMA jumpers (each board's DAC_A and ADC_B to its splitter), four N-male to SMA-female adapters (two for the antennas, two for the spectrum analyzer and the network analyzer) and two N-male 50 Ω loads.
- Two stands that hold the panels at the same height, about 1.5 m, and a clear line of sight of 7-10 m, or at least 4 m.
- A tape measure, an angle scale for turning a panel, and a metal sheet of about 0.5 m × 0.5 m for step 4.
- The SA124B on RF-clock-PC, where its driver runs, so antenna 2's cable has to reach that PC in steps 0 and 2. A vector network analyzer for step 1, if the lab has one.

## 6. Steps

**Step 0. The band with nothing transmitting.** Antenna 2's V port goes to the SA124B, its H port to a load, and it points along the path to be used. On RF-clock-PC run `sudo .venv/bin/python experiments/sa_sweep.py 5.7e9 5.9e9 --rbw 1e5 --ref -30 --average --sweeps 5 --out band_idle.npz` a few times over an hour. Record the strongest Wi-Fi levels in 5.7375-5.8625 GHz and, after step 2, compare them with the tone it reads at the same place, about −56 dBm at 7 m. Wi-Fi within about 10 dB of that tone will lower the tracker quality in step 3, since the link's bursts are a few dB weaker than the tone (inferred), and the panels should then face away from the access points.

**Step 1. Each antenna on its own (optional).** Calibrate the vector network analyzer at the end of the cable and adapter that will connect the antenna, and measure each port's return loss over 5.725-5.875 GHz and the isolation from the H port to the V port, with the panel facing open space. Both should meet the datasheet. Without an analyzer this step is skipped, since step 2 shows a bad port or connector too.

**Step 2. One direction, on the spectrum analyzer.** Board A plays a continuous tone with `scripts/tx_cw.py` of the [DAC spectrum report](../results/dac-spectrum-20261017/REPORT.md). Copy it to the board and run it there as root, as its docstring says (`sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python /tmp/tx_cw.py 600 --waveform rect`). It was written for the October bitstream, so first check it on the current one with a cable and 20 dB attenuator into the analyzer. It should run without error and the tone should appear at 5.800 GHz. Here "amplitude" is the tone's DAC code, 16000 by default, which gives about −23 dBm at DAC_A, a different scale from the link's amplitude in section 3. A's DAC_A goes through a band-pass filter to antenna 1's V port, and antenna 2's V port to the SA124B, both H ports to loads. The panels stand at the same height, facing each other, 7 m apart if possible. On RF-clock-PC run `sudo .venv/bin/python experiments/sa_sweep.py 5.7995e9 5.8005e9 --rbw 1e3 --sweeps 3` for each reading.
- **The received power.** At 7 m it should be about −56 dBm (about −51 dBm at 4 m), within a few dB.
- **Cross-polarization.** Roll antenna 2 by 90° about the line between the panels, keeping it pointed at antenna 1, or move the cable to its H port and the load to its V port. The power should fall by the datasheet's cross-polarization rejection.
- **The beam.** Turn antenna 2 left and right about its vertical axis in steps of a few degrees and note the two angles where the power is 3 dB below its peak. Their spread should match the datasheet's horizontal beamwidth.

A reading far below the budget points to a port, a connector or the pointing.

**Step 3. Both directions, both boards on one FS725.** Connect both nodes as in section 4, and both boards to one FS725 as in [board_setup.md](../docs/board_setup.md) section 3 (the 10 MHz and the 1PPS through the EPG1 to both boards). From RF-clock-PC run `.venv/bin/python experiments/m4c_v2_link.py --seconds 60`, and `uv run python experiments/m4c_check.py packets results/<run>` on each run.
- **The link.** For each node read `quality_db` in the run's `summary.json` and compare it with the table in section 3. `m4c_check.py packets` reports whether every measurement packet was decoded.
- **θ₀ of the air link.** On one FS725 the true offset is zero, so θ is θ₀. As [links/README.md](../links/README.md) asks, repeat the 60 s run over four power-ups of both boards and take the mean, then add the air link as a new wiring in `links/887329260072A_8873292600AB.json` and make it `current`. The cabled θ₀ does not hold for the air link.
- **θ's noise.** Run once more for 600 s and record the standard deviation of θ from one pair of bursts to the next and θ's time deviation at 1 s and 10 s, beside a cabled run of the same length. On the cable a single pair scatters by 0.8-1.0 ps rms ([precision report](../results/precision-20261105/REPORT.md)). If that scatter is set by the signal-to-noise ratio, it grows in proportion to the amplitude lost, by $10^{14/20} \approx 5$ times for the 14 dB of extra loss at 4 m with Wilkinson splitters (inferred). The cabled link's high quality suggests that something else sets it there, and then it grows less.

**Step 4. Reciprocity, the step that matters most for M6.** Keep both boards on one FS725 and run `.venv/bin/python experiments/m4c_v2_link.py --seconds 1800`. Hold each change below for 2 minutes, with 2 minutes of nothing moved before the first and after each, and note the start and end of each change by the clock the node logs use, the boards' system time (`date` on board A).
- Move one antenna 30 cm towards the other and back. Each one-way delay changes by 1.0 ns.
- Stand the metal sheet beside the path, halfway between the panels and about 1 m to the side, facing so that it reflects one panel towards the other, and take it away.
- Walk slowly through the path. The link may lose a few pairs while the beam is blocked.
- Turn one antenna by about 10° about its vertical axis and back.

From the node logs take θ's 10 s means (`rf_clock.twtt.logs`), and for each still period their mean and standard deviation. A change passes if the mean of θ's 10 s means while it lasts differs from the still periods' mean by less than three times the still periods' standard deviation. Check also that `delay_ns` moved by about 1.0 ns during the 30 cm move, and note the tracker quality throughout. A shift of θ means that the two directions did not see the same channel, or that the tracker locked onto a reflection, or that a lower signal-to-noise ratio pulled its peak. The quality and the one-way delay tell these apart. This indoor test checks the boards, the splitters, the antennas and the method over a few metres. It cannot bound how reciprocal the 48 km over-water path of M6 will be.

**Step 5. Two FS725s, B steered over the air.** Switch each board to its own FS725 as in [board_setup.md](../docs/board_setup.md) section 5.1, B's FS725 on the PL's serial line (section 5.1, step 5). Run `.venv/bin/python experiments/m4c_v2_link.py --scenario steer-pl --seconds 1200`, which steers to the `current` wiring's θ₀, the air link's from step 3 (`--wiring NAME` for another). The rms of θ − θ₀ from 300 s on is compared with the cabled runs of the same setting, λ 0.0003, steps of 0.1 s and B's FS725 at GA 1, which left 1.63 and 1.69 ps ([PL steering report](../results/pl-steering-20261116/REPORT.md)). A value clearly above that comes from the air link.

## 7. Before each step that transmits

- Before the first, the answer from the university's office for radio licensing (section 3).
- The wiring on the bench that day, and both boards and both FS725s free. Other work, such as the steering in B's PL, uses the same boards.
- The band-pass filter between every DAC_A and its antenna.
- θ₀ again on one FS725 (step 3) after any change of the parts that one direction passes alone, the splitters and the jumpers between each board and its splitter, or of the PPS cables. A change of the antenna cables, the filters, the antennas or their placement should not move θ₀, and step 4 tests that.
