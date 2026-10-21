"""Per-board configuration: what belongs to one RFSoC 4x2 rather than to the design.

``PL.open()`` reads it from ``/etc/rf-clock/board.json``, or from the file named by
the environment variable ``RF_CLOCK_BOARD_CONFIG``.  The file names its board by the
128-bit serial number of the board's AT24MAC402 EEPROM, and ``PL.open()`` refuses a
file written for another board.  ``python -m rf_clock.checks.vco_bands`` writes it.

``serial``        the AT24MAC402 serial number, 32 hex digits
``label``         free text, for example the board's S/N
``lmx_vco_band``  the VCO band (VCO_CAPCTRL) ``PL.open()`` forces on each LMX2594,
                  ``{"adc": 117, "dac": 122}``; a chip left out calibrates its band
                  at every clock write, which moves its converter by 8-10 ps per band
                  (results/lmx-sync-loopback-20261019 section 5)
``mts_dtc``       the SYSREF capture codes MTS picks its windows by, per converter and
                  clock, 4 tiles each, ``{"dac": {"t1": [62, -1, 61, -1]}, "adc": {"t1":
                  [...], "pll": [...]}}``, and when they were recorded (``recorded``:
                  time, runs, the VCO bands); written by ``rf_clock.checks.mts_dtc``.
                  Without them MTS takes the window nearest code 64, which can change
                  by a whole sample between power-ups when two windows lie about as
                  far from it (docs/ABI.md 7.1)
``band_table``    what ``vco_bands`` measured: per chip how often the calibration
                  picked each band (``calibrated``), the bands that lock
                  (``locked``) and, with the loopback cable, the DAC -> ADC loopback
                  delay of each band relative to both chips at the bands of
                  ``measured.reference`` (``loopback_ps``); when (``scanned``,
                  ``measured``)

Example (887329260072A; the delays from results/lmx-sync-loopback-20261019)::

    {"serial": "16908308541004718c1ca080a0800000", "label": "887329260072A",
     "lmx_vco_band": {"adc": 118, "dac": 122},
     "band_table": {"adc": {"calibrated": {"117": 4, "118": 16}, "locked": [109, ..., 118],
                            "loopback_ps": {"116": 17.4, "117": 9.6, "118": 0.0}},
                    "dac": {"calibrated": {"122": 18, "123": 2}, "locked": [113, ..., 123],
                            "loopback_ps": {"122": 0.0, "123": 7.7}},
                    "scanned": {"time": "...", "die_c": 43.1, "rf_clock": "1.3.0.6", "writes": 20},
                    "measured": {"time": "...", "die_c": 43.5, "rf_clock": "1.3.0.6",
                                 "runs": 3, "reference": {"adc": 118, "dac": 122}}}}
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bringup import BringupError

DEFAULT_PATH = Path("/etc/rf-clock/board.json")
PATH_ENV = "RF_CLOCK_BOARD_CONFIG"
LMX_SIDES = ("adc", "dac")
LMX_CAPCTRL_MAX = 183  # VCO_CAPCTRL range of the LMX2594 (TI SNAS696C R19)
# AT24MAC402 on PS I2C1: its 128-bit serial number is at offset 0x80
# of the extended-memory address 0x58.  No kernel driver is bound to it.
SERIAL_I2C_ADAPTER = "Cadence I2C at ff030000"
SERIAL_I2C_ADDRESS = 0x58
SERIAL_OFFSET = 0x80
I2C_SLAVE = 0x0703  # linux/i2c-dev.h


class BoardConfigError(BringupError):
    pass


@dataclass(frozen=True)
class BoardConfig:
    serial: str
    label: str = ""
    lmx_vco_band: dict[str, int] = field(default_factory=dict)
    band_table: dict[str, Any] = field(default_factory=dict)
    mts_dtc: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Any, source: str = "board config") -> BoardConfig:
        if not isinstance(data, dict):
            raise BoardConfigError(f"{source}: not a JSON object")
        unknown = set(data) - {"serial", "label", "lmx_vco_band", "band_table", "mts_dtc"}
        if unknown:
            raise BoardConfigError(f"{source}: unknown keys {sorted(unknown)}")
        serial = data.get("serial")
        if not isinstance(serial, str) or not re.fullmatch(r"[0-9a-f]{32}", serial):
            raise BoardConfigError(f"{source}: serial must be 32 lower-case hex digits, not {serial!r}")
        bands = data.get("lmx_vco_band", {})
        if not isinstance(bands, dict) or not set(bands) <= set(LMX_SIDES) or not all(
                type(b) is int and 0 <= b <= LMX_CAPCTRL_MAX for b in bands.values()):
            raise BoardConfigError(f"{source}: lmx_vco_band must map adc/dac to 0-{LMX_CAPCTRL_MAX}, "
                                   f"not {bands!r}")
        table = data.get("band_table", {})
        if not isinstance(table, dict):
            raise BoardConfigError(f"{source}: band_table must be an object")
        dtc = data.get("mts_dtc", {})
        if not isinstance(dtc, dict) or not all(
                kind == "recorded" or (kind in LMX_SIDES and isinstance(clocks, dict) and set(clocks) <= {"t1", "pll"}
                                       and all(isinstance(codes, list) and len(codes) == 4
                                               and all(type(c) is int and -1 <= c <= 127 for c in codes)
                                               for codes in clocks.values()))
                for kind, clocks in dtc.items()):
            raise BoardConfigError(f"{source}: mts_dtc must map adc/dac to t1/pll lists of 4 codes -1..127, "
                                   f"not {dtc!r}")
        return cls(serial=serial, label=str(data.get("label", "")), lmx_vco_band=dict(bands),
                   band_table=table, mts_dtc=dtc)

    def to_json(self) -> dict[str, Any]:
        return {"serial": self.serial, "label": self.label, "lmx_vco_band": self.lmx_vco_band,
                "band_table": self.band_table, "mts_dtc": self.mts_dtc}


def config_path() -> Path:
    return Path(os.environ.get(PATH_ENV) or DEFAULT_PATH)


def load_board_config(path: str | Path | None = None) -> BoardConfig | None:
    """The board config at ``path``, else at ``config_path()``.

    None if there is no file at the default path; a file named by ``path`` or by
    ``RF_CLOCK_BOARD_CONFIG`` must exist.
    """
    named = path or os.environ.get(PATH_ENV)
    file = Path(named) if named else DEFAULT_PATH
    if not named and not file.exists():
        return None
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError) as exc:
        raise BoardConfigError(f"board config {file}: {exc}") from exc
    return BoardConfig.from_json(data, source=str(file))


def save_board_config(config: BoardConfig, path: str | Path | None = None) -> Path:
    file = Path(path) if path else config_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(config.to_json(), indent=2) + "\n")
    return file


def read_board_serial() -> str:
    """The 128-bit serial number of this board's AT24MAC402, as 32 hex digits."""
    import fcntl  # board-only

    for adapter in Path("/sys/class/i2c-adapter").glob("i2c-*"):
        if (adapter / "name").read_text().strip() == SERIAL_I2C_ADAPTER:
            break
    else:
        raise BoardConfigError(f"no I2C adapter named {SERIAL_I2C_ADAPTER!r}")
    fd = os.open(f"/dev/{adapter.name}", os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_SLAVE, SERIAL_I2C_ADDRESS)
        os.write(fd, bytes([SERIAL_OFFSET]))  # sets the address, then a current-address read
        return os.read(fd, 16).hex()
    finally:
        os.close(fd)


def check_board(config: BoardConfig, serial: str, source: str = "board config") -> None:
    if config.serial != serial:
        raise BoardConfigError(f"{source} is for board {config.serial} ({config.label or 'no label'}), "
                               f"this board is {serial}")
