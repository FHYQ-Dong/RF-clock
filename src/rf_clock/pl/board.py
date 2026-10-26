"""The RF-clock PL on this RFSoC 4x2, as one object that owns its hardware blocks.

``PL.open()`` runs the power-up sequence of docs/ABI.md 7.1 with this board's
fixed choices (the overlay packaged with rf-clock, the project's LMK04828/LMX2594 files,
pl_resetn0, RFDC tile start-up and MTS, PPS lock).  The returned object only
holds the blocks, one per address space of docs/ABI.md 1.1; work is done
through each block's own methods:

=============  ===========================================================
``ctrl``       ``PLClient``: P2 registers -- every command (arm, wait, cancel),
               timestamp, PPS and status
``tx_buffer``  ``TXBuffer``: on-chip TX waveform buffer (AXI BRAM)
``rx_dma``     ``RXDMA``: the AXI DMA S2MM that writes RX windows into DDR
``rfdc``       ``RFDC``: xrfdc driver and the MTS result
``clock_gpio`` clock-status GPIO (``pl_clk0``), bit 0 = fabric MMCM locked
``overlay``    the PYNQ ``Overlay``
``bringup``    ``BringupResult`` (faults found and cleared at power-up)
``board``      ``BoardSetup``: board serial number, board config, LMX2594 VCOs
=============  ===========================================================
"""

from __future__ import annotations

import re
import struct
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..clocks.lmk04828 import LMK_FIELDS
from ..clocks.lmx2594 import LMX_FIELDS
from .board_config import BoardConfig, check_board, config_path, load_board_config, read_board_serial
from .bringup import (Bringup, BringupError, BringupResult, mmio_from_overlay, pynq_pl_reset,
                      pynq_register_factories, wait_for_boot)
from .client import PLClient
from .overlay import DEFAULT_BITSTREAM, check_overlay, package_version
from .rfdc import RFDCError, RFDCSetupResult, rfdc_driver, setup_rfdc_and_mts
from .rxdma import RXDMA, pynq_rx_dma
from .txbuf import TXBuffer

LMK_FREQ_MHZ = 100.0  # xrfclk LMK04828_100.0.txt: CLKin0 = FS725 10 MHz, PL_CLK 100 MHz
LMX_FREQ_MHZ = 480.0  # xrfclk LMX2594_480.0.txt: 480 MHz RFDC tile references
CLOCK_FILES = Path(__file__).resolve().parents[1] / "clocks" / "files"
# MTS target latencies, so the RFDC datapath delay is the same for every build
# (docs/ABI.md 7.1).  Measured without a target: DAC 1848 on run4 and run6,
# ADC 1236 on run4 and 1248 on run6; both adjust in steps of 6.  With the LMK
# divider SYNC (sync_lmk_dividers) the DAC tiles measure 1896 before alignment
# (offset 0, 2026-10-24), so the DAC target is 8 steps above that, as it was
# before.  A build whose tile latency exceeds a target fails MTS (TARGET_LOW);
# raise the target then and recalibrate the loopback delay.
DAC_TARGET_LATENCY = 1944
ADC_TARGET_LATENCY = 1296
# Clock-chip fields, (register, mask, shift) from the field tables of rf_clock.clocks.
LMX_VCO_PHASE_SYNC = LMX_FIELDS["VCO_PHASE_SYNC"][1]
LMK_SYNC_POL = LMK_FIELDS["SYNC_POL"][1]
LMK_SYSREF_MUX = LMK_FIELDS["SYSREF_MUX"][1]
LMK_SYNC_DIS = LMK_FIELDS["SYNC_DIS"][1]
LMK_SYNC_SETTLE_S = 2.0  # for PLL1 to pull DCLKout8 back to CLKin0 after the divider SYNC (nested zero delay)
LMX_CAL_WAIT_S = 0.01  # after an R0 write with FCAL_EN = 1 (TI SNAS696C 7.5)
LMX_FCAL_EN = LMX_FIELDS["FCAL_EN"][1]
LMX_MUXOUT_LD_SEL = LMX_FIELDS["MUXOUT_LD_SEL"][1]
LMX_CAPCTRL_FORCE = LMX_FIELDS["VCO_CAPCTRL_FORCE"][1]
# What lmx_readback returns, by LMX_FIELDS readback field.
LMX_READBACK = {"ld_vtune": "rb_LD_VTUNE", "vco_sel": "rb_VCO_SEL", "capctrl": "rb_VCO_CAPCTRL",
                "daciset": "rb_VCO_DACISET"}
