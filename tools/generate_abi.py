"""Generate ``rf_clock.pl.abi`` from the RTL ABI source files.

Usage from the repository root::

    python tools/generate_abi.py --write
    python tools/generate_abi.py --check

The parser intentionally accepts only literal Verilog localparams.  A newly
introduced expression must be reviewed rather than silently guessed by the
generator.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGS = ROOT / "PL" / "src" / "p_regs.vh"
DEFS = ROOT / "PL" / "src" / "p_defs.vh"
OUT = ROOT / "src" / "rf_clock" / "pl" / "abi.py"

LOCALPARAM = re.compile(
    r"localparam\s+(?:(?:\[[^]]+\]|integer)\s+)?"
    r"(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>[^;]+);"
)
VERILOG_LITERAL = re.compile(r"(?:(?P<bits>\d+)\s*)?'(?P<base>[bodhBODH])(?P<digits>[0-9a-fA-F_xXzZ]+)")


def parse_literal(raw: str) -> int:
    value = raw.strip()
    match = VERILOG_LITERAL.fullmatch(value)
    if match is None:
        if re.fullmatch(r"\d+", value):
            return int(value, 10)
        raise ValueError(f"unsupported localparam expression: {raw!r}")
    base = {"b": 2, "o": 8, "d": 10, "h": 16}[match.group("base").lower()]
    digits = match.group("digits").replace("_", "")
    if any(char.lower() in "xz" for char in digits):
        raise ValueError(f"unknown (X/Z) localparam value: {raw!r}")
    return int(digits, base)


def parse_source(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for match in LOCALPARAM.finditer(path.read_text(encoding="utf-8")):
        name = match.group("name")
        if name in result:
            raise ValueError(f"duplicate localparam {name} in {path}")
        result[name] = parse_literal(match.group("value"))
    return result


def parse_sources() -> dict[str, int]:
    result = parse_source(REGS)
    for name, value in parse_source(DEFS).items():
        if name in result:
            raise ValueError(f"duplicate ABI symbol across RTL sources: {name}")
        result[name] = value
    return result


def render(values: dict[str, int]) -> str:
    reg_names = [name for name in values if name.startswith("REG_")]
    control = [name for name in values if name.startswith("CTRL_")]
    status = [name for name in values if name.startswith("STAT_")]
    pps_config = [name for name in values if name.startswith("PPSCFG_")]
    pps_inputs = [name for name in values if name.startswith("PPSIN_")]
    build_version = [name for name in values if name.startswith("BUILDVER_")]
    states = ["ST_IDLE", "ST_ARMED", "ST_RUNNING", "ST_DRAIN", "ST_DONE", "ST_FAULT"]
    results = [name for name in values if name.startswith("RC_")]
    rx_flags = [name for name in values if name.startswith("RXF_")]
    faults = [name for name in values if name.startswith("FB_")]

    def indented(names: list[str], prefix: str = "") -> str:
        return "\n".join(
            f"        {name.removeprefix(prefix)} = 0x{values[name]:X}" for name in names
        )

    return f'''"""Generated from PL/src/p_regs.vh and p_defs.vh; do not edit."""

from enum import IntEnum, IntFlag

class ABI:
    """ARM-visible register offsets and bit positions from the RTL ABI."""

    class REG:
{indented(reg_names, "REG_")}

    class CTRL:
{indented(control, "CTRL_")}

    class STAT:
{indented(status, "STAT_")}

    class PPSCFG:
{indented(pps_config, "PPSCFG_")}

    class PPSIN:
{indented(pps_inputs, "PPSIN_")}

    class BUILDVER:
{indented(build_version, "BUILDVER_")}

    RX_RESULT_FLAGS_LSB = 0x{values["RX_RESULT_FLAGS_LSB"]:X}
    FAULT_INFO_COUNT_LSB = 0x{values["FAULT_INFO_COUNT_LSB"]:X}
    DESIGN_ID = 0x{values["P_DESIGN_ID"]:08X}
    VERSION = 0x{values["P_VERSION"]:08X}

class TxState(IntEnum):
{chr(10).join(f"    {name[3:]} = 0x{values[name]:X}" for name in states)}


RxState = TxState


class ResultCode(IntEnum):
{chr(10).join(f"    {name[3:]} = 0x{values[name]:X}" for name in results)}


class RxFlag(IntFlag):
{chr(10).join(f"    {name[4:]} = 1 << {values[name]}" for name in rx_flags)}


class FaultBit(IntFlag):
{chr(10).join(f"    {name[3:]} = 1 << {values[name]}" for name in faults)}
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose exactly one of --write or --check")
    expected = render(parse_sources())
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(expected, encoding="utf-8")
        return 0
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if current != expected:
        print(f"{OUT} is stale; run python tools/generate_abi.py --write")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
