"""Compare H09 RTL samples against the fixed-point Python reference.

The H09 testbench writes one row for every valid lane.  The expected CSV
columns are::

    beat,cycle,sample_index,lane,sample_I,sample_Q,sample_mask,
    sample_first,sample_last

``cycle`` is only diagnostic metadata and is not used for numerical
comparison.  ``sample_index`` is the absolute sample index for that lane.
Tail beats may contain fewer than eight rows.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


UTILS_DIR = Path(__file__).resolve().parent
if str(UTILS_DIR) not in sys.path:
    sys.path.insert(0, str(UTILS_DIR))

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover - depends on local environment
    raise SystemExit(
        "numpy is required. Install the project environment with "
        "`uv sync`, then run `uv run python utils/compare_h09_rtl.py ...`."
    ) from exc

try:
    from sim_h09_rrc_fir import (
        DEFAULT_CHIP_COUNT,
        DEFAULT_CONFIG_ID,
        DEFAULT_DAC_AMPLITUDE,
        DEFAULT_DATABASE,
        DEFAULT_LFSR_SEED,
        generate_test_chips,
        load_config,
        simulate_full_convolution,
    )
except ImportError as exc:  # pragma: no cover - import error is environment-specific
    raise SystemExit(
        "Could not import utils/sim_h09_rrc_fir.py. Run this script from the "
        "repository checkout."
    ) from exc


REQUIRED_COLUMNS = {
    "beat",
    "cycle",
    "sample_index",
    "lane",
    "sample_I",
    "sample_Q",
    "sample_mask",
    "sample_first",
    "sample_last",
}


def parse_integer(value: str, field: str, line_number: int) -> int:
    """Parse decimal or Verilog-style integer text from a CSV field."""
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


def read_rtl_csv(path: Path) -> list[dict[str, int]]:
    """Read and validate the testbench's one-valid-lane-per-row CSV."""
    if not path.exists():
        raise FileNotFoundError(f"RTL CSV not found: {path}")

    records: list[dict[str, int]] = []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"RTL CSV has no header: {path}")
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"RTL CSV is missing columns: {missing_text}")

        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"line {line_number}: malformed CSV row")
            records.append({
                field: parse_integer(row[field], field, line_number)
                for field in REQUIRED_COLUMNS
            })

    return records


def expected_records(samples, sps: int) -> list[dict[str, int]]:
    """Pack full-rate Python samples into the RTL beat representation."""
    records: list[dict[str, int]] = []
    beat_count = (len(samples) + sps - 1) // sps
    for beat_number in range(beat_count):
        sample_start = beat_number * sps
        keep = min(sps, len(samples) - sample_start)
        beat_mask = (1 << keep) - 1
        beat_first = int(beat_number == 0)
        beat_last = int(beat_number == beat_count - 1)
        for lane in range(keep):
            sample_index = sample_start + lane
            records.append({
                "beat": beat_number,
                "cycle": 0,
                "sample_index": sample_index,
                "lane": lane,
                "sample_I": int(samples[sample_index]),
                "sample_Q": 0,
                "sample_mask": beat_mask,
                "sample_first": beat_first,
                "sample_last": beat_last,
            })
    return records


def compare_records(
    actual: list[dict[str, int]],
    expected: list[dict[str, int]],
) -> tuple[int, int, dict[str, int] | None]:
    """Return mismatch count, maximum I error, and the first mismatch."""
    mismatch_count = 0
    max_abs_error = 0
    first_mismatch: dict[str, int] | None = None
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

    if len(actual) != len(expected):
        mismatch_count += abs(len(actual) - len(expected))
        first_mismatch = {
            "actual_rows": len(actual),
            "expected_rows": len(expected),
        }

    for row_number, (actual_row, expected_row) in enumerate(
        zip(actual, expected),
        start=1,
    ):
        sample_error = abs(actual_row["sample_I"] - expected_row["sample_I"])
        max_abs_error = max(max_abs_error, sample_error)
        different = {
            field: actual_row[field]
            for field in fields
            if actual_row[field] != expected_row[field]
        }
        if different:
            mismatch_count += 1
            if first_mismatch is None:
                first_mismatch = {
                    "row": row_number,
                    "actual_beat": actual_row["beat"],
                    "expected_beat": expected_row["beat"],
                    "actual_sample_index": actual_row["sample_index"],
                    "expected_sample_index": expected_row["sample_index"],
                    "actual_lane": actual_row["lane"],
                    "expected_lane": expected_row["lane"],
                    "actual_sample_I": actual_row["sample_I"],
                    "expected_sample_I": expected_row["sample_I"],
                }

    return mismatch_count, max_abs_error, first_mismatch


