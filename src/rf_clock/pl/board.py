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
=============  ===========================================================
"""

from __future__ import annotations

import re
import struct
import time
from pathlib import Path
from typing import Any, Callable

from .bringup import (Bringup, BringupResult, mmio_from_overlay, pynq_pl_reset,
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
# ADC 1236 on run4 and 1248 on run6; both adjust in steps of 6.  A build whose
# tile latency exceeds a target fails MTS (TARGET_LOW); raise the target then
# and recalibrate the loopback delay.
DAC_TARGET_LATENCY = 1896
ADC_TARGET_LATENCY = 1296
LMX_VCO_PHASE_SYNC = 1 << 14  # R0 bit
LMX_CAL_WAIT_S = 0.01  # after an R0 write with FCAL_EN = 1 (TI SNAS696C 7.5)


def clock_registers(name: str) -> list[int]:
    """Register words of a packaged TICS file, one per line, as xrfclk reads them."""
    words = []
    for line in (CLOCK_FILES / name).read_text().splitlines():
        match = re.search(r"0x[0-9A-Fa-f]+", line)
        if match:
            words.append(int(match.group(), 16))
    return words


def program_clocks(xrfclk: Any) -> None:
    """Program the LMK04828 and LMX2594 from the files in ``rf_clock.clocks``.

    ``xrfclk`` is the ``xrfclk.xrfclk`` module (PYNQ 3.1).  Its
    ``set_ref_clks()`` only reads the .txt files in its own package directory,
    into the table ``_Config``, and only when it has not found the devices
    yet.  Finding them here and filling ``_Config`` from our files makes it
    write our registers, so the board needs no copies inside xrfclk.  An LMX
    image with VCO_PHASE_SYNC set then gets its SYNC (``sync_lmx_phase``).
    """
    if not xrfclk.lmk_devices and not xrfclk.lmx_devices:
        xrfclk._find_devices()
    lmx = clock_registers(f"LMX2594_{LMX_FREQ_MHZ}.txt")
    xrfclk._Config["lmk04828"][LMK_FREQ_MHZ] = clock_registers(f"LMK04828_{LMK_FREQ_MHZ}.txt")
    xrfclk._Config["lmx2594"][LMX_FREQ_MHZ] = lmx
    xrfclk.set_ref_clks(lmk_freq=LMK_FREQ_MHZ, lmx_freq=LMX_FREQ_MHZ)
    if lmx[-1] & LMX_VCO_PHASE_SYNC:
        sync_lmx_phase(xrfclk, lmx[-1])


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
                 overlay: Any, bringup: BringupResult) -> None:
        self.ctrl = ctrl
        self.tx_buffer = tx_buffer
        self.rx_dma = rx_dma
        self.rfdc = rfdc
        self.overlay = overlay
        self.bringup = bringup

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
        """
        say = progress or (lambda message: None)
        parts: dict[str, Any] = {}

        installed = package_version() if check_version else None
        if installed is not None:
            manifest = check_overlay(bitstream, installed)
            say(f"rf-clock {installed}; " + (
                f"overlay manifest {manifest['build_version']} matches" if manifest
                else f"no {Path(bitstream).parent / 'manifest.json'}, checking BUILD_VERSION only"))

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
            program_clocks(xrfclk)
            time.sleep(1.0)  # let the LMK/LMX PLLs settle before pl_resetn0
            say(f"xrfclk wrote LMK04828_{LMK_FREQ_MHZ} + LMX2594_{LMX_FREQ_MHZ}")

        def reset() -> None:
            say("pl_resetn0 pulse")
            pynq_pl_reset()

        def rfdc_setup(overlay: Any) -> None:
            say("RFDC tile start-up and MTS")
            result: RFDCSetupResult | None = None
            try:
                result = setup_rfdc_and_mts(
                    overlay, ref_tile=ref_tile,
                    dac_target_latency=dac_target_latency, adc_target_latency=adc_target_latency)
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
        )
