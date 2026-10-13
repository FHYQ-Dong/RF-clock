# Standalone installed-IP configuration probe, not a board overlay or RF test.
# Creates only a NEW project; never opens or changes the official/P1/P2 projects.
# Usage: vivado -mode batch -source configure_rrc_rfdc.tcl -tclargs NEW_OUT [REF_MHZ]
if {[llength $argv] < 1 || [llength $argv] > 2} {error "expected NEW_OUT and optional reference MHz"}
if {[version -short] ne "2024.1"} {error "requires Vivado 2024.1"}
set out [file normalize [lindex $argv 0]]
if {[file exists $out]} {error "refusing to overwrite existing evidence: $out"}
set reference_mhz 480.000
if {[llength $argv] == 2} {set reference_mhz [lindex $argv 1]}
if {![string is double -strict $reference_mhz] || $reference_mhz < 100 || $reference_mhz > 500} {
    error "reference must be 100..500 MHz; this does not qualify an actual clock generator"
}
# Vivado's choice list compares the formatted string, not just its numeric value.
set reference_mhz [format %.3f $reference_mhz]
file mkdir $out
file copy [info script] $out/configure_rrc_rfdc.tcl
set manifest [open $out/sources.sha256 w]
puts $manifest [exec sha256sum [info script]]
puts $manifest [exec sha256sum $::env(XILINX_VIVADO)/data/ip/xilinx/usp_rf_data_converter_v2_6/component.xml]
puts $manifest [exec sha256sum $::env(XILINX_VIVADO)/data/ip/xilinx/clk_wiz_v6_0/component.xml]
close $manifest
file mkdir $out/metadata
file copy $::env(XILINX_VIVADO)/data/ip/xilinx/usp_rf_data_converter_v2_6/component.xml $out/metadata/rfdc_component.xml
file copy $::env(XILINX_VIVADO)/data/ip/xilinx/clk_wiz_v6_0/component.xml $out/metadata/clk_wiz_component.xml
proc config_snapshot {ip path} {
    set fd [open $path w]
    foreach key [lsort [list_property $ip CONFIG.*]] {puts $fd "$key\t[get_property $key $ip]"}
    close $fd
}
proc check_property {ip key expected} {
    set actual [get_property $key $ip]
    if {[string is double -strict $expected] && [string is double -strict $actual]} {
        if {abs($actual-$expected)>1e-6} {error "field mismatch: $key=$actual expected $expected"}
    } elseif {[string is boolean -strict $expected] && [string is boolean -strict $actual]} {
        if {[expr {!!$actual}] != [expr {!!$expected}]} {error "boolean field mismatch: $key=$actual expected $expected"}
    } elseif {$actual ne $expected} {error "field mismatch: $key=$actual expected $expected"}
}
set status [open $out/status.txt w]
puts $status "data_source=installed_ip_configuration\ndeployable=false\nboard_access=false\nreference_mhz=$reference_mhz"
flush $status
set failed [catch {
    create_project rrc_rfdc_profile $out/project -part xczu48dr-ffvg1517-2-e
    set_param general.maxThreads 2
    create_ip -name usp_rf_data_converter -vendor xilinx.com -library ip -version 2.6 -module_name rfdc_rrc
    set ip [get_ips rfdc_rrc]
    # DAC_A: 800 MS/s complex input, 9.6 GS/s DAC, high-pass IMR.
    # ADC_A candidate: 4.8 GS/s / 6 = 800 MS/s complex output.
    # Both use 8 complex samples per 100 MHz fabric beat. 300 MHz converter
    # clock outputs require separate divide-by-three clocking stages in a BD.
    # The GUI interpolation parameter is PRE-IMR: 6 * 2 = 12 total.
    # GUI NCO 3.8 GHz is accepted in this high-pass mode; 1.0 GHz is rejected.
    # This is not proof of runtime API frequency semantics or analog output.
    set requested [list \
        CONFIG.ADC_Slice00_Enable false CONFIG.ADC_Slice02_Enable false \
        CONFIG.ADC2_PLL_Enable true \
        CONFIG.ADC2_Refclk_Freq $reference_mhz CONFIG.ADC2_Sampling_Rate 4.8 \
        CONFIG.ADC2_Outclk_Freq 300.000 \
        CONFIG.ADC_Slice20_Enable false CONFIG.ADC_Slice22_Enable true \
        CONFIG.ADC_OBS22 false CONFIG.ADC_Data_Type22 1 CONFIG.ADC_Data_Width22 8 \
        CONFIG.ADC_Decimation_Mode22 6 CONFIG.ADC_Mixer_Mode22 0 CONFIG.ADC_Mixer_Type22 2 \
        CONFIG.ADC_NCO_Freq22 -1.000 CONFIG.ADC_Nyquist22 0 \
        CONFIG.DAC_Slice00_Enable false CONFIG.DAC_Slice02_Enable false \
        CONFIG.DAC2_PLL_Enable true \
        CONFIG.DAC2_Refclk_Freq $reference_mhz CONFIG.DAC2_Sampling_Rate 9.6 \
        CONFIG.DAC2_Outclk_Freq 300.000 \
        CONFIG.DAC_Slice20_Enable true CONFIG.DAC_Slice22_Enable false \
        CONFIG.DAC_Data_Type20 0 CONFIG.DAC_Data_Width20 16 CONFIG.DAC_Interpolation_Mode20 6 \
        CONFIG.DAC_Mixer_Mode20 0 CONFIG.DAC_Mixer_Type20 2 CONFIG.DAC_Mode20 2 \
        CONFIG.DAC_NCO_Freq20 3.800 CONFIG.DAC_Nyquist20 1 \
        CONFIG.DAC_RTS true CONFIG.ADC_RTS true]
    set fd [open $out/requested.tsv w]
    foreach {key value} $requested {puts $fd "$key\t$value"}
    close $fd
    set_property -dict $requested $ip
    config_snapshot $ip $out/ip_properties.tsv
    set fd [open $out/readback.tsv w]
    foreach {key value} $requested {puts $fd "$key\t[get_property $key $ip]"}
    foreach key {CONFIG.ADC0_Enable CONFIG.ADC2_Enable CONFIG.DAC0_Enable CONFIG.DAC2_Enable CONFIG.ADC2_Fabric_Freq CONFIG.DAC2_Fabric_Freq} {
        puts $fd "$key\t[get_property $key $ip]"
    }
    close $fd
    foreach {key expected} $requested {check_property $ip $key $expected}
    # Tile enable and fabric frequency are derived, not writeable controls.
    # Vivado NCO fields above use GHz; the runtime driver's Freq uses MHz.
    foreach {key expected} {CONFIG.ADC0_Enable 0 CONFIG.ADC1_Enable 0 CONFIG.ADC2_Enable 1 CONFIG.ADC3_Enable 0 CONFIG.DAC0_Enable 0 CONFIG.DAC1_Enable 0 CONFIG.DAC2_Enable 1 CONFIG.DAC3_Enable 0 CONFIG.ADC2_Fabric_Freq 100.0 CONFIG.DAC2_Fabric_Freq 100.0 CONFIG.ADC2_Refclk_Div 1 CONFIG.DAC2_Refclk_Div 1} {
        check_property $ip $key $expected
    }
    generate_target all $ip
    set rf_top $out/project/rrc_rfdc_profile.gen/sources_1/ip/rfdc_rrc/synth/rfdc_rrc.v
    set fd [open $rf_top r];set rtl [read $fd];close $fd
    foreach pattern {{input\s+\[255:0\]\s+s20_axis_tdata} {output\s+\[127:0\]\s+m22_axis_tdata} {output\s+\[127:0\]\s+m23_axis_tdata} {output\s+dac20_datapath_overflow} {input\s+\[2:0\]\s+dac20_fast_shutdown} {output\s+adc22_datapath_overflow} {output\s+adc23_datapath_overflow} {output\s+adc2_23_over_voltage}} {
        if {![regexp $pattern $rtl]} {error "missing generated RFDC port: $pattern"}
    }
    foreach {key expected} {C_ADC2_FBDIV 20 C_ADC2_OutDiv 2 C_ADC2_Vco 9600.0 C_DAC2_FBDIV 20 C_DAC2_OutDiv 1 C_DAC2_Vco 9600.0} {
        set pattern [format {%s=([^,]+),} $key]
        if {![regexp $pattern $rtl -> actual] || ![string is double -strict $actual] || abs($actual-$expected)>1e-6} {
            error "unexpected generated PLL setting: $key"
        }
    }
    foreach channel {adc dac} {
        create_ip -name clk_wiz -vendor xilinx.com -library ip -version 6.0 -module_name rrc_${channel}_clk
        set clk [get_ips rrc_${channel}_clk]
        set requested_clk [list CONFIG.PRIM_IN_FREQ 300.000 CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100.000 \
            CONFIG.PRIM_SOURCE No_buffer CONFIG.USE_RESET true CONFIG.RESET_TYPE ACTIVE_HIGH CONFIG.USE_LOCKED true]
        set_property -dict $requested_clk $clk
        foreach {key expected} $requested_clk {check_property $clk $key $expected}
        config_snapshot $clk $out/${channel}_clock_properties.tsv
        generate_target all $clk
    }
    report_ip_status -file $out/ip_status.rpt
    set fd [open $out/artifacts.sha256 w]
    foreach path [lsort [concat [glob $out/*.tsv] [glob $out/*status.rpt] [glob $out/project/rrc_rfdc_profile.srcs/sources_1/ip/*/*.xci] [glob $out/project/rrc_rfdc_profile.gen/sources_1/ip/*/synth/*.v] [glob $out/project/rrc_rfdc_profile.gen/sources_1/ip/rrc_*_clk/*.v]]] {
        puts $fd [exec sha256sum $path]
    }
    close $fd
} message options]
if {$failed} {
    puts $status "generated=false\nerror=$message"
    if {[dict exists $options -errorinfo]} {puts $status [dict get $options -errorinfo]}
} else {
    puts $status "generated=true\nstatic_checks=true\nruntime_qualified=false"
}
close $status
if {$failed} {error $message}
puts "RRC_RFDC_PROFILE_GENERATED: configuration artifacts only, not board acceptance"
