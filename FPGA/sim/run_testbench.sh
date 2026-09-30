#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
SRC_DIR="$REPO_DIR/FPGA/src"
SIM_DIR="$REPO_DIR/FPGA/sim"
BUILD_DIR="${TMPDIR:-/tmp}/rfclock_iverilog"
mkdir -p "$BUILD_DIR"

IVERILOG="${IVERILOG:-iverilog}"
VVP="${VVP:-vvp}"

# Scoop may resolve iverilog through a shim while omitting the real app/bin
# directory from PATH.  system.vpi depends on DLLs in that directory, so use
# the real binaries when the standard Scoop layout is detected.
if [[ "$IVERILOG" == "iverilog" ]]; then
    iverilog_path="$(command -v iverilog 2>/dev/null || true)"
    if [[ "$iverilog_path" == */scoop/shims/* ]]; then
        scoop_root="${iverilog_path%/shims/*}"
        scoop_iverilog_bin="$scoop_root/apps/iverilog/current/bin"
        if [[ -x "$scoop_iverilog_bin/iverilog" ]]; then
            export PATH="$scoop_iverilog_bin:$PATH"
            IVERILOG="$scoop_iverilog_bin/iverilog"
            VVP="$scoop_iverilog_bin/vvp"
        elif [[ -x "$scoop_iverilog_bin/iverilog.exe" ]]; then
            export PATH="$scoop_iverilog_bin:$PATH"
            IVERILOG="$scoop_iverilog_bin/iverilog.exe"
            VVP="$scoop_iverilog_bin/vvp.exe"
        fi
    fi
fi

usage() {
    cat <<'EOF'
Usage:
  ./run_testbench.sh             Run all registered testbenches
  ./run_testbench.sh H05         Run one testbench
  ./run_testbench.sh H01 H03     Run selected testbenches

Valid test names: H01 H02 H03 H04 H05 H08 ALL
EOF
}

if [[ $# -eq 0 ]]; then
        tests=(H01 H02 H03 H04 H05 H08)
else
    tests=()
    for requested in "$@"; do
        requested="${requested^^}"
        case "$requested" in
            -h|--help)
                usage
                exit 0
                ;;
            ALL)
                tests=(H01 H02 H03 H04 H05 H08)
                ;;
            H01|H02|H03|H04|H05|H08)
                if [[ ! " ${tests[*]} " =~ " $requested " ]]; then
                    tests+=("$requested")
                fi
                ;;
            *)
                echo "Unknown test '$requested'. Valid names: H01 H02 H03 H04 H05 H08 ALL" >&2
                exit 2
                ;;
        esac
    done
fi

if ! command -v "$IVERILOG" >/dev/null 2>&1; then
    echo "iverilog was not found: $IVERILOG" >&2
    exit 127
fi
if ! command -v "$VVP" >/dev/null 2>&1; then
    echo "vvp was not found: $VVP" >&2
    exit 127
fi

failed=()
for name in "${tests[@]}"; do
    case "$name" in
        H01) top=tb_H01_clock_reset_guard; dut=H01_clock_reset_guard.v; tb=tb_H01_clock_reset_guard.v ;;
        H02) top=tb_H02_AXI_control_regs; dut=H02_AXI_control_regs.v; tb=tb_H02_AXI_control_regs.v ;;
        H03) top=tb_H03_raw_tick; dut=H03_raw_tick.v; tb=tb_H03_raw_tick.v ;;
        H04) top=tb_H04_pps_capture; dut=H04_pps_capture.v; tb=tb_H04_pps_capture.v ;;
        H05) top=tb_H05_scheduled_execute; dut=H05_scheduled_execute.v; tb=tb_H05_scheduled_execute.v ;;
        H08) top=tb_H08_PRN_generator; dut=H08_PRN_generator.v; tb=tb_H08_PRN_generator.v ;;
    esac

    dut_path="$SRC_DIR/$dut"
    tb_path="$SIM_DIR/$tb"
    output_path="$BUILD_DIR/rfclock_${name,,}.out"

    if [[ ! -f "$dut_path" || ! -f "$tb_path" ]]; then
        echo "[FAIL ] $name: missing DUT or testbench source" >&2
        failed+=("$name")
        continue
    fi

    echo "[BUILD] $name"
    compile_log="$output_path.compile.log"
    if ! "$IVERILOG" -g2012 -I"$SRC_DIR" -s "$top" -o "$output_path" \
        "$dut_path" "$tb_path" >"$compile_log" 2>&1; then
        cat "$compile_log" >&2
        echo "[FAIL ] $name (iverilog)" >&2
        failed+=("$name")
        continue
    fi

    if grep -q 'Failed to open .*system\.vpi' "$compile_log"; then
        echo '[WARN ] iverilog emitted the known Scoop system.vpi startup warning; continuing.'
    fi
    grep -v -E 'Failed to open .*system\.vpi|^[[:space:]]*:[[:space:]]*(Operation completed successfully|操作成功完成。)[[:space:]]*$' \
        "$compile_log" || true

    echo "[RUN  ] $name"
    if "$VVP" "$output_path"; then
        echo "[PASS ] $name"
    else
        echo "[FAIL ] $name (vvp)" >&2
        failed+=("$name")
    fi
done

if [[ ${#failed[@]} -ne 0 ]]; then
    echo "Failed tests: ${failed[*]}" >&2
    exit 1
fi

echo "All selected testbenches passed: ${tests[*]}"
