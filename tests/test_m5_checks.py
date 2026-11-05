"""The per-second two-way computation of rf_clock.checks.m4c_link and the crossing
interpolation of rf_clock.checks.pps_pp_scan, on synthetic data."""

from __future__ import annotations

import pytest

from rf_clock.checks.m4c_link import SecondTheta
from rf_clock.checks.pps_pp_scan import crossings, ticks_moved
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
