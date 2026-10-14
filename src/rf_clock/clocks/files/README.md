# RFSoC 4x2 clock register images

`LMK04828_100.0.txt` is the FS725 external-reference image (`ext10m`), and
`LMK04828_100.1.txt` is the board-reference bring-up image (`onboard`). The
numeric suffixes are deliberately distinct `xrfclk` frequency keys; the mode
mapping is recorded in `plan/handoff_clock_config.md`. Do not use `onboard` for
time measurements.

`LMX2594_480.0.txt` is an integer-N image. With a 480 MHz reference it makes a
7.68 GHz VCO and a 16-way channel divide, producing 480 MHz on OUTA/OUTB.

The `*_500.0.txt` fixtures are copied from Xilinx RFSoC-MTS (MIT). The
`LMK04828_casper_ext10m.txt` fixture is the external-10-MHz attachment from
the PYNQ discussion referenced by the handoff; its redistribution licence has
not been established, so treat it as a provenance fixture until that is
resolved. `LMX2594_491.52.txt` is a Xilinx RFSoC-PYNQ reference fixture.
