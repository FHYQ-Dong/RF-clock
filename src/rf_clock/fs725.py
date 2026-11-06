"""The SRS FS725 rubidium frequency standard over RS-232 (plan section 7, ``fs725``).

The FS725 passes every command on its rear DB9 (DCE: a straight-through cable;
9600 baud, 8 data bits, no parity, 1 stop bit, XON/XOFF, no handshake lines) to the
PRS10 rubidium oscillator inside it, and returns the PRS10's replies unchanged
(FS725 manual, "Monitoring and Control").  The commands are therefore those of the
PRS10 manual, section "RS-232 Instruction Set": two letters and a parameter, ``?``
to query, a carriage return to end; a query's reply ends with a carriage return.
A unit that restarts sends ``PRS_10``.

Steering uses SF, the frequency offset in parts in 1e12 (-2000 to +2000, 1e-12 =
1 ps/s) relative to the calibration in the EEPROM.  Two things about it matter:

- the first SF command disables the calibration pot until the unit restarts, so
  ``SF 0`` is not "unchanged": ``FS725.frequency()`` reads the offset the pot set, and
  steering starts from that value;
- SF is ignored while the unit is phase-locked to a 1 pps input on its rear BNC
  (PL 1, the factory default, with pulses present); ``set_phase_lock(False)`` stops
  that until the next restart.

SF sets the magnetic field of the resonance cell through a 12-bit DAC, and the
frequency goes as the square of the field, linearized by the firmware; near the
nominal operating point one DAC step is about 4 SF units by the PRS10 manual's MR
equation, and 3.4 measured on B's unit (2026-11-03: SF 3 -> 103 moved the DAC,
``magnetic_dac()``, from 2612 to 2641, and theta's rate by 101.5 ps/s).

The 1 pps input is also time-tagged against the 1 pps output with 1 ns resolution
(``time_tag()``), and ``place_pulse()`` moves the 1 pps output earlier by 1 ns to
999 999 999 ns: with the other node's PPS on the rear input this aligns the two
PPS to a few ns before the two-way time transfer can see each other's bursts.

The unit that clocks a board is recorded in the board's config (``fs725``, docs/ABI.md
11.1): its serial number, the gain GA of its frequency lock loop, and the loop measured
at that gain (``rf_clock.checks.fs725_loop``).  ``check_config()`` checks a unit against
it and sets its GA, which the unit keeps only until it restarts.

Only the commands this module names are sent: every query, and SF, PL, PP, GA and VB.
Nothing writes the EEPROM (``!``), changes a calibration (MO, SP, SS, TS, PS, TO,
FC, LO, PH, MS, SD) or restarts the unit (RS, RC).

    run="sudo /usr/local/share/pynq-venv/bin/python"
    $run -m rf_clock.fs725 status --port /dev/ttyUSB0
    $run -m rf_clock.fs725 align --port /dev/ttyUSB0    # the other PPS on the rear 1 pps input
    $run -m rf_clock.fs725 sf 37 --port /dev/ttyUSB0    # set SF, e.g. back to where steering started
"""

from __future__ import annotations

import argparse
import json
import os
import select
import statistics
import sys
import time
import warnings
from dataclasses import dataclass
from typing import Callable, Protocol

DEFAULT_PORT = "/dev/ttyUSB0"
SF_LIMIT = 2000  # parts in 1e12
PP_LIMIT = 999_999_999  # ns
FC_HIGH = (0, 4095)  # FC's coarse DAC (PRS10 manual p. 24)
FC_LOW = (1024, 3072)  # its fine DAC, kept to half its range; FC = 1000 high + low in fine steps
TIMEOUT_S = 1.0
# The PRS10 status bytes (PRS10 manual, "Status Bytes"): bit -> condition, per byte.
STATUS_BITS = (
    ("+24 V electronics < 22 V", "+24 V electronics > 30 V", "+24 V heaters < 22 V", "+24 V heaters > 30 V",
     "lamp light too low", "lamp light too high", "lamp gate voltage too low", "lamp gate voltage too high"),
    ("RF synthesizer unlocked", "RF crystal varactor too low", "RF crystal varactor too high",
     "RF VCO control too low", "RF VCO control too high", "RF AGC control too low", "RF AGC control too high",
     "bad synthesizer parameter"),
    ("lamp below set point", "lamp above set point", "crystal below set point", "crystal above set point",
     "cell below set point", "cell above set point", "case too cold", "case too hot"),
    ("frequency lock control off", "frequency lock disabled", "10 MHz EFC too high", "10 MHz EFC too low",
     "analog calibration > 4.9 V", "analog calibration < 0.1 V", "", ""),
    ("1 pps PLL disabled", "< 256 good 1 pps inputs", "1 pps PLL active", "> 256 bad 1 pps inputs",
     "excessive time interval", "1 pps PLL restarted", "frequency control saturated", "no 1 pps input"),
    ("lamp restart", "watchdog reset", "bad interrupt vector", "EEPROM write failure", "EEPROM data corruption",
     "bad command syntax", "bad command parameter", "unit reset"),
)