LMX_LOCKED = 2  # rb_LD_VTUNE: 0 = tuning voltage low, 2 = locked, 3 = high
# The LMX2594 by SPI chip select (/dev/spidev0.<cs>): RF_PLL1 on CS 1 clocks the ADC
# tiles, RF_PLL2 on CS 2 the DAC tiles.  PYNQ's device tree names them the other way
# round (lmxdac, lmxadc; results/lmx-sync-loopback-20261019 section 2).
LMX_CHIP_SELECT = {"adc": 1, "dac": 2}
SPI_IOC_MESSAGE_1 = 0x40206B00  # _IOW('k', 0, struct spi_ioc_transfer)
# How far (5.3 ps codes) a picked MTS capture code may move from the one recorded in
# the board config before PL.open warns.  Windows repeat every 20 codes on the DAC
# and 37 on the ADC, so half a window, where the choice turns ambiguous, is 10 codes.
DTC_MARGIN = 6


def clock_registers(name: str) -> list[int]:
    """Register words of a packaged TICS file, one per line, as xrfclk reads them."""
    words = []
    for line in (CLOCK_FILES / name).read_text().splitlines():
        match = re.search(r"0x[0-9A-Fa-f]+", line)
        if match:
            words.append(int(match.group(), 16))
    return words


def program_clocks(xrfclk: Any, vco_band: dict[str, int] | None = None) -> None:
    """Program the LMK04828 and LMX2594 from the files in ``rf_clock.clocks``.

    ``xrfclk`` is the ``xrfclk.xrfclk`` module (PYNQ 3.1).  Its
    ``set_ref_clks()`` only reads the .txt files in its own package directory,
    into the table ``_Config``, and only when it has not found the devices
    yet.  Finding them here and filling ``_Config`` from our files makes it
    write our registers, so the board needs no copies inside xrfclk.  The
    LMK04828's dividers then get their SYNC (``sync_lmk_dividers``).
    ``vco_band`` ({"adc": 117, ...}) then rewrites those LMX2594 with their VCO
    band forced (``force_vco_band``).  An LMX image with VCO_PHASE_SYNC set then
    gets its SYNC (``sync_lmx_phase``).
    """
    if not xrfclk.lmk_devices and not xrfclk.lmx_devices:
        xrfclk._find_devices()
    lmk = clock_registers(f"LMK04828_{LMK_FREQ_MHZ}.txt")
    lmx = clock_registers(f"LMX2594_{LMX_FREQ_MHZ}.txt")
    xrfclk._Config["lmk04828"][LMK_FREQ_MHZ] = lmk
    xrfclk._Config["lmx2594"][LMX_FREQ_MHZ] = lmx
    xrfclk.set_ref_clks(lmk_freq=LMK_FREQ_MHZ, lmx_freq=LMX_FREQ_MHZ)
    sync_lmk_dividers(xrfclk, lmk)
    for side, band in (vco_band or {}).items():
        xrfclk._write_LMX_regs(force_vco_band(lmx, band), lmx_device(xrfclk, side))
    if lmx[-1] & LMX_VCO_PHASE_SYNC:
        sync_lmx_phase(xrfclk, lmx[-1])


