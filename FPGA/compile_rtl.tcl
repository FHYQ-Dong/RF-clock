# Vivado batch compile/synthesis script for RF-clock RTL.
#
# Syntax check only:
#   vivado -mode batch -source FPGA/compile_rtl.tcl -tclargs H09_pulse_shape
#
# Syntax check plus out-of-context synthesis:
#   vivado -mode batch -source FPGA/compile_rtl.tcl \
#       -tclargs H09_pulse_shape <part-name>
#
# The generated H09_rrc_coeffs.vh file is an include file.  It is not passed
# to read_verilog as an independent source; FPGA/data is added to the include
# search path instead.

set script_dir [file normalize [file dirname [info script]]]
set repo_dir   [file normalize [file join $script_dir ..]]
set fpga_dir   $script_dir
set src_dir    [file join $script_dir src]
set data_dir   [file join $script_dir data]
set build_dir  [file join $script_dir build vivado]

if {$argc >= 1} {
    set top [lindex $argv 0]
} else {
    set top H09_pulse_shape
}

if {$argc >= 2} {
    set part [lindex $argv 1]
} else {
    set part ""
}

if {![file isdirectory $src_dir]} {
    error "RTL source directory does not exist: $src_dir"
}

if {![file isdirectory $data_dir]} {
    error "Generated data directory does not exist: $data_dir"
}

set rtl_files [lsort [glob -nocomplain -directory $src_dir *.v]]
if {[llength $rtl_files] == 0} {
    error "No Verilog sources found in $src_dir"
}

file mkdir $build_dir

puts "\[INFO\] repository: $repo_dir"
puts "\[INFO\] top:        $top"
puts "\[INFO\] sources:    [llength $rtl_files] Verilog files"
puts "\[INFO\] include root: $fpga_dir"

# Read all RTL sources.  Header files are found through -include_dirs.
# Do not use -sv here: the generated coefficient file is intentionally written
# in Verilog-2001-compatible syntax.
read_verilog \
    -include_dirs [list $fpga_dir] \
    $rtl_files

puts "\[PASS\] RTL parsing completed"

# Without a part, this script intentionally stops after parsing.  This makes
# it useful for syntax checking on machines without a board-specific part
# selection.  Supplying a part enables out-of-context synthesis below.
if {$part eq ""} {
    puts "\[INFO\] No FPGA part supplied; synthesis was not run."
    puts "\[INFO\] Pass a part as the second tclarg to run synthesis."
    exit 0
}

puts "\[INFO\] running out-of-context synthesis for part $part"
synth_design -top $top -part $part -mode out_of_context

write_checkpoint -force [file join $build_dir "${top}_synth.dcp"]
report_utilization -file [file join $build_dir "${top}_utilization.rpt"]
report_timing_summary -file [file join $build_dir "${top}_timing_summary.rpt"]

puts "\[PASS\] out-of-context synthesis completed"
puts "\[INFO\] outputs: $build_dir"