class FS725Error(RuntimeError):
    """No reply, a malformed reply, or a setting that did not take."""


class Port(Protocol):
    def write(self, data: bytes) -> None: ...
    def read_line(self, timeout: float) -> bytes | None: ...
    def flush_input(self) -> None: ...
    def close(self) -> None: ...


class SerialPort:
    """A tty at 9600 baud, 8N1, raw, with XON/XOFF (the PRS10's serial format)."""

    def __init__(self, path: str) -> None:
        import termios

        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        cc = termios.tcgetattr(self.fd)[6]
        cc[termios.VMIN], cc[termios.VTIME] = 0, 0
        termios.tcsetattr(self.fd, termios.TCSANOW,
                          [termios.IXON | termios.IXOFF, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0,
                           termios.B9600, termios.B9600, cc])
        self.rest = b""
        self.flush_input()

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def read_line(self, timeout: float) -> bytes | None:
        """Up to the next carriage return, without it; None if none came in ``timeout``."""
        deadline = time.monotonic() + timeout
        while b"\r" not in self.rest:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                return None
            self.rest += os.read(self.fd, 256)
        line, self.rest = self.rest.split(b"\r", 1)
        return line.strip(b"\n")

    def flush_input(self) -> None:
        import termios

        termios.tcflush(self.fd, termios.TCIFLUSH)
        self.rest = b""

    def close(self) -> None:
        os.close(self.fd)


@dataclass(frozen=True)
class Status:
    """The six status bytes of ``ST?``.  The PRS10 latches each bit until it is read."""

    bytes: tuple[int, ...]

    def conditions(self) -> list[str]:
        """The condition of every set bit, by the PRS10 manual."""
        return [STATUS_BITS[i][bit] or f"ST{i + 1} bit {bit}"
                for i, value in enumerate(self.bytes) for bit in range(8) if value >> bit & 1]

    @property
    def rb_locked(self) -> bool:
        """The 10 MHz is frequency-locked to rubidium (ST4 bits 0 and 1 clear)."""
        return not self.bytes[3] & 0b11

    @property
    def pps_locked(self) -> bool:
        """The unit is phase-locking to a 1 pps input on its rear BNC (ST5 bit 2): SF is ignored."""
        return bool(self.bytes[4] & 0b100)

    @property
    def command_errors(self) -> bool:
        """A bad command syntax or parameter since the last read (ST6 bits 5 and 6)."""
        return bool(self.bytes[5] & 0b0110_0000)