def sync_lmk_dividers(xrfclk: Any, image: list[int]) -> None:
    """Synchronize every LMK04828 divider once PLL2 runs (TI SNAS605AS 9.3.2.1.1, steps 2d-3).

    The register file holds only the final SYNC settings, written in address order
    before PLL2's: 0x143 with SYNC_POL = 1 (SYNC asserted, level-sensitive), then
    0x144 = 0xFF (no divider takes SYNC).  So the dividers are released at the 0x144
    write, before PLL2 and its VCO are programmed and calibrated, and their relative
    phase rests on how that start-up goes: in a few power-ups in a thousand one
    board's converters came up 4 or 5 VCO periods (416.7 ps each) later against its
    PPS tick, with other MTS offsets (2026-10-21/24).  Here, as the data sheet's
    procedure does after all registers (TI SNAS605AS 9.3.2.1.1 and Table 1):
    SYSREF_MUX = 0, so that the dividers' SYNC line carries the SYNC instead of the
    image's continuous SYSREF (3); SYNC_POL = 0; every SYNC_DIS cleared (SYSREF's
    divider included); SYNC_POL 1 then 0 (all held, then released together on the
    running VCO, the SYNC reclocked by the distribution path); SYNC_DIS and
    SYSREF_MUX as in the image.  PLL1, in nested zero-delay mode on DCLKout8, then
    pulls all outputs back into line with the 10 MHz together.
    """
    registers = {word >> 8: word for word in image}  # 24-bit words: address above the data byte
    mux, pol, dis = (registers[LMK_FIELDS[name][0]] for name in ("SYSREF_MUX", "SYNC_POL", "SYNC_DIS"))
    time.sleep(LMX_CAL_WAIT_S)
    words = [mux & ~LMK_SYSREF_MUX, pol & ~LMK_SYNC_POL, dis & ~LMK_SYNC_DIS, pol | LMK_SYNC_POL,
             pol & ~LMK_SYNC_POL, dis, mux]
    for lmk in xrfclk.lmk_devices:
        xrfclk._write_LMK_regs(words, lmk)
    time.sleep(LMK_SYNC_SETTLE_S)


def lmx_device(xrfclk: Any, side: str) -> dict:
    """xrfclk's device entry of the LMX2594 that clocks the "adc" or "dac" tiles."""
    suffix = f".{LMX_CHIP_SELECT[side]}"
    (device,) = [d for d in xrfclk.lmx_devices if str(d["spi_device"]).endswith(suffix)]
    return device


def force_vco_band(image: list[int], band: int) -> list[int]:
    """An LMX2594 image with its VCO band forced to ``band`` instead of calibrated.

    VCO_CAPCTRL_FORCE = 1 with the band in VCO_CAPCTRL; the calibration still
    picks the VCO core and amplitude (TI SNAS696C 7.3.6.1).
    """
    force_reg = LMX_FIELDS["VCO_CAPCTRL_FORCE"][0]
    band_reg, band_mask, band_shift = LMX_FIELDS["VCO_CAPCTRL"]
    return [word | LMX_CAPCTRL_FORCE if word >> 16 == force_reg
            else (word & ~band_mask) | ((band << band_shift) & band_mask) if word >> 16 == band_reg
            else word for word in image]


def sync_lmx_phase(xrfclk: Any, r0: int) -> None:
    """Phase SYNC of every LMX2594: VCO_PHASE_SYNC 0 -> 1 in R0 (TI SNAS696C 7.3.10).

    Writing the image with VCO_PHASE_SYNC = 1 does not count as a SYNC.  The
    480 MHz image is SYNC category 2, where toggling the bit is the SYNC and its
    timing is not critical.  Without it the LMX channel divider starts in an
    arbitrary phase at every clock write, and the RFDC sample clocks follow
    it: after MTS the DAC output moved by 0 or half a sample (52 ps) from one
    clock write to the next (results/lmx-phase-sync-20261019).  ``r0`` is the
    image's final R0 word; FCAL_EN = 1 in it recalibrates the VCO after each
    write.
    """
    time.sleep(LMX_CAL_WAIT_S)
    for device in xrfclk.lmx_devices:
        with open(device["spi_device"], "rb+", buffering=0) as f:
            for word in (r0 & ~LMX_VCO_PHASE_SYNC, r0 | LMX_VCO_PHASE_SYNC):
                f.write(struct.pack(">I", word)[1:])
                time.sleep(LMX_CAL_WAIT_S)


@dataclass(frozen=True)
class LMXVCO:
    """One LMX2594's VCO after ``program_clocks``, read back over SPI."""

    forced: int | None  # band forced by program_clocks, None = calibrated
    capctrl: int  # rb_VCO_CAPCTRL: the calibration's band, also when one is forced
    vco_sel: int  # rb_VCO_SEL: VCO core
    daciset: int  # rb_VCO_DACISET: VCO amplitude
    ld_vtune: int  # rb_LD_VTUNE: LMX_LOCKED if the tuning voltage is in range

    @property
    def band(self) -> int:
        """The band the VCO runs in."""
        return self.capctrl if self.forced is None else self.forced

    @property
    def locked(self) -> bool:
        return self.ld_vtune == LMX_LOCKED

    def describe(self) -> str:
        how = "calibrated" if self.forced is None else f"forced (calibration chose {self.capctrl})"
        return (f"VCO{self.vco_sel} band {self.band} {how}, DACISET {self.daciset}, "
                + ("locked" if self.locked else f"NOT LOCKED (LD_VTUNE {self.ld_vtune})"))


