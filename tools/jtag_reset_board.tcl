# Remote hard reset of an RFSoC 4x2 through its JTAG cable.  Run it on the
# host the cable is plugged into (RF-clock-PC for the RF-clock board):
#
#   source /tools/Xilinx/Vivado/2024.1/settings64.sh
#   xsdb tools/jtag_reset_board.tcl 887329260072A
#
# The cable serial selects the board; list them with `xsdb` -> `connect` ->
# `targets -target-properties`.  rst -system reboots the SoC from its boot-mode
# pins (SD card).  With the debugger attached, A53 #0 stops at the reset catch
# when the CSU releases it, so resume it and disconnect before Linux starts the
# other cores.  The PL comes back with PYNQ's boot-time base overlay.

if {[llength $argv] != 1} {
    puts "usage: xsdb jtag_reset_board.tcl CABLE_SERIAL"
    exit 2
}
set cable [lindex $argv 0]

proc pick {name cable} {
    set filter "name == \"$name\" && jtag_cable_serial == \"$cable\""
    if {[llength [targets -target-properties -filter $filter]] != 1} {
        puts "RESET: no unique target '$name' on cable $cable"
        exit 1
    }
    targets -set -filter $filter
}

connect
pick PSU $cable
puts "RESET: rst -system on cable $cable"
rst -system

pick "Cortex-A53 #0" $cable
set st ""
for {set i 0} {$i < 50} {incr i} {
    if {![catch {state} st] && [string match "*Stopped*" $st]} break
    after 100
}
puts "RESET: A53 #0 $st"
if {[string match "*Stopped*" $st]} {
    con
    puts "RESET: A53 #0 [state]"
}
disconnect
puts "RESET: done; Linux should answer ssh in about 30 s"
exit
