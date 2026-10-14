"""LMX2594 TICS parser and frequency decoder (TI SNAS696C, section 7.6)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Register:
    address: int
    value: int

    @property
    def word(self) -> int:
        return (self.address << 16) | (self.value & 0xFFFF)


@dataclass(frozen=True)
class RegisterFile:
    entries: tuple[Register, ...]

    @property
    def registers(self) -> dict[int, int]:
        return {entry.address: entry.value for entry in self.entries}

    def render(self) -> str:
        return "\n".join(f"R{entry.address}\t0x{entry.word:06X}" for entry in self.entries) + "\n"


@dataclass(frozen=True)
class LMXConfig:
    registers: RegisterFile
    reference_hz: float
    osc_2x: bool
    multiplier: int
    pll_r: int
    pll_r_pre: int
    pfd_hz: float
    pll_n: int
    pll_num: int
    pll_den: int
    vco_hz: float
    channel_divider: int | None
    output_a_hz: float | None
    output_b_hz: float | None
    output_a_enabled: bool
    output_b_enabled: bool
    output_a_power: int
    output_b_power: int


_LINE = re.compile(r"^R(?P<address>[0-9]+)\s+0x(?P<word>[0-9A-Fa-f]{6})\s*$")


def load_lmx(path: str | Path) -> RegisterFile:
    entries: list[Register] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = _LINE.fullmatch(line)
        if not match:
            raise ValueError(f"{path}:{number}: expected R<n> TAB 24-bit hex word")
        address = int(match.group("address"))
        word = int(match.group("word"), 16)
        if word >> 16 != address:
            raise ValueError(f"{path}:{number}: word address 0x{word >> 16:X} != R{address}")
        entries.append(Register(address, word & 0xFFFF))
    if not entries:
        raise ValueError(f"{path}: empty register file")
    if entries[-1].address != 0:
        raise ValueError("LMX file must end with R0 so xrfclk can retrigger FCAL")
    return RegisterFile(tuple(entries))


_CHDIV = {0: 2, 1: 4, 2: 6, 3: 8, 4: 12, 5: 16, 6: 24, 7: 32,
          8: 48, 9: 64, 10: 72, 11: 96, 12: 128, 13: 192,
          14: 256, 15: 384, 16: 512, 17: 768}

# TI SNAS696C field table used by the decoder and by callers that need to
# inspect or edit a register image.  Masks are in the 16-bit register value.
LMX_FIELDS = {
    "OSC_2X": (9, 0x1000, 12),
    "MULT": (10, 0x0F80, 7),
    "PLL_R": (11, 0x0FF0, 4),
    "PLL_R_PRE": (12, 0x0FFF, 0),
    "PLL_N_MSB": (34, 0x0007, 0),
    "PLL_N_LSB": (36, 0xFFFF, 0),
    "PLL_DEN_HI": (38, 0xFFFF, 0),
    "PLL_DEN_LO": (39, 0xFFFF, 0),
    "PLL_NUM_HI": (42, 0xFFFF, 0),
    "PLL_NUM_LO": (43, 0xFFFF, 0),
    "OUTA_PD": (44, 0x0040, 6),
    "OUTB_PD": (44, 0x0080, 7),
    "OUTA_PWR": (44, 0x3F00, 8),
    "OUTA_MUX": (45, 0x1800, 11),
    "OUTB_MUX": (46, 0x0003, 0),
    "CHDIV": (75, 0x07C0, 6),
}


def encode_lmx(config: LMXConfig | RegisterFile) -> str:
    """Render an ordered LMX image; the final R0 write is retained."""
    return (config.registers if isinstance(config, LMXConfig) else config).render()


def decode_lmx(registers: RegisterFile, *, reference_hz: float) -> LMXConfig:
    """Decode the reference path, N divider, channel divider and RF outputs."""
    r = registers.registers
    osc_2x = bool((r.get(9, 0) >> 12) & 1)
    multiplier = (r.get(10, 0) >> 7) & 0x1F
    pll_r = (r.get(11, 0) >> 4) & 0xFF
    pll_r_pre = r.get(12, 0) & 0xFFF
    if not multiplier or not pll_r or not pll_r_pre:
        raise ValueError("LMX reference path has an invalid zero divider/multiplier")
    pfd_hz = reference_hz * (2 if osc_2x else 1) * multiplier / (pll_r * pll_r_pre)
    pll_n = ((r.get(34, 0) & 0x07) << 16) | r.get(36, 0)
    pll_den = (r.get(38, 0) << 16) | r.get(39, 0)
    pll_num = (r.get(42, 0) << 16) | r.get(43, 0)
    vco_hz = pfd_hz * (pll_n + (pll_num / pll_den if pll_den else 0.0))
    chdiv = _CHDIV.get((r.get(75, 0) >> 6) & 0x1F)
    out_a_mux = (r.get(45, 0) >> 11) & 0x03
    out_b_mux = r.get(46, 0) & 0x03
    out_a_enabled = not bool(r.get(44, 0) & (1 << 6))
    out_b_enabled = not bool(r.get(44, 0) & (1 << 7))

    def output(mux: int, enabled: bool) -> float | None:
        if not enabled:
            return None
        if mux == 1:
            return vco_hz
        if mux == 0 and chdiv:
            return vco_hz / chdiv
        return None

    return LMXConfig(
        registers, reference_hz, osc_2x, multiplier, pll_r, pll_r_pre, pfd_hz,
        pll_n, pll_num, pll_den, vco_hz, chdiv,
        output(out_a_mux, out_a_enabled), output(out_b_mux, out_b_enabled),
        out_a_enabled, out_b_enabled, (r.get(44, 0) >> 8) & 0x3F, r.get(45, 0) & 0x3F,
    )