def plot_comparison(
    actual: list[dict[str, int]],
    expected: list[dict[str, int]],
    sps: int,
    html_path: Path,
    show: bool,
) -> None:
    """Write an interactive RTL/golden/error comparison plot."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise SystemExit(
            "plotly is required for plotting. Install the project environment "
            "with `uv sync`, or pass `--no-plot`."
        ) from exc

    expected_values = np.asarray([row["sample_I"] for row in expected])
    actual_values = np.asarray([row["sample_I"] for row in actual])
    count = min(len(actual_values), len(expected_values))
    x = np.arange(count) / sps
    error = actual_values[:count] - expected_values[:count]

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.10,
        subplot_titles=("RTL versus Python golden output", "RTL - Python error"),
    )
    figure.add_trace(
        go.Scatter(x=x, y=expected_values[:count], mode="lines", name="Python golden"),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(x=x, y=actual_values[:count], mode="lines", name="RTL"),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(x=x, y=error, mode="lines", name="RTL - golden"),
        row=2,
        col=1,
    )
    figure.update_xaxes(title_text="Sample time (chips)", row=2, col=1)
    figure.update_yaxes(title_text="I amplitude", row=1, col=1)
    figure.update_yaxes(title_text="Error (LSB)", row=2, col=1)
    figure.update_layout(
        title="H09 RTL/Python comparison",
        template="plotly_white",
        height=800,
        hovermode="x unified",
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(str(html_path), include_plotlyjs=True, full_html=True, auto_open=False)
    print(f"comparison plot: {html_path}")
    if show:
        figure.show()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare H09 RTL CSV samples with the Python fixed-point model"
    )
    parser.add_argument("rtl_csv", type=Path, help="CSV emitted by tb_H09_pulse_shape")
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--config-id", type=int, default=DEFAULT_CONFIG_ID)
    parser.add_argument("--chips", type=int, default=DEFAULT_CHIP_COUNT)
    parser.add_argument("--amplitude", type=int, default=DEFAULT_DAC_AMPLITUDE)
    parser.add_argument(
        "--lfsr-seed",
        type=lambda value: int(value, 0),
        default=DEFAULT_LFSR_SEED,
        help="non-zero 16-bit H08-compatible LFSR seed",
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=None,
        help="comparison plot path (default: alongside RTL CSV)",
    )
    parser.add_argument("--no-plot", action="store_true", help="skip Plotly output")
    parser.add_argument("--no-show", action="store_true", help="do not open the plot")
    args = parser.parse_args()

    try:
        actual = read_rtl_csv(args.rtl_csv)
        config, coeffs = load_config(args.db, args.config_id)
        chips = generate_test_chips(args.chips, args.lfsr_seed)
        expected_samples = simulate_full_convolution(
            chips,
            coeffs,
            config["sps"],
            config["frac_bits"],
            args.amplitude,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    expected = expected_records(expected_samples, config["sps"])

    mismatch_count, max_abs_error, first_mismatch = compare_records(actual, expected)
    print(f"RTL rows:        {len(actual)}")
    print(f"Golden rows:     {len(expected)}")
    print(f"Max |I error|:   {max_abs_error}")
    if first_mismatch is not None:
        print(f"First mismatch:  {first_mismatch}")

    if not args.no_plot:
        html_path = args.html or args.rtl_csv.with_name(
            f"{args.rtl_csv.stem}_comparison.html"
        )
        plot_comparison(actual, expected, config["sps"], html_path, not args.no_show)

    if mismatch_count:
        print(f"FAIL: {mismatch_count} mismatched row(s)")
        return 1
    print("PASS: RTL output matches the Python golden model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
