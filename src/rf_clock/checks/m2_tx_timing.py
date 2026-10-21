"""M2 on the board: RFDC MTS and PPS-timed TX bursts.

Bring-up with RFDC tile start-up and DAC/ADC multi-tile sync, then load the
PRN+RRC waveform into the TX buffer and send one burst after every PPS, at
``PPS + offset``.  Digital checks (PASS):

- DAC and ADC MTS report OK;
- every burst's TX_ACTUAL equals its target (beat 0 accepted by the DAC at
  the requested tick) and lies exactly ``offset`` ticks after its PPS;
- no TX fault.

A burst whose arm reaches the PL too late (REJ_STALE: the target, the next
PPS, is already less than MIN_LEAD_TICKS away because this process stalled)
is counted as missed and the loop resynchronises on the next PPS.  Missed
bursts are a host-side event, reported but not a FAIL.

The analog half of M2 is outside this script: trigger a scope or a zero-span
spectrum analyser on the FS725 1PPS and watch the DAC output (vout20, tile 2,
the DAC_A connector): the burst must start at a fixed delay after the PPS, and
that delay must not change across restarts.  Each run appends its MTS
latencies to ``--log`` so restarts (each one rewrites the clocks and redoes
MTS) can be compared.

    sudo env BOARD=RFSoC4x2 XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python -m rf_clock.checks.m2_tx_timing 10

The RF output (3.8 GHz and its 5.8 GHz image) is beyond a low-bandwidth
scope; ``--scope-nco 100`` moves it to 100 MHz for the run.  The NCO phase at
the burst start differs from run to run, so a carrier-based reading moves by
up to a carrier period between runs.  For timing the burst start across
restarts use ``--scope-nco 0 --waveform square``: the mixer is bypassed and
the DAC outputs the I samples, a 25 MHz square wave whose first edge is the
burst start.
"""

from __future__ import annotations

import argparse
import sys
import time

from .board_common import RunLog, add_common_args, add_rfdc_args, mark, open_pl
from rf_clock.pl import TX_LAYOUT, WaveformGenerator, abi
from rf_clock.pl.beats import BeatPacker
from rf_clock.pl.client import CommandRejected, TargetMissed

R = abi.ABI.REG
PPS_PERIOD = 100_000_000
SCALE_NAMES = {0: "auto", 1: "1.0", 2: "0.7"}  # xrfdc.MIXER_SCALE_*


