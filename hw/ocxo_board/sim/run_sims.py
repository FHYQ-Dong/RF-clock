"""Run the OCXO board's LTspice simulations and summarise them (hw/ocxo_board/DESIGN.md, Simulations).

1. The tuning voltage path (efc_*.cir with efc_circuit.inc). Step response, small-signal response and noise at
   the OCXO's Vcon, with the LTC6655-5 and AD8675 models from LTspice's library.
2. The 10 MHz output (clock_edge.cir). The edge at the LMK04828 behind the 4x2's CLK IN, the swing at a
   high-impedance scope input, and the buffer's peak current, for three edge times of the 74LVC2G34.

Run on Windows with LTspice XVII installed::

    python hw/ocxo_board/sim/run_sims.py

It writes sim_results.json, and the figure sim_results.png with its Chinese version sim_results_zh.png, next
to this file.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import ltraw

HERE = Path(__file__).resolve().parent
LTSPICE = Path("C:/Program Files/LTC/LTspiceXVII/XVIIx64.exe")
EFC_SLOPE = 6e-8  # smallest tuning slope the OCXO's data sheet allows, fractional frequency per volt
TR_LIST = (0.7e-9, 1.5e-9, 3e-9)  # .step list in clock_edge.cir


def run(deck: str) -> dict[str, float]:
    """Run one deck in batch mode and return its .meas results from the log."""
    subprocess.run([str(LTSPICE), "-b", deck], cwd=HERE, check=True, timeout=600)
    log = (HERE / deck).with_suffix(".log").read_text(encoding="latin-1")
    out = {}
    for m in re.finditer(r"^(\w+):(.*)$", log, re.M):
        when = re.search(r" AT ([-+0-9.e]+)", m.group(2))  # a WHEN result is the time or frequency after AT
        value = when or re.search(r"=\s*\(?([-+0-9.e]+)", m.group(2))  # dB values come as (x dB, phase)
        if value:
            out[m.group(1)] = float(value.group(1))
    return out


def last_period(t: np.ndarray, *ys: np.ndarray, t0: float = 29.8e-6, t1: float = 29.9e-6):
    m = (t >= t0) & (t <= t1)
    return (t[m], *(y[m] for y in ys))


def edge(t: np.ndarray, v: np.ndarray) -> tuple[float, float]:
    """Peak-to-peak swing and the 20 to 80 % rising slope (V/ns) of one period."""
    lo, hi = v.min(), v.max()
    pp = hi - lo
    i20 = np.argmax(v > lo + 0.2 * pp)
    i80 = i20 + np.argmax(v[i20:] > lo + 0.8 * pp)
    t20 = np.interp(lo + 0.2 * pp, v[i20 - 1:i20 + 1], t[i20 - 1:i20 + 1])
    t80 = np.interp(lo + 0.8 * pp, v[i80 - 1:i80 + 1], t[i80 - 1:i80 + 1])
    return pp, 0.6 * pp / ((t80 - t20) * 1e9)


def main() -> None:
    res: dict = {}
    tran = run("efc_tran.cir")
    ac = run("efc_ac.cir")
    noise = run("efc_noise.cir")
    run("clock_edge.cir")
    res["efc"] = {
        "step_settle_99pct_ms": round(tran["settle_99"] * 1e3, 3),
        "step_overshoot_pct": round(tran["overshoot_pct"], 3),
        "corner_hz": round(ac["f3db"], 1),
        "ad8675_output_peaking_db": round(ac["out_peak"], 3),
        "noise_uv_rms": {"0.1-10 Hz": round(noise["vn_0p1_10"] * 1e6, 3), "10 Hz-1 kHz": round(noise["vn_10_1k"] * 1e6, 3),
                         "1 kHz-1 MHz": round(noise["vn_1k_1m"] * 1e6, 3)},
        "noise_0.1-10Hz_fractional_rms": float(f"{noise['vn_0p1_10'] * EFC_SLOPE:.2g}"),
    }

    d, s = ltraw.read(HERE / "clock_edge.raw")
    runs = []
    for k, (a, b) in enumerate(zip(s, s[1:] + [len(d["time"])])):
        t, v, vs, ia, ib = last_period(d["time"][a:b], d["V(lmk)"][a:b], d["V(b4)"][a:b], d["I(Roa)"][a:b],
                                       d["I(Rob)"][a:b])
        pp, slew = edge(t, v)
        runs.append({"edge_ns": TR_LIST[k] * 1e9, "lmk_vpp": round(pp, 3), "lmk_slew_v_per_ns": round(slew, 3),
                     "buffer_peak_ma_4x2": round(np.abs(ia).max() * 1e3, 1), "scope_vpp": round(vs.max() - vs.min(), 3),
                     "buffer_peak_ma_scope": round(np.abs(ib).max() * 1e3, 1), "_t": t, "_v": v, "_vs": vs,
                     "_ia": ia, "_ib": ib})
    res["clock"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in runs]
    (HERE / "sim_results.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(res, indent=1))

    dt, _ = ltraw.read(HERE / "efc_tran.raw")
    da, _ = ltraw.read(HERE / "efc_ac.raw")
    dn, _ = ltraw.read(HERE / "efc_noise.raw")
    for lang in ("en", "zh"):
        plot(lang, dt, da, dn, runs, ac)


TEXT = {
    "en": {"a": "(a) Vcon and the AD8675's output after a 1 mV step of the DAC",
           "a_x": "time from the step (ms)", "a_y": "change from before the step (mV)",
           "vcon": "Vcon", "out": "AD8675 output",
           "b": "(b) Gain from the DAC to Vcon and to the AD8675's output",
           "b_x": "frequency (Hz)", "b_y": "gain (dB)", "b_vcon": "to Vcon, corner {:.0f} Hz", "b_out": "to the AD8675's output",
           "c": "(c) Noise at Vcon, DAC at mid scale (2.5 V)", "c_x": "frequency (Hz)", "c_y": "noise density (nV/√Hz)",
           "d": "(d) Voltage at the LMK04828 behind the 4x2's CLK IN, rising edge", "d_x": "time within one 10 MHz period (ns)",
           "d_y": "voltage (V)", "edge": "buffer edge {:.1f} ns, slope {:.2f} V/ns (needs 0.15)",
           "e": "(e) Voltage at a scope input of 1 MΩ and 15 pF", "e_x": "time within one 10 MHz period (ns)",
           "e_y": "voltage less its mean over the period (V)", "edge2": "buffer edge {:.1f} ns",
           "f": "(f) Buffer output current, 1.5 ns edge", "f_x": "time within one 10 MHz period (ns)",
           "f_y": "current (mA)", "f_4x2": "into the 4x2 (J6)", "f_scope": "into the 1 MΩ scope input"},
    "zh": {"a": "(a) DAC 阶跃 1 mV 后的 Vcon 和 AD8675 输出",
           "a_x": "阶跃之后的时间（ms）", "a_y": "相对阶跃前的变化（mV）",
           "vcon": "Vcon", "out": "AD8675 输出",
           "b": "(b) 从 DAC 到 Vcon 和到 AD8675 输出的增益",
           "b_x": "频率（Hz）", "b_y": "增益（dB）", "b_vcon": "到 Vcon，转折频率 {:.0f} Hz", "b_out": "到 AD8675 输出",
           "c": "(c) Vcon 上的噪声，DAC 在半量程（2.5 V）", "c_x": "频率（Hz）", "c_y": "噪声谱密度（nV/√Hz）",
           "d": "(d) 4x2 的 CLK IN 后面 LMK04828 处的电压，上升沿", "d_x": "一个 10 MHz 周期内的时间（ns）",
           "d_y": "电压（V）", "edge": "缓冲器边沿 {:.1f} ns，斜率 {:.2f} V/ns（要求 0.15）",
           "e": "(e) 1 MΩ 并 15 pF 示波器输入处的电压", "e_x": "一个 10 MHz 周期内的时间（ns）",
           "e_y": "电压减去一个周期内的平均值（V）", "edge2": "缓冲器边沿 {:.1f} ns",
           "f": "(f) 缓冲器输出电流，边沿 1.5 ns", "f_x": "一个 10 MHz 周期内的时间（ns）",
           "f_y": "电流（mA）", "f_4x2": "接 4x2（J6）", "f_scope": "接 1 MΩ 示波器输入"},
}


def plot(lang: str, dt: dict, da: dict, dn: dict, runs: list, ac: dict) -> None:
    """The figure for the design notes, one quantity per panel, in English or Chinese."""
    if lang == "zh":
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    tx = TEXT[lang]
    fig, ax = plt.subplots(3, 2, figsize=(12, 12))
    tt = dt["time"] * 1e3 - 1.0
    m = (tt > -0.1) & (tt < 2)
    before = tt < -0.01
    ax[0, 0].plot(tt[m], (dt["V(vcon)"][m] - dt["V(vcon)"][before][-1]) * 1e3, label=tx["vcon"])
    ax[0, 0].plot(tt[m], (dt["V(out)"][m] - dt["V(out)"][before][-1]) * 1e3, "--", label=tx["out"])
    ax[0, 0].set(xlabel=tx["a_x"], ylabel=tx["a_y"], title=tx["a"])
    ax[0, 0].legend()
    f = da["frequency"].real
    ax[0, 1].semilogx(f, 20 * np.log10(np.abs(da["V(vcon)"])), label=tx["b_vcon"].format(ac["f3db"]))
    ax[0, 1].semilogx(f, 20 * np.log10(np.abs(da["V(out)"])), "--", label=tx["b_out"])
    ax[0, 1].set(xlabel=tx["b_x"], ylabel=tx["b_y"], title=tx["b"], ylim=(-80, 5))
    ax[0, 1].legend(loc="lower left")
    ax[1, 0].loglog(dn["frequency"].real, np.abs(dn["V(onoise)"]) * 1e9)
    ax[1, 0].set(xlabel=tx["c_x"], ylabel=tx["c_y"], title=tx["c"])
    for r in runs:
        tn = (r["_t"] - r["_t"][0]) * 1e9
        ax[1, 1].plot(tn, r["_v"], label=tx["edge"].format(r["edge_ns"], r["lmk_slew_v_per_ns"]))
        ax[2, 0].plot(tn, r["_vs"] - r["_vs"].mean(), label=tx["edge2"].format(r["edge_ns"]))
    r = runs[1]
    tn = (r["_t"] - r["_t"][0]) * 1e9
    ax[2, 1].plot(tn, r["_ia"] * 1e3, label=tx["f_4x2"])
    ax[2, 1].plot(tn, r["_ib"] * 1e3, "--", label=tx["f_scope"])
    ax[1, 1].set(xlabel=tx["d_x"], ylabel=tx["d_y"], title=tx["d"], xlim=(0, 25))
    ax[2, 0].set(xlabel=tx["e_x"], ylabel=tx["e_y"], title=tx["e"])
    ax[2, 1].set(xlabel=tx["f_x"], ylabel=tx["f_y"], title=tx["f"])
    for a in (ax[1, 1], ax[2, 0], ax[2, 1]):
        a.legend(fontsize=9)
    for a in ax.flat:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(HERE / ("sim_results.png" if lang == "en" else "sim_results_zh.png"), dpi=110)
    plt.close(fig)

if __name__ == "__main__":
    main()
