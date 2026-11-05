"""The per-second two-way computation and the node's control of rf_clock.twtt (pairs,
control), which rf_clock.checks.m4c_link runs, and the crossing interpolation of
rf_clock.checks.pps_pp_scan, on synthetic data."""

from __future__ import annotations

import pytest

from rf_clock.checks.pps_pp_scan import crossings, ticks_moved
from rf_clock.fs725 import FS725, FS725Error
from rf_clock.pl.board_config import BoardConfig, BoardConfigError
from rf_clock.twtt.control import SyncedRule, configure_fs725
from rf_clock.twtt.pairs import SecondTheta, StepTheta
from rf_clock.pl.abi import ABI
from rf_clock.pl.measurements import OwnMeasurement, PeerPacket

FS_PER_TICK = 10_000_000
SLOT_TICKS = 200_000
TX_OFFSET = 1000


def own(count: int, slot: int, t_rx_fs: int, locked: bool = True) -> OwnMeasurement:
    return OwnMeasurement(seq=0, pps_count=count, slot=slot, tick=0, locked=locked, window_bad=False, searched=False,
                          lag=0, fraction=0, phase=0, magnitude=0, t_rx_fs=t_rx_fs, quality=0, window_power=0)


def peer(count: int, slot: int, t_tx_fs: int, t_rx_fs: int, measured: bool = True) -> PeerPacket:
    status = 1 << ABI.PKST.MEASUREMENT if measured else 0
    return PeerPacket(seq=0, pps_count=count, slot=slot, tick=0, version=1, time_epoch=0, quality=0, status=status,
                      config_id=0, peer_pps_count=count, peer_slot=slot, t_tx_fs=t_tx_fs, t_rx_fs=t_rx_fs, phase=0)


def entries_for_b(count: int, slots: range, theta_fs: int, delay_fs: int):
    """Node B's queue for A's bursts in even slots: B's clock reads theta ahead of A's."""
    out = []
    for k in slots:
        t_tx_a = (k * SLOT_TICKS + TX_OFFSET) * FS_PER_TICK  # A's burst, A's time
        t_rx_b = t_tx_a + delay_fs + theta_fs  # its arrival in B's time
        t_tx_b_prev = ((k - 1) * SLOT_TICKS + TX_OFFSET) * FS_PER_TICK  # B's burst in slot k - 1, B's time
        t_rx_a_prev = t_tx_b_prev + delay_fs - theta_fs  # its arrival in A's time, carried in A's packet
        out += [own(count, k, t_rx_b), peer(count, k, t_tx_a, t_rx_a_prev)]
    return out


def test_second_theta_gives_theta_and_delay_per_second():
    s = SecondTheta("B", 500, SLOT_TICKS, TX_OFFSET)
    records = []
    for count, theta in ((10, -21_500), (11, -21_400)):  # fs
        for e in entries_for_b(count, range(2, 500, 2), theta, 292_005_000):
            r = s.add(e)
            if r:
                records.append(r)
    records.append(s.flush())
    assert [r["count"] for r in records] == [10, 11]
    assert records[0]["pairs"] == 249
    assert records[0]["theta_ns"] == pytest.approx(-0.0215, abs=1e-9)
    assert records[1]["theta_ns"] == pytest.approx(-0.0214, abs=1e-9)
    assert records[0]["delay_ns"] == pytest.approx(292.005, abs=1e-9)
    assert records[0]["theta_pair_sd_ps"] == pytest.approx(0.0, abs=1e-9)


def test_second_theta_over_the_last_slots():
    s = SecondTheta("B", 500, SLOT_TICKS, TX_OFFSET, late_slots=100)
    for k in range(2, 500, 2):  # theta -21.5 ps before slot 400, -20.5 ps from it on
        for e in entries_for_b(10, range(k, k + 1), -21_500 if k < 400 else -20_500, 292_005_000):
            s.add(e)
    r = s.flush()
    assert r is not None
    assert r["theta_late_ns"] == pytest.approx(-0.0205, abs=1e-9)
    assert r["theta_ns"] == pytest.approx((199 * -0.0215 + 50 * -0.0205) / 249, abs=1e-9)
    assert SecondTheta("B", 500, SLOT_TICKS, TX_OFFSET).flush() is None
    s = SecondTheta("B", 500, SLOT_TICKS, TX_OFFSET, late_slots=100)
    for e in entries_for_b(11, range(2, 100, 2), -21_500, 292_005_000):  # nothing in the last 100 slots
        s.add(e)
    r = s.flush()
    assert r is not None and r["theta_late_ns"] is None


def test_second_theta_skips_unlocked_windows_and_packets_without_a_measurement():
    s = SecondTheta("A", 500, SLOT_TICKS, TX_OFFSET)
    s.add(own(5, 3, 1, locked=False))
    assert s.add(peer(5, 3, 0, 0)) is None  # no OWN measurement of this window
    s.add(own(5, 5, 1))
    assert s.add(peer(5, 5, 0, 0, measured=False)) is None
    assert s.flush() is None


def test_ticks_moved_wraps_whole_seconds():
    assert ticks_moved(3 * 100_000_000 + 5, 5) == 0
    assert ticks_moved(2 * 100_000_000 + 4, 5) == -1
    assert ticks_moved(100_000_000 + 7, 5) == 2


def test_crossings_interpolate_a_toggling_step():
    steps = [(0, [0] * 8), (1, [0] * 8), (2, [-1] * 6 + [0] * 2), (3, [-1] * 8), (4, [-1] * 8),
             (5, [-2] * 4 + [-1] * 4), (6, [-2] * 8)]
    found = crossings(steps)
    assert len(found) == 2
    assert found[0] == pytest.approx(1 + 0.5 / 0.75)  # half the edges had moved two thirds into step 1 -> 2
    assert found[1] == pytest.approx(5.0)


