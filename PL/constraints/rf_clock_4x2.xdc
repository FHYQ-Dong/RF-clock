# RF-clock constraints for the RFSoC 4x2 (xczu48dr-ffvg1517-2-e).
#
# Pin sources:
#   PL_CLK, PL_SYSREF  RFSoC 4x2 reference manual appendix A: LVDS pairs
#                      FPGA_REFCLK_IN_P/N = AN11/AP11, SYS_REF_FPGA_P/N = AP18/AR18
#                      (RFSoC-MTS mts.xdc reads only the P pins as LVCMOS18; on
#                      this board that locked the MMCM only intermittently)
#   pps_in             RealDigitalOrg RFSoC4x2-BSP hw/constraints/4x2_1PPS.xdc
#                      (IRIG_COMP_OUT, output of the on-board 1PPS comparator)
#   pps_trig_in        same file (IRIG_TRIG_OUT, the Schmitt-trigger copy)
# RFDC clock, SYSREF and analog pins are dedicated and placed by the IP.

# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------
set_property PACKAGE_PIN AN11 [get_ports PL_CLK_clk_p]
set_property PACKAGE_PIN AP11 [get_ports PL_CLK_clk_n]
set_property PACKAGE_PIN AP18 [get_ports PL_SYSREF_P]
set_property PACKAGE_PIN AR18 [get_ports PL_SYSREF_N]
# HP banks 65/66 at 1.8 V: LVDS with the internal 100-ohm termination.
# DQS_BIAS self-biases the pair in case the board AC-couples it.
set_property IOSTANDARD LVDS [get_ports {PL_CLK_clk_p PL_CLK_clk_n PL_SYSREF_P PL_SYSREF_N}]
set_property DIFF_TERM_ADV TERM_100 [get_ports {PL_CLK_clk_p PL_CLK_clk_n PL_SYSREF_P PL_SYSREF_N}]
set_property DQS_BIAS TRUE [get_ports {PL_CLK_clk_p PL_CLK_clk_n PL_SYSREF_P PL_SYSREF_N}]
set_property PACKAGE_PIN AJ13 [get_ports pps_in]
set_property PACKAGE_PIN AH13 [get_ports pps_trig_in]
set_property IOSTANDARD LVCMOS18 [get_ports {pps_in pps_trig_in}]
# Both copies reach the FPGA through an NVT2008 pass-FET translator that has
# 1.15k pull-ups only on its 3.3 V side (schematic sheet 10, "1PPS"); its 1.8 V
# side needs a pull-up for the high level, so use the FPGA pull-up.
set_property PULLTYPE PULLUP [get_ports {pps_in pps_trig_in}]

# ---------------------------------------------------------------------------
# Clocks
# ---------------------------------------------------------------------------
# LMK04828 PL_CLK, 100 MHz (10x the FS725 reference).
create_clock -period 10.000 -name PL_CLK [get_ports PL_CLK_clk_p]

# PL_CLK enters on a non-dedicated route to the MMCM column (as in RFSoC-MTS).
set_property CLOCK_DEDICATED_ROUTE ANY_CMT_COLUMN [get_nets -of_objects [get_pins -hierarchical -filter {NAME =~ *pl_clk_bufg/U0/BUFG_O*}]]

# PL_SYSREF is launched by the LMK04828 aligned to PL_CLK (values from RFSoC-MTS).
set_input_delay -clock [get_clocks PL_CLK] -min 2.000 [get_ports PL_SYSREF_P]
set_input_delay -clock [get_clocks PL_CLK] -max 2.031 [get_ports PL_SYSREF_P]

# ---------------------------------------------------------------------------
# Asynchronous inputs (synchronized in RTL)
# ---------------------------------------------------------------------------
set_false_path -from [get_ports {pps_in pps_trig_in}]
set_false_path -to [get_cells -hierarchical -filter {NAME =~ *u_p0/lock_sync_reg[0]}]
set_false_path -to [get_cells -hierarchical -filter {NAME =~ *p_top_0/inst/overrange_sync_reg[0]}]

# ---------------------------------------------------------------------------
# p4 clock-domain crossings between the 100 MHz fabric and 250 MHz DMA clocks.
# Both clocks come from the same MMCM, so Vivado times the remaining crossings
# (FIFO memory read, dma_ready and TLAST synchronizers) as synchronous paths;
# they met timing in the first implementation.  The Gray pointers are bounded
# to one DMA clock period instead of the 100/250 MHz edge relationship.
# ---------------------------------------------------------------------------
set_max_delay -datapath_only 4.000 \
    -from [get_cells -hierarchical -filter {NAME =~ *u_p4/u_fifo/wgray_reg[*]}] \
    -to   [get_cells -hierarchical -filter {NAME =~ *u_p4/u_fifo/wgray_r1_reg[*]}]
set_max_delay -datapath_only 4.000 \
    -from [get_cells -hierarchical -filter {NAME =~ *u_p4/u_fifo/rgray_reg[*]}] \
    -to   [get_cells -hierarchical -filter {NAME =~ *u_p4/u_fifo/rgray_w1_reg[*]}]

# ---------------------------------------------------------------------------
# Bitstream
# ---------------------------------------------------------------------------
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
# Not PULLUP (the BSP default): in run3 that lit the unused PL LEDs and, with
# PL_CLK read single-ended, most likely pulled its N pin up so the MMCM never
# locked.
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLNONE [current_design]
set_property BITSTREAM.CONFIG.OVERTEMPSHUTDOWN ENABLE [current_design]
