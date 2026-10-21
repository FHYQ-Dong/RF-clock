# Board configs

Chinese translation: [README_zh.md](README_zh.md).

Each directory holds a copy of one board's `/etc/rf-clock/board.json`, named by the board's S/N: the board config that `PL.open()` reads (docs/ABI.md §11.1). The tools of docs/board_setup.md section 4 write it on the board (`rf_clock.checks.vco_bands`, `rf_clock.checks.mts_dtc`); the copies here are the reference for its format and the backup for when the SD card is rewritten. They are not part of the package.

| Board | Address | Forced VCO bands (ADC, DAC) | MTS capture codes (DAC T1; ADC T1; ADC PLL) |
| --- | --- | --- | --- |
| [887329260072A](887329260072A/board.json) | 192.168.2.99 | 118, 122 | 62/61; 62/65; 72/76 |
| [887329260101](887329260101/board.json) | 192.168.2.101 | 125, 128 | 54/53; 59/62; 68/73 |

887329260101 has no band delays in its band table yet: they need its own loopback cable (docs/board_setup.md section 4.7).

**Putting a copy back** (after the SD card was rewritten): the `serial` in the file must be the board's, which `PL.open()` checks anyway:

```bash
run="sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python"
$run -c "from rf_clock.pl.board_config import read_board_serial; print(read_board_serial())"
sudo install -D -m 644 board.json /etc/rf-clock/board.json
```

Then check the board as after any change (docs/board_setup.md section 6): `rf_clock.checks.mts_dtc check` and M2.

**Updating a copy**: after any step of docs/board_setup.md section 4 that writes the config, copy the board's file here and commit it, for example from RF-clock-PC: `ssh xilinx@192.168.2.99 cat /etc/rf-clock/board.json > boards/887329260072A/board.json`. Change values on the board, not here: through the tools, or a band chosen by hand followed by the steps docs/board_setup.md section 6 names for it; then copy the file.
