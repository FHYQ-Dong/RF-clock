"""High-level, clock-guarded access to the P2 control/status block (``pl.ctrl``)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import abi
from .mmio import ClockStatusIO, RegisterIO


class PLClientError(RuntimeError):
    """Base class for software-side PL interface errors."""


class ClockUnavailable(PLClientError):
    """The fabric clock status GPIO is not asserted."""


class IdentityError(PLClientError):
    """The loaded overlay is not the expected RF-clock design."""


class EpochChanged(PLClientError):
    """The clock lost/reacquired lock while a measurement was in flight."""


class CommandRejected(PLClientError):
    def __init__(self, operation: str, result: int) -> None:
        self.operation = operation
        self.result = int(result)
        try:
            name = abi.ResultCode(self.result).name
        except ValueError:
            name = "UNKNOWN"
        super().__init__(f"{operation} rejected: 0x{self.result:02x} ({name})")


class WaitTimeout(PLClientError):
    pass


class TargetMissed(PLClientError):
    """TX_ACTUAL / RX_ACTUAL differs from the armed target (never seen on the board)."""

    def __init__(self, operation: str, target: int, actual: int) -> None:
        self.operation = operation
        self.target = int(target)
        self.actual = int(actual)
        super().__init__(f"{operation}_ACTUAL 0x{actual:x} differs from target 0x{target:x}")


class RXWindowInvalid(PLClientError):
    """The window lost beats (RX_RESULT not DONE or a flag set): discard the measurement."""

    def __init__(self, flags: abi.RxFlag, result: int) -> None:
        self.flags = abi.RxFlag(flags)
        self.result = int(result)
        super().__init__(
            f"RX window invalid: result=0x{result:02x}, flags=0x{int(flags):x}"
        )


@dataclass(frozen=True)
class RXWindow:
    """A finished RX window as P2 reports it; the samples come from ``rx_dma.wait(beats)``."""

    target: int
    actual: int  # RX_ACTUAL: tick of the first sample
    beats: int  # RX_BEATS: beats sent into the DMA


@dataclass(frozen=True)
class Status:
    raw: int
    time_valid: bool
    clocks_locked: bool
    pps_locked: bool
    tick_exhausted: bool
    any_fault: bool
    dma_ready: bool
    tx_state: abi.TxState
    rx_state: abi.RxState

    @classmethod
    def decode(cls, raw: int) -> "Status":
        raw = int(raw) & 0xFFFF_FFFF
        tx_value = (raw >> abi.ABI.STAT.TX_STATE_LSB) & 0x7
        rx_value = (raw >> abi.ABI.STAT.RX_STATE_LSB) & 0x7
        try:
            tx_state = abi.TxState(tx_value)
        except ValueError as exc:
            raise PLClientError(f"unknown TX state {tx_value}") from exc
        try:
            rx_state = abi.RxState(rx_value)
        except ValueError as exc:
            raise PLClientError(f"unknown RX state {rx_value}") from exc
        return cls(
            raw=raw,
            time_valid=bool(raw & (1 << abi.ABI.STAT.TIME_VALID)),
            clocks_locked=bool(raw & (1 << abi.ABI.STAT.CLOCKS_LOCKED)),
            pps_locked=bool(raw & (1 << abi.ABI.STAT.PPS_LOCKED)),
            tick_exhausted=bool(raw & (1 << abi.ABI.STAT.TICK_EXHAUSTED)),
            any_fault=bool(raw & (1 << abi.ABI.STAT.ANY_FAULT)),
            dma_ready=bool(raw & (1 << abi.ABI.STAT.DMA_READY)),
            tx_state=tx_state,
            rx_state=rx_state,
        )


@dataclass(frozen=True)
class PPSInputs:
    """The board's two PPS copies as seen by the PL (``PPS_INPUTS``, ABI 1.1).

    Levels are the synchronized pin states; edge counts are wrapping 8-bit
    rising-edge counters.  An idle-high copy pulses low (time it inverted).
    """

    comp_level: bool
    trig_level: bool
    comp_edges: int
    trig_edges: int

    @classmethod
    def decode(cls, raw: int) -> "PPSInputs":
        raw = int(raw) & 0xFFFF_FFFF
        return cls(
            comp_level=bool(raw & (1 << abi.ABI.PPSIN.COMP_LEVEL)),
            trig_level=bool(raw & (1 << abi.ABI.PPSIN.TRIG_LEVEL)),
            comp_edges=(raw >> abi.ABI.PPSIN.COMP_EDGES_LSB) & 0xFF,
            trig_edges=(raw >> abi.ABI.PPSIN.TRIG_EDGES_LSB) & 0xFF,
        )


@dataclass(frozen=True)
class DeviceInfo:
    design_id: int
    version: int
    tx_depth: int
    rx_max_beats: int
    min_lead_ticks: int


class PLClient:
    """Own the ARM-visible ABI and enforce the PL access ordering rules.

    ``regs`` and ``clock_status`` are intentionally small protocols.  Pass
    ``PynqMMIO`` on the board and ``MemoryMMIO`` (or a fake model) in tests.
    No register is touched by the constructor; callers must explicitly call
    :meth:`validate` after the clock GPIO is known to be running.
    """

    def __init__(
        self,
        regs: RegisterIO,
        clock_status: ClockStatusIO,
        *,
        clock_status_offset: int = 0,
        clock_locked_mask: int = 1,
        expected_major: int = 1,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self.regs = regs
        self.clock_status = clock_status
        self.clock_status_offset = int(clock_status_offset)
        self.clock_locked_mask = int(clock_locked_mask)
        self.expected_major = int(expected_major)
        self._monotonic = monotonic
        self._sleep = sleep
        self._epoch: int | None = None
        self._armed: dict[str, int] = {}  # operation -> target of the last accepted doorbell
        self._info: DeviceInfo | None = None

    @property
    def info(self) -> DeviceInfo:
        """ID, VERSION and build parameters read by :meth:`validate`."""
        if self._info is None:
            raise IdentityError("call validate() first")
        return self._info

    def fabric_clock_locked(self) -> bool:
        value = int(self.clock_status.read(self.clock_status_offset))
        return bool(value & self.clock_locked_mask)

    def require_fabric_clock(self) -> None:
        if not self.fabric_clock_locked():
            raise ClockUnavailable("fabric MMCM is not locked; refusing P2/TX access")

    def _read(self, offset: int) -> int:
        self.require_fabric_clock()
        return int(self.regs.read(int(offset))) & 0xFFFF_FFFF

    def _write(self, offset: int, value: int) -> None:
        self.require_fabric_clock()
        value = int(value)
        if not 0 <= value <= 0xFFFF_FFFF:
            raise ValueError("P2 registers accept one 32-bit word")
        self.regs.write(int(offset), value)

    def read32(self, offset: int) -> int:
        return self._read(offset)

    def write32(self, offset: int, value: int) -> None:
        self._write(offset, value)

    def read64(self, lo_offset: int) -> int:
        """Read LO before HI, preserving the PL's atomic snapshot semantics."""
        lo = self._read(lo_offset)
        hi = self._read(int(lo_offset) + 4)
        return (hi << 32) | lo

    def validate(self) -> DeviceInfo:
        design_id = self._read(abi.ABI.REG.ID)
        version = self._read(abi.ABI.REG.VERSION)
        if design_id != abi.ABI.DESIGN_ID:
            raise IdentityError(f"unexpected PL ID 0x{design_id:08x}")
        major = version >> 16
        if major != self.expected_major:
            raise IdentityError(
                f"unsupported PL ABI major {major}; expected {self.expected_major}"
            )
        self._info = DeviceInfo(
            design_id=design_id,
            version=version,
            tx_depth=self._read(abi.ABI.REG.TX_DEPTH),
            rx_max_beats=self._read(abi.ABI.REG.RX_MAX_BEATS),
            min_lead_ticks=self._read(abi.ABI.REG.MIN_LEAD_TICKS),
        )
        self._epoch = self._read(abi.ABI.REG.TIME_EPOCH)
        return self._info

    def status(self) -> Status:
        return Status.decode(self._read(abi.ABI.REG.STATUS))

    def time_epoch(self) -> int:
        return self._read(abi.ABI.REG.TIME_EPOCH)

    def remember_epoch(self) -> int:
        self._epoch = self.time_epoch()
        return self._epoch

    def require_same_epoch(self, expected: int | None = None) -> int:
        current = self.time_epoch()
        baseline = self._epoch if expected is None else int(expected)
        if baseline is None:
            self._epoch = current
            return current
        if current != baseline:
            raise EpochChanged(f"TIME_EPOCH changed from {baseline} to {current}")
        return current

    def latest_pps(self) -> tuple[int, int]:
        """``(PPS_COUNT, PPS_TICK)`` of the most recent PPS edge.

        Reading PPS_TICK_LO latches PPS_TICK_HI and PPS_COUNT, so the pair
        always belongs to the same edge.
        """
        tick = self.read64(abi.ABI.REG.PPS_TICK_LO)
        return self._read(abi.ABI.REG.PPS_COUNT), tick

    def wait_next_pps(self, *, timeout: float = 2.5, poll_interval: float = 0.005) -> tuple[int, int]:
        """Block until a new PPS edge is captured; return its ``(count, tick)``.

        Scheduling right after an edge leaves almost a full second before the
        next one.
        """
        count0, _ = self.latest_pps()
        deadline = self._monotonic() + timeout
        while True:
            count, tick = self.latest_pps()
            if count != count0:
                return count, tick
            if self._monotonic() >= deadline:
                raise WaitTimeout(f"no PPS edge within {timeout:g} s")
            if poll_interval:
                self._sleep(poll_interval)

    def _command(self, operation: str, target: int, length: int, *, rx: bool,
                 repeat: int = 1) -> None:
        if not isinstance(target, int) or not 0 <= target < (1 << 64):
            raise ValueError("target must be an unsigned 64-bit integer")
        if not isinstance(length, int) or length <= 0:
            raise ValueError("length must be a positive integer number of beats")
        if not isinstance(repeat, int) or not 1 <= repeat <= abi.ABI.TX_REPEAT_MAX:
            raise ValueError(f"repeat must be 1..{abi.ABI.TX_REPEAT_MAX}")
        # TX_REPEAT exists from ABI 1.3; writing it on an older bitstream is a
        # bus error, so it is only written where it exists.
        has_repeat = not rx and self._info is not None and (self._info.version & 0xFFFF) >= 3
        if repeat != 1 and not has_repeat:
            self._require_minor(3, "TX_REPEAT")
        state = self.status().rx_state if rx else self.status().tx_state
        if state not in (abi.RxState.IDLE, abi.RxState.DONE):
            raise CommandRejected(operation, int(abi.ResultCode.REJ_BUSY))
        if self._epoch is not None:
            self.require_same_epoch()
        target_lo = abi.ABI.REG.RX_TARGET_LO if rx else abi.ABI.REG.TX_TARGET_LO
        length_reg = abi.ABI.REG.RX_LENGTH if rx else abi.ABI.REG.TX_LENGTH
        doorbell = abi.ABI.CTRL.RX_DOORBELL if rx else abi.ABI.CTRL.TX_DOORBELL
        seq_reg = abi.ABI.REG.RX_SEQ if rx else abi.ABI.REG.TX_SEQ
        seq_before = self._read(seq_reg)
        self._write(target_lo, target & 0xFFFF_FFFF)
        self._write(target_lo + 4, target >> 32)
        self._write(length_reg, length)
        if has_repeat:
            self._write(abi.ABI.REG.TX_REPEAT, repeat)
        self._write(abi.ABI.REG.CONTROL, 1 << doorbell)
        result_reg = abi.ABI.REG.RX_RESULT if rx else abi.ABI.REG.TX_RESULT
        result = self._read(result_reg) & 0xFF
        self._armed.pop(operation, None)
        self._check_accepted(operation, result, seq_reg, seq_before, rx=rx)
        self._armed[operation] = target

    def _check_accepted(
        self, operation: str, result: int, seq_reg: int, seq_before: int, *, rx: bool
    ) -> None:
        """Decide whether the doorbell was accepted from the RESULT read after it.

        A rejection leaves a 0x1x code.  An accepted command walks
        ARMED -> RUNNING -> DONE (RX may finish as RX_INVALID, TX may FAULT),
        and with a near target it can already be past ARMED when RESULT is
        read.  DONE/RX_INVALID only count if the completion sequence advanced,
        so the previous command's result is never mistaken for this one.
        """
        code = abi.ResultCode
        if result in (int(code.ARMED), int(code.RUNNING)):
            return
        finished = (int(code.DONE), int(code.RX_INVALID)) if rx else (int(code.DONE),)
        if result in finished and self._read(seq_reg) != seq_before:
            return
        if not rx and result in (
            int(code.FAULT_TREADY), int(code.FAULT_BUF_WRITE), int(code.FAULT_TIME)
        ):
            raise PLClientError(f"TX entered FAULT right after arming (result 0x{result:02x})")
        raise CommandRejected(operation, result)

    def arm_tx(self, target: int, beats: int, *, repeat: int = 1) -> None:
        """Play TX buffer beats 0..beats-1 from tick ``target``.

        ``repeat`` plays them that many times back to back without a gap
        (TX_REPEAT, ABI 1.3); TX_ACTUAL is the start of the first pass.
        """
        self._command("TX", target, beats, rx=False, repeat=repeat)

    def arm_rx(self, target: int, beats: int) -> None:
        self._command("RX", target, beats, rx=True)

    def _wait(self, operation: str, *, rx: bool, timeout: float, poll_interval: float) -> Status:
        if timeout < 0 or poll_interval < 0:
            raise ValueError("timeout and poll_interval must be non-negative")
        deadline = self._monotonic() + timeout
        while True:
            if self._epoch is not None:
                self.require_same_epoch()
            status = self.status()
            state = status.rx_state if rx else status.tx_state
            if state == abi.RxState.DONE:
                return status
            if not rx and state == abi.TxState.FAULT:
                result = self._read(abi.ABI.REG.TX_RESULT)
                raise PLClientError(f"TX entered FAULT (result 0x{result:02x})")
            if self._monotonic() >= deadline:
                raise WaitTimeout(f"timed out waiting for {operation}")
            if poll_interval:
                self._sleep(poll_interval)

    def _check_target(self, operation: str, actual: int) -> int:
        target = self._armed.get(operation)
        if target is not None and actual != target:
            raise TargetMissed(operation, target, actual)
        return actual if target is None else target

    def wait_tx(self, *, timeout: float = 1.0, poll_interval: float = 0.001) -> int:
        """Wait for TX DONE; return TX_ACTUAL, checked against the armed target."""
        self._wait("TX DONE", rx=False, timeout=timeout, poll_interval=poll_interval)
        actual = self.tx_actual()
        self._check_target("TX", actual)
        return actual

    def wait_rx(self, *, timeout: float = 1.0, poll_interval: float = 0.001) -> RXWindow:
        """Wait for RX DONE and check the window (docs/ABI.md 7.3 steps 5-7).

        ``RXWindowInvalid`` when the window lost beats, ``TargetMissed`` when
        RX_ACTUAL differs from the armed target.  P4 reports DONE only after
        the DMA took the window's last beat; read the samples with
        ``rx_dma.wait(window.beats)``.
        """
        self._wait("RX DONE", rx=True, timeout=timeout, poll_interval=poll_interval)
        code, flags = self.rx_result()
        if int(code) != int(abi.ResultCode.DONE) or flags:
            raise RXWindowInvalid(flags, int(code))
        actual = self.rx_actual()
        target = self._check_target("RX", actual)
        return RXWindow(target=target, actual=actual, beats=self.rx_beats())

    def tx_actual(self) -> int:
        return self.read64(abi.ABI.REG.TX_ACTUAL_LO)

    def rx_actual(self) -> int:
        return self.read64(abi.ABI.REG.RX_ACTUAL_LO)

    def rx_beats(self) -> int:
        return self._read(abi.ABI.REG.RX_BEATS)

    def rx_result(self) -> tuple[abi.ResultCode | int, abi.RxFlag]:
        raw = self._read(abi.ABI.REG.RX_RESULT)
        code_value = raw & 0xFF
        try:
            code: abi.ResultCode | int = abi.ResultCode(code_value)
        except ValueError:
            code = code_value
        return code, abi.RxFlag((raw >> abi.ABI.RX_RESULT_FLAGS_LSB) & 0xF)

    def cancel_tx(self) -> None:
        self._write(abi.ABI.REG.CONTROL, 1 << abi.ABI.CTRL.TX_CANCEL)

    def cancel_rx(self) -> None:
        self._write(abi.ABI.REG.CONTROL, 1 << abi.ABI.CTRL.RX_CANCEL)

    def clear_fault(self) -> None:
        self._write(abi.ABI.REG.CONTROL, 1 << abi.ABI.CTRL.CLEAR_FAULT)

    def _require_minor(self, minor: int, what: str) -> None:
        if self._info is None or (self._info.version & 0xFFFF) < minor:
            raise IdentityError(f"{what} needs PL ABI 1.{minor}; call validate() on a newer bitstream")

    def build_version(self) -> str:
        """rf-clock version the bitstream was built for (``BUILD_VERSION``), as ``"A.B.C.D"``."""
        self._require_minor(2, "BUILD_VERSION")
        raw = self._read(abi.ABI.REG.BUILD_VERSION)
        fields = (abi.ABI.BUILDVER.ABI_MAJOR_LSB, abi.ABI.BUILDVER.ABI_MINOR_LSB,
                  abi.ABI.BUILDVER.BUILD_LSB, abi.ABI.BUILDVER.PATCH_LSB)
        return ".".join(str((raw >> lsb) & 0xFF) for lsb in fields)

    def pps_inputs(self) -> PPSInputs:
        self._require_minor(1, "PPS_INPUTS")
        return PPSInputs.decode(self._read(abi.ABI.REG.PPS_INPUTS))

    def set_pps_config(self, *, source_trig: bool = False, invert: bool = False) -> None:
        """Select the timed PPS copy and edge.  A change restarts PPS lock."""
        self._require_minor(1, "PPS_CONFIG")
        self._write(
            abi.ABI.REG.PPS_CONFIG,
            (int(bool(source_trig)) << abi.ABI.PPSCFG.SRC_TRIG)
            | (int(bool(invert)) << abi.ABI.PPSCFG.INVERT),
        )
