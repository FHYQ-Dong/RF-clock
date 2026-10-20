# RFSoC 4x2 clock register images

`PL.open()` writes `LMK04828_100.0.txt` and `LMX2594_480.0.txt` from this
directory through PYNQ's `xrfclk` (`rf_clock.pl.board.program_clocks`); the
board needs no copies inside the `xrfclk` package. File format: one register
write per line, `R<n>` (optionally `R<n> (label)`), a tab, and the register
word in hex; the lines are written in file order.

## Clock plan

```text
FS725 10 MHz -> LMK04828 CLKin0 -> PLL1 -> VCXO 160 MHz -> PLL2 -> VCO 2400 MHz
                  DCLKout8 /24   -> PL_CLK 100 MHz      (PL pins AN11/AP11, LVDS)
                  SYSREF   /480  -> 5 MHz to the PL (AP18/AR18) and the RFDC
                  OUT0, OUT4 /5  -> 480 MHz references to the two LMX2594
LMX2594: 480 MHz reference -> VCO 7.68 GHz -> /16 -> 480 MHz to ADC/DAC tiles 0 and 2
```

SYSREF is 5 MHz: PG269 requires it below 10 MHz, and 5 MHz divides PL_CLK,
the 480 MHz references and both sample rates. The output assignment (OUT0/OUT4
LMX references, OUT8 PL clock, OUT3/OUT9/OUT11 SYSREF) was derived from the
4x2 reference manual and confirmed on the board by MMCM lock, PPS timing and
MTS.

## Files

| File | Use |
| --- | --- |
| `LMK04828_100.0.txt` | `ext10m`: PLL1 locked to the FS725 10 MHz on CLKin0, in nested zero-delay mode (0x13F = 0x0B: PLL1 N fed from DCLKout8; R = 12, N = 120, 833.3 kHz phase detector), so PL_CLK has one phase to the 10 MHz and PPS after every programming. The only image for measurements. |
| `LMK04828_100.1.txt` | `onboard`: on-board reference selection from RFSoC-MTS, same output rates. Does not follow the FS725; never use it for time measurements. |
| `LMX2594_480.0.txt` | Integer-N: 480 MHz reference, 7.68 GHz VCO, 16-way channel divider, 480 MHz on OUTA/OUTB, with phase SYNC (`make_phase_sync_config`: VCO_PHASE_SYNC = 1, so 4 of the 16 are in the PLL feedback and PLL_N is 40). `PL.open()` toggles VCO_PHASE_SYNC after writing (SYNC category 2), which fixes the output phase to the LMK reference; without it the RFDC sample clocks moved by 0 or half a DAC sample with each clock write. |
| `LMK04828_500.0.txt`, `LMX2594_500.0.txt` | Fixtures from Xilinx RFSoC-MTS (MIT), used to check the decoders. |
| `LMX2594_491.52.txt` | Fixture from Xilinx RFSoC-PYNQ, used to check the decoder. |

The numeric suffixes are `xrfclk` frequency keys, not frequencies: `100.0` and
`100.1` are two images with the same outputs. `make_100mhz_config()` in
`rf_clock.clocks.lmk04828` derives both from a base file and regenerates
`LMK04828_100.0.txt` unchanged from itself. The `ext10m` PLL1/CLKin0 settings
originally came from a community external-10-MHz image posted on the PYNQ
forum; that file is not kept here because its licence is unknown.

Still to verify on the board (plan/checklist.md section 2): LMK/LMX lock read
back over SPI, and PLL1 losing lock when the FS725 10 MHz is removed.
