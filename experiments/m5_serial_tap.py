"""M5: when each FC command really leaves on B's RS-232 line, against B's tick.

Problem.  The FS725 begins to answer a change of FC 23.5 ms after the node reads its tick right
after the write call returns (written_count, results/fc-response-20261112 section 2).  That time
holds the USB adapter's delay, the 12 characters at 9600 baud (12.5 ms) and the PRS10's firmware,
and the write itself comes 32-34 ms after the end of its step in the median, with a jitter that
the node's tick only sees from the ARM's side.  Only the line itself shows when each command
leaves and how long it takes, which splits the dead time and tells how much a write timed by the
PL (a UART on the Pmod, through a TTL-to-RS-232 module) could save.

Procedure.  An RS-232 breakout (DB9 male to female, with test points) sits between the
straight-through cable and the rear DB9 of B's FS725 (a DCE: the commands arrive on pin 3, the
replies leave on pin 2, ground on pin 5).  A probe on the command line goes to ``--channel``
(C1, DC 1 Mohm, 2 V/div), and C4 keeps B's output PPS (DAC_B, DC 50 ohm), whose edge comes
OFFSET ticks plus a fixed board delay (under 1 us) after each tick second.  The scope triggers on
that edge (rising through 0.1 V) and records about 1 s of the line at ``--rate`` (1 MS/s), so each
record holds 0.05 s before the edge and 0.95 s after it, one second of B's tick.  The script decodes the line (9600 baud 8N1, idle negative, a
start bit positive, the bits read at their centres) and writes, per record, each message (a run of
characters without a gap): its text, the time of its first start bit's leading edge from the PPS
edge and the end of its last stop bit.  The first ``--raw`` records are also saved whole
(``tap_raw_<n>.npz``).

``--analyze RUN`` matches each FC message to the node's ``fc_set`` record of the same value and
time (each record is one second, so a second whose FC values all agree within 0.1 s is the one)
and gives, per change, the line's start and end against the step's end and against written_count,
in ``serial_tap.json`` and ``serial_tap.png``.

Run on RF-clock-PC as root (the scope's USB node is root-only); experiments/m4c_v2_link.py
--scope-tap starts it for the length of a link run::

    sudo .venv/bin/python experiments/m5_serial_tap.py --seconds 1200 --out results/x/tap.jsonl
    .venv/bin/python experiments/m5_serial_tap.py --analyze results/m4c-v2-steer-...
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import time
from pathlib import Path

import numpy as np

SCOPE = "USB0::0x05FF::0x1023::4404N30414::INSTR"
BAUD = 9600
BIT_S = 1 / BAUD
CHAR_S = 10 * BIT_S  # start bit, 8 data bits, stop bit
THRESHOLD_V = 1.0  # RS-232 marks below -1 V, spaces above +1 V


def setup(channel: str, rate: float) -> list[str]:
    """VBS statements: the line on ``channel``, B's output PPS on C4 as the trigger, about 1 s at ``rate``."""
    return [f"app.Acquisition.{channel}.View = True", f'app.Acquisition.{channel}.Coupling = "DC1M"',
            f"app.Acquisition.{channel}.VerScale = 2", f"app.Acquisition.{channel}.VerOffset = 0",
            "app.Acquisition.C4.View = True", 'app.Acquisition.C4.Coupling = "DC50"',
            "app.Acquisition.C4.VerScale = 0.1", "app.Acquisition.C4.VerOffset = 0",
            "app.Acquisition.Horizontal.HorScale = 0.1", "app.Acquisition.Horizontal.HorOffset = -0.45",
            'app.Acquisition.Horizontal.Maximize = "FixedSampleRate"', f"app.Acquisition.Horizontal.SampleRate = {rate:g}",
            'app.Acquisition.Trigger.Type = "Edge"', 'app.Acquisition.Trigger.Source = "C4"',
            'app.Acquisition.Trigger.C4.Slope = "Positive"', "app.Acquisition.Trigger.C4.Level = 0.1"]


