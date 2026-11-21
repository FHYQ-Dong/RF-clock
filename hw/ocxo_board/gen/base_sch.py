"""The parts of the integrated OCXO board around its DAC section, for gen_sch.py: the 12 V input and its
protection, the buck converter to 5.6 V, the OCXO's 5 V regulator U2, the 3.3 V regulators U3 (+3V3_DAC) and U4
(+3V3_CLK), the tuning filter from DAC_OUT to the OCXO's tuning pin, the OCXO, and the 10 MHz buffer and its two
SMA outputs. gen_sch.py adds the DAC section and the SPI input from the RFSoC 4x2.

Positions are on a 2.54 mm grid, written as grid units through g(x, y).
"""

from __future__ import annotations

from schematic import Schematic, make_symbol

NAME = "ocxo_board"
U = 2.54

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
C1210 = "Capacitor_SMD:C_1210_3225Metric"
LED0603 = "LED_SMD:LED_0603_1608Metric"
TP_PAD = "TestPoint:TestPoint_Pad_D1.5mm"
TP_LOOP = "TestPoint:TestPoint_Keystone_5000-5004_Miniature"


def g(x: float, y: float) -> tuple[float, float]:
    return x * U, y * U


# TPS7A8400A in the RGR package (VQFN-20, TI SBVS267). Pin 21 is the thermal pad. Only pin 1 of the three
# OUT pins is a power output, so that ERC does not see three power outputs driving one net.
TPS7A84 = make_symbol(
    f"{NAME}:TPS7A8400A", "U", "TPS7A8400A", "Package_DFN_QFN:QFN-20-1EP_3.5x3.5mm_P0.5mm_EP2x2mm_ThermalVias",
    "https://www.ti.com/lit/ds/symlink/tps7a84a.pdf", "3 A low-noise LDO, adjustable, VQFN-20 (RGR)",
    (-10.16, 10.16, 10.16, -17.78),
    [("15", "IN", "power_in", -12.7, 7.62, 0), ("16", "IN", "power_in", -12.7, 5.08, 0),
     ("17", "IN", "power_in", -12.7, 2.54, 0), ("14", "EN", "input", -12.7, 0, 0),
     ("13", "NR_SS", "passive", -12.7, -2.54, 0), ("12", "BIAS", "input", -12.7, -5.08, 0),
     ("5", "50mV", "passive", -12.7, -7.62, 0), ("6", "100mV", "passive", -12.7, -10.16, 0),
     ("7", "200mV", "passive", -12.7, -12.7, 0),
     ("1", "OUT", "power_out", 12.7, 7.62, 180), ("19", "OUT", "passive", 12.7, 5.08, 180),
     ("20", "OUT", "passive", 12.7, 2.54, 180), ("2", "SNS", "input", 12.7, 0, 180),
     ("3", "FB", "input", 12.7, -2.54, 180), ("4", "PG", "open_collector", 12.7, -5.08, 180),
     ("9", "400mV", "passive", 12.7, -7.62, 180), ("10", "800mV", "passive", 12.7, -10.16, 180),
     ("11", "1.6V", "passive", 12.7, -12.7, 180),
     ("8", "GND", "power_in", -2.54, -20.32, 90), ("18", "GND", "power_in", 0, -20.32, 90),
     ("21", "EP", "power_in", 2.54, -20.32, 90)])

# Taitien NI-10M-3600 series double-oven OCXO (data sheet XO-0238 Rev 3, outline on page 4).
OCXO = make_symbol(
    f"{NAME}:NI-10M-3600", "X", "NI-10M-3660", f"{NAME}:OCXO_Taitien_NI-10M-3600",
    "https://www.taitien.com/wp-content/uploads/2026/07/NI-10M-3600.pdf",
    "10 MHz double-oven OCXO, CMOS output, 5 V, EFC 0 to 5 V",
    (-7.62, 7.62, 7.62, -7.62),
    [("1", "Vcon", "input", -10.16, 2.54, 0), ("2", "NC", "no_connect", -10.16, -2.54, 0),
     ("4", "OUT", "output", 10.16, 2.54, 180), ("3", "+VDC", "power_in", 0, 10.16, 270),
     ("5", "GND", "power_in", 0, -10.16, 90)])