def test_synced_rule_on_a_follows_the_link():
    rule = SyncedRule(role_b=False)
    assert rule.second({"count": 1, "pairs": 250}, True, None) is True
    assert rule.second({"count": 2, "pairs": 250}, True, None) is None  # no change
    assert rule.second({"count": 3, "pairs": 120}, True, None) is False  # B's packets stopped coming
    assert rule.second({"count": 4, "pairs": 250}, True, None) is True
    assert rule.lost() is False


def test_synced_rule_on_b_needs_a_settled_steering_error():
    rule = SyncedRule(role_b=True, set_rms_ps=50, set_mean_ps=20, clear_rms_ps=100)
    changes = []
    for k in range(59):  # 59 good seconds are not enough
        changes.append(rule.second({"count": k, "pairs": 250}, True, 30.0 * (-1) ** k))
    assert not any(changes) and not rule.synced
    assert rule.second({"count": 59, "pairs": 250}, True, 30.0) is True
    for k in range(60, 69):  # a burst of large errors: the 10 s rms climbs past 100 ps
        rule.second({"count": k, "pairs": 250}, True, 300.0 * (-1) ** k)
    assert not rule.synced
    assert rule.second({"count": 69, "pairs": 250}, True, None) is None  # steering stopped: stays clear
    # a gap in the seconds starts the window again
    for k in range(70, 130):
        rule.second({"count": k, "pairs": 250}, True, 10.0)
    assert rule.synced
    assert rule.lost() is False and rule.errors == []
    for k in range(200, 259):
        rule.second({"count": k, "pairs": 250}, True, 10.0)
    assert not rule.synced  # 59 consecutive seconds after the lock came back
    rule.second({"count": 259, "pairs": 250}, True, 10.0)
    assert rule.synced


def test_synced_rule_on_b_refuses_a_biased_error():
    rule = SyncedRule(role_b=True, set_rms_ps=100, set_mean_ps=30)
    for k in range(120):
        rule.second({"count": k, "pairs": 250}, True, 40.0)  # rms 40 < 100, but the mean 40 > 30
    assert not rule.synced


def test_step_theta_gives_the_late_mean_of_each_step():
    steps = StepTheta(step_slots=100, late_slots=50, slots_per_second=500)
    s = SecondTheta("B", 500, SLOT_TICKS, TX_OFFSET, pair_sink=steps.add)
    for k in range(2, 500, 2):  # theta -21.5 ps in the first half of each step, -20.5 ps in the second
        for e in entries_for_b(10, range(k, k + 1), -21_500 if k % 100 < 50 else -20_500, 292_005_000):
            s.add(e)
    for e in entries_for_b(11, range(2, 3), -21_500, 292_005_000):  # the next second's first pair ends step 4
        s.add(e)
    assert [r["step"] for r in steps.done] == [0, 1, 2, 3, 4]
    assert [r["index"] for r in steps.done] == [50, 51, 52, 53, 54]
    assert all(r["theta_late_ns"] == pytest.approx(-0.0205, abs=1e-9) for r in steps.done)
    assert steps.done[0]["pairs"] == 49 and steps.done[1]["pairs"] == 50


class NamedPRS10:
    """A PRS10 that answers ID? and GA? and takes GA."""

    def __init__(self) -> None:
        self.ga = 7
        self.replies: list[bytes] = []

    def write(self, data: bytes) -> None:
        command = data.decode().strip("\r")
        if command == "ID?":
            self.replies.append(b"PRS10_3.56_SN_107129")
        elif command == "GA?":
            self.replies.append(str(self.ga).encode())
        elif command.startswith("GA"):
            self.ga = int(command[2:])

    def read_line(self, timeout: float) -> bytes | None:
        return self.replies.pop(0) if self.replies else None

    def flush_input(self) -> None:
        self.replies.clear()

    def close(self) -> None:
        pass


def test_the_link_checks_its_fs725_against_the_board_config(monkeypatch):
    prs = NamedPRS10()
    monkeypatch.setattr(FS725, "open", lambda path, **kwargs: FS725(prs, **kwargs))
    entry = {"serial": "107129", "port": "/dev/serial/by-id/B", "gain": 8,
             "loops": {"8": {"k_per_s": 2.2, "lag_s": 1.2, "dead_s": 0.12}}}
    config = BoardConfig(serial="0" * 32, fs725=entry)
    lines: list[str] = []
    with pytest.warns(UserWarning, match="GA 7 -> 8"):
        found = configure_fs725(config, None, lambda kind, **fields: lines.append(kind))
    assert found == ("/dev/serial/by-id/B", (2.2, 1.2, 0.12))  # the config's port
    assert prs.ga == 8 and lines == ["fs725", "fs725_config"]
    assert configure_fs725(config, "/dev/ttyUSB0", lambda kind, **fields: None)[0] == "/dev/ttyUSB0"
    with pytest.raises(FS725Error, match="for FS725 S/N 41175, this board's is S/N 107129"):
        configure_fs725(BoardConfig(serial="0" * 32, fs725=entry | {"serial": "41175"}), None, lambda kind, **f: None)
    no_port = {k: v for k, v in entry.items() if k != "port"}
    with pytest.raises(BoardConfigError, match="names no FS725 port"):
        configure_fs725(BoardConfig(serial="0" * 32, fs725=no_port), None, lambda kind, **fields: None)
    with pytest.raises(BoardConfigError, match="no board config"):
        configure_fs725(None, "PORT", lambda kind, **fields: None)
