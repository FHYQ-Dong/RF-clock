"""The output PPS, P10 (docs/ABI.md 4.13): a step on DAC tile 0 (DAC_B, J20) once a second.

P10 counts the ticks of each second from the PPS_TICK of its locked PPS edge and
carries the count on by itself when an edge is missing (the flywheel).  The pulse
of each second starts in the tick presented at PPS_TICK + ``offset``: the I samples
of that tick from ``sample`` on (1.25 ns each) are ``amplitude``, ``width`` - 1 more
ticks follow, then ``idle`` again (since 2.2; zero before).  So the edge on J20 lies
``offset`` x 10 ns + ``sample`` x 1.25 ns plus the board's own delay after the start
of the node's second.  ``idle`` = -``amplitude`` doubles the step; the path is
AC-coupled, so ``idle`` sets no level at J20.  With a fraction (``set_frac``, since
2.3) the step's first sample is ``amplitude`` + (``idle`` - ``amplitude``) x frac / 2^16,
which delays the edge after the DAC's interpolation by up to one sample, nearly in
proportion (calibrated on the scope): the fractional delay of the theta-compensated
output PPS (``rf_clock.twtt.control.EdgeCompensation``, plan/pps_output.md section 7).
``PL.open()`` puts tile 0 at baseband (``rf_clock.pl.rfdc.baseband_dac``); the path
to J20 is AC-coupled, so the output is a short pulse (plan/pps_output.md).

The validity line (Pmod J1 pin 1) is high, and the LED W_LED_0 dark, unless the
output is enabled, its second was started by a locked PPS edge, the clocks are
locked, DAC tile 0 takes the stream, and ``synced`` is set: the ARM sets it once
the link is locked and the steering has settled.  ENABLE, OFFSET, SAMPLE, WIDTH,
AMPLITUDE, IDLE and the fraction are taken at the start of the next second; ``synced``
at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import abi
from .client import PLClient

REG = abi.ABI.REG
MIN_OFFSET = abi.ABI.PPSOUT_MIN_OFFSET  # ticks: the edge that starts a second is in by then
PPS_PERIOD_TICKS = 100_000_000  # the build's PPS period (p_top PPS_PERIOD)


@dataclass(frozen=True)
class PPSOutStatus:
    have_ref: bool  # a locked PPS edge has set the count
    running: bool  # enabled for the current second
    flywheel: bool  # the last pulse came from the flywheel
    invalid: bool  # the validity line: high, the output PPS is not valid
    pulses: int  # PPS_OUT_CNT
    flywheel_pulses: int  # PPS_OUT_FLYWHEEL_CNT
    last_tick: int  # tick of the last pulse's first beat


class PPSOut:
    """``pl.pps_out``: configure, enable and watch the output PPS."""

    def __init__(self, client: PLClient) -> None:
        self.client = client

    def configure(self, *, amplitude: int, offset: int = MIN_OFFSET, sample: int = 0, width: int = 4,
                  idle: int = 0, enable: bool = True) -> None:
        """Set the pulse; it takes effect at the next second.  ``amplitude`` and ``idle``
        are signed 16-bit DAC codes; ValueError for values the registers would refuse."""
        for name, code in (("amplitude", amplitude), ("idle", idle)):
            if not -0x8000 <= int(code) <= 0x7FFF:
                raise ValueError(f"{name} must be a signed 16-bit value")
        if not 0 <= int(sample) <= 7:
            raise ValueError("sample must be 0 .. 7")
        if not 1 <= int(width) <= 0xFFFF:
            raise ValueError("width must be 1 .. 65535 beats")
        if not MIN_OFFSET <= int(offset) < PPS_PERIOD_TICKS:
            raise ValueError(f"offset must be {MIN_OFFSET} .. {PPS_PERIOD_TICKS - 1} ticks")
        self.client.write32(REG.PPS_OUT_OFFSET, int(offset))
        self.client.write32(REG.PPS_OUT_SAMPLE, int(sample))
        self.client.write32(REG.PPS_OUT_WIDTH, int(width))
        self.client.write32(REG.PPS_OUT_AMPLITUDE, int(amplitude) & 0xFFFF)
        self.client.write32(REG.PPS_OUT_IDLE, int(idle) & 0xFFFF)
        self._control(abi.ABI.PPSOUTCTL.ENABLE, enable)

    @property
    def amplitude(self) -> int:
        raw = self.client.read32(REG.PPS_OUT_AMPLITUDE) & 0xFFFF
        return raw - 0x10000 if raw & 0x8000 else raw

    @property
    def idle(self) -> int:
        raw = self.client.read32(REG.PPS_OUT_IDLE) & 0xFFFF
        return raw - 0x10000 if raw & 0x8000 else raw

    @property
    def offset(self) -> int:
        return self.client.read32(REG.PPS_OUT_OFFSET)

    @property
    def sample(self) -> int:
        return self.client.read32(REG.PPS_OUT_SAMPLE)

    @property
    def width(self) -> int:
        return self.client.read32(REG.PPS_OUT_WIDTH)

    @property
    def frac(self) -> int | None:
        """The edge's delay in 2^-16 of a sample, or None while FRAC is clear."""
        if not self.client.read32(REG.PPS_OUT_CONTROL) >> abi.ABI.PPSOUTCTL.FRAC & 1:
            return None
        return self.client.read32(REG.PPS_OUT_FRAC) & 0xFFFF

    def set_frac(self, frac: int | None) -> None:
        """Delay the edge by ``frac`` / 2^16 of a sample (0 .. 65535), from the next second;
        None clears FRAC, the plain step."""
        if frac is None:
            self._control(abi.ABI.PPSOUTCTL.FRAC, False)
            return
        if not 0 <= int(frac) <= 0xFFFF:
            raise ValueError("frac must be 0 .. 65535")
        self.client.write32(REG.PPS_OUT_FRAC, int(frac))
        self._control(abi.ABI.PPSOUTCTL.FRAC, True)

    def enable(self) -> None:
        self._control(abi.ABI.PPSOUTCTL.ENABLE, True)

    def disable(self) -> None:
        """Clear ENABLE: no pulse from the next second on."""
        self._control(abi.ABI.PPSOUTCTL.ENABLE, False)

    def set_synced(self, synced: bool) -> None:
        """The ARM's word on the time scale, shown on the validity line at once."""
        self._control(abi.ABI.PPSOUTCTL.SYNCED, synced)

    def status(self) -> PPSOutStatus:
        raw = self.client.read32(REG.PPS_OUT_STATUS)
        st = abi.ABI.PPSOUTST
        return PPSOutStatus(
            have_ref=bool(raw >> st.HAVE_REF & 1),
            running=bool(raw >> st.RUNNING & 1),
            flywheel=bool(raw >> st.FLYWHEEL & 1),
            invalid=bool(raw >> st.INVALID & 1),
            pulses=self.client.read32(REG.PPS_OUT_CNT),
            flywheel_pulses=self.client.read32(REG.PPS_OUT_FLYWHEEL_CNT),
            last_tick=self.client.read64(REG.PPS_OUT_TICK_LO),
        )

    def _control(self, bit: int, on: bool) -> None:
        control = self.client.read32(REG.PPS_OUT_CONTROL)
        control = control | (1 << bit) if on else control & ~(1 << bit)
        self.client.write32(REG.PPS_OUT_CONTROL, control)
