"""M4c checks that need both nodes' logs, or a window copied to DDR, offline.

Problem.  The link closes the two-way equations on each node, but that is only
indirect evidence that the packets carry the right numbers and that the PL
computes what rf_clock.plmodel defines.  Two checks make it direct:

``packets RUN``  every PEER packet a node decoded is compared field by field with
    the sender: its T_rx, carrier phase and quality with the sender's own queue entry
    for the window it measured (this node's burst in the slot before), its T_tx with
    the slot the burst was sent in, its config_id, version, slot and second.
``copies RUN``   every window a node copied to DDR (``m4c_link --copy-every``) is run
    through rf_clock.plmodel with the same reference: the tracker's estimate at the
    lock lag it started from (the lag of the node's measurement two slots before),
    the window's sum of I^2 + Q^2, the quality and T_rx must equal the node's queue
    entry for that window to the bit, and the packet the model despreads from the
    copy (at that lock lag, with this window's correlation as the carrier phase
    reference) must equal the PEER entry the PL decoded from it.
``scenario RUN`` the protocol test the run's settings name (experiments/m4c_v2_link.py
    ``--scenario``), judged by what it must show; every scenario also runs
    ``packets``, and ``copy`` runs ``copies``.

RUN is a results/m4c-v2-* directory::

    uv run python experiments/m4c_check.py packets results/m4c-v2-link-20261103_102922
    uv run python experiments/m4c_check.py scenario results/m4c-v2-relock-<time>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from rf_clock.pl.waveform import generate_test_chips
from rf_clock.plmodel import correlator as p8
from rf_clock.plmodel import fir
from rf_clock.plmodel import packet as p9

TICK_FS = 10_000_000
PKST_MEASUREMENT = 1


def load(run: Path, role: str) -> list[dict]:
    path = run / f"m4c_{role}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_packets(run: Path) -> int:
    logs = {role: load(run, role) for role in ("A", "B")}
    configs = {role: next(e for e in log if e["type"] == "config") for role, log in logs.items()}
    own = {role: {(e["pps_count"], e["slot"]): e for e in log if e["type"] == "own"} for role, log in logs.items()}
    dropped = {role: next((e["dropped"] for e in reversed(log) if e["type"] == "result"), 0) for role, log in logs.items()}
    failures = 0
    for role, peer in (("A", "B"), ("B", "A")):
        cfg = configs[peer]
        slots, slot_ticks = cfg["slots_per_second"], cfg["slot_ticks"]
        n = matched = measured = lost = 0
        for p in (e for e in logs[role] if e["type"] == "peer"):
            n += 1
            problems = []
            if p["slot"] != p["peer_slot"]:
                problems.append(f"slot {p['peer_slot']} in a window of slot {p['slot']}")
            if p["t_tx_fs"] != (p["peer_slot"] * slot_ticks + cfg["tx_offset"]) * TICK_FS:
                problems.append(f"T_tx {p['t_tx_fs']}")
            if p["version"] != p9.FORMAT_VERSION:
                problems.append(f"version {p['version']}")
            if p["status"] & (1 << PKST_MEASUREMENT):
                measured += 1
                k = p["peer_slot"]
                key = (p["peer_pps_count"] - (1 if k == 0 else 0), (k - 1) % slots)
                sent = own[peer].get(key)
                if sent is None and dropped[peer]:
                    lost += 1  # the sender's queue dropped its entry: nothing to compare with
                elif sent is None:
                    problems.append(f"no OWN entry of {peer} for second {key[0]} slot {key[1]}")
                else:
                    matched += 1
                    for field, mine in (("t_rx_fs", "t_rx_fs"), ("phase", "phase"), ("quality", "quality")):
                        if p[field] != sent[mine]:
                            problems.append(f"{field} {p[field]} != {peer}'s {sent[mine]}")
                    if not sent["locked"]:
                        problems.append(f"MEASUREMENT set but {peer}'s entry is not locked")
            if problems:
                failures += 1
                if failures <= 10:
                    print(f"{role} PEER seq {p['seq']} (second {p['pps_count']} slot {p['slot']}): " + "; ".join(problems))
        ids = {p["config_id"] for p in logs[role] if p["type"] == "peer"}
        print(f"{role}: {n} PEER packets from {peer}, {measured} with a measurement, {matched} matched to {peer}'s "
              f"OWN entries field by field" + (f", {lost} whose entry {peer}'s queue dropped" if lost else "")
              + f"; config_id {sorted(ids)}")
    print(f"packets: {'PASS' if failures == 0 else f'FAIL ({failures} packets differ)'}")
    return failures


def check_copies(run: Path) -> int:
    failures = 0
    checked = 0
    for role in ("A", "B"):
        log = load(run, role)
        cfg = next(e for e in log if e["type"] == "config")
        slots, slot_ticks = cfg["slots_per_second"], cfg["slot_ticks"]
        prn = generate_test_chips(cfg["prn_chips"], cfg["prn_seed"])
        taps = fir.coefficients(cfg["amplitude"])
        ref = fir.shape(prn, taps).astype(np.int64)
        cref = p9.code_reference(taps)
        own_by_tick = {e["tick"]: e for e in log if e["type"] == "own"}
        own = {(e["pps_count"], e["slot"]): e for e in log if e["type"] == "own"}
        peer = {(e["pps_count"], e["slot"]): e for e in log if e["type"] == "peer"}
        window = 8 * cfg["window_beats"]
        for copy in (e for e in log if e["type"] == "copy"):
            path = run / f"copy_{role}" / Path(copy["file"]).name
            entry = own_by_tick.get(copy["tick"])
            if entry is None or not path.exists():
                print(f"{role}: copy at tick {copy['tick']}: " + ("no OWN entry" if entry is None else f"{path} missing"))
                failures += 1
                continue
            k = entry["slot"]
            before = own.get((entry["pps_count"] - (1 if k < 2 else 0), (k - 2) % slots))
            if before is None or not before["locked"] or not entry["locked"]:
                print(f"{role}: copy of second {entry['pps_count']} slot {k}: not locked here or two slots before; skipped")
                continue
            checked += 1
            data = np.load(path)
            rx_i, rx_q = data["i"].astype(np.int64), data["q"].astype(np.int64)
            problems = []
            w_i, w_q = rx_i[:window], rx_q[:window]
            power = int(np.dot(w_i, w_i) + np.dot(w_q, w_q))
            if power != entry["window_power"]:
                problems.append(f"window power {entry['window_power']} != model {power}")
            lag0 = before["lag"]
            ci, cq = p8.track_correlations(w_i, w_q, ref, lag0)
            powers = [p8.power(a, b) for a, b in zip(ci, cq)]
            peak = p8.peak_at(ci, cq, powers.index(max(powers)), lag0 - 4)
            sec_tick = entry["tick"] - (k * slot_ticks + cfg["rx_offset"])
            want = dict(lag=peak.lag, fraction=peak.fraction, phase=peak.phase, magnitude=peak.magnitude,
                        quality=p8.quality(p8.power(peak.c_i, peak.c_q), power, cfg["quality_offset"]),
                        t_rx_fs=p8.t_rx_fs(entry["tick"], sec_tick, peak.lag, peak.fraction))
            for field, value in want.items():
                if entry[field] != value:
                    problems.append(f"{field} {entry[field]} != model {value}")
            got = peer.get((entry["pps_count"], k))
            start = lag0 + 8 * (cfg["prn_chips"] + p9.GUARD_CHIPS)
            decoded = p9.unpack(p9.from_bits(p9.despread(rx_i, rx_q, start, peak.c_i, peak.c_q, cref)))
            if decoded is None:
                problems.append("the model's despread packet fails its CRC")
            elif got is None:
                problems.append("the PL decoded no packet from this window")
            else:
                for field, value in (("t_tx_fs", decoded.t_tx_fs), ("t_rx_fs", decoded.t_rx_fs),
                                     ("phase", decoded.phase), ("quality", decoded.quality),
                                     ("config_id", decoded.config_id), ("status", decoded.status),
                                     ("peer_slot", decoded.slot), ("peer_pps_count", decoded.pps_count),
                                     ("time_epoch", decoded.time_epoch), ("version", decoded.version)):
                    if got[field] != value:
                        problems.append(f"PEER {field} {got[field]} != model {value}")
            print(f"{role} copy second {entry['pps_count']} slot {k}: " + ("; ".join(problems) if problems else
                  f"equal to the bit (lag {entry['lag']}, fraction {entry['fraction']}, phase {entry['phase']}, "
                  f"magnitude {entry['magnitude']}, quality {entry['quality']}; packet equal)"))
            failures += bool(problems)
    print(f"copies: {checked} checked, {'PASS' if failures == 0 and checked else 'FAIL'}")
    return failures if checked else 1


def events(log: list[dict], kind: str) -> list[dict]:
    return [e for e in log if e["type"] == kind]


def check_scenario(run: Path) -> int:
    settings = json.loads((run / "settings.json").read_text(encoding="utf-8"))
    scenario = settings.get("scenario", "link")
    logs = {role: load(run, role) for role in ("A", "B")}
    result = {role: events(log, "result")[-1] for role, log in logs.items()}
    problems: list[str] = []

    def expect(cond: bool, what: str) -> None:
        print(f"  {'ok  ' if cond else 'FAIL'} {what}")
        if not cond:
            problems.append(what)

    def link_ok(role: str) -> None:
        r = result[role]
        expect(r["locked"] and r["lost_after_lock"] == 0 and r["crc_errors"] == 0 and r["dropped"] == 0
               # runs before 2026-11-04 could count one packet whose window was read before the first lock
               and r["skipped_after_lock"] == 0 and 0 <= r["peer_packets"] - r["peer_windows"] <= 1,
               f"{role}: locked to the end, nothing lost, every peer burst decoded ({r['peer_packets']}/{r['peer_windows']})")

    print(f"scenario {scenario}: A {result['A']}\n            B {result['B']}")
    if scenario in ("link", "copy", "edge-low", "edge-high"):
        for role in ("A", "B"):
            link_ok(role)
        if scenario.startswith("edge"):
            for role in ("A", "B"):
                lag = events(logs[role], "locked")[0]["lag"]
                lags = events(logs[role], "config")[0]
                expect(lag < 64 if scenario == "edge-low" else lag > 10900,
                       f"{role}: locked at lag {lag} (expected {lags['expected_lag']})")
    if scenario == "copy":
        failures = check_copies(run)
        expect(failures == 0, "the copied windows equal the model")
    if scenario == "search":
        for role in ("A", "B"):
            link_ok(role)
            asked = len(events(logs[role], "search_request"))
            locked_reports = [e for e in events(logs[role], "report") if e["locked"]]
            off = [e for e in locked_reports if abs(e["strongest"]["lag"] - e["lock_lag"]) > 1]
            flagged = [e for e in events(logs[role], "own") if e["searched"] and e["locked"]]
            expect(asked >= 5 and len(locked_reports) >= asked - 1,
                   f"{role}: {asked} searches asked while locked, {len(locked_reports)} reported")
            expect(not off, f"{role}: every such report's strongest peak at the lock lag ({len(off)} off)")
            expect(len(flagged) >= asked - 1, f"{role}: {len(flagged)} locked measurements of searched windows")
    if scenario == "relock":
        a, b = result["A"], result["B"]
        expect(a["lost_after_lock"] >= 1 and a["relocks"] >= 1,
               f"A lost the lock ({a['lost_after_lock']}) and took it again by itself ({a['relocks']} re-locks)")
        expect(a["lost_after_lock"] == 1 and a["relocks"] == 1,
               "A locked again only once, on B's return: no re-lock on a noise peak during the silence")
        expect(len(events(logs["A"], "accept")) == 1, "A needed no second ACCEPT")
        expect(a["locked"] and b["locked"], "both locked at the end")
        expect(len(events(logs["B"], "accept")) == 2, "B, silenced, was accepted again after its restart")
        silent = events(logs["B"], "silent")[0]["time"]
        gap = [e for e in events(logs["A"], "own") if not e["locked"]]
        expect(bool(gap), f"A's windows without B's bursts are marked unlocked ({len(gap)})")
        expect(a["crc_errors"] <= a["lost_after_lock"],
               f"A: bad CRCs ({a['crc_errors']}) only in a window armed while locked as the bursts vanished")
        print(f"  B silent at {silent:.1f}")
    if scenario == "reject":
        a = result["A"]
        expect(not events(logs["A"], "accept") and not a["locked"] and a["relocks"] == 0,
               "A, expecting the peak 568 samples away, accepted nothing and never locked")
        expect(len(events(logs["A"], "reject")) >= 3, f"A rejected {len(events(logs['A'], 'reject'))} reports")
        waits = [e["correlator"]["waiting"] for e in events(logs["A"], "status")[3:]]
        expect(bool(waits) and all(waits), "A's correlator kept waiting for the ARM")
        link_ok("B")
        unmeasured = [e for e in events(logs["B"], "peer") if not e["status"] & (1 << PKST_MEASUREMENT)]
        expect(len(unmeasured) == len(events(logs["B"], "peer")), "A's packets carry no measurement")
    if scenario == "refusals":
        for role in ("A", "B"):
            r = events(logs[role], "refusals")[0]
            expect(r["tx"] == "REJ_BUSY" and r["rx"] == "REJ_BUSY", f"{role}: P2 TX and RX refused ({r['tx']}, {r['rx']})")
            expect(r["burst_write_refused"] == 1 and r["burst_memory_unchanged"],
                   f"{role}: the burst memory write refused and counted, the memory unchanged")
            dropped = result[role]["dropped"]
            seqs = [e["seq"] for e in logs[role] if e["type"] in ("own", "peer")]
            gaps = sum((b - a - 1) % 65536 for a, b in zip(seqs, seqs[1:]))
            expect(dropped > 0 and gaps == dropped, f"{role}: {dropped} entries dropped, the sequence skips {gaps}")
            expect(result[role]["locked"] and result[role]["lost_after_lock"] == 0 and result[role]["crc_errors"] == 0,
                   f"{role}: the link itself unaffected")
    if scenario in ("acquire", "free", "sf-step", "steer"):
        acquired = events(logs["B"], "acquired")
        first = acquired[0] if acquired else {}
        expect(first.get("offset_ns") is not None,
               f"B decoded A's packet in its long window: A's PPS {first.get('offset_ns')} ns from B's")
        after = [e for e in acquired if e["label"] == "after PP"]
        if events(logs["B"], "place_pulse"):
            goal = events(logs["B"], "place_pulse")[0].get("goal_ns", 0.0)
            expect(bool(after) and abs(after[0]["offset_ns"] - goal) <= 20,
                   f"after PP, B's PPS {after[0]['offset_ns'] if after else None} ns from A's (target {goal} ns)")
        for role in ("A", "B"):
            link_ok(role)
    if scenario == "sf-step":
        sf = events(logs["B"], "sf")
        expect(len(sf) == 2 and sf[1]["sf"] == events(logs["B"], "frequency_control")[0]["sf0"],
               f"B: SF stepped and back ({[e['sf'] for e in sf]})")
        if len(sf) == 2:
            rows = [e for e in logs["B"] if e["type"] == "second"]

            def slope(lo: float, hi: float) -> float:
                k = [(r["time"], r["theta_ns"] * 1000) for r in rows if lo < r["time"] < hi]
                t = [x for x, _ in k]
                y = [v for _, v in k]
                mt, my = sum(t) / len(t), sum(y) / len(y)
                return sum((x - mt) * (v - my) for x, v in zip(t, y)) / sum((x - mt) ** 2 for x in t)

            before = slope(rows[0]["time"], sf[0]["time"])
            during = slope(sf[0]["time"] + 5, sf[1]["time"])
            after = slope(sf[1]["time"] + 5, rows[-1]["time"] + 1)
            step = sf[0]["sf"] - sf[1]["sf"]
            response = during - (before + after) / 2
            expect(abs(response - step) <= 0.2 * abs(step),
                   f"theta's rate {before:.2f} -> {during:.2f} -> {after:.2f} ps/s: {response:+.2f} ps/s for SF {step:+d}")
        fits = events(logs["B"], "sf_response")
        for fit in fits:  # the delay is judged; the rate over +-20 s carries the clocks' wander (+-20 %), the
            # rates over the 120 s windows above judge the step's size
            expect(0.0 <= fit["delay_s"] <= 3.0,
                   f"FS725 answer to SF {fit['sf_change']:+d}: acting {fit['delay_s']:.2f} s after the command "
                   f"({fit['ps_per_s_per_sf']:.3f} ps/s per unit over {fit['seconds']} s)")
        if not fits:
            print("  (no sf_response lines: a node from before 2026-11-04)")
    if scenario == "steer":
        stopped = events(logs["B"], "steer_stopped") + events(logs["B"], "restore_failed")
        expect(not stopped, f"B: the steering ran to the end ({[e['reason'] for e in stopped]})")
        sf = events(logs["B"], "sf")
        sf0 = events(logs["B"], "frequency_control")[0]["sf0"]
        expect(bool(sf) and sf[-1]["sf"] == sf0 and sf[-1]["why"] == "restore",
               f"B: SF back at its start {sf0} at the end")
        target = float(settings["node_args"]["B"].split("--steer-target-ns")[1].split()[0]) * 1000
        rows = [e for e in logs["B"] if e["type"] == "second"]
        late = [r["theta_ns"] * 1000 - target for r in rows if r["time"] - rows[0]["time"] >= 600]
        if late:
            mean = sum(late) / len(late)
            rms = (sum(x * x for x in late) / len(late)) ** 0.5
            expect(abs(mean) < 100 and rms < 300,
                   f"theta - target from 600 s on: mean {mean:.1f} ps, rms {rms:.1f} ps over {len(late)} s")
    if any(e["type"] in ("own", "peer") for e in logs["A"]):
        failures = check_packets(run)
        expect(failures == 0, "every PEER packet equals the sender's measurement")
    else:
        print("  (no queue entries logged: --no-entries)")
    print(f"scenario {scenario}: {'PASS' if not problems else 'FAIL'}")
    return len(problems)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("check", choices=("packets", "copies", "scenario"))
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    failures = {"packets": check_packets, "copies": check_copies, "scenario": check_scenario}[args.check](args.run)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