class FS725:
    """One FS725 on a serial port.  ``log`` receives every setting written, as a dict."""

    def __init__(self, port: Port, *, timeout: float = TIMEOUT_S, log: Callable[[dict], None] | None = None) -> None:
        self.port = port
        self.timeout = timeout
        self.log = log or (lambda entry: None)

    @classmethod
    def open(cls, path: str = DEFAULT_PORT, **kwargs) -> FS725:
        unit = cls(SerialPort(path), **kwargs)
        unit.port.write(b"VB0\r")  # no prompts (the power-on default)
        return unit

    def close(self) -> None:
        self.port.close()

    def query(self, command: str) -> str:
        """Send a query (``SF?``, ``FC!?``, ``AD10?``) and return its reply."""
        command = command.replace(" ", "").upper()
        if not command.endswith("?") or len(command) < 3 or not command[:2].isalpha():
            raise ValueError(f"not a query: {command!r}")
        self.port.flush_input()
        self.port.write(command.encode() + b"\r")
        line = self.port.read_line(self.timeout)
        if line == b"PRS_10":  # the unit restarted just now; its reply follows
            self.log({"time": time.time(), "event": "unit restarted (PRS_10)"})
            line = self.port.read_line(self.timeout)
        if line is None:
            raise FS725Error(f"no reply to {command} within {self.timeout} s")
        return line.decode("ascii", "replace").strip()

    def _int(self, command: str) -> int:
        reply = self.query(command)
        try:
            return int(reply)
        except ValueError:
            raise FS725Error(f"{command} returned {reply!r}") from None

    def _set(self, command: str, value: int) -> None:
        self.port.write(f"{command}{value}\r".encode())

    def identify(self) -> str:
        """Model, firmware and serial number, for example ``PRS10_3.24_SN_12345``."""
        return self.query("ID?")

    def serial(self) -> str:
        """The PRS10's serial number, from ``identify()``."""
        identity = self.identify()
        _, sep, serial = identity.rpartition("_SN_")
        if not sep or not serial.isdigit():
            raise FS725Error(f"ID? returned {identity!r}")
        return serial

    def status(self) -> Status:
        """The six status bytes; reading clears the latched bits."""
        reply = self.query("ST?")
        try:
            values = tuple(int(v) for v in reply.split(","))
        except ValueError:
            values = ()
        if len(values) != 6:
            raise FS725Error(f"ST? returned {reply!r}")
        return Status(values)

    def frequency(self) -> int:
        """SF: the frequency offset in parts in 1e12, whichever source set it (pot, SF, 1 pps PLL)."""
        return self._int("SF?")

    def set_frequency(self, sf: int) -> None:
        """Set SF and read it back; FS725Error if the unit did not take it (a 1 pps PLL holds it)."""
        if not isinstance(sf, int) or abs(sf) > SF_LIMIT:
            raise ValueError(f"SF must be an integer within +-{SF_LIMIT}: {sf!r}")
        before = self.frequency()
        self._set("SF", sf)
        after = self.frequency()
        self.log({"time": time.time(), "command": "SF", "value": sf, "before": before, "after": after})
        if after != sf:
            raise FS725Error(f"SF {sf} not taken: SF? returns {after} (is the unit phase-locked to a 1 pps input?)")

    def phase_lock(self) -> bool:
        """PL: whether the unit phase-locks to a 1 pps input on its rear BNC."""
        return self._int("PL?") == 1

    def set_phase_lock(self, on: bool) -> None:
        """Enable or disable the 1 pps PLL until the next restart (the EEPROM keeps the default)."""
        self._set("PL", int(on))
        taken = self.phase_lock()
        self.log({"time": time.time(), "command": "PL", "value": int(on), "after": int(taken)})
        if taken != on:
            raise FS725Error(f"PL {int(on)} not taken")

    def time_tag(self) -> int | None:
        """TT: when the latest 1 pps input arrived after the 1 pps output, 0 to 999 999 999 ns;
        None if no new one."""
        value = self._int("TT?")
        return None if value < 0 else value

    def place_pulse(self, ns: int) -> None:
        """PP: move the 1 pps output earlier by ``ns`` (1 to 999 999 999); it also moves the time tags."""
        if not isinstance(ns, int) or not 0 < ns <= PP_LIMIT:
            raise ValueError(f"PP takes 1 to {PP_LIMIT} ns: {ns!r}")
        self._set("PP", ns)
        self.log({"time": time.time(), "command": "PP", "value": ns})

    def magnetic_dac(self) -> int:
        """MR: the 12-bit DAC setting of the magnetic field, from the calibration and SF."""
        return self._int("MR?")

    def write_frequency(self, sf: int) -> None:
        """Set SF without reading it back or logging it, for a law that changes it several
        times a second; the caller logs it and checks SF now and then (``set_frequency``
        reads back and logs)."""
        if not isinstance(sf, int) or not -SF_LIMIT <= sf <= SF_LIMIT:
            raise ValueError(f"SF takes -{SF_LIMIT} to {SF_LIMIT}: {sf!r}")
        self._set("SF", sf)

    def frequency_control(self) -> tuple[int, int]:
        """FC: the crystal's tuning as the frequency lock loop sets it, (high, low) DAC; the
        low step is 1.5e-12 of frequency by the manual's p. 24, about 1e-12 by its p. 47,
        0.97e-12 measured on B's unit (results/fs725-fll-20261105).  It changed between
        most reads 0.1 s apart, so more often than the loop's 0.2 s frame; about 30 reads
        a second are possible."""
        reply = self.query("FC?")
        try:
            high, low = (int(v) for v in reply.split(","))
        except ValueError as exc:
            raise FS725Error(f"FC? returned {reply!r}") from exc
        return high, low

    def write_frequency_control(self, high: int, low: int) -> None:
        """Set FC, the crystal's tuning, without reading it back, for a law that writes it
        several times a second (``FrequencyControl`` with the FC actuator; the caller logs and
        checks it).  Meant for GA 1: the loop then pulls FC back with a 128 s time constant,
        about 2 low steps a second 200 steps off, so a value written every step stays (measured
        on B's unit, 2026-11-06); at the normal gains the loop pulls it back within seconds.
        Not at GA 0 or with the loop stopped (LO 0): the unit then reports itself unlocked and
        its 1PPS stops (PRS10 manual p. 20, "LM"; measured).  The fine DAC is kept to its half
        range (``FC_LOW``, p. 24); the crystal follows FC through a 1 s RC (p. 47)."""
        if not isinstance(high, int) or not FC_HIGH[0] <= high <= FC_HIGH[1]:
            raise ValueError(f"FC's coarse DAC takes {FC_HIGH[0]} to {FC_HIGH[1]}: {high!r}")
        if not isinstance(low, int) or not FC_LOW[0] <= low <= FC_LOW[1]:
            raise ValueError(f"FC's fine DAC takes {FC_LOW[0]} to {FC_LOW[1]}: {low!r}")
        self.port.write(f"FC{high},{low}\r".encode())

    def gain(self) -> int:
        """GA: the frequency lock loop's gain, 0 (the loop stopped) to 10; each step up halves
        the loop's time constant, 7 (about 2 s) in normal operation, 8 about 1 s, 10 about
        0.25 s, and doubles its noise bandwidth (PRS10 manual p. 26-27)."""
        return self._int("GA?")

    def set_gain(self, ga: int) -> None:
        """Set GA until the next restart (not written to EEPROM) and read it back."""
        if not isinstance(ga, int) or not 0 <= ga <= 10:
            raise ValueError(f"GA takes 0 to 10: {ga!r}")
        before = self.gain()
        self._set("GA", ga)
        after = self.gain()
        self.log({"time": time.time(), "command": "GA", "value": ga, "before": before, "after": after})
        if after != ga:
            raise FS725Error(f"GA {ga} not taken: GA? returns {after}")

    def case_temperature(self) -> float:
        """The case temperature sensor, degrees C (AD10, 10 mV/degree)."""
        return float(self.query("AD10?")) * 100