def decode(t: np.ndarray, v: np.ndarray) -> list[dict]:
    """The characters on an RS-232 line sampled at times ``t`` (s), as messages, each a run of characters
    whose start bits follow each other within 1.6 character times, ended by a carriage return (the node
    sends its query right after each FC command, without a gap).  Each message holds the time of
    its first start bit's leading edge (``t0_s``, where the line crosses 0 V), the end of its last
    stop bit (``t1_s``), its text and the number of characters whose stop bit was not a mark."""
    dt = float(t[1] - t[0])
    n = v.size

    def at(te: float, s: float) -> float:
        m = int(round((te + s - t[0]) / dt))
        return float(v[min(max(m, 0), n - 1)])

    chars: list[tuple[float, int, bool]] = []
    busy_until = -np.inf
    for k in np.nonzero((v[:-1] <= 0) & (v[1:] > 0))[0]:  # every rise through 0 V
        te = float(t[k] + (0 - v[k]) / (v[k + 1] - v[k]) * dt)
        if te < busy_until or te + 10 * BIT_S > t[-1]:
            continue  # inside a character, or too late in the record
        if at(te, 0.5 * BIT_S) < THRESHOLD_V:
            continue  # no start bit (a spike)
        byte = sum((1 << b) for b in range(8) if at(te, (1.5 + b) * BIT_S) < 0)  # LSB first, a mark is 1
        chars.append((te, byte, at(te, 9.5 * BIT_S) < 0))
        busy_until = te + 9.6 * BIT_S
    messages: list[dict] = []
    for te, byte, ok in chars:
        if messages and te - messages[-1]["_last"] < 1.6 * CHAR_S and not messages[-1]["text"].endswith("<0d>"):
            m = messages[-1]
        else:
            m = {"t0_s": te, "text": "", "framing_errors": 0}
            messages.append(m)
        m["_last"] = te
        m["text"] += chr(byte) if 32 <= byte < 127 else f"<{byte:02x}>"
        m["framing_errors"] += 0 if ok else 1
    for m in messages:
        m["t1_s"] = m.pop("_last") + CHAR_S
    return messages


def capture(args: argparse.Namespace) -> None:
    from tanglab_instruments import HDO9404

    stop = False

    def on_term(signum, frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, on_term)
    scope = HDO9404(SCOPE, backend="pyusb")
    statements = setup(args.channel, args.rate)
    before = {s.split(" = ")[0]: scope.vbs_query(s.split(" = ")[0]) for s in statements}
    for statement in statements:
        scope.vbs(statement)
    scope.trigger_mode = "Stopped"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + args.seconds
    n = 0
    with out.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"type": "start", "time": time.time(), "settings_before": before, "setup": statements,
                            "channel": args.channel, "rate": args.rate}) + "\n")
        f.flush()
        while time.time() < end and not stop:
            asked = time.time()
            if scope.vbs_query("app.Acquisition.Acquire(3, False)").strip().lower() not in ("1", "true", "-1"):
                f.write(json.dumps({"type": "no_trigger", "time": asked}) + "\n")
                f.flush()
                continue
            got = time.time()
            t, v = scope.get_waveform(args.channel)
            if n < args.raw:
                np.savez_compressed(out.with_name(f"tap_raw_{n}.npz"), t=t.astype(np.float64), v=v.astype(np.float32))
            messages = decode(t, v)
            f.write(json.dumps({"type": "acq", "time": got, "asked": asked, "points": int(t.size),
                                "t_first_s": float(t[0]), "t_last_s": float(t[-1]),
                                "v_min": float(v.min()), "v_max": float(v.max()), "messages": messages}) + "\n")
            f.flush()
            n += 1
        f.write(json.dumps({"type": "stop", "time": time.time(), "acquisitions": n}) + "\n")
    scope.trigger_mode = "Auto"
    print(f"{n} acquisitions in {out}")