def build(name: str, title: str, comments: tuple[str, str]) -> Schematic:
    """The schematic with these parts, R8 fed by the DAC section's output DAC_OUT, which gen_sch.py adds."""
    Schematic.custom = {TPS7A84[1]: TPS7A84, OCXO[1]: OCXO}
    s = Schematic(name, title, "0.1", "2026-11-11", list(comments))
    p = s.place

    def hang(ref: str, x: float, rail_y: float, value: str, fp: str, bottom: str | None = "GND",
             lib: str = "Device:C", left: bool = False):
        """A two-pin part whose pin 1 sits on a horizontal rail at rail_y and whose pin 2 goes to ``bottom``."""
        at = {"ref_at": (-2.54, -1.27, "right"), "value_at": (-2.54, 1.27, "right")} if left else {}
        part = p(ref, lib, x * U, rail_y * U + 3.81, value=value, fp=fp, ref_at=at.get("ref_at"),
                 value_at=at.get("value_at"))
        if bottom:
            s.net(part["2"], bottom)
        return part

    def rail(y, *xs, net=None, flag_at=None):
        """A horizontal wire through the points (x, y), with the net's power symbol at its right end."""
        pts = sorted(set(xs) | ({flag_at} if flag_at else set()))
        s.wire(*(g(x, y) for x in pts))
        if net:
            s.power(*g(pts[-1], y), net, 90)
        if flag_at:
            s.flag(*g(flag_at, y))

    # ------------------------------------------------------------ 12 V input and the buck converter
    s.text(*g(6, 10), "12 V input and buck converter to 5.64 V")
    j1 = p("J1", "Connector:Barrel_Jack_Switch", *g(8, 17), value="12 V 2 A, floating",
           fp="Connector_BarrelJack:BarrelJack_Horizontal")
    f1 = p("F1", "Device:Polyfuse", *g(16, 16), 90, value="2 A hold", fp="Fuse:Fuse_1812_4532Metric")
    d2 = p("D2", "Diode:SMAJ15A", *g(21, 19.5), 270, value="SMAJ15A", ref_at=(3.5, -1.27), value_at=(5.5, 1.27))
    d1 = p("D1", "Diode:SS34", *g(26, 16), 180, value="SS34", ref_at=(0, -2.54), value_at=(0, 2.54))
    s.join(j1["1"], f1["1"])
    s.join(f1["2"], g(21, 16), d1["2"])
    s.join(d2["1"], g(21, 16))
    s.net(d2["2"], "GND")
    s.nc(j1["3"])
    s.net(j1["2"], "GND", path=((2 * U, 0), (0, 2 * U)))
    hang("C1", 31, 16, "22u 25V", C1206)
    hang("C2", 36, 16, "10u 25V", C1206)
    hang("C3", 41, 16, "100n 25V", C0603)
    rail(16, 27.5, 31, 36, 41, 48, net="+12V", flag_at=45)

    u1 = p("U1", "Regulator_Switching:TPS563208", *g(16, 30))
    s.net(u1["3"], "+12V", path=((-2 * U, 0), (0, -2 * U)))
    s.net(u1["5"], "BUCK_EN")
    s.net(u1["1"], "GND")
    s.net(u1["6"], "BUCK_BST")
    s.net(u1["4"], "BUCK_FB")
    r3 = p("R3", "Device:R", *g(8, 36), 90, value="10k", fp=R0603)
    s.net(r3["1"], "+12V", path=((0, -U),))
    s.net(r3["2"], "BUCK_EN")
    c4 = p("C4", "Device:C", *g(24, 36), 90, value="100n", fp=C0603)
    s.net(c4["1"], "BUCK_BST")
    s.net(c4["2"], "BUCK_SW")
    l1 = p("L1", "Device:L", *g(26, 29), 90, value="3.3u", fp="Inductor_SMD:L_Bourns_SRP5030T")
    s.join(u1["2"], g(22, 29), l1["1"])
    s.label(*g(22, 29), "BUCK_SW", 90)
    hang("C5", 31, 29, "22u 16V", C1206)
    hang("C6", 36, 29, "22u 16V", C1206)
    r1 = hang("R1", 41, 29, "63.4k 1%", R0603, bottom=None, lib="Device:R")
    r2 = p("R2", "Device:R", *g(41, 35), value="10k 1%", fp=R0603)
    s.join(r1["2"], g(41, 33), r2["1"])
    s.label(*g(41, 33), "BUCK_FB", 180)
    s.net(r2["2"], "GND")
    p("FB1", "Device:FerriteBead_Small", *g(47, 29), 90, value="470R 4A", fp="Inductor_SMD:L_1206_3216Metric")
    rail(29, 27.5, 29, 31, 36, 41, 46, flag_at=44)
    s.label(*g(29, 29), "+5V6", 0)
    hang("C7", 51, 29, "22u 16V", C1206)
    rail(29, 48, 51, 58, net="+5V6_F", flag_at=54)
    s.text(*g(6, 42), "R1, R2 set 0.768 V x (1 + R1/R2) = 5.64 V. The TPS563208 switches at a fixed 580 kHz.",
           1.27, False)
    s.text(*g(6, 43.5), "L1 saturates above 4 A, more than the peak current of about 2.7 A.", 1.27, False)

    # ------------------------------------------------------------ the OCXO's 5 V
    s.text(*g(62, 10), "OCXO supply 4.985 V, 3 A")
    u2 = p("U2", TPS7A84[1], *g(80, 22))
    hang("C8", 68, 19, "22u 16V", C1206)
    rail(19, 64, 68, 75)
    s.power(*g(64, 19), "+5V6_F", 90)
    s.join(u2["15"], u2["16"], u2["17"], u2["14"])
    c9 = p("C9", "Device:C", *g(70, 24.5), value="100n", fp=C0603)
    s.join(u2["13"], c9["1"])
    s.net(c9["2"], "GND")
    s.net(u2["12"], "GND", path=((-U, 0), (0, U)))
    for n in ("5", "6", "7", "9", "10", "11", "2"):
        s.nc(u2[n])
    s.join(u2["1"], u2["19"], u2["20"])
    s.net(u2["3"], "LDO_FB")
    s.net(u2["4"], "LDO_PG")
    s.join(u2["8"], u2["18"], u2["21"])
    s.net(u2["18"], "GND", mark=False)
    hang("C11", 89, 19, "47u 16V", C1210)
    hang("C12", 94, 19, "10u 10V", C0805)
    hang("C13", 99, 19, "10u 10V", C0805)
    r4 = hang("R4", 104, 19, "12.4k", R0603, bottom=None, lib="Device:R")
    c10 = hang("C10", 108, 19, "10n", C0603, bottom=None)
    r5 = p("R5", "Device:R", *g(104, 26.5), value="2.37k", fp=R0603)
    s.join(r4["2"], g(104, 24), r5["1"])
    s.join(c10["2"], g(108, 24), g(104, 24))
    s.label(*g(104, 24), "LDO_FB", 180)
    s.net(r5["2"], "GND")
    hang("R6", 112, 19, "10k", R0603, bottom="LDO_PG", lib="Device:R")
    r7 = hang("R7", 116, 19, "2.2k", R0603, bottom=None, lib="Device:R")
    d3 = p("D3", "Device:LED", *g(116, 26.5), 90, value="green", fp=LED0603, ref_at=(4.5, -1.27), value_at=(5.5, 1.27))
    s.join(r7["2"], d3["2"])
    s.net(d3["1"], "GND")
    rail(19, 85, 89, 94, 99, 104, 108, 112, 116, 120, net="+5V_OCXO")
    s.text(*g(62, 34), "R4, R5 set 0.8 V x (1 + R4/R5) = 4.985 V. R4, R5 are 0.1 %, 25 ppm/K.", 1.27, False)
    s.text(*g(62, 35.5), "The ANY-OUT pins and SNS stay open. BIAS goes to ground (TI SBVS291C, table 5-1).", 1.27, False)
    s.text(*g(62, 37), "OUT needs at least 25 uF and IN at least 5 uF of ceramic capacity at their DC voltage.", 1.27, False)

    # ------------------------------------------------------------ 3.3 V for the DAC section and the clock buffer
    s.text(*g(126, 10), "3.3 V for the DAC section and for the clock buffer")
    u3 = p("U3", "Regulator_Linear:LP38693MP-3.3", *g(140, 18))
    hang("C14", 132, 17, "10u 10V", C0805, left=True)
    rail(17, 128, 132, 136)
    s.power(*g(128, 17), "+5V6_F", 90)
    s.join(u3["4"], u3["1"])
    s.nc(u3["2"])
    s.net(u3["5"], "GND")
    hang("C15", 147, 17, "22u 10V", C1206)
    hang("C16", 152, 17, "100n", C0603)
    r9 = hang("R9", 156, 17, "1k", R0603, bottom=None, lib="Device:R")
    d5 = p("D5", "Device:LED", *g(156, 24.5), 90, value="green", fp=LED0603, ref_at=(-4, -1.27), value_at=(-5, 1.27))
    s.join(r9["2"], d5["2"])
    s.net(d5["1"], "GND")
    rail(17, 144, 147, 152, 156, 159, net="+3V3_DAC")
    u4 = p("U4", "Regulator_Linear:TPS7A20xxxDBV", *g(140, 33), value="TPS7A2033PDBV")
    hang("C17", 132, 32, "1u 10V", C0603, left=True)
    rail(32, 128, 132, 137)
    s.power(*g(128, 32), "+5V6_F", 90)
    s.join(u4["1"], u4["3"])
    s.nc(u4["4"])
    s.net(u4["2"], "GND")
    hang("C18", 147, 32, "1u 10V", C0603)
    hang("C19", 152, 32, "100n", C0603)
    rail(32, 143, 147, 152, 156, net="+3V3_CLK")

    # ------------------------------------------------------------ tuning voltage and the OCXO
    s.text(*g(78, 50), "Tuning voltage from the DAC (DAC_OUT) and the OCXO")
    r8 = p("R8", "Device:R", *g(87, 58), 90, value="100 0.1%", fp=R0603)
    s.net(r8["1"], "DAC_OUT")
    c21 = p("C21", "Device:C", *g(91, 61.5), value="1u C0G", fp=C1210)
    c22 = p("C22", "Device:C", *g(96, 61.5), value="100n C0G", fp=C0805)
    s.net(c21["2"], "GND")
    s.net(c22["2"], "GND")
    d4 = p("D4", "Diode:BAV99", *g(101, 69), 90, value="BAV199", ref_at=(-3.81, -1.27), value_at=(-5.08, 1.27))
    s.net(d4["2"], "+5V_OCXO")
    s.net(d4["1"], "GND")
    tp6 = p("TP6", "Connector:TestPoint", *g(106, 58), value="VCON", fp=TP_PAD, hide_value=True)
    x1 = p("X1", OCXO[1], *g(116, 59), ref_at=(2.54, 10.5, "left"), value_at=(2.54, 12.5, "left"))
    s.join(r8["2"], g(91, 58), g(96, 58), g(103, 58), tp6["1"], g(108, 58), x1["1"])
    s.join(c21["1"], g(91, 58))
    s.join(c22["1"], g(96, 58))
    s.join(d4["3"], g(103, 58))
    s.label(*g(108, 58), "VCON", 0)
    s.nc(x1["2"])
    s.net(x1["3"], "+5V_OCXO")
    s.net(x1["5"], "GND")
    hang("C23", 112, 68, "10u 10V", C0805)
    hang("C24", 117, 68, "100n", C0603)
    rail(68, 108, 112, 117)
    s.power(*g(108, 68), "+5V_OCXO", 90)
    s.text(*g(78, 76), "R8 is thin film, 25 ppm/K.", 1.27, False)
    s.text(*g(78, 77.5), "R8 with C21 and C22 is a low-pass at 1.45 kHz. C21, C22 are C0G.", 1.27, False)
    s.text(*g(78, 79), "The BAV199 leaks a few pA. A Schottky clamp would leak about 1 uA, 100 uV on R8.",
           1.27, False)

    # ------------------------------------------------------------ 10 MHz buffer and outputs
    s.text(*g(126, 50), "10 MHz buffer and outputs")
    r17 = p("R17", "Device:R", *g(129, 58), 90, value="0", fp=R0603)
    s.join(x1["4"], r17["1"])
    u5a = p("U5", "74xGxx:74LVC2G34", *g(139, 58), unit=1, fp="Package_TO_SOT_SMD:SOT-363_SC-70-6")
    u5b = p("U5", "74xGxx:74LVC2G34", *g(139, 66), unit=2, fp="Package_TO_SOT_SMD:SOT-363_SC-70-6")
    u5c = p("U5", "74xGxx:74LVC2G34", *g(130, 80), unit=3, fp="Package_TO_SOT_SMD:SOT-363_SC-70-6",
            ref_at=(5.08, -1.27, "left"), value_at=(5.08, 1.27, "left"))
    s.join(r17["2"], g(132, 58), u5a["1"])
    s.join(u5b["3"], g(132, 66), g(132, 58))
    for unit, out, y, (rr, cc, jj) in ((u5a, "6", 58, ("R18", "C25", "J6")), (u5b, "4", 66, ("R19", "C26", "J7"))):
        r = p(rr, "Device:R", *g(147.5, y), 90, value="33", fp=R0603)
        c = p(cc, "Device:C", *g(152.5, y), 90, value="100n", fp=C0603)
        j = p(jj, "Connector:Conn_Coaxial", *g(158, y), value="SMA", fp="Connector_Coaxial:SMA_Amphenol_132289_EdgeMount",
                 hide_value=True)
        s.join(unit[out], r["1"])
        s.join(r["2"], c["1"])
        s.join(c["2"], j["1"])
        s.net(j["2"], "GND")
    s.net(u5c["5"], "+3V3_CLK")
    s.net(u5c["2"], "GND")
    c27 = p("C27", "Device:C", *g(141, 80), value="100n", fp=C0603)
    s.net(c27["1"], "+3V3_CLK")
    s.net(c27["2"], "GND")
    s.text(*g(126, 88), "J6 (SMA) to the 4x2's CLK IN (J14), J7 (SMA) to the scope or White Rabbit.", 1.27, False)
    s.text(*g(126, 89.5), "J7 into 50 ohm only. Into a high-impedance input", 1.27, False)
    s.text(*g(126, 91), "the buffer would drive 34 to 42 mA at each edge.", 1.27, False)
    s.text(*g(126, 92.5), "The 74LVC2G34's inputs take 5 V at a 3.3 V supply.", 1.27, False)

    # ------------------------------------------------------------ mounting holes, test points, ground flag
    s.text(*g(6, 98), "Mounting holes and test points")
    for i in range(4):  # plain holes, so that a metal base plate cannot join the board's ground a second time
        p(f"H{i + 1}", "Mechanical:MountingHole", *g(10 + 5 * i, 102), value="M3",
          fp="MountingHole:MountingHole_3.2mm_M3")
    for i, net in enumerate(("+12V", "+5V6_F", "+5V_OCXO", "+3V3_DAC", "+3V3_CLK", "LDO_PG")):
        ref = f"TP{i + 1 if i < 5 else 7}"
        tp = p(ref, "Connector:TestPoint", *g(36 + 8 * i, 104), 180, value=net, fp=TP_PAD, hide_value=True,
               ref_at=(3.81, 3.0))
        s.net(tp["1"], net)
    for i, ref in enumerate(("TP8", "TP9")):
        tp = p(ref, "Connector:TestPoint", *g(86 + 6 * i, 102), value="GND loop", fp=TP_LOOP, hide_value=True)
        s.net(tp["1"], "GND")
    s.flag(*g(100, 104))
    s.power(*g(100, 104), "GND", 270)
    return s
