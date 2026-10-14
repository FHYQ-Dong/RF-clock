"""Explicit RFSoC power-up ordering for the software interface."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import abi
from .client import DeviceInfo, PLClient
from .mmio import PynqMMIO, RegisterIO
from .overlay import require_same_release


class BringupError(RuntimeError):
    pass


def overlay_address(overlay: Any, name: str, *, default_length: int = 0x1000) -> tuple[int, int]:
    """Return ``(base, length)`` of an overlay IP or memory from the HWH.

    PYNQ lists register IPs in ``ip_dict`` and AXI BRAM controllers (the TX
    buffer, ``tx_bram_ctrl``) in ``mem_dict``.  Versions have used
    ``phys_addr``/``baseaddr``/``base_address`` for the base and
    ``addr_range``/``size`` for the length; accept all of them.
    """
    for table_name in ("ip_dict", "mem_dict"):
        table = getattr(overlay, table_name, None) or {}
        if name in table:
            description = table[name]
            break
    else:
        raise BringupError(f"overlay has no IP or memory named {name!r}")
    base = next(
        (description[key] for key in ("phys_addr", "baseaddr", "base_address") if key in description),
        None,
    )
    if base is None:
        raise BringupError(f"overlay entry {name!r} has no physical address")
    length = next(
        (description[key] for key in ("addr_range", "size") if key in description),
        default_length,
    )
    return int(base), int(length)


def mmio_from_overlay(overlay: Any, ip_name: str, *, default_length: int = 0x1000) -> PynqMMIO:
    """Create a PYNQ MMIO wrapper for an overlay IP or memory (see :func:`overlay_address`)."""
    base, length = overlay_address(overlay, ip_name, default_length=default_length)
    return PynqMMIO(base, length)


@dataclass(frozen=True)
class BringupResult:
    overlay: Any
    client: PLClient
    info: DeviceInfo
    # FAULT_BITS / FAULT_INFO found at start-up, recorded before CLEAR_FAULT.
    initial_fault_bits: int = 0
    initial_fault_info: int = 0


class Bringup:
    """Run the ABI-defined start sequence using injected board operations.

    ``overlay_loader`` loads the bitstream; ``clock_setup`` writes the
    LMK/LMX files; ``pl_reset`` pulses pl_resetn0 so the fabric MMCM
    reacquires lock on the new PL_CLK; ``rfdc_setup`` starts the selected
    RFDC tiles and performs ADC/DAC MTS.  Keeping these board-specific actions
    as callbacks avoids pretending that PYNQ/xrfdc APIs are identical across
    images.  The callback order is the fixed order from ``docs/ABI.md``
    section 7.1: the overlay replaces PYNQ's boot-time design before the
    clocks change, because that design also runs on LMK clocks.

    The FS725 1PPS must be connected: P1 needs one reference PPS edge plus
    four good periods (about 5 s) before ``pps_locked`` is set, so
    ``pps_lock_timeout`` defaults to 10 s.  ``pps_source_trig`` and
    ``pps_invert`` are written to ``PPS_CONFIG`` before that wait.

    With ``package_version`` set, the bitstream's ``BUILD_VERSION`` must have
    the same A.B.C; it is checked right after ``validate()``.
    """

    def __init__(
        self,
        *,
        clock_setup: Callable[[], None],
        overlay_loader: Callable[[Path], Any],
        register_factory: Callable[[Any], RegisterIO],
        clock_status_factory: Callable[[Any], Any],
        rfdc_setup: Callable[[Any], object] | None = None,  # return value is ignored
        pl_reset: Callable[[], None] | None = None,
        pps_source_trig: bool = False,
        pps_invert: bool = False,
        package_version: str | None = None,
        p2_ip_name: str = "p_top_0",
        clock_gpio_ip_name: str = "clk_status_gpio",
        fabric_lock_timeout: float = 1.0,
        time_valid_timeout: float = 1.0,
        pps_lock_timeout: float = 10.0,
        poll_interval: float = 0.1,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock_setup = clock_setup
        self.overlay_loader = overlay_loader
        self.register_factory = register_factory
        self.clock_status_factory = clock_status_factory
        self.rfdc_setup = rfdc_setup
        self.pl_reset = pl_reset
        self.pps_source_trig = bool(pps_source_trig)
        self.pps_invert = bool(pps_invert)
        self.package_version = package_version
        self.p2_ip_name = p2_ip_name
        self.clock_gpio_ip_name = clock_gpio_ip_name
        self.fabric_lock_timeout = float(fabric_lock_timeout)
        self.time_valid_timeout = float(time_valid_timeout)
        self.pps_lock_timeout = float(pps_lock_timeout)
        self.poll_interval = float(poll_interval)
        self._monotonic = monotonic
        self._sleep = sleep

    def _wait_status(self, client: PLClient, field: str, timeout: float) -> None:
        deadline = self._monotonic() + timeout
        while not getattr(client.status(), field):
            if self._monotonic() >= deadline:
                raise BringupError(f"PL {field} not set within {timeout:g} s after overlay load")
            self._sleep(self.poll_interval)

    def _wait_fabric_lock(self, clock_status: Any) -> None:
        deadline = self._monotonic() + self.fabric_lock_timeout
        while not (int(clock_status.read(0)) & 1):
            if self._monotonic() >= deadline:
                raise BringupError(
                    f"fabric MMCM not locked within {self.fabric_lock_timeout:g} s after clock setup"
                )
            self._sleep(self.poll_interval)

    def run(self, bitstream: str | Path) -> BringupResult:
        overlay = self.overlay_loader(Path(bitstream))
        self.clock_setup()
        if self.pl_reset is not None:
            self.pl_reset()
        # P2/TX fabric-domain accesses are unsafe until this GPIO says the
        # MMCM is locked.  Check it before touching RFDC controls or
        # constructing the client.
        clock_status = self.clock_status_factory(overlay)
        self._wait_fabric_lock(clock_status)
        if self.rfdc_setup is not None:
            self.rfdc_setup(overlay)
        regs = self.register_factory(overlay)
        client = PLClient(regs, clock_status)
        info = client.validate()
        if self.package_version is not None:
            require_same_release(client.build_version(), self.package_version, "the loaded bitstream")
        client.set_pps_config(source_trig=self.pps_source_trig, invert=self.pps_invert)
        self._wait_status(client, "time_valid", self.time_valid_timeout)
        self._wait_status(client, "pps_locked", self.pps_lock_timeout)
        fault_bits = fault_info = 0
        if client.status().any_fault:
            # ABI §7.1: record what happened before clearing it.
            fault_bits = client.read32(abi.ABI.REG.FAULT_BITS)
            fault_info = client.read32(abi.ABI.REG.FAULT_INFO)
            client.clear_fault()
        client.remember_epoch()
        return BringupResult(
            overlay=overlay,
            client=client,
            info=info,
            initial_fault_bits=fault_bits,
            initial_fault_info=fault_info,
        )


def wait_for_boot(timeout: float = 300.0) -> str:
    """Block until systemd finishes booting; return the final state.

    PYNQ's bootpy.service starts about a minute after the kernel and loads
    the base overlay, replacing whatever is in the PL at that moment (seen on
    the board 2026-10-14).  Load the RF-clock overlay only after it is done.
    """
    import subprocess

    done = subprocess.run(["systemctl", "is-system-running", "--wait"],
                          capture_output=True, text=True, timeout=timeout)
    return done.stdout.strip()


def pynq_pl_reset(*, emio_index: int = 95, pulse: float = 0.01) -> None:
    """Pulse pl_resetn0 (EMIO GPIO 95 on Zynq UltraScale+).

    It resets the fabric MMCM and every proc_sys_reset in the design, so the
    MMCM reacquires lock on the PL_CLK that ``clock_setup`` just programmed.
    """
    from pynq import GPIO  # board-only dependency

    pin = GPIO(GPIO.get_gpio_pin(emio_index), "out")
    pin.write(0)
    time.sleep(pulse)
    pin.write(1)


def pynq_register_factories(*, p2_ip_name: str = "p_top_0", gpio_ip_name: str = "clk_status_gpio"):
    """Return HWH-backed factories for the standard overlay IP names."""

    def registers(overlay: Any) -> RegisterIO:
        return mmio_from_overlay(overlay, p2_ip_name)

    def clock_status(overlay: Any) -> RegisterIO:
        return mmio_from_overlay(overlay, gpio_ip_name)

    return registers, clock_status
