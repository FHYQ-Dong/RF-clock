# Generate the RF-clock RFDC profile and its 300 MHz -> 100 MHz clock helpers.
#
# Run from the RF-clock project root after sourcing Vivado 2024.1:
#
#   vivado -mode batch -source FPGA/ip/generate_rfdc_ip.tcl \
#       -tclargs FPGA/ip/rfdc_profile_480mhz
#
# Optional arguments:
#   OUT_DIR [PART] [REFERENCE_MHZ]
#
# The script refuses to overwrite an existing OUT_DIR.  It generates a new
# Vivado project below OUT_DIR/project and does not create a bitstream.

if {[llength $argv] < 1 || [llength $argv] > 3} {
    error "usage: vivado -mode batch -source generate_rfdc_ip.tcl -tclargs OUT_DIR ?PART? ?REFERENCE_MHZ?"
}

if {[version -short] ne "2024.1"} {
    error "this profile is qualified only with Vivado 2024.1; found [version -short]"
}

set out [file normalize [lindex $argv 0]]
set part xczu48dr-ffvg1517-2-e
set reference_mhz 480.000
if {[llength $argv] >= 2} { set part [lindex $argv 1] }
if {[llength $argv] == 3} { set reference_mhz [lindex $argv 2] }

if {[file exists $out]} {
    error "refusing to overwrite existing output directory: $out"
}
if {![string is double -strict $reference_mhz] ||
    $reference_mhz < 100.0 || $reference_mhz > 500.0} {
    error "reference clock must be between 100 and 500 MHz"
}
set reference_mhz [format %.3f $reference_mhz]

file mkdir $out
file copy [info script] [file join $out generate_rfdc_ip.tcl]

set status_path [file join $out status.txt]
set status [open $status_path w]
puts $status "tool=Vivado [version -short]"
puts $status "part=$part"
puts $status "reference_mhz=$reference_mhz"
puts $status "state=starting"
puts $status "runtime_qualified=false"
flush $status

proc snapshot_properties {obj path} {
    set fd [open $path w]
    foreach key [lsort [list_property $obj CONFIG.*]] {
        puts $fd "$key\t[get_property $key $obj]"
    }
    close $fd
}

proc check_property {obj key expected} {
    set actual [get_property $key $obj]
    if {[string is double -strict $expected] &&
        [string is double -strict $actual]} {
        if {[expr {abs(double($actual) - double($expected))}] > 1.0e-6} {
            error "$key mismatch: actual=$actual expected=$expected"
        }
    } elseif {[string tolower $actual] ne [string tolower $expected]} {
        error "$key mismatch: actual=$actual expected=$expected"
    }
}

set failed [catch {
    create_project rfdc_profile [file join $out project] -part $part
    set_param general.maxThreads 2

    create_ip \
        -name usp_rf_data_converter \
        -vendor xilinx.com \
        -library ip \
        -version 2.6 \
        -module_name rfdc_rrc
    set rfdc [get_ips rfdc_rrc]

    # ADC tile 2 / blocks 22 and 23: 4.8 GS/s, decimation 6, 800 MS/s output.
    set rfdc_config [list \
        CONFIG.ADC_Slice00_Enable false \
        CONFIG.ADC_Slice02_Enable false \
        CONFIG.ADC2_PLL_Enable true \
        CONFIG.ADC2_Refclk_Freq $reference_mhz \
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
        CONFIG.DAC2_Refclk_Freq $reference_mhz \
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
    snapshot_properties $rfdc [file join $out rfdc_properties.tsv]

    set requested [open [file join $out requested.tsv] w]
    foreach {key value} $rfdc_config { puts $requested "$key\t$value" }
    close $requested

    foreach {key value} $rfdc_config { check_property $rfdc $key $value }
    foreach {key value} {
        CONFIG.ADC2_Enable       1
        CONFIG.DAC2_Enable       1
        CONFIG.ADC2_Fabric_Freq  100.0
        CONFIG.DAC2_Fabric_Freq  100.0
        CONFIG.ADC2_Refclk_Div   1
        CONFIG.DAC2_Refclk_Div   1
    } { check_property $rfdc $key $value }

    generate_target all $rfdc

    # The RFDC converter/output clock is 300 MHz. These helpers generate the
    # 100 MHz AXIS clock used by H03-H10 and the RFDC s2_axis_aclk port.
    foreach channel {adc dac} {
        create_ip \
            -name clk_wiz \
            -vendor xilinx.com \
            -library ip \
            -version 6.0 \
            -module_name rrc_${channel}_clk
        set clk [get_ips rrc_${channel}_clk]
        set clk_config [list \
            CONFIG.PRIM_IN_FREQ 300.000 \
            CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100.000 \
            CONFIG.PRIM_SOURCE No_buffer \
            CONFIG.USE_RESET true \
            CONFIG.RESET_TYPE ACTIVE_HIGH \
            CONFIG.USE_LOCKED true]
        set_property -dict $clk_config $clk
        foreach {key value} $clk_config { check_property $clk $key $value }
        snapshot_properties $clk [file join $out ${channel}_clock_properties.tsv]
        generate_target all $clk
    }

    set rf_top [file join $out project rfdc_profile.gen sources_1 ip rfdc_rrc synth rfdc_rrc.v]
    if {![file exists $rf_top]} {
        error "generated RFDC HDL was not found: $rf_top"
    }
    set fd [open $rf_top r]
    set rtl [read $fd]
    close $fd
    foreach pattern {
        {input\s+\[255:0\]\s+s20_axis_tdata}
        {input\s+s20_axis_tvalid}
        {output\s+s20_axis_tready}
        {input\s+s2_axis_aclk}
        {input\s+s2_axis_aresetn}
        {output\s+dac20_datapath_overflow}
        {input\s+\[2:0\]\s+dac20_fast_shutdown}
    } {
        if {![regexp $pattern $rtl]} {
            error "missing generated RFDC port: $pattern"
        }
    }

    report_ip_status -file [file join $out ip_status.rpt]
    puts $status "state=complete"
    puts $status "generated=true"
    puts $status "static_checks=true"
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

puts "RFDC_PROFILE_GENERATED: $out"
puts "runtime_qualified=false"