def spi_transfer(f: Any, data: bytes) -> bytes:
    """One full-duplex spidev transfer: the bytes read while ``data`` is written."""
    import ctypes
    import fcntl  # board-only

    tx = ctypes.create_string_buffer(data, len(data))
    rx = ctypes.create_string_buffer(len(data))
    fcntl.ioctl(f, SPI_IOC_MESSAGE_1, struct.pack("=QQIIHBBBBBB", ctypes.addressof(tx),
                                                  ctypes.addressof(rx), len(data), 0, 0, 8, 0, 0, 0, 0, 0))
    return rx.raw


def lmx_readback(spi_device: str | Path, r0: int) -> dict[str, int]:
    """The VCO calibration result of one LMX2594 (TI SNAS696C 7.3.5.2).

    ``r0`` is the R0 word last written.  With MUXOUT_LD_SEL = 0 the chip puts the
    addressed register on MUXout, which the board buffers onto the SPI data-out
    line.  Both R0 writes here clear FCAL_EN, so that neither recalibrates the
    VCO, and keep VCO_PHASE_SYNC as it is, so that neither is a SYNC; the second
    puts lock detect back on MUXout.
    """
    fields = {name: LMX_FIELDS[field] for name, field in LMX_READBACK.items()}
    r0 &= ~LMX_FCAL_EN
    with open(spi_device, "rb+", buffering=0) as f:
        spi_transfer(f, struct.pack(">I", r0 & ~LMX_MUXOUT_LD_SEL)[1:])
        words = {addr: int.from_bytes(spi_transfer(f, bytes([0x80 | addr, 0, 0]))[1:], "big")
                 for addr in sorted({addr for addr, _, _ in fields.values()})}
        spi_transfer(f, struct.pack(">I", r0 | LMX_MUXOUT_LD_SEL)[1:])
    return {name: (words[addr] & mask) >> shift for name, (addr, mask, shift) in fields.items()}


def mts_dtc_warnings(mts: RFDCSetupResult, config: BoardConfig | None) -> list[str]:
    """What the MTS capture codes and offsets say about the board config's ``mts_dtc``."""
    out = []
    for kind, result in (("dac", mts.dac), ("adc", mts.adc)):
        usual = (config.mts_dtc.get(kind) or {}).get("offset") if config else None
        if usual is None:
            out.append(f"MTS {kind.upper()} offsets not recorded in the board config: a power-up that puts the "
                       f"converters a few LMK VCO periods (416.7 ps) off against the PPS tick goes unnoticed "
                       f"(rf_clock.checks.mts_dtc record --write)")
        else:
            for tile in result.tiles:
                if usual[tile] >= 0 and result.offset[tile] != usual[tile]:
                    out.append(f"MTS {kind.upper()} tile {tile} offset {result.offset[tile]}, recorded {usual[tile]}: "
                               f"in this power-up the converters may sit a few LMK VCO periods (416.7 ps each) off "
                               f"against the PPS tick; open the PL again")
        if not result.dtc_target:
            out.append(f"MTS {kind.upper()} capture windows not set by the board config: MTS takes the window "
                       f"nearest code 64, which can move the converter by a whole sample at the next power-up "
                       f"(rf_clock.checks.mts_dtc)")
            continue
        for clock, target in result.dtc_target.items():
            picked = result.dtc[f"{clock}_code"]
            for tile in result.tiles:
                if target[tile] >= 0 and abs(picked[tile] - target[tile]) > DTC_MARGIN:
                    out.append(f"MTS {kind.upper()} tile {tile} {clock.upper()} capture code {picked[tile]} is "
                               f"{abs(picked[tile] - target[tile])} codes from the recorded {target[tile]}: the "
                               f"clock has moved toward a window boundary; record the codes again")
    recorded = (config.mts_dtc.get("recorded") or {}).get("bands") if config else None
    if config is not None and recorded is not None and recorded != config.lmx_vco_band:
        out.append(f"MTS capture codes recorded with VCO bands {recorded}, the board config forces "
                   f"{config.lmx_vco_band}: record them again")
    return out


