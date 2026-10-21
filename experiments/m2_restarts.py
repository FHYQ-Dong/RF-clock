"""M2 sub-experiment: is the DAC burst time relative to PPS the same after every restart?

Problem.  M2's acceptance (plan section 9) is a burst delay relative to the PPS
that is fixed and unchanged across reboots, i.e. MTS making the RFDC latency
deterministic.  One M2 run cannot show that; this repeats M2 over many restarts
and measures each run's mean burst start on the HDO9404-MS.  The overnight series
(results/m2-restarts-20261016_212509) found each board landing in one of two
states 52 ps apart (half a DAC sample), traced to the LMX2594 output-divider
phase and removed by its phase SYNC in rf-clock 1.3.0.4
(results/lmx-phase-sync-20261019).  Rerun it after changes to the clocks, the
RFDC set-up or the bitstream.

Procedure.  For each run: starts M2 (a rect burst on every PPS) on both boards, waits
for it to settle, makes sure the envelope traces F1/F2 are not clipped,
clears the scope statistics, collects for a while, stops the scope, reads
the measurement statistics, saves a LabNotebook entry, downloads it and
deletes it from the scope, waits for M2 to finish and reboots both boards
(not after the last run).  A run whose scope part fails is recorded with
its error and the scope is USB-reset; the next run goes on.  Runs stop at
--runs, at --until, or after a file named STOP appears in the results
directory.

The scope setup is recalled once, while the first run's bursts are on
screen (a math trace's data range is fixed by the data it first sees), and
its measurements are laid out as PARAM_LAYOUT: P3/P4 Delay of the envelopes
F1/F2 (Filter(Square(C2/C3))), P5 DeltaDelay F1 -> F2, P6/P7 their Width.
C2 is DAC_A of S/N 887329260072A (.99), C3 that of S/N 887329260101 (.101).

Run on RF-clock-PC as root (the scope's USB node is root-only), with the
project's ``instruments`` dependency group (``uv sync --only-group instruments
--no-install-project``; the scope driver is ``tanglab_instruments.HDO9404``
over pyusb); board commands run over ssh as the sudo user::

    sudo .venv/bin/python experiments/m2_restarts.py --runs 5
    sudo .venv/bin/python experiments/m2_restarts.py --runs 1000 --until 08:00

Results go to results/m2-restarts-<time>/: runNN.lnb (the LabNotebook entry;
experiments/lnb.py unpacks it), runNN_<board>.log (M2 output), runs.jsonl (one
record per run) and summary.json (spread of the per-run means).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from tanglab_instruments import HDO9404

ROOT = Path(__file__).resolve().parent.parent
SCOPE = "USB0::0x05FF::0x1023::4404N30414::INSTR"  # HDO9404-MS on RF-clock-PC's USB
BOARDS = {"99": "192.168.2.99", "101": "192.168.2.101"}
TRACE_BOARD = {"F1": "99", "F2": "101"}  # F1 = Filter(Square(C2)), F2 = Filter(Square(C3))
M2 = ("cd ~/RF-clock && (nohup setsid sudo -n env BOARD=RFSoC4x2 XILINX_XRT=/usr .venv/bin/python "
      "-m rf_clock.checks.m2_tx_timing {bursts} {m2_args} > {log} 2>&1 < /dev/null &)")
M2_ARGS = "--waveform rect --amplitude 16000 --offset-ticks 0"
M2_RUNNING = "pgrep -f 'rf_clock[.]checks[.]m2_tx_timing'"  # [.] keeps pgrep off this shell
BURST_LINE = r"^ +[0-9]+ +[0-9]+ +[0-9]+ "
# P1/P2 (carrier frequency of C2/C3) stay as recalled; the setup's Delay on
# the raw carrier (old P3/P4) caught noise crossings and is replaced.
PARAM_LAYOUT = {"P3": ("Delay", "F1", ""), "P4": ("Delay", "F2", ""), "P5": ("DeltaDelay", "F1", "F2"),
                "P6": ("Width", "F1", ""), "P7": ("Width", "F2", "")}
PARAMS = ["P1", "P2", *PARAM_LAYOUT]
CLIPPED = 100  # samples at full scale that mark a trace as clipped


def ssh(ip: str, cmd: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    argv = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"xilinx@{ip}", cmd]
    user = os.environ.get("SUDO_USER")
    if user:  # root has no key for the boards
        argv = ["sudo", "-u", user, *argv]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 255, "", "timeout")


def boot_id(ip: str) -> str | None:
    r = ssh(ip, "cat /proc/sys/kernel/random/boot_id", timeout=15)
    return r.stdout.strip() if r.returncode == 0 else None


def reboot(timeout: float = 600) -> None:
    before = {name: boot_id(ip) for name, ip in BOARDS.items()}
    for ip in BOARDS.values():
        ssh(ip, "sudo -n systemctl reboot", timeout=15)
    deadline = time.monotonic() + timeout
    for name, ip in BOARDS.items():
        while (now := boot_id(ip)) is None or now == before[name]:
            if time.monotonic() > deadline:
                raise TimeoutError(f"board {name} did not come back")
            time.sleep(5)
    time.sleep(10)


def m2_progress(ip: str, log: str) -> tuple[int, str]:
    r = ssh(ip, f"cd ~/RF-clock && grep -cE '{BURST_LINE}' {log}; grep -E '^M2 digital|Traceback' {log}")
    lines = r.stdout.split("\n")
    return (int(lines[0]) if lines[0].isdigit() else 0), " ".join(lines[1:]).strip()


def wait_m2(logs: dict[str, str], bursts: int, timeout: float, done: bool = False) -> dict[str, str]:
    """Wait until every board printed `bursts` burst lines (or finished, if `done`)."""
    deadline = time.monotonic() + timeout
    while True:
        state = {name: m2_progress(BOARDS[name], log) for name, log in logs.items()}
        if all(end if done else (n >= bursts or end) for n, end in state.values()):
            return {name: end or f"{n} bursts" for name, (n, end) in state.items()}
        if time.monotonic() > deadline:
            return {name: (end or f"{n} bursts") + " (timeout)" for name, (n, end) in state.items()}
        time.sleep(2)


def open_scope() -> HDO9404:
    return HDO9404(SCOPE, backend="pyusb")


def configure_scope(scope: HDO9404, setup: Path, extra: list[str]) -> None:
    if code := scope.recall_setup(setup):
        print(f"setup recall reported execution error {code}; some settings may be missing", flush=True)
    for stmt in extra:
        scope.vbs(stmt)
    for p, (engine, src1, src2) in PARAM_LAYOUT.items():
        scope.set_parameter(p, engine, src1, src2 or None)
        if engine == "Width":
            scope.vbs(f'app.Measure.{p}.Operator.LevelType = "Percent"')
    scope.trigger_mode = "Normal"


def unclip(scope: HDO9404, traces: tuple[str, ...] = ("F1", "F2"), tries: int = 3) -> dict[str, int]:
    """Refit clipped math traces to the bursts on screen; return the clipped sample counts left."""
    left = {}
    for trace in traces:
        for _ in range(tries):
            if (n := scope.clipped_samples(trace)) < CLIPPED:
                break
            scope.reframe_math(trace)
            time.sleep(6)  # a few acquisitions (the scope takes ~2 s each)
        left[trace] = n
    return left


def chown_tree(path: Path) -> None:
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        for p in [path, *path.rglob("*")]:
            os.chown(p, int(uid), int(gid))


def summarize(records: list[dict]) -> dict:
    out = {}
    for p in PARAM_LAYOUT:
        rows = [r["stats"][p] for r in records if "stats" in r and not math.isnan(r["stats"][p]["mean"])]
        if rows:
            means = [row["mean"] for row in rows]
            out[p] = {"engine": rows[0]["ParamEngine"], "source1": rows[0]["Source1"], "runs": len(means),
                      "mean_of_means": statistics.fmean(means),
                      "sdev_of_means": statistics.stdev(means) if len(means) > 1 else 0.0,
                      "pp_of_means": max(means) - min(means), "means": means,
                      "within_run_sdev": [row["sdev"] for row in rows], "num": [row["num"] for row in rows]}
    return out


def measure(scope: HDO9404, args: argparse.Namespace, out: Path, tag: str, record: dict, configure: bool) -> None:
    """The scope part of one run, while both boards burst."""
    if configure:
        configure_scope(scope, args.setup, args.scope_vbs or [])
        print(f"scope set up from {args.setup}", flush=True)
        time.sleep(6)
    record["clipped_before"] = unclip(scope)
    scope.clear_sweeps()
    time.sleep(args.collect)
    scope.trigger_mode = "Stopped"
    try:
        record["clipped_after"] = {t: scope.clipped_samples(t) for t in ("F1", "F2")}
        record["stats"] = scope.get_statistics(PARAMS)
        rid, record["scope_file"], data = scope.save_notebook_entry()
        (out / f"{tag}.lnb").write_bytes(data)
        if not args.keep_scope_entries:
            scope.delete_notebook_entry(rid)
            time.sleep(2)
            record["scope_entry_deleted"] = not scope.file_exists(record["scope_file"])
    finally:
        scope.trigger_mode = "Normal"


def reconnect(scope: HDO9404 | None) -> HDO9404 | None:
    """USB-reset the scope (a stuck command parser needs it), or open a new session."""
    try:
        if scope is None:
            scope = open_scope()
        else:
            scope.reset_usb()
        scope.trigger_mode = "Normal"
        return scope
    except Exception as exc:  # retried at the next run
        print(f"scope reconnect failed: {exc!r}", flush=True)
        return None


def next_time(hhmm: str) -> datetime:
    t = datetime.combine(datetime.now().date(), datetime.strptime(hhmm, "%H:%M").time())
    return t if t > datetime.now() else t + timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--until", metavar="HH:MM", help="start no run after this local time")
    parser.add_argument("--setup", type=Path, default=ROOT / "results/3.8GHz-DAC-PPS-delay-01/Setup.lss")
    parser.add_argument("--settle", type=int, default=10, help="bursts before the scope statistics start")
    parser.add_argument("--collect", type=float, default=120, help="seconds of scope statistics per run")
    parser.add_argument("--m2-args", default=M2_ARGS,
                        help=f"M2 options after the burst count (default: {M2_ARGS!r}); e.g. add "
                             "'--waveform square --scope-nco 0' for the DAC without mixer, IMR high-pass or mix-mode")
    parser.add_argument("--scope-vbs", action="append", metavar="STATEMENT",
                        help="extra scope setting after the setup recall, e.g. 'app.Acquisition.C2.VerScale = 0.08'")
    parser.add_argument("--no-reboot", action="store_true",
                        help="do not reboot between runs (each M2 still reloads the bitstream and redoes MTS)")
    parser.add_argument("--keep-scope-entries", action="store_true",
                        help="keep each run's LabNotebook entry on the scope (default: delete after download)")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "results" / f"m2-restarts-{stamp}"
    out.mkdir(parents=True)
    until = next_time(args.until) if args.until else None
    bursts = args.settle + int(args.collect) + 90  # M2 keeps bursting until the entry is saved
    scope: HDO9404 | None = open_scope()
    records: list[dict] = []
    configured = False

    def stop_reason() -> str:
        if until and datetime.now() >= until:
            return f"reached {args.until}"
        return "STOP file" if (out / "STOP").exists() else ""

    try:
        for name, ip in BOARDS.items():
            if ssh(ip, M2_RUNNING).returncode == 0:
                raise RuntimeError(f"M2 already running on board {name}")
        print(f"results in {out}" + (f"; until {until:%Y-%m-%d %H:%M}" if until else ""), flush=True)
        for run in range(1, args.runs + 1):
            tag = f"run{run:03d}"
            logs = {name: f"m2_restart_{stamp}_{tag}.log" for name in BOARDS}
            record: dict = {"run": run, "start": datetime.now().isoformat(timespec="seconds"),
                            "trace_board": TRACE_BOARD, "m2_args": args.m2_args, "scope_vbs": args.scope_vbs,
                            "rebooted": run > 1 and not args.no_reboot}
            for name, ip in BOARDS.items():
                ssh(ip, M2.format(bursts=bursts, m2_args=args.m2_args, log=logs[name]))
            record["settle"] = wait_m2(logs, args.settle, timeout=240)
            if all(s.endswith("bursts") for s in record["settle"].values()):
                try:
                    if scope is None:
                        raise RuntimeError("scope not connected")
                    measure(scope, args, out, tag, record, configure=not configured)
                    configured = True
                except Exception as exc:  # keep going overnight; the record says what failed
                    record["error"] = repr(exc)
                    scope = reconnect(scope)
            record["m2"] = wait_m2(logs, bursts, timeout=bursts + 120, done=True)
            for name, ip in BOARDS.items():
                text = ssh(ip, f"cat ~/RF-clock/{logs[name]}").stdout
                (out / f"{tag}_{name}.log").write_text(text, newline="\n")
                record.setdefault("mts", {})[name] = [ln[3:] for ln in text.splitlines() if ln.startswith("m: ")
                                                      and " MTS " in ln]
            records.append(record)
            with open(out / "runs.jsonl", "a", newline="\n") as f:
                f.write(json.dumps(record) + "\n")
            s = record.get("stats", {})
            print(f"{tag} {record['start']}: m2 {record['m2']}  clipped {record.get('clipped_after')}  " + "  ".join(
                f"{p} {s[p]['mean'] * 1e9:.3f}±{s[p]['sdev'] * 1e12:.0f}ps(n={s[p]['num']:.0f})"
                for p in PARAM_LAYOUT if p in s) + (f"  ERROR {record['error']}" if "error" in record else ""),
                flush=True)
            chown_tree(out)
            reason = "" if run == args.runs else stop_reason()
            if run == args.runs or reason:
                if reason:
                    print(f"stopping: {reason}", flush=True)
                break
            if not args.no_reboot:
                reboot()
    finally:
        summary = summarize(records)
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", newline="\n")
        chown_tree(out)
    for p, row in summary.items():
        print(f"{p} {row['engine']}({row['source1']}): {row['runs']} runs, mean {row['mean_of_means'] * 1e9:.4f} ns, "
              f"run-to-run sdev {row['sdev_of_means'] * 1e12:.1f} ps, p-p {row['pp_of_means'] * 1e12:.1f} ps")


if __name__ == "__main__":
    main()
