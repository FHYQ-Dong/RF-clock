# Build the RF-clock RFDC block design and generate a reusable HDL wrapper.
#
# This script deliberately leaves board-dependent pins at the wrapper boundary:
# the RFDC reference clocks, SYSREF, ADC input, DAC output, AXI-Lite, AXIS, and
# fabric reset are all exposed.  H00_top can therefore instantiate the wrapper
# and connect the project-specific H01-H10 logic without editing generated HDL.
#
# Run from the RF-clock repository root with Vivado 2024.1:
#
#   source /tools/Xilinx/Vivado/2024.1/settings64.sh
#   vivado -mode batch \
#     -source FPGA/ip/create_rfdc_block_design.tcl \
#     -tclargs FPGA/ip/rf_clock_bd_480mhz
#
# Optional arguments:
#   OUT_DIR [PART] [WRAPPER_OUT]
#
# OUT_DIR is never overwritten.  WRAPPER_OUT defaults to
# FPGA/src/rf_clock_bd_wrapper.v relative to the current working directory.

if {[llength $argv] < 1 || [llength $argv] > 3} {
    error "usage: vivado -mode batch -source create_rfdc_block_design.tcl -tclargs OUT_DIR ?PART? ?WRAPPER_OUT?"
}

if {[version -short] ne "2024.1"} {
    error "this script is qualified only with Vivado 2024.1; found [version -short]"
}

set out_dir [file normalize [lindex $argv 0]]
set part xczu48dr-ffvg1517-2-e
set wrapper_out [file normalize [file join [pwd] FPGA src rf_clock_bd_wrapper.v]]
if {[llength $argv] >= 2} { set part [lindex $argv 1] }
if {[llength $argv] == 3} { set wrapper_out [file normalize [lindex $argv 2]] }

if {[file exists $out_dir]} {
    error "refusing to overwrite existing output directory: $out_dir"
}
if {[file exists $wrapper_out]} {
    error "refusing to overwrite existing wrapper: $wrapper_out"
}

file mkdir $out_dir
file mkdir [file dirname $wrapper_out]
file copy [info script] [file join $out_dir create_rfdc_block_design.tcl]

set project_dir [file join $out_dir project]
set project_name rf_clock_bd
set bd_name rf_clock_bd
set status_path [file join $out_dir status.txt]
set status [open $status_path w]
puts $status "tool=Vivado [version -short]"
puts $status "part=$part"
puts $status "state=starting"
puts $status "wrapper=$wrapper_out"
flush $status

proc require_bd_pin {cell pin} {
    set p [get_bd_pins ${cell}/${pin} -quiet]
    if {[llength $p] != 1} {
        error "required BD pin not found: ${cell}/${pin}"
    }
    return $p
}

proc require_bd_intf_pin {cell intf} {
    set p [get_bd_intf_pins ${cell}/${intf} -quiet]
    if {[llength $p] != 1} {
        error "required BD interface pin not found: ${cell}/${intf}"
    }
    return $p
}

proc expose_pin {cell pin name} {
    set p [require_bd_pin $cell $pin]
    # All scalar ports exposed by this wrapper are one bit wide.  Create clock
    # ports with -type clk up front; Vivado makes the type property read-only
    # after creation.
    if {[string equal -nocase [get_property TYPE $p] "clk"]} {
        set ext [create_bd_port -dir [get_property DIR $p] -type clk \
            -freq_hz 100000000 $name]
    } else {
        set ext [create_bd_port -dir [get_property DIR $p] $name]
    }
    connect_bd_net $p $ext
    return $ext
}

proc expose_intf {cell intf name} {
    set before [get_bd_intf_ports -quiet]
    make_bd_intf_pins_external [require_bd_intf_pin $cell $intf]
    set after [get_bd_intf_ports -quiet]
    set new_ports {}
    foreach p $after {
        if {[lsearch -exact $before $p] < 0} { lappend new_ports $p }
    }
    if {[llength $new_ports] != 1} {
        error "expected one new external interface for ${cell}/${intf}; got $new_ports"
    }
    set ext [lindex $new_ports 0]
    set_property name $name $ext
    return $ext
}

proc add_const {name width value} {
    set cell [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 $name]
    set_property -dict [list CONFIG.CONST_WIDTH $width CONFIG.CONST_VAL $value] $cell
    return $cell
}

