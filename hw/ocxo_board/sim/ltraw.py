"""Read LTspice XVII .raw files (binary or ASCII; real transient or complex AC/noise) into numpy arrays."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def read(path: str | Path) -> tuple[dict[str, np.ndarray], list[int]]:
    """All variables by name, and the start index of each .step run (one entry if not stepped)."""
    raw = Path(path).read_bytes()
    enc = "utf-16-le" if raw[1:2] == b"\x00" else "latin-1"
    width = 2 if enc == "utf-16-le" else 1
    marker = next(m for m in ("Binary:\n", "Values:\n") if m.encode(enc) in raw)
    split = raw.index(marker.encode(enc)) + len(marker) * width
    header = raw[:split].decode(enc)
    lines = header.splitlines()
    info = {ln.split(":", 1)[0]: ln.split(":", 1)[1].strip() for ln in lines if ":" in ln and not ln.startswith("\t")}
    nvar, npt = int(info["No. Variables"]), int(info["No. Points"])
    flags = info.get("Flags", "")
    names = [ln.split("\t")[2] for ln in lines if ln.startswith("\t") and len(ln.split("\t")) > 3]
    complex_ = "complex" in flags
    body = raw[split:]
    if marker.startswith("Binary"):
        if complex_:
            data = np.frombuffer(body, dtype="<c16", count=npt * nvar).reshape(npt, nvar)
        elif "double" in flags:
            data = np.frombuffer(body, dtype="<f8", count=npt * nvar).reshape(npt, nvar)
        else:
            rec = np.dtype([("t", "<f8"), ("v", "<f4", (nvar - 1,))])
            arr = np.frombuffer(body, dtype=rec, count=npt)
            data = np.column_stack([arr["t"], arr["v"].astype(float)])
    else:
        vals = body.decode(enc).split()
        rows, i = [], 0
        while len(rows) < npt:
            i += 1  # point index
            row = []
            for _ in range(nvar):
                v = vals[i]
                i += 1
                row.append(complex(*map(float, v.split(","))) if complex_ else float(v))
            rows.append(row)
        data = np.array(rows)
    out = {n: data[:, k] for k, n in enumerate(names)}
    x = np.abs(out[names[0]].real) if not complex_ else out[names[0]].real
    starts = [0] + [k for k in range(1, len(x)) if x[k] < x[k - 1]]
    out[names[0]] = x + (float(info.get("Offset", 0)) if not complex_ else 0)  # a .tran start time is stored apart
    return out, starts