def die_c() -> float | None:
    """The hottest RFSoC die sensor (AMS), in degrees C; None off the board.  The LMK04828
    and LMX2594 have no sensor, so this stands for the board's temperature."""
    temps = [int(p.read_text()) for p in Path("/sys/bus/iio/devices/iio:device0").glob("in_temp*_input")]
    return max(temps) / 1000 if temps else None


def read_lmx_vco(xrfclk: Any, vco_band: dict[str, int] | None = None) -> dict[str, LMXVCO]:
    """Each LMX2594's VCO after ``program_clocks(xrfclk, vco_band)``, by "adc"/"dac"."""
    r0 = clock_registers(f"LMX2594_{LMX_FREQ_MHZ}.txt")[-1]
    return {side: LMXVCO(forced=(vco_band or {}).get(side),
                         **lmx_readback(lmx_device(xrfclk, side)["spi_device"], r0))
            for side in LMX_CHIP_SELECT}


@dataclass(frozen=True)
class BoardSetup:
    """``pl.board``: what ``PL.open()`` found and set on this board."""

    serial: str = ""  # AT24MAC402 serial number
    config: BoardConfig | None = None  # None: no board config file
    lmx_vco: dict[str, LMXVCO] = field(default_factory=dict)  # "adc"/"dac"; empty if clocks not written


class RFDC:
    """``pl.rfdc``: the xrfdc driver and the MTS result of ``PL.open()``."""

    def __init__(self, driver: Any, mts: RFDCSetupResult | None = None) -> None:
        self.driver = driver  # xrfdc.RFdc: driver.dac_tiles[2].blocks[0].MixerSettings, ...
        self._mts = mts

    @property
    def mts(self) -> RFDCSetupResult:
        if self._mts is None:
            raise RuntimeError("MTS did not run (PL.open(mts=False))")
        return self._mts