set failed [catch {
    create_project $project_name $project_dir -part $part
    set_property target_language Verilog [current_project]
    set_param general.maxThreads 2

    create_bd_design $bd_name

    # RFDC: ADC tile 2 block 23 (4.8 GS/s, decimation 6) and DAC tile 2
    # block 20 (9.6 GS/s, interpolation 6, NCO 3.8 GHz).
    set rfdc [create_bd_cell -type ip \
        -vlnv xilinx.com:ip:usp_rf_data_converter:2.6 rfdc_rrc]
    set rfdc_config [list \
        CONFIG.ADC_Slice00_Enable false \
        CONFIG.ADC_Slice02_Enable false \
        CONFIG.ADC2_PLL_Enable true \
        CONFIG.ADC2_Refclk_Freq 480.000 \
        CONFIG.ADC2_Sampling_Rate 4.8 \
        CONFIG.ADC2_Outclk_Freq 300.000 \
        CONFIG.ADC_Slice20_Enable false \
        CONFIG.ADC_Slice22_Enable true \
        CONFIG.ADC_OBS22 false \
        CONFIG.ADC_Data_Type22 1 \
        CONFIG.ADC_Data_Width22 8 \
        CONFIG.ADC_Decimation_Mode22 6 \
        CONFIG.ADC_Mixer_Mode22 0 \
        CONFIG.ADC_Mixer_Type22 2 \
        CONFIG.ADC_NCO_Freq22 -1.000 \
        CONFIG.ADC_Nyquist22 0 \
        CONFIG.DAC_Slice00_Enable false \
        CONFIG.DAC_Slice02_Enable false \
        CONFIG.DAC2_PLL_Enable true \
        CONFIG.DAC2_Refclk_Freq 480.000 \
        CONFIG.DAC2_Sampling_Rate 9.6 \
        CONFIG.DAC2_Outclk_Freq 300.000 \
        CONFIG.DAC_Slice20_Enable true \
        CONFIG.DAC_Slice22_Enable false \
        CONFIG.DAC_Data_Type20 0 \
        CONFIG.DAC_Data_Width20 16 \
        CONFIG.DAC_Interpolation_Mode20 6 \
        CONFIG.DAC_Mixer_Mode20 0 \
        CONFIG.DAC_Mixer_Type20 2 \
        CONFIG.DAC_Mode20 2 \
        CONFIG.DAC_NCO_Freq20 3.800 \
        CONFIG.DAC_Nyquist20 1 \
        CONFIG.DAC_RTS true \
        CONFIG.ADC_RTS true]
    set_property -dict $rfdc_config $rfdc

    # Each tile fabric clock is 300 MHz.  Keep ADC and DAC clock domains
    # separate, then expose their 100 MHz derivatives to the RTL.
    foreach channel {adc dac} {
        set clk [create_bd_cell -type ip \
            -vlnv xilinx.com:ip:clk_wiz:6.0 rrc_${channel}_clk]
        set_property -dict [list \
            CONFIG.PRIM_IN_FREQ 300.000 \
            CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100.000 \
            CONFIG.PRIM_SOURCE No_buffer \
            CONFIG.USE_RESET true \
            CONFIG.RESET_TYPE ACTIVE_HIGH \
            CONFIG.USE_LOCKED true] $clk
    }

    # One active-high fabric reset drives both clock-wizard resets and is
    # inverted for RFDC's active-low AXIS resets.
    set reset_inv [create_bd_cell -type ip \
        -vlnv xilinx.com:ip:util_vector_logic:2.0 fabric_resetn_inv]
    set_property -dict [list CONFIG.C_SIZE 1 CONFIG.C_OPERATION not] $reset_inv
    set fabric_reset [expose_pin rrc_dac_clk reset fabric_reset]
    connect_bd_net $fabric_reset [require_bd_pin rrc_adc_clk reset]
    connect_bd_net $fabric_reset [require_bd_pin fabric_resetn_inv Op1]
    connect_bd_net [require_bd_pin fabric_resetn_inv Res] \
        [require_bd_pin rfdc_rrc s2_axis_aresetn]
    connect_bd_net [require_bd_pin fabric_resetn_inv Res] \
        [require_bd_pin rfdc_rrc m2_axis_aresetn]

    # RFDC reference clocks and SYSREF are board-level differential inputs.
    expose_intf rfdc_rrc sysref_in SYSREF_IN
    expose_intf rfdc_rrc adc2_clk ADC2_CLK
    expose_intf rfdc_rrc dac2_clk DAC2_CLK

    # AXI-Lite is deliberately exposed rather than hidden behind a PS block;
    # H00 can connect it to the PS interconnect or a custom AXI master.
    expose_pin rfdc_rrc s_axi_aclk s_axi_aclk
    expose_pin rfdc_rrc s_axi_aresetn s_axi_aresetn
    expose_intf rfdc_rrc s_axi S_AXI

    # RFDC fabric clocks and clock-wizard lock indications.
    connect_bd_net [require_bd_pin rfdc_rrc clk_dac2] \
        [require_bd_pin rrc_dac_clk clk_in1]
    connect_bd_net [require_bd_pin rfdc_rrc clk_adc2] \
        [require_bd_pin rrc_adc_clk clk_in1]
    connect_bd_net [require_bd_pin rrc_dac_clk clk_out1] \
        [require_bd_pin rfdc_rrc s2_axis_aclk]
    connect_bd_net [require_bd_pin rrc_adc_clk clk_out1] \
        [require_bd_pin rfdc_rrc m2_axis_aclk]
    expose_pin rrc_dac_clk clk_out1 tx_clk_100
    expose_pin rrc_dac_clk locked tx_clk_100_locked
    expose_pin rrc_adc_clk clk_out1 rx_clk_100
    expose_pin rrc_adc_clk locked rx_clk_100_locked

    # H09_pulse_rfdc_bridge connects directly to this AXIS interface.  There
    # is no FIFO here: TREADY must be high before a frame starts, and a
    # mid-frame stall is a TX fault according to the project contract.
    expose_intf rfdc_rrc s20_axis S20_AXIS

    # ADC stream is exposed for the receive/loopback path.
    expose_intf rfdc_rrc m22_axis M22_AXIS
    expose_intf rfdc_rrc m23_axis M23_AXIS

    # Preserve clock/reset association metadata at the wrapper boundary.
    set_property CONFIG.ASSOCIATED_BUSIF {S_AXI} \
        [get_bd_ports s_axi_aclk]
    set_property CONFIG.ASSOCIATED_RESET {s_axi_aresetn} \
        [get_bd_ports s_axi_aclk]
    set_property CONFIG.ASSOCIATED_BUSIF {S20_AXIS} \
        [get_bd_ports tx_clk_100]
    set_property CONFIG.ASSOCIATED_BUSIF {M22_AXIS:M23_AXIS} \
        [get_bd_ports rx_clk_100]

    # This reusable BD has no AXI master/address space.  The RFDC register
    # segment is intentionally left unassigned; a board-level PS/SmartConnect
    # must assign its base address after this wrapper is instantiated.  A
    # BD41-1356 critical warning during validation is therefore expected here.

    # Board RF pins and RFDC health/status outputs.
    expose_intf rfdc_rrc vin2_23 VIN2_23
    expose_intf rfdc_rrc vout20 VOUT20
    foreach p {dac20_datapath_overflow adc22_datapath_overflow \
               adc23_datapath_overflow adc2_23_over_voltage \
               adc2_23_over_range adc2_23_over_threshold1 \
               adc2_23_over_threshold2 adc2_23_cm_over_voltage \
               adc2_23_cm_under_voltage irq} {
        expose_pin rfdc_rrc $p $p
    }

    # Unused event/clear inputs are tied inactive in this reusable wrapper.
    # They can be replaced by H10 wiring in a future board-specific BD.
    set zero1 [add_const tie_zero_1 1 0]
    set zero3 [add_const tie_zero_3 3 0]
    foreach p {dac20_pl_event adc22_pl_event adc23_pl_event \
               adc2_23_clear_ov adc2_23_clear_or} {
        connect_bd_net [get_bd_pins $zero1/dout] [require_bd_pin rfdc_rrc $p]
    }
    connect_bd_net [get_bd_pins $zero3/dout] \
        [require_bd_pin rfdc_rrc dac20_fast_shutdown]

    save_bd_design
    validate_bd_design

    set bd_file [get_files ${bd_name}.bd]
    if {[llength $bd_file] != 1} {
        error "could not locate generated BD file"
    }
    generate_target all $bd_file
    make_wrapper -files $bd_file -top
    set generated_wrapper [file join $project_dir ${project_name}.gen sources_1 bd ${bd_name} hdl ${bd_name}_wrapper.v]
    if {![file exists $generated_wrapper]} {
        set generated_wrapper [lindex [get_files -quiet -filter {NAME =~ *_wrapper.v}] end]
    }
    if {![file exists $generated_wrapper]} {
        error "generated HDL wrapper was not found"
    }
    file copy -force $generated_wrapper $wrapper_out
    report_ip_status -file [file join $out_dir ip_status.rpt]
    write_bd_tcl -force [file join $out_dir ${bd_name}.tcl]

    puts $status "state=complete"
    puts $status "generated=true"
    puts $status "bd=[file join $out_dir ${bd_name}.tcl]"
    puts $status "wrapper=$wrapper_out"
    flush $status
} message options]

if {$failed} {
    puts $status "state=failed"
    puts $status "error=$message"
    if {[dict exists $options -errorinfo]} {
        puts $status [dict get $options -errorinfo]
    }
}
close $status
if {$failed} { error $message }

puts "RF_CLOCK_BD_GENERATED: $out_dir"
puts "WRAPPER: $wrapper_out"