def check_config(unit: FS725, entry: dict, source: str = "board config") -> tuple[float, float, float] | None:
    """Check that ``unit`` is the FS725 of a board config (``entry``: its ``fs725``) and set
    what the entry fixes, the frequency lock loop's gain; return the loop measured at that
    gain, (k per s, lag s, dead time s), or None.

    FS725Error when the entry is empty or names another unit: ``rf_clock.checks.fs725_loop
    --write`` measures this unit's loop and writes the entry.  A gain that differs is set,
    until the unit restarts, with a warning that says from what to what."""
    serial = unit.serial()
    hint = "measure it first: python -m rf_clock.checks.fs725_loop --port PORT --gain GA --write"
    if not entry:
        raise FS725Error(f"{source} has no FS725, this board's is S/N {serial}; {hint}")
    if entry["serial"] != serial:
        raise FS725Error(f"{source} is for FS725 S/N {entry['serial']}, this board's is S/N {serial}; {hint}")
    gain = unit.gain()
    if gain != entry["gain"]:
        unit.set_gain(entry["gain"])
        warnings.warn(f"FS725 S/N {serial}: GA {gain} -> {entry['gain']}, as {source} sets it", stacklevel=2)
    loop = entry.get("loops", {}).get(str(entry["gain"]))
    return None if loop is None else (loop["k_per_s"], loop["lag_s"], loop["dead_s"])


