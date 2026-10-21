"""Slots, timestamps, pairing and the two-way equations (plan/RF_clock_modules.md section 4).

Each board counts 10 ns ticks from its latest PPS edge.  Every second, A transmits
in slot AB while B listens, and B transmits in slot BA while A listens.  With every
time measured from the board's own PPS edge of that second:

    m_AB = T_rx,B - T_tx,A = dt_A + p + dr_B + theta
    m_BA = T_rx,A - T_tx,B = dt_B + p + dr_A - theta
    theta = (m_AB - m_BA)/2 - Delta/2,   one-way delay = (m_AB + m_BA)/2

theta is B's clock reading minus A's at the same instant.  The non-reciprocity Delta
comes from a zero-baseline calibration; ``TwoWay.theta_ns`` is (m_AB - m_BA)/2
without it.

A record is one board's second, as ``rf_clock.twtt.node`` makes it and the link
sends it::

    {"node": "A", "count": PPS_COUNT, "pps_tick": PPS_TICK, "t_sys": system time at the edge,
     "slots": {"AB": {"tx": TX_ACTUAL}, "BA": {"rx": {"tick": RX_ACTUAL, "peak": samples,
                                                      "phase": rad, "psl_db": dB}}}}

A slot that failed has ``"error"`` instead.  The two boards' records of one PPS edge
are matched by their system times, which NTP keeps within milliseconds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

TICK_NS = 10.0
SAMPLE_NS = 1.25
SLOTS = {"AB": 10_000_000, "BA": 60_000_000}  # ticks after the PPS edge: 100 ms and 600 ms
PRE_TICKS = 125  # an RX window opens 1.25 us before its slot
# What each node does in each slot: (transmit, receive).  "AB" is one board playing
# both nodes through its own loopback cable, a bench self-test with theta = 0.
ROLES = {
    "A": {"AB": (True, False), "BA": (False, True)},
    "B": {"AB": (False, True), "BA": (True, False)},
    "AB": {"AB": (True, True), "BA": (True, True)},
}
MIN_PSL_DB = 10.0  # a weaker correlation peak is no measurement
PAIR_TOLERANCE_S = 0.3  # largest system-time difference of one PPS edge on the two boards


def plan(node: str) -> list[tuple[str, int, bool, bool]]:
    """One second of this node, in time order: (slot, ticks after the PPS edge, transmit, receive)."""
    return sorted(((slot, SLOTS[slot], *ROLES[node][slot]) for slot in SLOTS), key=lambda s: s[1])


def one_way_ns(tx_record: dict, rx_record: dict, slot: str) -> float | None:
    """m of one slot: the receive time on the receiving board minus the transmit time
    on the transmitting board, each from its own PPS edge.  None if either is missing
    or the correlation peak is too weak."""
    tx = tx_record["slots"].get(slot, {}).get("tx")
    rx = rx_record["slots"].get(slot, {}).get("rx")
    if tx is None or rx is None or rx["psl_db"] < MIN_PSL_DB:
        return None
    ticks = (rx["tick"] - rx_record["pps_tick"]) - (tx - tx_record["pps_tick"])  # integers, exact
    return ticks * TICK_NS + rx["peak"] * SAMPLE_NS


@dataclass(frozen=True)
class TwoWay:
    """The two-way result of one second."""

    t_sys: float  # A's system time at the PPS edge
    count_a: int  # PPS_COUNT of that edge on each board
    count_b: int
    m_ab_ns: float
    m_ba_ns: float

    @property
    def theta_ns(self) -> float:
        return (self.m_ab_ns - self.m_ba_ns) / 2

    @property
    def delay_ns(self) -> float:
        return (self.m_ab_ns + self.m_ba_ns) / 2

    def to_json(self) -> dict:
        return {**asdict(self), "theta_ns": self.theta_ns, "delay_ns": self.delay_ns}


def two_way(a: dict, b: dict) -> TwoWay | None:
    """The result of one second from A's and B's records of it (one "AB" record twice
    for the self-test); None if a slot failed on either board."""
    m_ab, m_ba = one_way_ns(a, b, "AB"), one_way_ns(b, a, "BA")
    if m_ab is None or m_ba is None:
        return None
    return TwoWay(t_sys=a["t_sys"], count_a=a["count"], count_b=b["count"], m_ab_ns=m_ab, m_ba_ns=m_ba)


class Pairer:
    """Matches A's and B's records of the same PPS edge by their system times."""

    def __init__(self, tolerance_s: float = PAIR_TOLERANCE_S, keep: int = 10) -> None:
        self.tolerance_s = tolerance_s
        self.keep = keep
        self.pending: dict[str, list[dict]] = {"A": [], "B": []}

    def add(self, record: dict) -> tuple[dict, dict] | None:
        """Add a record; return (A's, B's) once its counterpart is there."""
        node = record["node"]
        if node == "AB":
            return record, record
        other = "B" if node == "A" else "A"
        match = min(self.pending[other], key=lambda r: abs(r["t_sys"] - record["t_sys"]), default=None)
        if match is not None and abs(match["t_sys"] - record["t_sys"]) <= self.tolerance_s:
            self.pending[other].remove(match)
            return (record, match) if node == "A" else (match, record)
        self.pending[node] = (self.pending[node] + [record])[-self.keep:]
        return None
