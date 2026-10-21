"""The link process: exchanges each board's records with the other board and pairs them.

``rf_clock.twtt.node`` starts it beside the PL loop and hands it every record
through a pipe.  It never touches the hardware, so a slow or broken connection
cannot delay a slot.  A listens on ``port``; B connects to A and reconnects whenever
the connection drops.  Messages are JSON, one per line: ``hello`` once per
connection, then a ``record`` for every second; records made while disconnected are
sent on reconnection (the last ``BACKLOG``).  Both boards pair the records and
compute the two-way result.

Everything goes into one JSON-lines log, each line with the system time: ``info``
(this board), ``hello`` (the other board), ``local`` and ``peer`` records,
``result`` for every paired second (``theta_ns``, ``delay_ns``; null when a slot
failed), and ``event`` (connections, errors).
"""

from __future__ import annotations

import json
import socket
import time
from collections import deque
from multiprocessing.connection import wait
from typing import Any, Callable

from .timing import Pairer, two_way

DEFAULT_PORT = 50725
BACKLOG = 30
RETRY_S = 2.0


def encode(message: dict) -> bytes:
    return (json.dumps(message) + "\n").encode()


class LineReader:
    """Splits a byte stream into JSON messages, one per line."""

    def __init__(self) -> None:
        self.rest = b""

    def feed(self, data: bytes) -> list[dict]:
        *lines, self.rest = (self.rest + data).split(b"\n")
        return [json.loads(line) for line in lines if line.strip()]


class Link:
    """One board's link state, without the sockets: what to log and what to send."""

    def __init__(self, node: str, log: Callable[[dict], None]) -> None:
        self.node = node
        self.log = log
        self.info: dict = {}
        self.pairer = Pairer()
        self.unsent: deque[dict] = deque(maxlen=BACKLOG)

    def hello(self) -> dict:
        return {**self.info, "type": "hello", "node": self.node}

    def local(self, message: dict) -> dict:
        """A message from the PL loop; returns what to send to the other board."""
        if message["type"] == "info":
            self.info = {k: v for k, v in message.items() if k != "type"}
            self.log(message)
            return self.hello()
        self.log({**message, "type": "local"})
        self._pair(message)
        return {**message, "type": "record"}

    def peer(self, message: dict) -> None:
        """A message from the other board."""
        if message["type"] == "hello":
            self.log(message)
            if {self.node, message.get("node")} != {"A", "B"}:
                self.log({"type": "event", "event": f"peer is node {message.get('node')}, this is {self.node}"})
        elif message["type"] == "record":
            self.log({**message, "type": "peer"})
            self._pair(message)

    def _pair(self, record: dict) -> None:
        pair = self.pairer.add({k: v for k, v in record.items() if k != "type"})
        if pair is not None:
            a, b = pair
            result = two_way(a, b)
            self.log({"type": "result", "t_sys": a["t_sys"], "count_a": a["count"], "count_b": b["count"],
                      **(result.to_json() if result else {"theta_ns": None, "delay_ns": None})})


def run_link(conn: Any, node: str, peer: str | None, port: int, log_path: str, bind: str = "") -> None:
    """The link process: until the PL loop sends None through ``conn``.  A listens on
    ``bind`` (all interfaces by default)."""
    with open(log_path, "a") as f:

        def log(entry: dict) -> None:
            f.write(json.dumps({"time": time.time(), **entry}) + "\n")
            f.flush()

        link = Link(node, log)
        listener = socket.create_server((bind, port)) if node == "A" else None
        sock: socket.socket | None = None
        reader = LineReader()
        next_try = 0.0

        def connected(new: socket.socket, how: str) -> socket.socket | None:
            nonlocal reader
            reader = LineReader()
            log({"type": "event", "event": how})
            for message in [link.hello(), *link.unsent]:
                try:
                    new.sendall(encode(message))
                except OSError as exc:
                    log({"type": "event", "event": f"send failed: {exc}"})
                    new.close()
                    return None
            link.unsent.clear()
            return new

        def drop(reason: str) -> None:
            nonlocal sock
            log({"type": "event", "event": f"disconnected: {reason}"})
            if sock is not None:
                sock.close()
            sock = None

        try:
            while True:
                if node == "B" and sock is None and peer and time.monotonic() >= next_try:
                    try:
                        sock = connected(socket.create_connection((peer, port), timeout=1.0),
                                         f"connected to {peer}:{port}")
                    except OSError:
                        next_try = time.monotonic() + RETRY_S
                waiting = [conn] + ([sock] if sock is not None else [listener] if listener is not None else [])
                for ready in wait(waiting, timeout=1.0):
                    if ready is conn:
                        message = conn.recv()
                        if message is None:
                            return
                        out = link.local(message)
                        if node == "AB":
                            continue
                        if sock is None:
                            if out["type"] == "record":  # a hello goes out on connection anyway
                                link.unsent.append(out)
                            continue
                        try:
                            sock.sendall(encode(out))
                        except OSError as exc:
                            if out["type"] == "record":
                                link.unsent.append(out)
                            drop(str(exc))
                    elif ready is listener:
                        new, address = listener.accept()
                        sock = connected(new, f"accepted {address[0]}")
                    elif sock is not None:
                        try:
                            data = sock.recv(65536)
                        except OSError as exc:
                            drop(str(exc))
                            continue
                        if not data:
                            drop("closed by the other board")
                            continue
                        for message in reader.feed(data):
                            link.peer(message)
        finally:
            if sock is not None:
                sock.close()
            if listener is not None:
                listener.close()
