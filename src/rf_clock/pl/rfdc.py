"""RFDC tile start-up and multi-tile sync (MTS) on the board.

PYNQ 3.1.1's ``xrfdc`` wraps the RFdc driver but not its MTS API, although
its ``libxrfdc.so`` exports it.  That library is built from embeddedsw
``xilinx-v2020.2`` (PYNQ ``sdbuild/packages/xrfdc/package/Makefile``), so the
MTS declarations below copy that release's ``xrfdc_mts.h``.  They must match
it exactly: later releases add a ``RefTile`` argument to
``XRFdc_MultiConverter_Init``.

The design enables ADC and DAC tiles 0 and 2 (tile 2 carries the signal,
tile 0 exists for MTS; ``AGENTS.md``).  Everything here needs the PYNQ
runtime and is imported lazily, so the module loads on a development machine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ADC = 0  # XRFDC_ADC_TILE
DAC = 1  # XRFDC_DAC_TILE
TILE_STARTED = 0xF
DEFAULT_TILES = (0, 2)

# XRFDC_MTS_* status bits (embeddedsw 2020.2 xrfdc_mts.h); 0 is XRFDC_MTS_OK.
MTS_STATUS_BITS = {
    1: "NOT_SUPPORTED",
    2: "TIMEOUT",
    4: "MARKER_RUN",
    8: "MARKER_MISM",
    16: "DELAY_OVER",
    32: "TARGET_LOW",
    64: "IP_NOT_READY",
    128: "DTC_INVALID",
    512: "NOT_ENABLED",
    2048: "SYSREF_GATE_ERROR",
    4096: "SYSREF_FREQ_NDONE",
}

_MTS_CDEF = """
typedef struct {
    u32 RefTile;
    u32 IsPLL;
    int Target[4];
    int Scan_Mode;
    int DTC_Code[4];
    int Num_Windows[4];
    int Max_Gap[4];
    int Min_Gap[4];
    int Max_Overlap[4];
} XRFdc_MTS_DTC_Settings;

typedef struct {
    u32 RefTile;
    u32 Tiles;
    int Target_Latency;
    int Offset[4];
    int Latency[4];
    int Marker_Delay;
    int SysRef_Enable;
    XRFdc_MTS_DTC_Settings DTC_Set_PLL;
    XRFdc_MTS_DTC_Settings DTC_Set_T1;
} XRFdc_MultiConverter_Sync_Config;

u32 XRFdc_MultiConverter_Sync(XRFdc *InstancePtr, u32 Type, XRFdc_MultiConverter_Sync_Config *ConfigPtr);
void XRFdc_MultiConverter_Init(XRFdc_MultiConverter_Sync_Config *ConfigPtr, int *PLL_CodesPtr, int *T1_CodesPtr);
u32 XRFdc_MTS_Sysref_Config(XRFdc *InstancePtr, XRFdc_MultiConverter_Sync_Config *DACSyncConfigPtr,
                            XRFdc_MultiConverter_Sync_Config *ADCSyncConfigPtr, u32 SysRefEnable);