FC_TEXT = re.compile(r"^FC(\d+),(\d+)<0d>")


def analyze(run: Path) -> dict:
    """Match the line's FC messages to node B's fc_set records and time them (module docstring)."""
    from rf_clock.twtt import logs

    acqs = [json.loads(x) for x in (run / "tap.jsonl").read_text(encoding="utf-8").splitlines()
            if x.startswith('{"type": "acq"')]
    b = logs.read(run / "m4c_B.jsonl", ("fc_set", "second"))
    sets = [x for x in b if x["type"] == "fc_set" and x.get("written_count") is not None]
    written = np.array([x["written_count"] for x in sets])
    seconds = [x for x in b if x["type"] == "second"]
    # the board's unix time against its count, to narrow the search (the PC's clock may differ by seconds)
    unix_minus_count = float(np.median([x["time"] - x["count"] for x in seconds]))
    rows, unmatched, queries, query_gaps = [], 0, 0, []
    for a in acqs:
        fcs = [(m, FC_TEXT.match(m["text"])) for m in a["messages"]]
        queries += sum(1 for m, hit in fcs if hit is None)
        # how long before each FC command the last other message (a query) ended
        for i, (m, hit) in enumerate(fcs):
            if hit is not None and i > 0 and fcs[i - 1][1] is None:
                query_gaps.append((m["t0_s"] - fcs[i - 1][0]["t1_s"]) * 1e3)
        fcs = [(m, int(hit.group(1)) * 1000 + int(hit.group(2))) for m, hit in fcs if hit is not None]
        if not fcs:
            continue
        guess = int(round(a["time"] - unix_minus_count))
        # A change logs an fc_set record; a step that keeps FC writes the same value again without one, so
        # a message may have no record.  The second is the one where most messages find their record within
        # 20 ms (the line starts within 0.1 ms of written_count), and the rest are counted as unmatched.
        best = None
        for s0 in range(guess - 8, guess + 9):
            pick = []
            for m, value in fcs:
                k = int(np.argmin(np.abs(written - (s0 + m["t0_s"]))))
                if abs(written[k] - (s0 + m["t0_s"])) < 0.02 and sets[k]["fc"] == value:
                    pick.append((m, sets[k]))
            if 2 * len(pick) >= len(fcs) and (best is None or len(pick) > len(best[1])):
                best = (s0, pick)
        if best is None:
            unmatched += len(fcs)
            continue
        s0, pick = best
        unmatched += len(fcs) - len(pick)
        for m, x in pick:
            step_end = x["written_count"] - x["late_s"]
            # a step whose writes can run past the record's end (step 8 with a record to 0.95 s) is cut:
            # its later commands are missing, so it is left out of the times against the step's end
            rows.append({"second": s0, "step": (int(round((step_end - s0) * 10)) - 1) % 10,
                         "cut": float(step_end - s0 + 0.08 > a["t_last_s"]),
                         "started_ms": (x["at_count"] - step_end) * 1e3, "written_ms": x["late_s"] * 1e3,
                         "line_start_ms": (s0 + m["t0_s"] - step_end) * 1e3,
                         "line_end_ms": (s0 + m["t1_s"] - step_end) * 1e3,
                         "chars": len(m["text"].replace("<0d>", "\r")), "framing_errors": m["framing_errors"]})
    if not rows:
        raise SystemExit(f"no FC message matched ({unmatched} unmatched, {len(acqs)} records)")
    r = {k: np.array([x[k] for x in rows], dtype=float) for k in rows[0]}

    def stats(x: np.ndarray) -> dict:
        return {f"p{q}": round(float(np.percentile(x, q)), 2) for q in (1, 50, 99)} | {"max": round(float(x.max()), 2)}

    whole = r["cut"] == 0
    d = r["line_start_ms"] - r["written_ms"]
    out = {"run": run.name, "records": len(acqs), "record_s": [round(min(a["t_first_s"] for a in acqs), 3),
                                                                round(max(a["t_last_s"] for a in acqs), 3)],
           "fc_matched": len(rows), "fc_unmatched": unmatched, "cut_steps": sorted(set(r["step"][~whole].astype(int).tolist())),
           "commands_by_step": {int(k): int((r["step"] == k).sum()) for k in range(10)},
           "other_messages": queries, "framing_errors": int(r["framing_errors"].sum()),
           "query_end_before_fc_ms_min": round(float(min(query_gaps)), 2) if query_gaps else None,
           "written_ms": stats(r["written_ms"][whole]), "line_start_ms": stats(r["line_start_ms"][whole]),
           "line_end_ms": stats(r["line_end_ms"][whole]),
           "line_start_less_written_ms": stats(d) | {"min": round(float(d.min()), 2)},
           "line_ms": stats(r["line_end_ms"] - r["line_start_ms"]),
           "by_step": {int(s): {"written_ms": round(float(np.median(r["written_ms"][r["step"] == s])), 2),
                                "line_start_ms": round(float(np.median(r["line_start_ms"][r["step"] == s])), 2),
                                "line_end_ms": round(float(np.median(r["line_end_ms"][r["step"] == s])), 2)}
                       for s in sorted(set(r["step"].astype(int)))}}
    (run / "serial_tap.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    figure(run, r)
    return out


def figure(run: Path, r: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
    steps = np.arange(10)
    for key, colour, label, dx in (("written_ms", "C0", "the node's tick read after the write call returns", -0.15),
                                   ("line_start_ms", "C3", "the command's first start bit on the line", 0.0),
                                   ("line_end_ms", "C2", "the end of its last stop bit", 0.15)):
        sel = [(r["step"] == s) & (r["cut"] == 0) for s in steps]
        med = [np.median(r[key][m]) if m.any() else np.nan for m in sel]
        lo = [np.percentile(r[key][m], 1) if m.any() else np.nan for m in sel]
        hi = [r[key][m].max() if m.any() else np.nan for m in sel]
        med, lo, hi = np.array(med), np.array(lo), np.array(hi)
        ax[0].errorbar(steps + dx, med, yerr=[med - lo, hi - med], fmt="o", color=colour, ms=5, capsize=3,
                       label=f"{label}, median, bars from 1 % to the latest")
    ax[0].set(title="When each change of FC is written and on the line", xlabel="step within the second (0.1 s each)",
              ylabel="ms after the step's end, B's tick", xticks=steps)
    ax[0].set_ylim(15, 85)
    for k in sorted(set(r["step"][r["cut"] == 1].astype(int).tolist())):
        ax[0].text(k, 20, "left out,\nthe record\nends first", ha="center", fontsize=7.5, color="0.35")
    ax[0].legend(fontsize=8, loc="upper left")
    d = r["line_start_ms"] - r["written_ms"]
    ax[1].hist(d, bins=60, color="C3")
    ax[1].set(title="The line's start against the tick read after the write",
              xlabel="first start bit on the line less the tick read after the write call returns (ms)",
              ylabel="FC commands")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(run / "serial_tap.png", dpi=130)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--out", help="tap.jsonl to append to (capture)")
    parser.add_argument("--channel", default="C1", choices=("C1", "C2", "C3"), help="the probe on the command line")
    parser.add_argument("--rate", type=float, default=1e6, help="samples per second")
    parser.add_argument("--raw", type=int, default=3, help="records saved whole")
    parser.add_argument("--analyze", metavar="RUN", help="a link run with tap.jsonl and m4c_B.jsonl")
    args = parser.parse_args()
    if args.analyze:
        print(json.dumps(analyze(Path(args.analyze)), indent=1))
    elif args.out:
        capture(args)
    else:
        parser.error("--out (capture) or --analyze RUN")


if __name__ == "__main__":
    main()
