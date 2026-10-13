"""Integer reference model for the H09 eight-lane RRC FIR.

The model reads the exact quantized coefficients from H09_config.db. It does
not regenerate coefficients from beta, so its result can be compared directly
with the eventual RTL implementation.
"""

import argparse
import csv
import html
import sqlite3
import webbrowser
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "FPGA" / "data" / "H09_config.db"
DEFAULT_CONFIG_ID = 2261112279
DEFAULT_DAC_AMPLITUDE_BASE_2 = 9
DEFAULT_DAC_AMPLITUDE = 1 << DEFAULT_DAC_AMPLITUDE_BASE_2
DEFAULT_CHIP_COUNT = 4096
DEFAULT_LFSR_SEED = 0xACE1


def load_config(database_path: Path, config_id: int):
    """Load one H09 configuration and its ordered integer FIR coefficients."""
    if not database_path.exists():
        raise FileNotFoundError(f"configuration database not found: {database_path}")

    with sqlite3.connect(database_path) as conn:
        row = conn.execute(
            """
            SELECT
                H09_RRC_SAMPLES_PER_SYMBOL,
                H09_RRC_SPAN_CHIPS,
                H09_RRC_TAP_COUNT,
                H09_RRC_COEFF_BITS,
                H09_RRC_FRAC_BITS,
                H09_RRC_BETA
            FROM H09_config
            WHERE H09_CONFIG_ID = ?
            """,
            (config_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"configuration ID not found: {config_id}")

        rows = conn.execute(
            """
            SELECT TAP_INDEX, COEFFICIENT
            FROM H09_coeff
            WHERE H09_CONFIG_ID = ?
            ORDER BY TAP_INDEX
            """,
            (config_id,),
        ).fetchall()

    sps, span_chips, tap_count, coeff_bits, frac_bits, beta = row
    coeffs = [int(coefficient) for _, coefficient in rows]
    tap_indices = [int(tap_index) for tap_index, _ in rows]

    if len(coeffs) != tap_count:
        raise ValueError(
            f"expected {tap_count} coefficients, got {len(coeffs)}"
        )
    if tap_indices != list(range(tap_count)):
        raise ValueError("tap indices are not contiguous")
    if coeffs != coeffs[::-1]:
        raise ValueError("FIR coefficients are not symmetric")

    minimum = -(1 << (coeff_bits - 1))
    maximum = (1 << (coeff_bits - 1)) - 1
    if any(value < minimum or value > maximum for value in coeffs):
        raise ValueError("coefficient exceeds the configured signed range")

    config = {
        "id": int(config_id),
        "sps": int(sps),
        "span_chips": int(span_chips),
        "tap_count": int(tap_count),
        "coeff_bits": int(coeff_bits),
        "frac_bits": int(frac_bits),
        "beta": float(beta),
    }
    return config, coeffs


def round_shift_signed(value: int, frac_bits: int) -> int:
    """Round a signed integer to the nearest value after a right shift."""
    value = int(value)
    offset = 1 << (frac_bits - 1)

    if value >= 0:
        return (value + offset) >> frac_bits
    return -(((-value) + offset) >> frac_bits)


def quantize_output(
    accumulator: int,
    amplitude: int,
    frac_bits: int,
) -> int:
    """Apply power-of-two amplitude scaling and signed 16-bit saturation."""
    amplitude = int(amplitude)
    if amplitude <= 0 or (amplitude & (amplitude - 1)) != 0:
        raise ValueError("amplitude must be a positive power of two")
    amplitude_base_2 = amplitude.bit_length() - 1
    product = int(accumulator) << amplitude_base_2
    value = round_shift_signed(product, frac_bits)
    return max(-32768, min(32767, value))


def simulate_full_convolution(
    chips,
    coeffs,
    sps: int,
    frac_bits: int,
    amplitude: int,
):
    """Simulate the fixed-point RRC FIR at the full sample rate."""
    if len(chips) == 0:
        raise ValueError("chips cannot be empty")

    # The last chip is at sample index (N - 1) * SPS.
    input_length = (len(chips) - 1) * sps + 1
    upsampled = np.zeros(input_length, dtype=np.int64)
    upsampled[::sps] = chips

    accumulator = np.convolve(
        upsampled,
        np.asarray(coeffs, dtype=np.int64),
    )

    return np.asarray([
        quantize_output(value, amplitude, frac_bits)
        for value in accumulator
    ], dtype=np.int16)


def generate_test_chips(count: int, seed: int = DEFAULT_LFSR_SEED):
    """Generate BPSK chips with the same 16-bit LFSR as H08.

    H08 outputs the old state bit 0 and advances the state only when a chip
    is accepted.  Its feedback is ``old[0] ^ old[2] ^ old[3] ^ old[5]`` and
    the next state is ``{feedback, old[15:1]}``.  This function emits +1 for
    bit one and -1 for bit zero, matching the BPSK convention used by the
    fixed-point reference model.
    """
    if count <= 0:
        raise ValueError("chip count must be positive")
    if not 0 < seed <= 0xFFFF:
        raise ValueError("LFSR seed must be a non-zero 16-bit value")

    chips = np.empty(count, dtype=np.int64)
    state = int(seed)
    for index in range(count):
        chips[index] = 1 if (state & 1) else -1
        feedback = (
            ((state >> 0) ^ (state >> 2) ^ (state >> 3) ^ (state >> 5))
            & 1
        )
        state = ((feedback << 15) | (state >> 1)) & 0xFFFF
    return chips


def _build_python_figure(
    samples,
    chips,
    coeffs,
    sps: int,
    frac_bits: int,
    amplitude: int,
    sample_rate_hz: float | None = None,
):
    """Build the three-panel Python-reference diagnostic figure.

    Without an RF sample rate in the configuration database, the frequency
    axis is expressed in cycles per chip.  When ``sample_rate_hz`` is given,
    all frequency axes are shown in MHz instead. The Plotly figure remains
    interactive when it is saved as an HTML file.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    sample_time = np.arange(len(samples)) / sps
    chip_time = np.arange(len(chips))

    window = np.hanning(len(samples))
    windowed_samples = np.asarray(samples, dtype=np.float64) * window
    spectrum = np.fft.rfft(windowed_samples)
    magnitude = np.abs(spectrum)
    peak = np.max(magnitude)
    spectrum_db = 20.0 * np.log10(np.maximum(magnitude / peak, 1e-12))

    # Zero-padding only interpolates the plotted response; it does not add
    # information beyond the 81 quantized FIR coefficients.
    coefficient_values = np.asarray(coeffs, dtype=np.float64) / (1 << frac_bits)
    coefficient_fft_size = max(4096, 8 * len(coefficient_values))
    transfer = np.fft.rfft(coefficient_values, n=coefficient_fft_size)
    transfer_magnitude = np.abs(transfer)
    transfer_peak = np.max(transfer_magnitude)
    transfer_db = 20.0 * np.log10(
        np.maximum(transfer_magnitude / transfer_peak, 1e-12)
    )

    if sample_rate_hz is None:
        frequency = np.fft.rfftfreq(len(samples), d=1.0 / sps)
        frequency_label = "Frequency (cycles/chip)"
    else:
        frequency = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate_hz)
        frequency = frequency / 1e6
        frequency_label = "Frequency (MHz)"

    transfer_frequency = np.fft.rfftfreq(
        coefficient_fft_size,
        d=(1.0 / sps) if sample_rate_hz is None else (1.0 / sample_rate_hz),
    )
    if sample_rate_hz is not None:
        transfer_frequency = transfer_frequency / 1e6

    figure = make_subplots(
        rows=3,
        cols=1,
        vertical_spacing=0.09,
        subplot_titles=(
            "H09 RRC output waveform",
            "H09 output spectrum (Hann-windowed FFT)",
            "H09 FIR transfer function |H(f)| from quantized h[n]",
        ),
    )
    figure.add_trace(
        go.Scatter(
            x=sample_time,
            y=samples,
            mode="lines",
            name="RRC output",
            hovertemplate="chip time=%{x:.4f}<br>amplitude=%{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=chip_time,
            y=chips * amplitude,
            mode="lines",
            line_shape="hv",
            opacity=0.45,
            name="BPSK chips",
            hovertemplate="chip=%{x}<br>amplitude=%{y}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=frequency,
            y=spectrum_db,
            mode="lines",
            name="Output spectrum",
            hovertemplate=(
                "frequency=%{x:.6g}<br>magnitude=%{y:.2f} dB<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=transfer_frequency,
            y=transfer_db,
            mode="lines",
            name="FIR transfer function",
            hovertemplate=(
                "frequency=%{x:.6g}<br>magnitude=%{y:.2f} dB<extra></extra>"
            ),
        ),
        row=3,
        col=1,
    )

    figure.update_xaxes(title_text="Chip time", row=1, col=1, showgrid=True)
    figure.update_yaxes(title_text="Amplitude", row=1, col=1, showgrid=True)
    for row in (2, 3):
        figure.update_xaxes(
            title_text=frequency_label,
            row=row,
            col=1,
            showgrid=True,
        )
        figure.update_yaxes(
            title_text="Magnitude (dB, normalized)",
            range=[-100, 3],
            row=row,
            col=1,
            showgrid=True,
        )

    figure.update_layout(
        title="H09 RRC diagnostics",
        template="plotly_white",
        height=1100,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    return figure


VERILOG_CSV_COLUMNS = {
    "beat",
    "sample_index",
    "lane",
    "sample_I",
    "sample_Q",
    "sample_mask",
    "sample_first",
    "sample_last",
}


def _parse_csv_integer(value: str, field: str, line_number: int) -> int:
    text = value.strip()
    try:
        return int(text, 0)
    except ValueError:
        try:
            return int(text, 10)
        except ValueError as exc:
            raise ValueError(
                f"line {line_number}: {field} is not an integer: {value!r}"
            ) from exc


def _parse_cli_integer(value: str) -> int:
    """Parse decimal or Python-style hexadecimal command-line integers."""
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid integer value: {value!r}"
        ) from exc


def load_verilog_results(path: Path) -> list[dict[str, int]]:
    """Read the valid-lane CSV emitted by ``tb_H09_pulse_shape``."""
    if not path.exists():
        raise FileNotFoundError(f"Verilog result file not found: {path}")

    records: list[dict[str, int]] = []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"Verilog result file has no CSV header: {path}")
        missing = VERILOG_CSV_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                "Verilog result CSV is missing columns: "
                + ", ".join(sorted(missing))
            )

        for line_number, row in enumerate(reader, start=2):
            if any(row.get(field) in (None, "") for field in VERILOG_CSV_COLUMNS):
                raise ValueError(f"line {line_number}: malformed Verilog CSV row")
            records.append({
                field: _parse_csv_integer(row[field], field, line_number)
                for field in VERILOG_CSV_COLUMNS
            })
    return records


def _expected_verilog_records(samples, sps: int) -> list[dict[str, int]]:
    expected_count = len(samples)
    beat_count = (expected_count + sps - 1) // sps
    records = []
    for sample_index, sample_value in enumerate(samples):
        beat = sample_index // sps
        lane = sample_index % sps
        keep = min(sps, expected_count - beat * sps)
        records.append({
            "beat": beat,
            "sample_index": sample_index,
            "lane": lane,
            "sample_I": int(sample_value),
            "sample_Q": 0,
            "sample_mask": (1 << keep) - 1,
            "sample_first": int(beat == 0),
            "sample_last": int(beat == beat_count - 1),
        })
    return records


def compare_verilog_results(
    records: list[dict[str, int]],
    python_samples,
    sps: int,
) -> dict:
    """Compare RTL valid-lane records with the flattened Python output."""
    expected = _expected_verilog_records(python_samples, sps)
    mismatch_count = abs(len(records) - len(expected))
    max_abs_error = 0
    first_mismatch = None
    fields = (
        "beat",
        "sample_index",
        "lane",
        "sample_I",
        "sample_Q",
        "sample_mask",
        "sample_first",
        "sample_last",
    )

    for row_number, (actual, reference) in enumerate(
        zip(records, expected),
        start=1,
    ):
        sample_error = abs(actual["sample_I"] - reference["sample_I"])
        max_abs_error = max(max_abs_error, sample_error)
        differences = {
            field: {
                "actual": actual[field],
                "expected": reference[field],
            }
            for field in fields
            if actual[field] != reference[field]
        }
        if differences:
            mismatch_count += 1
            if first_mismatch is None:
                first_mismatch = {
                    "row": row_number,
                    "differences": differences,
                }

    if len(records) != len(expected) and first_mismatch is None:
        first_mismatch = {
            "actual_rows": len(records),
            "expected_rows": len(expected),
        }

    return {
        "passed": mismatch_count == 0,
        "actual_count": len(records),
        "expected_count": len(expected),
        "mismatch_count": mismatch_count,
        "max_abs_error": max_abs_error,
        "first_mismatch": first_mismatch,
    }


def _build_verilog_figure(
    records: list[dict[str, int]],
    python_samples,
    sps: int,
    sample_rate_hz: float | None,
):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    verilog_samples = np.asarray(
        [row["sample_I"] for row in records], dtype=np.int64
    )
    python_values = np.asarray(python_samples, dtype=np.int64)
    verilog_x = np.asarray(
        [row["sample_index"] for row in records], dtype=np.float64
    ) / sps
    python_x = np.arange(len(python_values), dtype=np.float64) / sps
    count = min(len(verilog_samples), len(python_values))
    error = verilog_samples[:count] - python_values[:count]

    if len(verilog_samples):
        windowed = verilog_samples.astype(np.float64) * np.hanning(
            len(verilog_samples)
        )
        magnitude = np.abs(np.fft.rfft(windowed))
        peak = np.max(magnitude)
        spectrum_db = (
            np.zeros_like(magnitude)
            if peak == 0
            else 20.0 * np.log10(np.maximum(magnitude / peak, 1e-12))
        )
        if sample_rate_hz is None:
            frequency = np.fft.rfftfreq(len(verilog_samples), d=1.0 / sps)
            frequency_label = "Frequency (cycles/chip)"
        else:
            frequency = (
                np.fft.rfftfreq(len(verilog_samples), d=1.0 / sample_rate_hz)
                / 1e6
            )
            frequency_label = "Frequency (MHz)"
    else:
        frequency = np.empty(0)
        spectrum_db = np.empty(0)
        frequency_label = (
            "Frequency (MHz)" if sample_rate_hz is not None
            else "Frequency (cycles/chip)"
        )

    figure = make_subplots(
        rows=3,
        cols=1,
        vertical_spacing=0.09,
        subplot_titles=(
            "Verilog output waveform",
            "Verilog output spectrum (Hann-windowed FFT)",
            "Verilog - Python sample error",
        ),
    )
    figure.add_trace(
        go.Scatter(x=verilog_x, y=verilog_samples, mode="lines", name="Verilog output"),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(x=python_x, y=python_values, mode="lines", name="Python golden"),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=frequency,
            y=spectrum_db,
            mode="lines",
            name="Verilog output spectrum",
        ),
        row=2,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=np.arange(count, dtype=np.float64) / sps,
            y=error,
            mode="lines",
            name="Verilog - Python",
        ),
        row=3,
        col=1,
    )
    figure.update_xaxes(title_text="Sample time (chips)", row=1, col=1, showgrid=True)
    figure.update_yaxes(title_text="Amplitude", row=1, col=1, showgrid=True)
    figure.update_xaxes(title_text=frequency_label, row=2, col=1, showgrid=True)
    figure.update_yaxes(
        title_text="Magnitude (dB, normalized)",
        range=[-100, 3],
        row=2,
        col=1,
    )
    figure.update_xaxes(title_text="Sample time (chips)", row=3, col=1, showgrid=True)
    figure.update_yaxes(title_text="Error (LSB)", row=3, col=1, showgrid=True)
    figure.update_layout(
        title="H09 Verilog simulation",
        template="plotly_white",
        height=1100,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return figure


def _comparison_html(comparison: dict | None, path: Path | None) -> str:
    if comparison is None:
        return (
            "<div class='not-run'>Verilog validation: not run "
            "(the --verilog-results option was not provided).</div>"
        )

    status = "PASS" if comparison["passed"] else "FAIL"
    status_class = "status-pass" if comparison["passed"] else "status-fail"
    status_line = (
        "Verilog validation: "
        f"<span class='{status_class}'>{status}</span>"
    )
    lines = [
        f"Result file: {path}",
        f"Sample count: Verilog {comparison['actual_count']} / "
        f"Python {comparison['expected_count']}",
        f"Maximum absolute error: {comparison['max_abs_error']} LSB",
        f"Mismatched rows: {comparison['mismatch_count']}",
    ]
    if comparison["first_mismatch"] is not None:
        lines.append(f"First mismatch: {comparison['first_mismatch']}")
    return "<div class='comparison'>" + "<br>".join(
        [status_line] + [html.escape(line) for line in lines]
    ) + "</div>"


def _metadata_html(rows: list[tuple[str, object]]) -> str:
    """Render simulation configuration and result metadata as plain HTML."""
    table_rows = []
    for label, value in rows:
        table_rows.append(
            "<tr>"
            f"<th scope='row'>{html.escape(str(label))}</th>"
            f"<td>{html.escape(str(value))}</td>"
            "</tr>"
        )
    return "<table class='metadata'><tbody>" + "".join(table_rows) + "</tbody></table>"


def plot_diagnostics(
    samples,
    chips,
    coeffs,
    sps: int,
    frac_bits: int,
    amplitude: int,
    sample_rate_hz: float | None = None,
    html_path: Path | None = None,
    show: bool = True,
    verilog_records: list[dict[str, int]] | None = None,
    verilog_results_path: Path | None = None,
    comparison: dict | None = None,
    metadata: list[tuple[str, object]] | None = None,
):
    """Write Python and optional Verilog diagnostics as one HTML document."""
    import plotly.io as pio

    python_figure = _build_python_figure(
        samples,
        chips,
        coeffs,
        sps,
        frac_bits,
        amplitude,
        sample_rate_hz,
    )
    python_html = pio.to_html(
        python_figure,
        full_html=False,
        include_plotlyjs=True,
    )
    if verilog_records is None:
        verilog_html = _comparison_html(None, None)
    else:
        verilog_figure = _build_verilog_figure(
            verilog_records,
            samples,
            sps,
            sample_rate_hz,
        )
        verilog_html = _comparison_html(comparison, verilog_results_path)
        verilog_html += pio.to_html(
            verilog_figure,
            full_html=False,
            include_plotlyjs=False,
        )

    metadata_html = ""
    if metadata is not None:
        metadata_html = (
            "<h2>Configuration and simulation summary</h2>"
            + _metadata_html(metadata)
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>H09 RRC simulation diagnostics</title>
<style>
body {{ font-family: sans-serif; margin: 1.5rem; }}
section {{ margin-bottom: 3rem; }}
section h1 {{ border-bottom: 2px solid #555; padding-bottom: .4rem; }}
h2 {{ margin-top: 1.5rem; }}
.metadata {{ border-collapse: collapse; margin: 1rem 0 1.5rem; }}
.metadata th, .metadata td {{ padding: .2rem 1.5rem .2rem 0; text-align: left;
  vertical-align: top; }}
.metadata th {{ font-weight: 600; }}
.comparison, .not-run {{ background: #f3f3f3; border-left: 4px solid #777;
  padding: .8rem 1rem; line-height: 1.6; }}
.comparison .status-pass {{ color: #198754; font-weight: 600; }}
.comparison .status-fail {{ color: #dc3545; font-weight: 600; }}
</style>
</head>
<body>
<section>
<h1>Python simulation</h1>
{metadata_html}
{python_html}
</section>
<section>
<h1>Verilog simulation</h1>
{verilog_html}
</section>
</body>
</html>
"""

    if html_path is not None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(document, encoding="utf-8")
        print(f"interactive plot: {html_path}")

    if show:
        if html_path is not None:
            webbrowser.open(html_path.resolve().as_uri())
        else:
            python_figure.show()


def main():
    parser = argparse.ArgumentParser(
        description="Simulate the H09 fixed-point eight-lane RRC FIR"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE,
        help="H09 SQLite database path",
    )
    parser.add_argument(
        "--config-id",
        type=int,
        default=DEFAULT_CONFIG_ID,
        help="H09_CONFIG_ID to load",
    )
    parser.add_argument(
        "--chips",
        type=int,
        default=DEFAULT_CHIP_COUNT,
        help=(
            "number of test chips generated by the 16-bit H08-compatible "
            f"LFSR (default: {DEFAULT_CHIP_COUNT})"
        ),
    )
    parser.add_argument(
        "--lfsr-seed",
        type=_parse_cli_integer,
        default=DEFAULT_LFSR_SEED,
        help=(
            "non-zero 16-bit H08-compatible LFSR seed; decimal or 0x... "
            f"(default: 0x{DEFAULT_LFSR_SEED:04X})"
        ),
    )
    parser.add_argument(
        "--amplitude",
        type=int,
        default=DEFAULT_DAC_AMPLITUDE,
        help=(
            "DAC amplitude (must be a positive power of two; it is not "
            "currently stored in H09_config.db)"
        ),
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="run checks without generating or opening a plot",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="generate/save the Plotly figure without opening a browser window",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=Path.cwd() / "H09_rrc_sim.html",
        help=(
            "output path for the standalone interactive HTML figure "
            "(default: ./H09_rrc_sim.html)"
        ),
    )
    parser.add_argument(
        "--verilog-results",
        "--verilog-result",
        "--verilog-csv",
        dest="verilog_results",
        type=Path,
        default=None,
        help=(
            "optional h09_rtl_samples.csv emitted by the Verilog testbench; "
            "when provided, compare it with the Python result and add a "
            "Verilog section to the HTML"
        ),
    )
    parser.add_argument(
        "--sample-rate-hz",
        type=float,
        default=None,
        help=(
            "sample-stream rate for the spectrum axis; without it, frequency "
            "is shown in cycles per chip"
        ),
    )
    args = parser.parse_args()

    if args.sample_rate_hz is not None and args.sample_rate_hz <= 0:
        parser.error("--sample-rate-hz must be positive")
    if args.amplitude <= 0 or (args.amplitude & (args.amplitude - 1)) != 0:
        parser.error("--amplitude must be a positive power of two")
    config, coeffs = load_config(args.db, args.config_id)
    try:
        chips = generate_test_chips(args.chips, args.lfsr_seed)
    except ValueError as exc:
        parser.error(str(exc))

    samples = simulate_full_convolution(
        chips,
        coeffs,
        config["sps"],
        config["frac_bits"],
        args.amplitude,
    )

    expected_samples = len(chips) * config["sps"] + (
        config["tap_count"] - config["sps"]
    )

    assert len(samples) == expected_samples
    output_beats = (
        len(samples) + config["sps"] - 1
    ) // config["sps"]

    verilog_records = None
    verilog_comparison = None
    if args.verilog_results is None:
        print(
            "Verilog validation: not performed "
            "(no --verilog-results path was provided)."
        )
    else:
        try:
            verilog_records = load_verilog_results(args.verilog_results)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        verilog_comparison = compare_verilog_results(
            verilog_records,
            samples,
            config["sps"],
        )
        status = "PASS" if verilog_comparison["passed"] else "FAIL"
        print(
            f"Verilog validation: {status} "
            f"({verilog_comparison['actual_count']} RTL samples, "
            f"{verilog_comparison['mismatch_count']} mismatches, "
            f"max error {verilog_comparison['max_abs_error']} LSB)"
        )
        if verilog_comparison["first_mismatch"] is not None:
            print(f"first mismatch: {verilog_comparison['first_mismatch']}")

    print(f"config id:        {config['id']}")
    print(f"beta:             {config['beta']}")
    print(f"SPS:              {config['sps']}")
    print(f"tap count:        {config['tap_count']}")
    print(f"chip count:       {len(chips)}")
    print(f"output samples:   {len(samples)}")
    print(f"output beats:     {output_beats}")
    print("full-rate reference: PASS")

    metadata = [
        ("Database", args.db),
        ("Config ID", config["id"]),
        ("RRC beta", config["beta"]),
        ("RRC span (chips)", config["span_chips"]),
        ("Samples per symbol (SPS)", config["sps"]),
        ("Tap count", config["tap_count"]),
        ("Coefficient width", f"{config['coeff_bits']} bits"),
        ("Fractional width", f"{config['frac_bits']} bits"),
        (
            "Coefficient format",
            f"signed Q{config['coeff_bits'] - config['frac_bits']}."
            f"{config['frac_bits']}",
        ),
        ("DAC amplitude", args.amplitude),
        (
            "DAC amplitude base-2",
            f"2^{args.amplitude.bit_length() - 1}",
        ),
        (
            "Scaling formula",
            "saturate(round(accumulator * DAC amplitude / 2^fractional width))",
        ),
        ("Output format", "signed 16-bit with saturation"),
        ("Quantization", "nearest rounding after power-of-two scaling"),
        ("Output range", "-32768 to 32767"),
        ("BPSK chip mapping", "LFSR bit 1 -> +1, bit 0 -> -1"),
        ("LFSR seed", f"0x{args.lfsr_seed:04X}"),
        ("Chip count", len(chips)),
        ("Output samples", len(samples)),
        ("Output beats", output_beats),
        ("Full-rate reference", "PASS"),
        (
            "Spectrum window",
            "Hann",
        ),
        (
            "Sample rate",
            f"{args.sample_rate_hz} Hz"
            if args.sample_rate_hz is not None
            else "not specified",
        ),
    ]

    if not args.no_plot:
        plot_diagnostics(
            samples,
            chips,
            coeffs,
            config["sps"],
            config["frac_bits"],
            args.amplitude,
            args.sample_rate_hz,
            html_path=args.html,
            show=not args.no_show,
            verilog_records=verilog_records,
            verilog_results_path=args.verilog_results,
            comparison=verilog_comparison,
            metadata=metadata,
        )

    if verilog_comparison is not None and not verilog_comparison["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
