from __future__ import annotations

import pytest

from rf_clock.fs725 import FS725, FS725Error, Status, align, pps_offset_ns


class FakePRS10:
    """A PRS10 behind a serial port: answers the queries this driver sends and keeps
    SF, PL and the 1 pps output position."""

    def __init__(self, *, sf: int = 37, pll: int = 1, pps_locked: bool = False, input_ns: int = 0) -> None:
        self.sf, self.pll, self.pps_locked = sf, pll, pps_locked
        self.ga = 7
        self.input_ns = input_ns  # when the 1 pps input arrives, from a fixed origin
        self.output_ns = 0
        self.replies: list[bytes] = []
        self.sent: list[str] = []
        self.restart_pending = False

    def write(self, data: bytes) -> None:
        command = data.decode().strip("\r")
        self.sent.append(command)
        name, arg = command[:2], command[2:]
        if arg == "?":
            reply = {"ID": "PRS10_3.24_SN_12345", "SF": str(self.sf), "PL": str(self.pll), "MR": "3010", "GA": str(self.ga), "FC": "1675,1618",
                     "TT": str((self.input_ns - self.output_ns) % 1_000_000_000),
                     "ST": f"0,0,0,0,{4 if self.pps_locked else 128},0"}[name]
            if self.restart_pending:
                self.replies.append(b"PRS_10")
                self.restart_pending = False
            self.replies.append(reply.encode())
        elif command == "AD10?":
            self.replies.append(b"0.512")
        elif name == "SF" and not self.pps_locked:
            self.sf = int(arg)
        elif name == "PL":
            self.pll = int(arg)
            self.pps_locked = self.pps_locked and bool(self.pll)
        elif name == "GA":
            self.ga = int(arg)
        elif name == "PP":
            self.output_ns -= int(arg)  # earlier

    def read_line(self, timeout: float) -> bytes | None:
        return self.replies.pop(0) if self.replies else None

    def flush_input(self) -> None:
        self.replies.clear()

    def close(self) -> None:
        pass


def test_queries_read_the_unit():
    prs, log = FakePRS10(), []
    unit = FS725(prs, log=log.append)
    assert unit.identify() == "PRS10_3.24_SN_12345"
    assert unit.frequency() == 37
    assert unit.phase_lock() is True
    assert unit.magnetic_dac() == 3010
    assert unit.case_temperature() == pytest.approx(51.2)
    assert unit.frequency_control() == (1675, 1618)
    assert unit.gain() == 7
    assert log == []  # queries are not logged


def test_set_gain_reads_back_logs_and_never_writes_eeprom():
    prs, log = FakePRS10(), []
    unit = FS725(prs, log=log.append)
    unit.set_gain(8)
    assert prs.ga == 8
    assert log[-1] | {"time": 0} == {"time": 0, "command": "GA", "value": 8, "before": 7, "after": 8}
    assert not any(c.startswith("GA!") for c in prs.sent)
    for bad in (11, -1, 7.5):
        with pytest.raises(ValueError):
            unit.set_gain(bad)  # type: ignore[arg-type]


def test_set_frequency_reads_back_and_logs():
    prs, log = FakePRS10(sf=37), []
    unit = FS725(prs, log=log.append)
    unit.set_frequency(-120)
    assert prs.sf == -120
    assert log[-1] | {"time": 0} == {"time": 0, "command": "SF", "value": -120, "before": 37, "after": -120}


def test_set_frequency_refuses_out_of_range_and_reports_a_pll_that_holds_sf():
    unit = FS725(FakePRS10())
    with pytest.raises(ValueError):
        unit.set_frequency(2001)
    with pytest.raises(ValueError):
        unit.set_frequency(1.5)  # type: ignore[arg-type]
    held = FS725(FakePRS10(pps_locked=True))
    with pytest.raises(FS725Error, match="not taken"):
        held.set_frequency(10)


def test_only_queries_go_through_query():
    unit = FS725(FakePRS10())
    for command in ("SF 10", "FC!", "RS1", "?"):
        with pytest.raises(ValueError):
            unit.query(command)


def test_a_restart_message_before_a_reply_is_skipped():
    prs, log = FakePRS10(sf=5), []
    prs.restart_pending = True
    assert FS725(prs, log=log.append).frequency() == 5
    assert "restarted" in log[0]["event"]


def test_no_reply_is_an_error():
    class Silent(FakePRS10):
        def write(self, data: bytes) -> None:
            pass

    with pytest.raises(FS725Error, match="no reply"):
        FS725(Silent(), timeout=0.01).frequency()


def test_status_bytes_are_decoded():
    status = Status((16, 0, 21, 1, 130, 96))
    assert "lamp light too low" in status.conditions()
    assert "cell below set point" in status.conditions()
    assert not status.rb_locked
    assert not status.pps_locked
    assert status.command_errors
    assert Status((0, 0, 0, 0, 4, 0)).pps_locked


def test_time_tags_give_the_signed_offset():
    assert pps_offset_ns([120, 121, 119]) == 120
    assert pps_offset_ns([999_999_900, 999_999_901]) == pytest.approx(-99.5)


@pytest.mark.parametrize("input_ns", [250, -250, 400_000_000])
def test_align_puts_the_output_on_the_input(monkeypatch, input_ns):
    monkeypatch.setattr("rf_clock.fs725.time.sleep", lambda s: None)
    prs = FakePRS10(input_ns=input_ns)

    class Ticking(FakePRS10):
        pass

    # Each TT? is a new tag: vary the input by a nanosecond so successive tags differ.
    tags = iter(range(1000))
    original = prs.write

    def write(data: bytes) -> None:
        if data.startswith(b"TT?"):
            prs.input_ns = input_ns + next(tags) % 2
        original(data)

    prs.write = write  # type: ignore[method-assign]
    offset = align(FS725(prs), samples=4, say=lambda s: None)
    assert offset == pytest.approx(input_ns + 0.5, abs=0.6)
    assert prs.pll == 0
    assert abs((prs.input_ns - prs.output_ns + 500_000_000) % 1_000_000_000 - 500_000_000) <= 1
