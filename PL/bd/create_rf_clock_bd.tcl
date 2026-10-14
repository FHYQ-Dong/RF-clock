# Build the RF-clock block design for the RFSoC 4x2 and optionally implement it.
#
#   source /tools/Xilinx/Vivado/2024.1/settings64.sh
#   vivado -mode batch -source PL/bd/create_rf_clock_bd.tcl \
#       -tclargs OUT_DIR BOARD_REPO ?STAGE?
#
# OUT_DIR     new directory for the Vivado project; never overwritten.  Keep it
#             outside the Syncthing-synchronized repository.
# BOARD_REPO  directory that contains rfsoc4x2/1.0/board.xml, i.e. board_files/
#             of https://github.com/RealDigitalOrg/RFSoC4x2-BSP
# STAGE       bd (default): block design, validation, HDL wrapper
#             synth:        bd + synthesis
#             impl:         synth + implementation, bitstream, XSA and HWH
#
# Clocking (plan/RF_clock_modules_zh.md §6): the LMK04828 is locked to the
# FS725 10 MHz and drives PL_CLK (100 MHz) and PL_SYSREF.  PL_CLK feeds one
# MMCM whose 100 MHz output clocks the RFDC AXIS ports, p_top and raw_tick,
# and whose 250 MHz output clocks the RX DMA.  PL_SYSREF is synchronized to
# the fabric clock and drives the RFDC user_sysref inputs (MTS).

if {[llength $argv] < 2 || [llength $argv] > 3} {
    error "usage: vivado -mode batch -source create_rf_clock_bd.tcl -tclargs OUT_DIR BOARD_REPO ?bd|synth|impl?"
}
if {[version -short] ne "2024.1"} {
    error "this script is qualified only with Vivado 2024.1; found [version -short]"
}

set out_dir    [file normalize [lindex $argv 0]]
set board_repo [file normalize [lindex $argv 1]]
set stage      bd
if {[llength $argv] == 3} { set stage [lindex $argv 2] }
if {[lsearch -exact {bd synth impl} $stage] < 0} {
    error "STAGE must be bd, synth or impl; got $stage"
}
if {[file exists $out_dir]} {
    error "refusing to overwrite existing output directory: $out_dir"
}
if {![file exists [file join $board_repo rfsoc4x2 1.0 board.xml]]} {
    error "RFSoC 4x2 board files not found below $board_repo"
}

set script_dir [file dirname [file normalize [info script]]]
set repo_dir   [file normalize [file join $script_dir .. ..]]
set src_dir    [file join $repo_dir PL src]
set xdc_file   [file join $repo_dir PL constraints rf_clock_4x2.xdc]