class PL:
    """The PL's hardware blocks after power-up (see the module docstring)."""

    def __init__(self, *, ctrl: PLClient, tx_buffer: TXBuffer, rx_dma: RXDMA, rfdc: RFDC,
                 overlay: Any, bringup: BringupResult, board: BoardSetup | None = None) -> None:
        self.ctrl = ctrl
        self.tx_buffer = tx_buffer
        self.rx_dma = rx_dma
        self.rfdc = rfdc
        self.overlay = overlay
        self.bringup = bringup
        self.board = board or BoardSetup()

    @property
    def clock_gpio(self) -> Any:
        return self.ctrl.clock_status

    @classmethod
    def open(
        cls,
        bitstream: str | Path = DEFAULT_BITSTREAM,
        *,
        write_clocks: bool = True,
        mts: bool = True,
        pps_source_trig: bool = False,
        pps_invert: bool = False,
        ref_tile: int = 0,
        dac_target_latency: int = DAC_TARGET_LATENCY,
        adc_target_latency: int = ADC_TARGET_LATENCY,
        check_version: bool = True,
        board_config: BoardConfig | str | Path | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> "PL":
        """Power up the board (docs/ABI.md 7.1) and return its PL blocks.

        ``write_clocks=False`` keeps the LMK/LMX as they are (already written
        since boot); ``mts=False`` skips RFDC start-up and MTS.  ``progress``
        receives one line per step.  On failure the exception carries
        ``.overlay`` and, once MTS ran, ``.mts``.

        The default bitstream is the one packaged with rf-clock.  Unless
        ``check_version=False`` (bitstreams before PL ABI 1.2), its
        manifest is checked before loading and ``BUILD_VERSION`` after; both
        must have the installed package's A.B.C (``VersionMismatch``).

        The board config (``rf_clock.pl.board_config``) is ``board_config``, or
        the file it names, or else the one at the default path.  It must name
        this board's serial number (``BoardConfigError``).  Each LMX2594 runs in
        the VCO band the config forces, or else in the one its calibration
        picks, with a warning: that band can differ at the next power-up.
        Either way both must lock (``BringupError``).  MTS picks its SYSREF
        capture windows by the codes the config records, or else nearest code
        64, with a warning, and warns too when a picked code has moved more
        than ``DTC_MARGIN`` from its record.
        """
        say = progress or (lambda message: None)
        parts: dict[str, Any] = {}

        installed = package_version() if check_version else None
        if installed is not None:
            manifest = check_overlay(bitstream, installed)
            say(f"rf-clock {installed}; " + (
                f"overlay manifest {manifest['build_version']} matches" if manifest
                else f"no {Path(bitstream).parent / 'manifest.json'}, checking BUILD_VERSION only"))

        config = (board_config if isinstance(board_config, BoardConfig)
                  else load_board_config(board_config))
        serial = read_board_serial()
        if config is None:
            say(f"board {serial}: no board config at {config_path()}")
        else:
            check_board(config, serial)
            say(f"board {serial} ({config.label or 'no label'}): board config matches")
        bands = config.lmx_vco_band if config else {}

        def load(path: Path) -> Any:
            from pynq import Overlay  # board-only

            say(f"boot state: {wait_for_boot()}")
            say(f"Overlay({path}) start")
            parts["overlay"] = Overlay(str(path))
            say("Overlay loaded")
            return parts["overlay"]

        def clocks() -> None:
            if not write_clocks:
                say("clocks left as they are")
                return
            from xrfclk import xrfclk  # board-only  # pyright: ignore[reportMissingImports]

            say("xrfclk start")
            program_clocks(xrfclk, bands)
            time.sleep(1.0)  # let the LMK/LMX PLLs settle before pl_resetn0
            say(f"xrfclk wrote LMK04828_{LMK_FREQ_MHZ} + LMX2594_{LMX_FREQ_MHZ}")
            parts["lmx_vco"] = vco = read_lmx_vco(xrfclk, bands)
            for side, v in vco.items():
                say(f"{side.upper()} LMX2594 {v.describe()}")
            unlocked = [f"{side.upper()} {v.describe()}" for side, v in vco.items() if not v.locked]
            if unlocked:
                raise BringupError("LMX2594 not locked: " + "; ".join(unlocked))
            calibrated = [f"{side.upper()} {v.band}" for side, v in vco.items() if v.forced is None]
            if calibrated:
                warnings.warn(f"LMX2594 VCO band calibrated, not set by the board config at "
                              f"{config_path()}: {', '.join(calibrated)}; the next power-up can pick "
                              f"another band and move the converter by 8-10 ps "
                              f"(rf_clock.checks.vco_bands)", stacklevel=4)

        def reset() -> None:
            say("pl_resetn0 pulse")
            pynq_pl_reset()

        def rfdc_setup(overlay: Any) -> None:
            say("RFDC tile start-up and MTS")
            result: RFDCSetupResult | None = None
            try:
                result = setup_rfdc_and_mts(
                    overlay, ref_tile=ref_tile,
                    dac_target_latency=dac_target_latency, adc_target_latency=adc_target_latency,
                    dtc_target={k: {c: v for c, v in clocks.items() if c in ("t1", "pll")}
                                for k, clocks in config.mts_dtc.items() if k in LMX_CHIP_SELECT} if config else None)
                for warning in mts_dtc_warnings(result, config):
                    warnings.warn(warning, stacklevel=4)
            except RFDCError as exc:
                result = getattr(exc, "result", None)
                raise
            finally:
                parts["mts"] = result
                if result is not None:
                    say(result.dac.describe())
                    say(result.adc.describe())

        registers, clock_status = pynq_register_factories()
        try:
            result = Bringup(
                overlay_loader=load, clock_setup=clocks, pl_reset=reset,
                register_factory=registers, clock_status_factory=clock_status,
                rfdc_setup=rfdc_setup if mts else None,
                pps_source_trig=pps_source_trig, pps_invert=pps_invert,
                package_version=installed,
            ).run(bitstream)
        except Exception as exc:
            exc.overlay = parts.get("overlay")  # type: ignore[attr-defined]
            exc.mts = parts.get("mts")  # type: ignore[attr-defined]
            raise

        from pynq import allocate  # board-only

        overlay = result.overlay
        return cls(
            ctrl=result.client,
            tx_buffer=TXBuffer(mmio_from_overlay(overlay, "tx_bram_ctrl"), result.client),
            rx_dma=RXDMA(pynq_rx_dma(overlay),
                         buffer_factory=lambda nbytes: allocate(shape=(nbytes,), dtype="u1")),
            rfdc=RFDC(driver=rfdc_driver(overlay), mts=parts.get("mts")),
            overlay=overlay,
            bringup=result,
            board=BoardSetup(serial=serial, config=config, lmx_vco=parts.get("lmx_vco", {})),
        )