def low_band_dac(pl, nco_mhz: float):
    """Move the DAC tile 2 output to ``nco_mhz`` for a low-bandwidth scope.

    In operation the datapath is IMR high-pass (fs/4..fs/2) in mix-mode
    (Nyquist zone 2) with the NCO at -1000 MHz: 3.8 GHz and its 5.8 GHz
    image.  Here it is IMR low-pass (0..fs/4) in Nyquist zone 1 with the NCO
    at ``nco_mhz`` (0: coarse mixer bypassed, the DAC outputs the I samples
    and no NCO phase is involved), and the fine-mixer scale is 1.0 instead of the 0.7 that
    "auto" means for C2R (+3 dB; safe because these waveforms have Q = 0).
    The DAC output current cannot be raised instead: VOP needs DAC_AVTT =
    3.0 V (PG269) and the RFSoC 4x2 has 2.5 V.  Whether the latency stays the
    same is not verified, so a delay measured this way is not the RF delay.
    Returns a function that restores the operating settings.
    """
    import xrfdc  # board-only  # pyright: ignore[reportMissingImports]

    block = pl.rfdc.driver.dac_tiles[2].blocks[0]
    mixer = dict(block.MixerSettings)
    saved = (block.DataPathMode, block.IMRPassMode, block.NyquistZone)

    def apply(path_mode: int, imr_mode: int, zone: int, settings: dict) -> None:
        block.DataPathMode = path_mode
        block.IMRPassMode = imr_mode
        block.NyquistZone = zone
        block.MixerSettings = settings
        block.UpdateEvent(xrfdc.EVENT_MIXER)

    # DataPathMode 2 = XRFDC_DATAPATH_MODE_DUC_0_FSDIVFOUR; IMRPassMode 0 = low pass.
    if nco_mhz == 0:
        apply(2, 0, 1, {**mixer, "MixerType": xrfdc.MIXER_TYPE_COARSE,
                        "CoarseMixFreq": xrfdc.COARSE_MIX_BYPASS, "Freq": 0.0})
        print(f"DAC tile 2 for a scope: DataPathMode {block.DataPathMode}, IMRPassMode "
              f"{block.IMRPassMode}, NyquistZone {block.NyquistZone}, mixer bypassed "
              f"(MixerType {block.MixerSettings['MixerType']}, CoarseMixFreq "
              f"{block.MixerSettings['CoarseMixFreq']})")
    else:
        apply(2, 0, 1, {**mixer, "Freq": float(nco_mhz), "FineMixerScale": xrfdc.MIXER_SCALE_1P0})
        print(f"DAC tile 2 for a scope: DataPathMode {block.DataPathMode}, IMRPassMode "
              f"{block.IMRPassMode}, NyquistZone {block.NyquistZone}, NCO {block.MixerSettings['Freq']:.1f} MHz, "
              f"fine-mixer scale {SCALE_NAMES.get(block.MixerSettings['FineMixerScale'], '?')}")
    # "Auto" leaves the scale register as it is, so restore 0.7 explicitly.
    restored = {**mixer, "FineMixerScale": xrfdc.MIXER_SCALE_0P7
                if mixer["FineMixerScale"] == xrfdc.MIXER_SCALE_AUTO else mixer["FineMixerScale"]}
    return lambda: apply(*saved, restored)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    add_common_args(parser)
    add_rfdc_args(parser)
    parser.add_argument("bursts", nargs="?", type=int, default=10)
    parser.add_argument("--offset-ticks", type=int, default=100_000,
                        help="burst start after the PPS, in 10-ns ticks (default 1 ms)")
    parser.add_argument("--amplitude", type=float, default=None,
                        help="waveform amplitude (default: WaveformGenerator's)")
    parser.add_argument("--waveform", choices=("prn", "rect", "square"), default="prn",
                        help="prn: PRN+RRC burst; rect: constant I; square: 25 MHz square wave in I "
                             "(same length; sharp start, use with --scope-nco 0)")
    parser.add_argument("--scope-nco", type=float, default=None, metavar="MHZ",
                        help="put the DAC output at MHZ for a low-bandwidth scope (restored at exit); "
                             "0 bypasses the mixer")
    parser.add_argument("--log", default="m2_runs.jsonl", help="append one JSON record per run")
    args = parser.parse_args()

    log = RunLog(args.log, bitstream=args.bitstream, offset_ticks=args.offset_ticks)
    pl = open_pl(args, log)
    if pl is None:
        return 1

    gen = WaveformGenerator() if args.amplitude is None else WaveformGenerator(amplitude=args.amplitude)
    beats = gen.generate_beats()
    log.record["waveform"] = {"config_id": gen.config_id, "chips": gen.chip_count,
                              "amplitude": gen.amplitude, "beats": len(beats)}
    if args.waveform in ("rect", "square"):
        level = int(min(gen.amplitude, 32767))
        n = len(beats) * 8
        if args.waveform == "rect":
            samples = [level] * n
        else:  # 25 MHz: +level for 2 beats (20 ns), -level for 2 beats
            samples = [level if (k // 16) % 2 == 0 else -level for k in range(n)]
        beats = BeatPacker(TX_LAYOUT).pack_samples([(v, 0) for v in samples])
        log.record["waveform"] = {args.waveform + "_level": level, "beats": len(beats)}
    if args.offset_ticks < 0 or args.offset_ticks + len(beats) >= PPS_PERIOD:
        print("offset must be >= 0 and the burst must end before the next PPS")
        return 2
    t_load = time.monotonic()
    pl.tx_buffer.write_beats(beats)
    mark(f"TX buffer loaded: {len(beats)} beats ({len(beats) * 10 / 1000:.2f} us) "
         f"in {(time.monotonic() - t_load) * 1e3:.1f} ms")

    ctrl = pl.ctrl
    seq0 = ctrl.read32(R.TX_SEQ)
    print(f"{'n':>4} {'PPS_COUNT':>9} {'target':>16} {'actual-target':>13} {'actual-PPS':>10} {'TX_SEQ':>6}")
    deltas, rels, bad, missed = set(), set(), 0, 0
    restore = None
    if args.scope_nco is not None:
        log.record["scope_nco_mhz"] = args.scope_nco
        restore = low_band_dac(pl, args.scope_nco)
    try:
        count0, pps0 = ctrl.wait_next_pps()              # start just after a PPS edge
        for n in range(args.bursts):
            # Every PPS: the burst follows the PPS after pps0, and that PPS is
            # pps0 for the next burst (the burst ends well before the one after).
            target = pps0 + PPS_PERIOD + args.offset_ticks
            try:
                ctrl.arm_tx(target, len(beats))          # TX_TARGET, TX_LENGTH, doorbell
            except CommandRejected as exc:
                if exc.result != abi.ResultCode.REJ_STALE:
                    raise
                missed += 1                              # this process was too late: skip a PPS
                print(f"{n:4d} {'':9s} {target:16d}  arm too late (REJ_STALE), burst missed", flush=True)
                count0, pps0 = ctrl.wait_next_pps()
                continue
            try:
                actual = ctrl.wait_tx(timeout=2.5)       # TX_ACTUAL
            except TargetMissed as exc:
                actual = exc.actual
            count, pps = ctrl.latest_pps()
            delta, rel = actual - target, actual - pps
            good = delta == 0 and rel == args.offset_ticks and count == count0 + 1
            bad += not good
            deltas.add(delta)
            rels.add(rel)
            print(f"{n:4d} {count:9d} {target:16d} {delta:13d} {rel:10d} "
                  f"{ctrl.read32(R.TX_SEQ):6d}{'' if good else '  <-- unexpected'}", flush=True)
            count0, pps0 = count, pps
    finally:
        if restore is not None:
            restore()
            print("DAC tile 2 back to its operating settings (fine-mixer scale "
                  f"{SCALE_NAMES.get(pl.rfdc.driver.dac_tiles[2].blocks[0].MixerSettings['FineMixerScale'], '?')})")

    st = ctrl.status()
    fault_bits = ctrl.read32(R.FAULT_BITS)
    seq_done = ctrl.read32(R.TX_SEQ) - seq0
    mts = pl.rfdc.mts
    passed = (mts.dac.ok and mts.adc.ok and bad == 0 and seq_done == args.bursts - missed
              and fault_bits == 0 and st.tx_state == abi.TxState.DONE)
    log.record.update({"bursts": args.bursts, "bursts_done": seq_done, "bursts_missed": missed,
                       "actual_minus_target": sorted(deltas), "actual_minus_pps": sorted(rels),
                       "fault_bits": fault_bits})
    print("summary:")
    print(f"  {mts.dac.describe()}")
    print(f"  {mts.adc.describe()}")
    print(f"  bursts done           {seq_done} of {args.bursts}"
          + (f" ({missed} missed: armed too late)" if missed else ""))
    print(f"  TX_ACTUAL - target    {sorted(deltas)}")
    print(f"  TX_ACTUAL - PPS_TICK  {sorted(rels)} (expected {args.offset_ticks})")
    print(f"  FAULT_BITS            0x{fault_bits:x}")
    print("M2 digital:", "PASS" if passed else "FAIL")
    log.write(passed)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