"""
SYSREF_WAIT_S = 0.001  # SYSREF is 5 MHz: an edge every 200 ns


class RFDCError(RuntimeError):
    pass


def decode_mts_status(status: int) -> list[str]:
    """Names of the XRFDC_MTS_* bits set in ``status`` (empty for OK)."""
    names = [name for bit, name in MTS_STATUS_BITS.items() if status & bit]
    unknown = status & ~sum(MTS_STATUS_BITS)
    if unknown:
        names.append(f"UNKNOWN_0x{unknown:x}")
    return names


@dataclass(frozen=True)
class MTSResult:
    kind: str  # "DAC" or "ADC"
    status: int
    tiles: tuple[int, ...]
    ref_tile: int
    target_latency: int  # requested; -1 = align to the largest measured
    latency: tuple[int, ...]  # per tile 0..3, after alignment
    offset: tuple[int, ...]  # per tile 0..3, applied delay steps
    # SYSREF capture delay codes the driver picked (0..127) and the number of
    # capture windows it found, per tile 0..3, for the PLL and the T1 clock: they
    # follow the tile clock's phase to SYSREF, which latency and offset do not.
    dtc: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # The codes the windows were picked by ("t1", "pll"), from the board config;
    # empty: the driver's own choice, the window nearest code 64.
    dtc_target: dict[str, tuple[int, ...]] = field(default_factory=dict)

    @property
    def on_target(self) -> bool:
        """Every tile ends at the requested latency (always true for -1).

        The driver adds delay in whole steps, so a target that is not a whole
        number of steps above a tile's latency leaves a remainder without an
        error status.
        """
        return self.target_latency < 0 or all(
            self.latency[t] == self.target_latency for t in self.tiles)

    @property
    def ok(self) -> bool:
        return self.status == 0 and self.on_target

    def describe(self) -> str:
        problems = decode_mts_status(self.status) + ([] if self.on_target else ["OFF_TARGET"])
        state = "OK" if self.ok else "|".join(problems)
        per_tile = ", ".join(
            f"tile {t}: latency {self.latency[t]} offset {self.offset[t]}" for t in self.tiles
        )
        return f"{self.kind} MTS {state} (ref tile {self.ref_tile}; {per_tile})"


@dataclass(frozen=True)
class RFDCSetupResult:
    dac: MTSResult
    adc: MTSResult
    started_after_startup_call: bool  # tiles needed an explicit StartUp
    mixers: dict[str, Any]  # signal-tile mixer settings read back
    nco_synced: bool = False  # NCO phases restarted at a SYSREF edge (sync_nco_phase)


_MTS: tuple[Any, Any] | None = None  # (cffi.FFI, dlopen'ed libxrfdc)


def _mts_api() -> tuple[Any, Any]:
    """cffi handle for the MTS functions, sharing xrfdc's types."""
    global _MTS
    if _MTS is None:
        import cffi
        import xrfdc  # board-only  # pyright: ignore[reportMissingImports]

        ffi = cffi.FFI()
        ffi.include(xrfdc._ffi)
        ffi.cdef(_MTS_CDEF)
        lib = ffi.dlopen(str(Path(str(xrfdc.__file__)).with_name("libxrfdc.so")))
        _MTS = (ffi, lib)
    return _MTS


def rfdc_driver(overlay: Any, name: str = "rfdc") -> Any:
    """The xrfdc.RFdc driver for the overlay's RFDC IP.

    PYNQ binds xrfdc.RFdc only if xrfdc was imported before the Overlay was
    created; otherwise the attribute is a DefaultIP.  Build the driver from
    the HWH description in that case.
    """
    import xrfdc  # board-only  # pyright: ignore[reportMissingImports]

    ip = getattr(overlay, name)
    if isinstance(ip, xrfdc.RFdc):
        return ip
    return xrfdc.RFdc(overlay.ip_dict[name])


def tile_states(rfdc: Any) -> dict[str, list[int]]:
    """TileState of every ADC and DAC tile (0xF = started)."""
    import xrfdc  # board-only  # pyright: ignore[reportMissingImports]

    status = xrfdc._ffi.new("XRFdc_IPStatus*")
    xrfdc._lib.XRFdc_GetIPStatus(rfdc._instance, status)
    return {
        "ADC": [int(status.ADCTileStatus[t].TileState) for t in range(4)],
        "DAC": [int(status.DACTileStatus[t].TileState) for t in range(4)],
    }


