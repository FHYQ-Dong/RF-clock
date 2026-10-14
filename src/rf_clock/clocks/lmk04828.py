"""LMK04828 TICS register files.

Field positions follow TI LMK04821/26/28 SNAS605AS, section 9.7.  The
ordered register list is retained because xrfclk writes it verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re

LMK_VCXO_HZ = 160_000_000.0
SYSREF_HZ = 5_000_000.0
FS725_REF_HZ = 10_000_000.0
PL_CLK_HZ = 100_000_000.0
LMK_VCO_RANGES_HZ = {0: (2_370_000_000.0, 2_630_000_000.0),
                    1: (2_920_000_000.0, 3_080_000_000.0)}


def validate_sysref_frequency(sysref_hz: float, clock_hz: tuple[float, ...] = (
    100_000_000.0, 480_000_000.0, 4_800_000_000.0, 9_600_000_000.0,
)) -> None:
    """Apply the project-level PG269 frequency checks.

    PG269 also specifies pulse shape, phase, and continuous operation; those
    properties require RFDC/board validation and are intentionally outside
    this arithmetic helper.
    """
    if not 0 < sysref_hz < 10_000_000.0:
        raise ValueError("PG269 requires SYSREF to be positive and strictly below 10 MHz")
    for frequency in clock_hz:
        quotient = frequency / sysref_hz
        if abs(quotient - round(quotient)) > 1e-9:
            raise ValueError(f"SYSREF {sysref_hz:g} Hz does not divide {frequency:g} Hz")


@dataclass(frozen=True)
class Register:
    address: int
    value: int
    label: str = ""

    @property
    def word(self) -> int:
        return (self.address << 8) | (self.value & 0xFF)


@dataclass(frozen=True)
class RegisterFile:
    entries: tuple[Register, ...]

    @property
    def registers(self) -> dict[int, int]:
        return {entry.address: entry.value for entry in self.entries}

    def render(self) -> str:
        lines = []
        for entry in self.entries:
            suffix = f" ({entry.label})" if entry.label else ""
            lines.append(f"R{entry.address}{suffix}\t0x{entry.word:06X}")
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Output:
    index: int
    frequency_hz: float | None
    enabled: bool
    format_code: int
    format_name: str
    kind: str


@dataclass(frozen=True)
class LMKConfig:
    registers: RegisterFile
    vcxo_hz: float = LMK_VCXO_HZ
    pll1_reference_hz: float | None = None
    pll1_input: str = "unknown"
    pll1_r: int = 0
    pll1_n: int = 0
    # Clock the PLL1 N divider counts: "OSCin" (the VCXO) normally; in
    # zero-delay mode the feedback-mux source, e.g. "DCLKout8".
    pll1_feedback: str = "OSCin"
    pll1_output_hz: float | None = None  # rate of pll1_feedback when locked
    pll2_r: int = 0
    pll2_p: int = 0
    pll2_n: int = 0
    vco_hz: float | None = None
    vco_mux: int = 0
    sysref_div: int = 0
    sysref_hz: float | None = None
    outputs: tuple[Output, ...] = ()

    @property
    def clock_outputs_hz(self) -> dict[str, float]:
        return {f"{output.kind}{output.index}": output.frequency_hz
                for output in self.outputs
                if output.enabled and output.frequency_hz is not None}


_LINE = re.compile(r"^R(?P<address>[0-9]+)(?:\s+\((?P<label>[^)]*)\))?\s+0x(?P<word>[0-9A-Fa-f]{6})\s*$")


def load_lmk(path: str | Path) -> RegisterFile:
    """Parse a TICS LMK file and reject malformed/address-mismatched lines."""
    entries: list[Register] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = _LINE.fullmatch(line)
        if not match:
            raise ValueError(f"{path}:{number}: expected R<n> TAB 24-bit hex word")
        address = int(match.group("address"))
        word = int(match.group("word"), 16)
        if word >> 8 != address:
            raise ValueError(f"{path}:{number}: word address 0x{word >> 8:X} != R{address}")
        entries.append(Register(address, word & 0xFF, match.group("label") or ""))
    if not entries:
        raise ValueError(f"{path}: empty register file")
    return RegisterFile(tuple(entries))


def _field(registers: dict[int, int], address: int, mask: int, shift: int = 0) -> int:
    return (registers.get(address, 0) & mask) >> shift


def _set_field(values: dict[int, int], address: int, mask: int, shift: int, field: int) -> None:
    values[address] = (values.get(address, 0) & ~mask) | ((field << shift) & mask)


def _divider(field: int) -> int:
    return 32 if field == 0 else field


_FORMATS = {0: "powerdown", 1: "LVDS", 2: "HSDS 6 mA", 3: "HSDS 8 mA",
            4: "HSDS 10 mA", 5: "LVPECL 1600 mV", 6: "LVPECL 2000 mV", 7: "LCPECL"}

# TI SNAS605AS field table.  Each value is (register, mask, shift) in the
# 8-bit data byte; the output-pair fields are repeated at 0x100 + 8*n.
LMK_FIELDS = {
    "PLL2_R": (0x160, 0x0F, 0),
    "PLL2_P": (0x162, 0xE0, 5),
    "PLL2_REF_2X_EN": (0x162, 0x01, 0),
    "PLL2_N_MSB": (0x166, 0x03, 0),
    "PLL2_N_MID": (0x167, 0xFF, 0),
    "PLL2_N_LSB": (0x168, 0xFF, 0),
    "SYSREF_MUX": (0x139, 0x03, 0),
    "SYSREF_DIV_MSB": (0x13A, 0x1F, 0),
    "SYSREF_DIV_LSB": (0x13B, 0xFF, 0),
    "DCLKOUT_DIV": (0x100, 0x1F, 0),
    "DCLKOUT_MUX": (0x103, 0x03, 0),
    "DCLKOUT_FMT": (0x107, 0x07, 0),
    "SDCLKOUT_FMT": (0x107, 0x70, 4),
}


def decode_lmk(registers: RegisterFile, *, vcxo_hz: float = LMK_VCXO_HZ,
               pll1_reference_hz: float | None = None,
               pll1_input: str = "unknown") -> LMKConfig:
    """Decode TI fields and calculate PLL2, device-clock, and SYSREF rates."""
    r = registers.registers
    pll2_r = ((_field(r, 0x160, 0x0F) << 8) | r.get(0x161, 0))
    p_code = _field(r, 0x162, 0xE0, 5)
    pll2_p = {0: 8, 1: 2}.get(p_code, p_code)
    pll2_n = ((_field(r, 0x166, 0x03) << 16) | (r.get(0x167, 0) << 8) | r.get(0x168, 0))
    ref_doubler = bool(r.get(0x162, 0) & 0x01)
    vco_hz = None
    if pll2_r and pll2_p and pll2_n:
        vco_hz = vcxo_hz * (2 if ref_doubler else 1) * pll2_n * pll2_p / pll2_r

    pll1_r = ((r.get(0x153, 0) & 0x3F) << 8) | r.get(0x154, 0)
    pll1_n = ((r.get(0x159, 0) & 0x3F) << 8) | r.get(0x15A, 0)
    pll1_feedback = "OSCin"
    if _field(r, 0x13F, 0x08, 3) and _field(r, 0x13F, 0x01):  # PLL1_NCLK_MUX, FB_MUX_EN
        pll1_feedback = ("DCLKout6", "DCLKout8", "SYSREF", "FBCLKin")[_field(r, 0x13F, 0x06, 1)]
    pll1_output_hz = None
    if pll1_reference_hz is not None and pll1_r:
        pll1_output_hz = pll1_reference_hz * pll1_n / pll1_r

    sysref_div = ((_field(r, 0x13A, 0x1F) << 8) | r.get(0x13B, 0))
    sysref_hz = vco_hz / sysref_div if vco_hz and sysref_div else None
    vco_mux = _field(r, 0x138, 0x60, 5)
    outputs: list[Output] = []
    for index in range(0, 14, 2):
        base = 0x100 + (index // 2) * 8
        div = _divider(_field(r, base, 0x1F))
        mux = _field(r, base + 3, 0x03)
        fmt = _field(r, base + 7, 0x07)
        enabled = fmt != 0
        frequency = vco_hz / div if enabled and vco_hz and mux != 2 else None
        outputs.append(Output(index, frequency, enabled, fmt, _FORMATS[fmt], "DCLKout"))
        sd_fmt = _field(r, base + 7, 0x70, 4)
        sd_enabled = sd_fmt != 0
        outputs.append(Output(index + 1, sysref_hz if sd_enabled else None, sd_enabled,
                              sd_fmt, _FORMATS[sd_fmt], "SDCLKout"))
    return LMKConfig(registers=registers, vcxo_hz=vcxo_hz,
                     pll1_reference_hz=pll1_reference_hz, pll1_input=pll1_input,
                     pll1_r=pll1_r, pll1_n=pll1_n, pll1_feedback=pll1_feedback,
                     pll1_output_hz=pll1_output_hz, pll2_r=pll2_r,
                     pll2_p=pll2_p, pll2_n=pll2_n, vco_hz=vco_hz,
                     vco_mux=vco_mux, sysref_div=sysref_div,
                     sysref_hz=sysref_hz, outputs=tuple(outputs))


def encode_lmk(config: LMKConfig | RegisterFile) -> str:
    """Render an ordered LMK register file in xrfclk-compatible syntax."""
    return (config.registers if isinstance(config, LMKConfig) else config).render()


def _set_divider(values: dict[int, int], index: int, divider: int) -> None:
    if divider < 1 or divider > 32:
        raise ValueError("LMK DCLK divider must be in [1, 32]")
    base = 0x100 + (index // 2) * 8
    _set_field(values, base, 0x1F, 0, 0 if divider == 32 else divider)


def make_100mhz_config(base: RegisterFile, *, mode: str) -> LMKConfig:
    """Derive the project's 100/480/5 MHz plan from a known TICS file.

    ``ext10m`` retains the CASPER CLKin0/PLL1 settings and adds nested
    zero-delay mode with DCLKout8 (PL_CLK) as PLL1 feedback.  ``onboard``
    retains the known RFSoC-MTS automatic/on-board reference selection.  Both
    use the board's 160 MHz VCXO and the same PLL2/output plan; only ext10m
    is valid for FS725 measurements.
    """
    if mode not in {"ext10m", "onboard"}:
        raise ValueError("mode must be ext10m or onboard")
    values = base.registers.copy()
    # 160 MHz / 2 * 15 * 2 = 2400 MHz VCO (TI registers 0x160-0x168).
    _set_field(values, 0x160, 0x0F, 0, 0)
    values[0x161] = 0x02
    values[0x166] = values.get(0x166, 0) & 0xF8
    values[0x167] = 0x00
    values[0x168] = 0x0F
    # 2400 MHz is in LMK04828 VCO0's specified range; do not retain the
    # RFSoC-MTS fixture's VCO1 selection when deriving the 100 MHz image.
    _set_field(values, 0x138, 0x60, 5, 0)
    # The RFSoC 4x2 scheme labels OUT0/OUT4 as the two LMX references;
    # OUT6/OUT12 are duplicate board clock consumers in the shipped image.
    # Keep every enabled 245.76-MHz branch coherent at 480 MHz.  OUT8 is PL_CLK.
    _set_divider(values, 0, 5)
    _set_divider(values, 4, 5)
    _set_divider(values, 6, 5)
    _set_divider(values, 12, 5)
    _set_divider(values, 8, 24)
    # SYSREF is shared by PL and RFDC.  2400/480 = 5 MHz, continuous mode.
    _set_field(values, 0x13A, 0x1F, 0, 0x01)
    values[0x13B] = 0xE0
    values[0x139] = (values.get(0x139, 0) & 0xFC) | 0x03
    if mode == "ext10m":
        # Nested zero-delay (SNAS605AS Table 7): PLL1 locks DCLKout8 = PL_CLK,
        # not the VCXO, to the FS725 10 MHz on CLKin0, so PL_CLK and every
        # output synchronized with it have one phase to the 10 MHz (and the
        # PPS) after each programming.  Without it the start of the /24
        # divider picks one of 24 PL_CLK phases 0.42 ns apart, and the PPS
        # sub-tick phase changed with every clock write (M2 scope test,
        # 2026-10-14).  N = R * 100 MHz / 10 MHz keeps the 833.3 kHz phase
        # detector and the loop gain (VCXO/192 before, VCXO * 100/160 / 120).
        pll1_r = ((values.get(0x153, 0) & 0x3F) << 8) | values.get(0x154, 0)
        pll1_n = pll1_r * round(PL_CLK_HZ / FS725_REF_HZ)
        _set_field(values, 0x159, 0x3F, 0, pll1_n >> 8)
        values[0x15A] = pll1_n & 0xFF
        # PLL1_NCLK_MUX = feedback mux, FB_MUX = DCLKout8, FB_MUX_EN = 1.
        _set_field(values, 0x13F, 0x0F, 0, 0b1011)
    result = RegisterFile(tuple(
        # TICS files intentionally contain two R0 writes: INIT=0x90 first,
        # then the normal R0 value.  A dict keyed only by address must not
        # collapse that ordered pair.
        replace(entry, value=entry.value if entry.address == 0 and entry.label == "INIT"
                else values.get(entry.address, entry.value))
        for entry in base.entries
    ))
    decoded = decode_lmk(result, vcxo_hz=LMK_VCXO_HZ,
                          pll1_reference_hz=FS725_REF_HZ if mode == "ext10m" else None,
                          pll1_input="CLKin0/FS725" if mode == "ext10m"
                          else "automatic onboard reference")
    low, high = LMK_VCO_RANGES_HZ[decoded.vco_mux]
    if decoded.vco_hz is None or not low <= decoded.vco_hz <= high:
        raise ValueError(f"VCO {decoded.vco_hz} Hz is outside LMK VCO{decoded.vco_mux} range")
    validate_sysref_frequency(decoded.sysref_hz or 0.0)
    if mode == "ext10m" and (decoded.pll1_feedback != "DCLKout8"
                             or decoded.pll1_output_hz != decoded.clock_outputs_hz["DCLKout8"]):
        raise ValueError("PLL1 zero-delay feedback is not DCLKout8 at its output rate")
    return decoded
