"""Reading the log ``rf_clock.checks.m4c_link`` writes: one JSON object per line, its ``type``
first (``config``, the queue entries ``own`` and ``peer`` unless ``--no-entries``, ``second``,
``step``, ``steer``, ``sf``, ``fc``, ``status``, ``result``, ...; docs/ABI.md 13).

``read`` loads the lines of the types asked for, ``pairs`` forms every pair of a log of queue
entries as the node does (``rf_clock.twtt.pairs``), ``seconds`` and ``fc`` give theta's and FC's
series, and ``steering_error`` the steering error, theta less the target over the seconds after
the first ``settle_s``, by which steering runs are compared in the reports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np

from .pairs import Pair, Pairer, SlotTiming


def _prefixes(types: Iterable[str]) -> tuple[str, ...]:
    return tuple(f'{{"type": "{t}"' for t in types)


def read(path: str | Path, types: Iterable[str] | None = None) -> list[dict]:
    """The log's lines, or those of the given types; a line's type is matched at its start
    before it is parsed, which keeps a log of queue entries (hundreds of MB) quick to scan."""
    wanted = _prefixes(types) if types is not None else None
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip() and (wanted is None or line.startswith(wanted))]


def config(entries: list[dict]) -> dict:
    return next(e for e in entries if e["type"] == "config")


def timing(config_line: dict) -> SlotTiming:
    return SlotTiming(config_line["slots_per_second"], config_line["slot_ticks"], config_line["tx_offset"])


def pairs(path: str | Path) -> list[Pair]:
    """Every pair of a log with the queue entries, as the node formed them, read line by line."""
    out: list[Pair] = []
    pairer: Pairer | None = None
    entry_types = _prefixes(("own", "peer"))
    with open(path, encoding="utf-8") as f:
        for line in f:
            if pairer is None:
                if line.startswith(_prefixes(("config",))):
                    c = json.loads(line)
                    pairer = Pairer(timing(c), c["role"] == "A")
            elif line.startswith(entry_types):
                p = pairer.add_record(json.loads(line))
                if p is not None:
                    out.append(p)
    return out


def seconds(entries: list[dict], min_pairs: int = 240) -> tuple[np.ndarray, np.ndarray]:
    """Each ``second`` line with at least ``min_pairs`` pairs (of 250), by count: the second
    (second k spans k .. k + 1 on the node's PPS count) and theta, ps, the mean over it."""
    rows = sorted((e for e in entries if e["type"] == "second" and e["pairs"] >= min_pairs), key=lambda e: e["count"])
    return np.array([r["count"] for r in rows], dtype=float), np.array([r["theta_ns"] for r in rows]) * 1e3


def fc(entries: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Each ``fc`` line: when it was read, on the node's PPS count, and FC in low DAC steps
    (1000 high + low)."""
    rows = [e for e in entries if e["type"] == "fc"]
    return (np.array([r["count"] for r in rows], dtype=float),
            np.array([1000 * r["high"] + r["low"] for r in rows], dtype=float))


def steering_error(entries: list[dict], settle_s: float = 300) -> dict:
    """theta less the steering target over each second from ``settle_s`` after the first one
    on (``seconds``): their number, mean, rms and sd, ps, and the first second counted."""
    target = config(entries)["tests"]["steer_target_ns"] * 1e3
    k, th = seconds(entries)
    err = th[k >= k[0] + settle_s] - target
    return dict(seconds=int(err.size), mean_ps=float(err.mean()), rms_ps=float(np.sqrt(np.mean(err ** 2))),
                sd_ps=float(err.std()), from_count=int(k[0] + settle_s), target_ps=target)
