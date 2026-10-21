from __future__ import annotations

import json
import socket
import sys
import threading
import time
from multiprocessing import Pipe
from types import SimpleNamespace

import numpy as np
import pytest

from rf_clock.twtt.link import Link, LineReader, encode, run_link
from rf_clock.twtt.timing import PRE_TICKS, SLOTS, Pairer, plan, two_way


def test_each_node_has_its_slots_in_time_order():
    assert plan("A") == [("AB", SLOTS["AB"], True, False), ("BA", SLOTS["BA"], False, True)]
    assert plan("B") == [("AB", SLOTS["AB"], False, True), ("BA", SLOTS["BA"], True, False)]
    assert plan("AB") == [("AB", SLOTS["AB"], True, True), ("BA", SLOTS["BA"], True, True)]


def records(theta_ns: float, path_ns: float, *, t_sys: float = 100.0, psl_db: float = 20.0):
    """A's and B's records of one second with B's clock theta ahead of A's."""
    def peak(m_ns):  # the window opens PRE_TICKS before the slot
        return (m_ns + PRE_TICKS * 10) / 1.25

    a = {"node": "A", "count": 7, "pps_tick": 1_000, "t_sys": t_sys,
         "slots": {"AB": {"tx": 1_000 + SLOTS["AB"]},
                   "BA": {"rx": {"tick": 1_000 + SLOTS["BA"] - PRE_TICKS, "peak": peak(path_ns - theta_ns),
                                 "phase": 0.0, "psl_db": psl_db}}}}
    b = {"node": "B", "count": 42, "pps_tick": 5_000_000, "t_sys": t_sys + 0.004,
         "slots": {"AB": {"rx": {"tick": 5_000_000 + SLOTS["AB"] - PRE_TICKS, "peak": peak(path_ns + theta_ns),
                                 "phase": 0.0, "psl_db": psl_db}},
                   "BA": {"tx": 5_000_000 + SLOTS["BA"]}}}
    return a, b


def test_two_way_gives_theta_and_the_one_way_delay():
    result = two_way(*records(theta_ns=3.2, path_ns=300.0))
    assert result.theta_ns == pytest.approx(3.2)
    assert result.delay_ns == pytest.approx(300.0)
    assert (result.count_a, result.count_b) == (7, 42)


def test_a_failed_or_weak_slot_gives_no_result():
    a, b = records(0.0, 300.0, psl_db=6.0)
    assert two_way(a, b) is None
    a, b = records(0.0, 300.0)
    b["slots"]["BA"] = {"error": "TX target missed"}
    assert two_way(a, b) is None


def test_records_of_one_edge_are_paired_by_system_time():
    pairer = Pairer()
    a, b = records(0.0, 300.0)
    assert pairer.add(a) is None
    late = {**b, "t_sys": a["t_sys"] + 0.5}  # another edge
    assert pairer.add(late) is None
    assert pairer.add(b) == (a, b)
    assert pairer.pending == {"A": [], "B": [late]}
    self_test = {**a, "node": "AB"}
    assert pairer.add(self_test) == (self_test, self_test)


def test_line_reader_joins_split_messages():
    reader = LineReader()
    data = encode({"type": "x", "n": 1}) + encode({"type": "y"})
    assert reader.feed(data[:5]) == []
    assert reader.feed(data[5:]) == [{"type": "x", "n": 1}, {"type": "y"}]


def test_both_links_log_the_result():
    logs = {"A": [], "B": []}
    links = {node: Link(node, logs[node].append) for node in logs}
    a, b = records(theta_ns=-1.5, path_ns=290.0)
    links["B"].peer(links["A"].local({"type": "info", "serial": "aa", "label": "887329260072A"}))
    links["B"].peer(links["A"].local({"type": "record", **a}))
    links["A"].peer(links["B"].local({"type": "record", **b}))
    for node in logs:
        (result,) = [entry for entry in logs[node] if entry["type"] == "result"]
        assert result["theta_ns"] == pytest.approx(-1.5)
        assert result["delay_ns"] == pytest.approx(290.0)
    assert [e["type"] for e in logs["B"]] == ["hello", "peer", "local", "result"]


