#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
FPGA_DIR="$REPO_DIR/FPGA"
SRC_DIR="$REPO_DIR/FPGA/src"
DATA_DIR="$REPO_DIR/FPGA/data"
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
  ./run_testbench.sh H09 --h09-output-dir ./FPGA/data

Valid test names: H01 H02 H03 H04 H05 H08 H09 ALL

H09 defaults to FPGA/data for its three CSV output files. Set
H09_OUTPUT_DIR or pass --h09-output-dir to override that directory.
EOF
}

H09_OUTPUT_DIR="${H09_OUTPUT_DIR:-$DATA_DIR}"

if [[ $# -eq 0 ]]; then
        tests=(H01 H02 H03 H04 H05 H08 H09)
else
    tests=()
    skip_h09_dir=0
    for requested in "$@"; do
        if [[ $skip_h09_dir -eq 1 ]]; then
            H09_OUTPUT_DIR="$requested"
            skip_h09_dir=0
            continue
        fi
        if [[ "$requested" == "--h09-output-dir" ]]; then
            skip_h09_dir=1
            continue
        fi
        requested="${requested^^}"
        case "$requested" in
            -h|--help)
                usage
                exit 0
                ;;
            ALL)
                tests=(H01 H02 H03 H04 H05 H08 H09)
                ;;
            H01|H02|H03|H04|H05|H08|H09)
                if [[ ! " ${tests[*]} " =~ " $requested " ]]; then
                    tests+=("$requested")
                fi
                ;;
            *)
                echo "Unknown test '$requested'. Valid names: H01 H02 H03 H04 H05 H08 H09 ALL" >&2
                exit 2
                ;;
        esac
    done
    if [[ $skip_h09_dir -eq 1 ]]; then
        echo "--h09-output-dir requires a directory" >&2
        exit 2
    fi
fi

mkdir -p "$H09_OUTPUT_DIR"

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
        H09) top=tb_H09_pulse_shape; dut=H09_pulse_shape.v; tb=tb_H09_pulse_shape.v ;;
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
    if ! "$IVERILOG" -g2012 -I"$FPGA_DIR" -s "$top" -o "$output_path" \
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
    run_args=("$output_path")
    if [[ "$name" == "H09" ]]; then
        run_args+=(
            "+SAMPLE_FILE=$H09_OUTPUT_DIR/h09_rtl_samples.csv"
            "+BEAT_FILE=$H09_OUTPUT_DIR/h09_rtl_beats.csv"
            "+INPUT_FILE=$H09_OUTPUT_DIR/h09_rtl_input.csv"
        )
    fi
    if "$VVP" "${run_args[@]}"; then
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