def start_tiles(
    rfdc: Any,
    tiles: tuple[int, ...] = DEFAULT_TILES,
    *,
    settle: float = 1.0,
    timeout: float = 5.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait for the ADC/DAC ``tiles`` to reach the started state.

    After pl_resetn0 the IP restarts its power-up sequence on its own; if a
    tile is not started within ``settle`` seconds, call StartUp on it and
    wait up to ``timeout``.  Returns True if StartUp had to be called.
    """

    def pending() -> list[tuple[str, int]]:
        states = tile_states(rfdc)
        return [(kind, t) for kind in ("ADC", "DAC") for t in tiles
                if states[kind][t] != TILE_STARTED]

    called = False
    deadline = monotonic() + settle
    while True:
        waiting = pending()
        if not waiting:
            return called
        if monotonic() >= deadline:
            if called:
                raise RFDCError(f"RFDC tiles not started: {waiting}, states {tile_states(rfdc)}")
            for kind, t in waiting:
                (rfdc.adc_tiles if kind == "ADC" else rfdc.dac_tiles)[t].StartUp()
            called = True
            deadline = monotonic() + timeout
        sleep(0.05)


def run_mts(
    rfdc: Any,
    kind: str,
    tiles: tuple[int, ...] = DEFAULT_TILES,
    *,
    ref_tile: int = 0,
    target_latency: int = -1,
    dtc_target: dict[str, Any] | None = None,
) -> MTSResult:
    """Run XRFdc_MultiConverter_Sync for the ADC or DAC ``tiles``.

    ``dtc_target`` ({"t1": 4 codes, "pll": 4 codes}, either optional) makes the
    driver pick, in each tile, the SYSREF capture window nearest that code instead
    of the one nearest code 64 (``XRFdc_MultiConverter_Init`` with codes: the
    "reload" scan).  Windows repeat every clock period, so the codes recorded
    once keep the same window, and so the same latency to the sample, at every
    power-up while the clock stays within half a window of them.
    """
    if kind not in ("ADC", "DAC"):
        raise ValueError("kind must be 'ADC' or 'DAC'")
    ffi, lib = _mts_api()
    config = ffi.new("XRFdc_MultiConverter_Sync_Config*")
    target = {clock: tuple(int(c) for c in codes) for clock, codes in (dtc_target or {}).items()}
    codes = {clock: ffi.new("int[4]", list(target[clock])) if clock in target else ffi.NULL for clock in ("pll", "t1")}
    lib.XRFdc_MultiConverter_Init(config, codes["pll"], codes["t1"])
    config.Tiles = sum(1 << t for t in tiles)
    config.RefTile = int(ref_tile)
    config.Target_Latency = int(target_latency)
    status = int(lib.XRFdc_MultiConverter_Sync(rfdc._instance, DAC if kind == "DAC" else ADC, config))
    dtc = {f"{name}_{key}": tuple(int(getattr(settings, field_)[t]) for t in range(4))
           for name, settings in (("pll", config.DTC_Set_PLL), ("t1", config.DTC_Set_T1))
           for key, field_ in (("code", "DTC_Code"), ("windows", "Num_Windows"))}
    return MTSResult(
        kind=kind,
        status=status,
        tiles=tuple(tiles),
        ref_tile=int(ref_tile),
        target_latency=int(target_latency),
        latency=tuple(int(config.Latency[t]) for t in range(4)),
        offset=tuple(int(config.Offset[t]) for t in range(4)),
        dtc=dtc,
        dtc_target=target,
    )


def sync_nco_phase(
    rfdc: Any,
    tiles: tuple[int, ...] = DEFAULT_TILES,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Restart the NCO phase of block 0 of every ADC and DAC tile at one SYSREF edge.

    MTS aligns the data paths but not the NCO phase accumulators.  The IP
    starts them on a tile event, issued by software at an arbitrary instant, so
    after every PL.open the carrier phase landed on one of three values
    120 degrees apart, on the DAC and on the ADC
    (results/lmx-sync-loopback-20261019, section 4).  This is PG269's "Use
    Case 1: Synchronize Digital Features Using SYSREF for Single Device": with
    the SYSREF receivers off, each mixer takes SYSREF as its event source and
    ResetNCOPhase arms a phase reset; turning the receivers on lets the next
    SYSREF edge apply it, and they are turned off again.  A SYSREF period
    (200 ns) holds whole NCO periods (760 at 3.8 GHz, 200 at 1.0 GHz), so every
    edge gives the same phase.  The event sources are then set back, so later
    mixer changes with UpdateEvent keep working.
    """
    import xrfdc  # board-only  # pyright: ignore[reportMissingImports]

    ffi, lib = _mts_api()
    configs = []
    for _ in (DAC, ADC):
        config = ffi.new("XRFdc_MultiConverter_Sync_Config*")
        lib.XRFdc_MultiConverter_Init(config, ffi.NULL, ffi.NULL)
        config.Tiles = sum(1 << t for t in tiles)
        configs.append(config)

    def sysref_receivers(enable: int) -> None:
        status = int(lib.XRFdc_MTS_Sysref_Config(rfdc._instance, configs[0], configs[1], enable))
        if status:
            raise RFDCError(f"XRFdc_MTS_Sysref_Config({enable}) failed: {decode_mts_status(status)}")

    blocks = [kind_tiles[t].blocks[0] for kind_tiles in (rfdc.dac_tiles, rfdc.adc_tiles) for t in tiles]
    sources = [block.MixerSettings["EventSource"] for block in blocks]
    sysref_receivers(0)
    for block in blocks:
        block.MixerSettings = {**block.MixerSettings, "EventSource": xrfdc.EVNT_SRC_SYSREF}
        block.ResetNCOPhase()
    sysref_receivers(1)
    sleep(SYSREF_WAIT_S)
    sysref_receivers(0)
    for block, source in zip(blocks, sources):
        block.MixerSettings = {**block.MixerSettings, "EventSource": source}


def setup_rfdc_and_mts(
    overlay: Any,
    *,
    rfdc_name: str = "rfdc",
    tiles: tuple[int, ...] = DEFAULT_TILES,
    signal_tile: int = 2,
    ref_tile: int = 0,
    dac_target_latency: int = -1,
    adc_target_latency: int = -1,
    dtc_target: dict[str, dict[str, Any]] | None = None,
) -> RFDCSetupResult:
    """Start the ADC/DAC tiles, run DAC then ADC MTS (``docs/ABI.md`` 7.1) and,
    if both succeed, restart the NCO phases at a SYSREF edge (``sync_nco_phase``).
    ``dtc_target`` ({"dac": ..., "adc": ...}) passes each converter's capture codes
    to ``run_mts``.

    Raises RFDCError if either sync reports a non-OK status; the error carries
    the result so the latencies can still be logged.
    """
    rfdc = rfdc_driver(overlay, rfdc_name)
    called = start_tiles(rfdc, tiles)
    dtc_target = dtc_target or {}
    dac = run_mts(rfdc, "DAC", tiles, ref_tile=ref_tile, target_latency=dac_target_latency,
                  dtc_target=dtc_target.get("dac"))
    adc = run_mts(rfdc, "ADC", tiles, ref_tile=ref_tile, target_latency=adc_target_latency,
                  dtc_target=dtc_target.get("adc"))
    nco_synced = dac.ok and adc.ok
    if nco_synced:
        sync_nco_phase(rfdc, tiles)
    mixers = {
        "DAC": dict(rfdc.dac_tiles[signal_tile].blocks[0].MixerSettings),
        "ADC": dict(rfdc.adc_tiles[signal_tile].blocks[0].MixerSettings),
    }
    result = RFDCSetupResult(dac=dac, adc=adc, started_after_startup_call=called, mixers=mixers,
                             nco_synced=nco_synced)
    if not (dac.ok and adc.ok):
        error = RFDCError(f"{dac.describe()}; {adc.describe()}")
        error.result = result  # type: ignore[attr-defined]
        raise error
    return result