@pytest.mark.skipif(sys.platform == "win32", reason="multiprocessing.connection.wait takes no sockets on "
                    "Windows; the link runs on the board")
def test_links_exchange_records_over_tcp(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    pipes, threads = {}, []
    for node, peer in (("A", None), ("B", "127.0.0.1")):
        ours, theirs = Pipe()
        pipes[node] = ours
        threads.append(threading.Thread(target=run_link, daemon=True,
                                        args=(theirs, node, peer, port, str(tmp_path / f"{node}.jsonl"), "127.0.0.1")))
    for thread in threads:
        thread.start()
    a, b = records(theta_ns=2.0, path_ns=310.0)
    pipes["A"].send({"type": "info", "serial": "aa"})
    pipes["B"].send({"type": "info", "serial": "bb"})
    pipes["A"].send({"type": "record", **a})  # possibly before B is connected: kept and sent later
    pipes["B"].send({"type": "record", **b})

    def results(node):
        path = tmp_path / f"{node}.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [e for e in map(json.loads, lines) if e["type"] == "result"]

    deadline = time.monotonic() + 15
    while not (results("A") and results("B")) and time.monotonic() < deadline:
        time.sleep(0.1)
    for pipe in pipes.values():
        pipe.send(None)
    for thread in threads:
        thread.join(timeout=5)
    for node in ("A", "B"):
        (result,) = results(node)
        assert result["theta_ns"] == pytest.approx(2.0)


class FakeBoard:
    """PL.open's object for run_node: a loopback of ``delay`` samples after each slot."""

    def __init__(self, delay: int) -> None:
        from rf_clock.pl.client import RXWindow
        from rf_clock.pl.waveform import WaveformGenerator

        ref = WaveformGenerator().generate()
        self.delay, self.ref, self.pps, self.armed = delay, ref, 0, {}
        ctrl = SimpleNamespace(cancel_tx=lambda: None, cancel_rx=lambda: None)
        ctrl.wait_next_pps = self.next_pps
        ctrl.arm_tx = lambda target, beats: self.armed.update(tx=target)
        ctrl.arm_rx = lambda target, beats: self.armed.update(rx=(target, beats))
        ctrl.wait_tx = lambda timeout: self.armed["tx"]
        ctrl.wait_rx = lambda timeout: RXWindow(*self.armed["rx"][:1] * 2, beats=self.armed["rx"][1])
        self.ctrl = ctrl
        self.tx_buffer = SimpleNamespace(write_beats=lambda beats: None)
        self.rx_dma = SimpleNamespace(prepare=lambda beats: None, wait=self.samples)

    def next_pps(self):
        self.pps += 100_000_000
        return self.pps // 100_000_000, self.pps

    def samples(self, beats):
        x = np.zeros(beats * 8, dtype=np.complex64)
        start = PRE_TICKS * 8 + self.delay  # the burst starts PRE_TICKS after the window
        x[start:start + len(self.ref)] = self.ref
        return SimpleNamespace(samples=x)


def test_the_self_test_loop_measures_its_loopback_with_theta_zero():
    records_ = []
    done = run_node_quietly(FakeBoard(delay=230), "AB", 2, records_.append)
    assert done == 2 and [r["count"] for r in records_] == [1, 2]
    record = records_[0]
    assert record["slots"]["AB"]["rx"]["peak"] == pytest.approx(PRE_TICKS * 8 + 230, abs=1e-3)
    result = two_way(record, record)
    assert result.theta_ns == pytest.approx(0.0, abs=1e-9)  # the same window in both slots
    assert result.delay_ns == pytest.approx(230 * 1.25, abs=1e-2)


def run_node_quietly(board, node, seconds, emit):
    from rf_clock.twtt.node import run_node

    return run_node(board, node, seconds, emit, say=lambda line: None)