# The rf-clock package version A.B.C.D from pyproject.toml goes into P2
# BUILD_VERSION (0xAABBCCDD), so software can check the bitstream was built for
# its release.  A.B must be the register map VERSION (docs/ABI.md 14.1).
set f [open [file join $repo_dir pyproject.toml] r]
set pyproject [read $f]
close $f
if {![regexp -line {^version\s*=\s*"(\d+)\.(\d+)\.(\d+)\.(\d+)"} $pyproject -> v_a v_b v_c v_d]} {
    error "pyproject.toml has no version = \"A.B.C.D\""
}
set f [open [file join $src_dir p_regs.vh] r]
set regs [read $f]
close $f
if {![regexp {P_VERSION\s*=\s*32'h([0-9A-Fa-f_]+)} $regs -> p_version]} {
    error "P_VERSION not found in p_regs.vh"
}
scan [string map {_ {}} $p_version] %x p_version
set abi_text "[expr {$p_version >> 16}].[expr {$p_version & 0xFFFF}]"
if {"$v_a.$v_b" ne $abi_text} {
    error "pyproject.toml version $v_a.$v_b.$v_c.$v_d must start with the register map VERSION $abi_text"
}
foreach v [list $v_a $v_b $v_c $v_d] {
    if {$v > 255} { error "version field $v does not fit BUILD_VERSION (8 bits)" }
}
set build_version_text "$v_a.$v_b.$v_c.$v_d"
set build_version [expr {($v_a << 24) | ($v_b << 16) | ($v_c << 8) | $v_d}]

set part       xczu48dr-ffvg1517-2-e
set board_part realdigital.org:rfsoc4x2:part0:1.0
set bd_name    rf_clock
set jobs       8

file mkdir $out_dir
file copy [info script] [file join $out_dir create_rf_clock_bd.tcl]
set status [open [file join $out_dir status.txt] w]
puts $status "tool=Vivado [version -short]"
puts $status "part=$part"
puts $status "board_part=$board_part"
puts $status "stage=$stage"
puts $status "build_version=$build_version_text"
flush $status

proc pin {path} {
    set p [get_bd_pins $path -quiet]
    if {[llength $p] != 1} { error "BD pin not found: $path" }
    return $p
}

proc intf {path} {
    set p [get_bd_intf_pins $path -quiet]
    if {[llength $p] != 1} { error "BD interface pin not found: $path" }
    return $p
}

proc external_intf {path name} {
    set before [get_bd_intf_ports -quiet]
    make_bd_intf_pins_external [intf $path]
    foreach p [get_bd_intf_ports -quiet] {
        if {[lsearch -exact $before $p] < 0} {
            set_property name $name $p
            return $p
        }
    }
    error "no external port created for $path"
}

proc tie_zero {cell_name pins} {
    foreach p $pins {
        set width [expr {[get_property LEFT $p] eq "" ? 1 :
                         abs([get_property LEFT $p] - [get_property RIGHT $p]) + 1}]
        set c [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 \
            ${cell_name}_[string map {/ _} [get_property NAME $p]]]
        set_property -dict [list CONFIG.CONST_WIDTH $width CONFIG.CONST_VAL 0] $c
        connect_bd_net [get_bd_pins $c/dout] $p
    }
}

set failed [catch {
    set_param board.repoPaths [list $board_repo]
    create_project rf_clock [file join $out_dir project] -part $part
    set_property board_part $board_part [current_project]
    set_property target_language Verilog [current_project]

    # ---------------------------------------------------------------
    # RTL and constraints.
    # ---------------------------------------------------------------
    add_files -norecurse [glob [file join $src_dir p*.v]]
    add_files -norecurse [glob [file join $src_dir p_*.vh]]
    set_property file_type {Verilog Header} [get_files -filter {NAME =~ *.vh}]
    # RTL includes are written relative to PL/ (e.g. `include "src/p_defs.vh").
    set_property include_dirs [list [file join $repo_dir PL]] [get_filesets sources_1]
    # The module-reference HDL parser (create_bd_cell -reference) ignores
    # include_dirs and resolves includes against the working directory
    # (probed with Vivado 2024.1); synthesis uses include_dirs.
    cd [file join $repo_dir PL]
    add_files -fileset constrs_1 -norecurse $xdc_file
    update_compile_order -fileset sources_1

    create_bd_design $bd_name

    # ---------------------------------------------------------------
    # Processing system: board preset, HPM0 for control, HP0 for RX DMA.
    # ---------------------------------------------------------------
    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 ps]
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
        -config {apply_board_preset "1"} $ps
    set_property -dict [list \
        CONFIG.PSU__USE__M_AXI_GP0 1 \
        CONFIG.PSU__USE__M_AXI_GP1 0 \
        CONFIG.PSU__USE__M_AXI_GP2 0 \
        CONFIG.PSU__USE__S_AXI_GP2 1 \
        CONFIG.PSU__SAXIGP2__DATA_WIDTH 128 \
        CONFIG.PSU__FPGA_PL0_ENABLE 1 \
        CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 100 \
    ] $ps

    # ---------------------------------------------------------------
    # Board clocks: PL_CLK -> IBUFDS -> BUFG -> MMCM (100 MHz fabric,
    # 250 MHz DMA).  PL_CLK and PL_SYSREF are LVDS pairs on the 4x2
    # (FPGA_REFCLK_IN_P/N, SYS_REF_FPGA_P/N; reference manual appendix A).
    # ---------------------------------------------------------------
    # Ports PL_CLK_clk_p/n (a differential clock interface) and PL_SYSREF_P/N.
    set pl_clk [create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:diff_clock_rtl:1.0 PL_CLK]
    set_property CONFIG.FREQ_HZ 100000000 $pl_clk
    create_bd_port -dir I PL_SYSREF_P
    create_bd_port -dir I PL_SYSREF_N
    create_bd_port -dir I pps_in
    create_bd_port -dir I pps_trig_in

    foreach name {pl_clk_ibufds pl_sysref_ibufds} {
        set c [create_bd_cell -type ip -vlnv xilinx.com:ip:util_ds_buf:2.2 $name]
        set_property CONFIG.C_BUF_TYPE IBUFDS $c
    }
    connect_bd_intf_net $pl_clk [intf pl_clk_ibufds/CLK_IN_D]
    connect_bd_net [get_bd_ports PL_SYSREF_P] [pin pl_sysref_ibufds/IBUF_DS_P]
    connect_bd_net [get_bd_ports PL_SYSREF_N] [pin pl_sysref_ibufds/IBUF_DS_N]

    set bufg [create_bd_cell -type ip -vlnv xilinx.com:ip:util_ds_buf:2.2 pl_clk_bufg]
    set_property CONFIG.C_BUF_TYPE BUFG $bufg
    connect_bd_net [pin pl_clk_ibufds/IBUF_OUT] [pin pl_clk_bufg/BUFG_I]

    set mmcm [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 fabric_mmcm]
    set_property -dict [list \
        CONFIG.PRIMITIVE MMCM \
        CONFIG.PRIM_SOURCE Global_buffer \
        CONFIG.PRIM_IN_FREQ 100.000 \
        CONFIG.USE_PHASE_ALIGNMENT true \
        CONFIG.NUM_OUT_CLKS 2 \
        CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100.000 \
        CONFIG.CLKOUT2_USED true \
        CONFIG.CLKOUT2_REQUESTED_OUT_FREQ 250.000 \
        CONFIG.USE_LOCKED true \
        CONFIG.USE_RESET true \
        CONFIG.RESET_TYPE ACTIVE_LOW \
    ] $mmcm
    connect_bd_net [pin pl_clk_bufg/BUFG_O] [pin fabric_mmcm/clk_in1]
    connect_bd_net [pin ps/pl_resetn0] [pin fabric_mmcm/resetn]

    set clk_ps     [pin ps/pl_clk0]
    set clk_fabric [pin fabric_mmcm/clk_out1]
    set clk_dma    [pin fabric_mmcm/clk_out2]

    # PL_SYSREF is captured on PL_CLK and synchronized into the fabric clock.
    set sysref_sync [create_bd_cell -type ip -vlnv xilinx.com:ip:xpm_cdc_gen:1.0 sysref_sync]
    set_property -dict [list \
        CONFIG.CDC_TYPE xpm_cdc_single \
        CONFIG.DEST_SYNC_FF 2 \
        CONFIG.INIT_SYNC_FF true \
        CONFIG.WIDTH 1 \
    ] $sysref_sync
    connect_bd_net [pin pl_sysref_ibufds/IBUF_OUT] [pin sysref_sync/src_in]
    connect_bd_net [pin pl_clk_bufg/BUFG_O] [pin sysref_sync/src_clk]
    connect_bd_net $clk_fabric [pin sysref_sync/dest_clk]

    # ---------------------------------------------------------------
    # Resets, one per clock domain.
    # ---------------------------------------------------------------
    foreach {name clk locked} [list \
        rst_ps     $clk_ps     {} \
        rst_fabric $clk_fabric [pin fabric_mmcm/locked] \
        rst_dma    $clk_dma    [pin fabric_mmcm/locked]] {
        create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 $name
        connect_bd_net $clk [pin $name/slowest_sync_clk]
        connect_bd_net [pin ps/pl_resetn0] [pin $name/ext_reset_in]
        if {$locked ne ""} { connect_bd_net $locked [pin $name/dcm_locked] }
    }
    set rstn_ps     [pin rst_ps/peripheral_aresetn]
    set rstn_fabric [pin rst_fabric/peripheral_aresetn]
    set rstn_dma    [pin rst_dma/peripheral_aresetn]

    # ---------------------------------------------------------------
    # RF Data Converter (plan/RF_clock_modules_zh.md §3).
    #   DAC tile 2 block 0: 9.6 GS/s, IMR high-pass datapath (x6 then x2 =
    #     x12), NCO 3.8 GHz, mix-mode -> 5.8 GHz in the 2nd Nyquist zone
    #     (vout20).
    #   ADC tile 2 block 0: 4.8 GS/s, /6, I/Q, NCO -1.0 GHz, 5.8 GHz
    #     undersampled in the 3rd Nyquist zone (vin2_01).
    #   800 MS/s I/Q, 100 MHz AXIS, 480 MHz tile reference clocks.
    #
    # MTS (deterministic latency across power-ups): RFDC 2.6 offers it only
    # when at least two tiles of a kind take part and block 0 of every such
    # tile is enabled.  The RFSoC 4x2 brings out ADC/DAC tiles 0 and 2, each
    # with its own reference clock, so tile 0 joins with the same datapath on
    # its own PLL; its streams are unused.  No clock distribution is used
    # (a distributing DAC tile would be limited to 7.0 GS/s).
    # ---------------------------------------------------------------
    set rfdc [create_bd_cell -type ip -vlnv xilinx.com:ip:usp_rf_data_converter:2.6 rfdc]

    set rfdc_cfg [list CONFIG.ADC_RTS true CONFIG.DAC_RTS false \
        CONFIG.ADC_Slice00_Enable true CONFIG.ADC_Slice02_Enable false \
        CONFIG.ADC_Slice20_Enable true CONFIG.ADC_Slice22_Enable false \
        CONFIG.DAC_Slice00_Enable true CONFIG.DAC_Slice20_Enable true]
    foreach t {0 2} {
        lappend rfdc_cfg \
            CONFIG.ADC${t}_PLL_Enable true CONFIG.ADC${t}_Refclk_Freq 480.000 \
            CONFIG.ADC${t}_Sampling_Rate 4.8 CONFIG.ADC${t}_Outclk_Freq 300.000 \
            CONFIG.ADC_Data_Type${t}0 1 CONFIG.ADC_Data_Width${t}0 8 \
            CONFIG.ADC_Decimation_Mode${t}0 6 CONFIG.ADC_Mixer_Type${t}0 2 \
            CONFIG.ADC_Mixer_Mode${t}0 0 CONFIG.ADC_NCO_Freq${t}0 -1.000 CONFIG.ADC_Nyquist${t}0 0 \
            CONFIG.DAC${t}_PLL_Enable true CONFIG.DAC${t}_Refclk_Freq 480.000 \
            CONFIG.DAC${t}_Sampling_Rate 9.6 CONFIG.DAC${t}_Outclk_Freq 300.000 \
            CONFIG.DAC_Data_Type${t}0 0 CONFIG.DAC_Data_Width${t}0 16 \
            CONFIG.DAC_Interpolation_Mode${t}0 6 CONFIG.DAC_Mixer_Type${t}0 2 \
            CONFIG.DAC_Mixer_Mode${t}0 0 CONFIG.DAC_Mode${t}0 2 \
            CONFIG.DAC_NCO_Freq${t}0 3.800 CONFIG.DAC_Nyquist${t}0 1
    }
    set_property -dict $rfdc_cfg $rfdc
    # MTS can only be enabled once both tiles exist with block 0 enabled.
    set_property -dict [list \
        CONFIG.ADC0_Multi_Tile_Sync true CONFIG.ADC2_Multi_Tile_Sync true \
        CONFIG.DAC0_Multi_Tile_Sync true CONFIG.DAC2_Multi_Tile_Sync true] $rfdc

    set expect {}
    foreach t {0 2} {
        lappend expect CONFIG.ADC${t}_Multi_Tile_Sync true CONFIG.DAC${t}_Multi_Tile_Sync true \
                       CONFIG.ADC${t}_Fabric_Freq 100.0 CONFIG.DAC${t}_Fabric_Freq 100.0 \
                       CONFIG.ADC${t}_Sampling_Rate 4.8 CONFIG.DAC${t}_Sampling_Rate 9.6 \
                       CONFIG.ADC${t}_Clock_Source $t CONFIG.DAC${t}_Clock_Source [expr {$t + 4}]
    }
    lappend expect CONFIG.ADC_Data_Type20 1 CONFIG.ADC_Decimation_Mode20 6 \
                   CONFIG.DAC_Interpolation_Mode20 6 CONFIG.DAC_Mode20 2 CONFIG.DAC_Nyquist20 1 \
                   CONFIG.ADC1_Enable 0 CONFIG.DAC1_Enable 0 CONFIG.ADC3_Enable 0 CONFIG.DAC3_Enable 0
    foreach {key expected} $expect {
        set actual [get_property $key $rfdc]
        if {[string is double -strict $expected]} {
            if {abs($actual - $expected) > 1e-6} { error "$key=$actual, expected $expected" }
        } elseif {[string tolower $actual] ne $expected} {
            error "$key=$actual, expected $expected"
        }
    }

    # Record every RFDC pin so the wiring below can be reviewed.
    set fd [open [file join $out_dir rfdc_pins.txt] w]
    foreach p [lsort [get_bd_pins rfdc/*]] { puts $fd "pin  $p [get_property DIR $p]" }
    foreach p [lsort [get_bd_intf_pins rfdc/*]] { puts $fd "intf $p [get_property MODE $p]" }
    close $fd

    connect_bd_net $clk_ps  [pin rfdc/s_axi_aclk]
    connect_bd_net $rstn_ps [pin rfdc/s_axi_aresetn]
    foreach t {0 2} {
        connect_bd_net $clk_fabric  [pin rfdc/s${t}_axis_aclk]
        connect_bd_net $clk_fabric  [pin rfdc/m${t}_axis_aclk]
        connect_bd_net $rstn_fabric [pin rfdc/s${t}_axis_aresetn]
        connect_bd_net $rstn_fabric [pin rfdc/m${t}_axis_aresetn]
    }
    connect_bd_net [pin sysref_sync/dest_out] [pin rfdc/user_sysref_adc]
    connect_bd_net [pin sysref_sync/dest_out] [pin rfdc/user_sysref_dac]

    external_intf rfdc/sysref_in sysref_in
    foreach a {adc0_clk adc2_clk dac0_clk dac2_clk vin0_01 vin2_01 vout00 vout20} {
        external_intf rfdc/$a $a
    }

    # Unused RFDC event/clear/shutdown inputs and the DAC tile 0 stream
    # (MTS-only) are tied inactive.  ADC tile 0 streams stay unconnected
    # (TREADY defaults high).
    set unused {}
    foreach p [get_bd_pins rfdc/*] {
        set n [get_property NAME $p]
        if {[get_property DIR $p] eq "I" &&
            [regexp {(_pl_event|_clear_ov|_clear_or|_fast_shutdown)$|^s00_axis_t(data|valid)$} $n]} {
            lappend unused $p
        }
    }
    tie_zero tie $unused

    # ---------------------------------------------------------------
    # ADC tile 2 block 0: I (m20) and Q (m21) combined into one 256-bit
    # stream {Q, I}.
    # ---------------------------------------------------------------
    set comb [create_bd_cell -type ip -vlnv xilinx.com:ip:axis_combiner:1.1 adc_iq_combiner]
    set_property -dict [list CONFIG.NUM_SI 2 CONFIG.TDATA_NUM_BYTES 16] $comb
    connect_bd_net $clk_fabric  [pin adc_iq_combiner/aclk]
    connect_bd_net $rstn_fabric [pin adc_iq_combiner/aresetn]
    connect_bd_intf_net [intf rfdc/m20_axis] [intf adc_iq_combiner/S00_AXIS]
    connect_bd_intf_net [intf rfdc/m21_axis] [intf adc_iq_combiner/S01_AXIS]
    # ---------------------------------------------------------------
    # p_top (P0-P4, P6).
    # ---------------------------------------------------------------
    set top [create_bd_cell -type module -reference p_top p_top_0]
    set_property CONFIG.BUILD_VERSION $build_version $top
    connect_bd_net $clk_fabric  [pin p_top_0/clk]
    connect_bd_net $rstn_fabric [pin p_top_0/arst_n]
    connect_bd_net $clk_dma     [pin p_top_0/dma_clk]
    connect_bd_net $rstn_dma    [pin p_top_0/dma_arst_n]
    connect_bd_net [pin fabric_mmcm/locked] [pin p_top_0/clocks_locked_async]
    connect_bd_net [get_bd_ports pps_in] [pin p_top_0/pps_in]
    connect_bd_net [get_bd_ports pps_trig_in] [pin p_top_0/pps_trig_in]
    connect_bd_intf_net [intf p_top_0/m_axis_dac] [intf rfdc/s20_axis]
    connect_bd_intf_net [intf adc_iq_combiner/M_AXIS] [intf p_top_0/s_axis_adc]
    set ovr [get_bd_pins rfdc/adc2_01_over_range -quiet]
    if {[llength $ovr] != 1} { error "RFDC over-range pin not found; see rfdc_pins.txt" }
    connect_bd_net $ovr [pin p_top_0/adc_overrange]

    # TX waveform buffer controller (256-bit BRAM port A of P3).
    set bram_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 tx_bram_ctrl]
    set_property -dict [list \
        CONFIG.PROTOCOL AXI4 \
        CONFIG.DATA_WIDTH 256 \
        CONFIG.SINGLE_PORT_BRAM 1 \
        CONFIG.ECC_TYPE 0 \
        CONFIG.READ_LATENCY 1 \
    ] $bram_ctrl
    connect_bd_net $clk_fabric  [pin tx_bram_ctrl/s_axi_aclk]
    connect_bd_net $rstn_fabric [pin tx_bram_ctrl/s_axi_aresetn]
    connect_bd_intf_net [intf tx_bram_ctrl/BRAM_PORTA] [intf p_top_0/BRAM_PORTA]

    # RX DMA: S2MM only, simple mode, 26-bit length (docs/ABI.md §4.6.9).
    set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma:7.1 rx_dma]
    set_property -dict [list \
        CONFIG.c_include_sg 0 \
        CONFIG.c_include_mm2s 0 \
        CONFIG.c_include_s2mm 1 \
        CONFIG.c_sg_length_width 26 \
        CONFIG.c_addr_width 64 \
        CONFIG.c_m_axi_s2mm_data_width 256 \
        CONFIG.c_s_axis_s2mm_tdata_width 256 \
        CONFIG.c_s2mm_burst_size 64 \
    ] $dma
    connect_bd_net $clk_ps   [pin rx_dma/s_axi_lite_aclk]
    connect_bd_net $clk_dma  [pin rx_dma/m_axi_s2mm_aclk]
    connect_bd_net $rstn_ps  [pin rx_dma/axi_resetn]
    connect_bd_intf_net [intf p_top_0/m_axis_s2mm] [intf rx_dma/S_AXIS_S2MM]

    # DMA -> HP0 (256-bit, 250 MHz) through a SmartConnect width converter.
    set sc_hp [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_hp]
    set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 1] $sc_hp
    connect_bd_net $clk_dma  [pin sc_hp/aclk]
    connect_bd_net $rstn_dma [pin sc_hp/aresetn]
    connect_bd_net $clk_dma  [pin ps/saxihp0_fpd_aclk]
    connect_bd_intf_net [intf rx_dma/M_AXI_S2MM] [intf sc_hp/S00_AXI]
    connect_bd_intf_net [intf sc_hp/M00_AXI] [intf ps/S_AXI_HP0_FPD]

    # Clock status readable without the fabric clock.  An access to p_top or
    # the TX buffer while the fabric clock is absent stalls the SmartConnect
    # clock crossing and hangs the bus, so software reads this first
    # (docs/ABI.md §7.1).  bit 0 = fabric MMCM locked.
    set clk_gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 clk_status_gpio]
    set_property -dict [list CONFIG.C_ALL_INPUTS 1 CONFIG.C_GPIO_WIDTH 1] $clk_gpio
    connect_bd_net $clk_ps  [pin clk_status_gpio/s_axi_aclk]
    connect_bd_net $rstn_ps [pin clk_status_gpio/s_axi_aresetn]
    connect_bd_net [pin fabric_mmcm/locked] [pin clk_status_gpio/gpio_io_i]

    # Control SmartConnect: PS HPM0 (pl_clk0) -> p_top regs and TX buffer
    # (fabric clock), RFDC, DMA and clock-status registers (pl_clk0).
    set sc_ctrl [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 sc_ctrl]
    set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 5 CONFIG.NUM_CLKS 2] $sc_ctrl
    connect_bd_net $clk_ps     [pin sc_ctrl/aclk]
    connect_bd_net $clk_fabric [pin sc_ctrl/aclk1]
    connect_bd_net $rstn_ps    [pin sc_ctrl/aresetn]
    connect_bd_net $clk_ps     [pin ps/maxihpm0_fpd_aclk]
    connect_bd_intf_net [intf ps/M_AXI_HPM0_FPD] [intf sc_ctrl/S00_AXI]
    connect_bd_intf_net [intf sc_ctrl/M00_AXI] [intf p_top_0/s_axi]
    connect_bd_intf_net [intf sc_ctrl/M01_AXI] [intf tx_bram_ctrl/S_AXI]
    connect_bd_intf_net [intf sc_ctrl/M02_AXI] [intf rfdc/s_axi]
    connect_bd_intf_net [intf sc_ctrl/M03_AXI] [intf rx_dma/S_AXI_LITE]
    connect_bd_intf_net [intf sc_ctrl/M04_AXI] [intf clk_status_gpio/S_AXI]

    # ---------------------------------------------------------------
    # Address map (fixed so software and docs/ABI.md can rely on it).
    # ---------------------------------------------------------------
    set ps_data [get_bd_addr_spaces ps/Data]
    foreach {seg offset range} {
        p_top_0/s_axi/reg0        0xA0000000 4K
        rx_dma/S_AXI_LITE/Reg     0xA0010000 64K
        clk_status_gpio/S_AXI/Reg 0xA0020000 4K
        rfdc/s_axi/Reg            0xA0040000 256K
        tx_bram_ctrl/S_AXI/Mem0   0xA0400000 4M
    } {
        set s [get_bd_addr_segs $seg -quiet]
        if {[llength $s] != 1} { error "address segment not found: $seg" }
        assign_bd_address -offset $offset -range $range -target_address_space $ps_data $s
    }
    assign_bd_address
    set fd [open [file join $out_dir address_map.txt] w]
    foreach s [lsort [get_bd_addr_segs -of_objects [get_bd_addr_spaces]]] {
        puts $fd "$s offset=[get_property OFFSET $s] range=[get_property RANGE $s]"
    }
    close $fd

    validate_bd_design
    save_bd_design
    write_bd_tcl -force [file join $out_dir ${bd_name}_bd.tcl]

    set bd_file [get_files ${bd_name}.bd]
    generate_target all $bd_file
    set wrapper [make_wrapper -files $bd_file -top]
    add_files -norecurse $wrapper
    set_property top ${bd_name}_wrapper [current_fileset]
    update_compile_order -fileset sources_1
    puts $status "bd=complete"
    flush $status

    # ---------------------------------------------------------------
    # Optional synthesis / implementation.
    # ---------------------------------------------------------------
    if {$stage eq "synth" || $stage eq "impl"} {
        launch_runs synth_1 -jobs $jobs
        wait_on_run synth_1
        if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {
            error "synthesis failed: [get_property STATUS [get_runs synth_1]]"
        }
        puts $status "synth=complete"
        flush $status
    }
    if {$stage eq "impl"} {
        launch_runs impl_1 -to_step write_bitstream -jobs $jobs
        wait_on_run impl_1
        if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
            error "implementation failed: [get_property STATUS [get_runs impl_1]]"
        }
        open_run impl_1
        report_timing_summary -file [file join $out_dir timing_summary.rpt]
        report_utilization    -file [file join $out_dir utilization.rpt]
        report_cdc            -file [file join $out_dir cdc.rpt]
        set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
        set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
        puts $status "wns=$wns"
        puts $status "whs=$whs"
        set impl_dir [get_property DIRECTORY [get_runs impl_1]]
        file copy -force [file join $impl_dir ${bd_name}_wrapper.bit] [file join $out_dir ${bd_name}.bit]
        write_hw_platform -fixed -include_bit -force [file join $out_dir ${bd_name}.xsa]
        foreach hwh [glob -nocomplain [file join $out_dir project rf_clock.gen sources_1 bd $bd_name hw_handoff *.hwh]] {
            file copy -force $hwh [file join $out_dir ${bd_name}.hwh]
        }
        puts $status "impl=complete"
        if {$wns < 0 || $whs < 0} { puts $status "timing=FAILED" } else { puts $status "timing=met" }
    }
    puts $status "state=complete"
} message options]

if {$failed} {
    puts $status "state=failed"
    puts $status "error=$message"
    if {[dict exists $options -errorinfo]} { puts $status [dict get $options -errorinfo] }
}
close $status
if {$failed} { error $message }
puts "RF_CLOCK_BD_DONE: $out_dir ($stage)"