def pps_offset_ns(tags: list[int]) -> float:
    """The input PPS minus the output PPS, from time tags, within +-0.5 s."""
    signed = [t if t < 500_000_000 else t - 1_000_000_000 for t in tags]
    return statistics.median(signed)


def align(unit: FS725, *, samples: int = 10, say: Callable[[str], None] = print) -> float:
    """Move the 1 pps output onto the 1 pps input by PP; returns the offset before, ns.

    With the PLL off, ``samples`` time tags give the input's arrival after the output.
    PP only moves the output earlier, so moving it later by that offset is moving it
    earlier by one second minus the offset; the output then coincides with the input
    to a few ns (the cables and the time-tag calibration remain).  It moves against the
    10 MHz, so check the board's PPS capture margin afterwards (M1), and, where the PPS
    is retimed by a clock locked to this 10 MHz (the EPG1 of the M4 wiring), the
    retimer's margin too; everything fed from this PPS moves with it.  The PLL stays
    off only until the unit restarts: take the other PPS off the rear input once
    aligned, or a restart locks the unit to it."""
    if unit.phase_lock():
        unit.set_phase_lock(False)
        say("1 pps PLL disabled until the next restart")
    tags: list[int] = []
    deadline = time.monotonic() + 3 * samples + 5
    while len(tags) < samples:
        if time.monotonic() > deadline:
            raise FS725Error("no 1 pps input time tags (is the other PPS on the rear 1 pps input?)")
        tag = unit.time_tag()
        if tag is not None and (not tags or tag != tags[-1]):
            tags.append(tag)
        time.sleep(0.5)
    offset = pps_offset_ns(tags)
    say(f"input PPS {offset:+.0f} ns after the output (time tags {min(tags)}..{max(tags)} ns)")
    move = round(-offset) % 1_000_000_000  # earlier by -offset: later by offset, modulo a second
    if move:
        unit.place_pulse(move)
        say(f"PP {move}: output moved {offset:+.0f} ns")
        say("now: take the other PPS off the rear 1 pps input (a restart turns the PLL on again); "
            "check the board's PPS capture (M1) and any retimer of this PPS (the EPG1)")
    return offset


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("command", choices=("status", "align", "sf", "gain"),
                        help="status: read everything; align: move the 1 pps output onto the rear 1 pps input; "
                             "sf: set the frequency offset to VALUE; gain: set the frequency lock loop's "
                             "gain to VALUE until the next restart (not written to EEPROM)")
    parser.add_argument("value", nargs="?", type=int, help="sf: parts in 1e12, -2000 to 2000; gain: 0 to 10")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"serial device (default {DEFAULT_PORT})")
    parser.add_argument("--log", default="fs725.jsonl", help="JSON-lines log of every setting written")
    args = parser.parse_args()
    if (args.command in ("sf", "gain")) != (args.value is not None):
        parser.error("sf and gain take a value, and only they")

    def log(entry: dict) -> None:
        with open(args.log, "a") as f:
            f.write(json.dumps(entry) + "\n")

    unit = FS725.open(args.port, log=log)
    try:
        if args.command == "status":
            status = unit.status()
            print(f"identity      {unit.identify()}")
            print(f"status bytes  {','.join(map(str, status.bytes))}: {'; '.join(status.conditions()) or 'none set'}")
            print(f"rubidium lock {'yes' if status.rb_locked else 'NO'}")
            print(f"SF            {unit.frequency()} (parts in 1e12)")
            print(f"magnetic DAC  {unit.magnetic_dac()}")
            print(f"FLL gain      GA {unit.gain()}; FC {','.join(map(str, unit.frequency_control()))}")
            print(f"1 pps PLL     {'enabled' if unit.phase_lock() else 'disabled'}"
                  f"{', active (SF is ignored)' if status.pps_locked else ''}")
            tag = unit.time_tag()
            print(f"time tag      {'none' if tag is None else f'{tag} ns'}")
            print(f"case          {unit.case_temperature():.1f} C")
        elif args.command == "sf":
            before = unit.frequency()
            unit.set_frequency(args.value)
            print(f"SF {before} -> {args.value}")
        elif args.command == "gain":
            before = unit.gain()
            unit.set_gain(args.value)
            print(f"GA {before} -> {args.value} (until the next restart)")
        else:
            align(unit)
    finally:
        unit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
