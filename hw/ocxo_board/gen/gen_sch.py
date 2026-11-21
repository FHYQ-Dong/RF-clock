"""Generate the integrated OCXO board's schematic (plan 2), hw/ocxo_board/ocxo_board.kicad_sch.

The DAC section is the EVAL-CN0531-PMDZ's circuit, copied from its schematic (02_058962 Rev B, sheet 2) with
its jumpers set for a 0-5 V output: LT3471 makes +-8 V
from the 3.3 V rail, LT3042 and LT3093 make +-7.5 V, the LTC6655-5 reference is buffered by an AD8676 to the
AD5791's positive reference (the negative reference is ground), and an AD8675 at unity gain (RFB tied to its
output, RBUF = 1) drives the tuning filter directly. The AD5791's SPI comes from the RFSoC 4x2's PL over a 2x6
Pmod cable into J8, through an ISO7741 digital isolator, so that the cable does not join the board's ground to
the RFSoC's a second time. The rest of the board comes from base_sch.py. The custom symbols of both go into
ocxo_board.kicad_sym, and the board's own footprints are in ocxo_board.pretty (footprints.py).

    python hw/ocxo_board/gen/gen_sch.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from base_sch import C0603, C0805, C1206, OCXO, R0603, TP_PAD, TPS7A84, U, build, g
from kisexp import Q, dump, seed
from parts import PARTS, lookup
from schematic import Schematic, add_part_fields, make_symbol

BOARD = Path(__file__).resolve().parent.parent

NAME = "ocxo_board"

# LTC6655-5 in MSOP-8 (MS8), with the same pins as the CN0531's LS8 part.
LTC6655 = make_symbol(
    f"{NAME}:LTC6655-5", "U", "LTC6655BHMS8-5", "Package_SO:MSOP-8_3x3mm_P0.65mm",
    "https://www.analog.com/media/en/technical-documentation/data-sheets/ltc6655.pdf",
    "5 V precision reference, 0.25 ppm noise, 2 ppm/C, MSOP-8", (-7.62, 5.08, 7.62, -7.62),
    [("2", "VIN", "power_in", -10.16, 2.54, 0), ("1", "~{SHDN}", "input", -10.16, -2.54, 0),
     ("7", "VOUT_F", "power_out", 10.16, 2.54, 180), ("6", "VOUT_S", "input", 10.16, 0, 180),
     ("3", "GND", "power_in", -2.54, -10.16, 90), ("4", "GND", "passive", 0, -10.16, 90),
     ("5", "GND", "passive", 2.54, -10.16, 90), ("8", "GND", "passive", 5.08, -10.16, 90)])

# ISO7741 in SOIC-16 wide (DW), TI SLLSEP4K table 4-1. Side 1 (pins 1-8) faces the Pmod cable, side 2 (pins 9-16)
# the board. Channels A to C go from side 1 to side 2, channel D back. EN1 and EN2 enable the outputs when high.
ISO7741 = make_symbol(
    f"{NAME}:ISO7741DW", "U", "ISO7741DW", "Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm",
    "https://www.ti.com/lit/ds/symlink/iso7741.pdf",
    "Quad digital isolator, 3 forward and 1 reverse channel, default output high, SOIC-16 wide",
    (-7.62, 12.7, 7.62, -15.24),
    [("1", "VCC1", "power_in", -10.16, 10.16, 0), ("7", "EN1", "input", -10.16, 7.62, 0),
     ("3", "INA", "input", -10.16, 2.54, 0), ("4", "INB", "input", -10.16, 0, 0),
     ("5", "INC", "input", -10.16, -2.54, 0), ("6", "OUTD", "output", -10.16, -5.08, 0),
     ("2", "GND1", "power_in", -10.16, -10.16, 0), ("8", "GND1", "passive", -10.16, -12.7, 0),
     ("16", "VCC2", "power_in", 10.16, 10.16, 180), ("10", "EN2", "input", 10.16, 7.62, 180),
     ("14", "OUTA", "output", 10.16, 2.54, 180), ("13", "OUTB", "output", 10.16, 0, 180),
     ("12", "OUTC", "output", 10.16, -2.54, 180), ("11", "IND", "input", 10.16, -5.08, 180),
     ("9", "GND2", "power_in", 10.16, -10.16, 180), ("15", "GND2", "passive", 10.16, -12.7, 180)])

PARTS_V2 = {
    ("4.7k", "R_0603_1608Metric"): ("0603WAF4701T5E", "C23162", ""),
    ("105k", "R_0603_1608Metric"): ("0603WAF1053T5E", "C16840", ""),
    ("15k", "R_0603_1608Metric"): ("0603WAF1502T5E", "C22809", ""),
    ("120k", "R_0603_1608Metric"): ("0603WAF1203T5E", "C25808", ""),
    ("200k", "R_0603_1608Metric"): ("0603WAF2003T5E", "C25811", ""),
    ("1.3k", "R_0603_1608Metric"): ("0603WAF1301T5E", "C22767", ""),
    ("1M", "R_0603_1608Metric"): ("0603WAF1004T5E", "C22935", ""),
    ("51k", "R_0603_1608Metric"): ("0603WAF5102T5E", "C23196", ""),
    ("75k", "R_0603_1608Metric"): ("0603WAF7502T5E", "C23242", "sets 7.5 V (100 uA SET current)"),
    ("19.6k", "R_0603_1608Metric"): ("0603WAF1962T5E", "C22898", ""),
    ("470n", "C_0603_1608Metric"): ("CL10B474KA8NNNC", "C1623", "X7R 25 V"),
    ("33p", "C_0603_1608Metric"): ("CL10C330JB8NNNC", "C1663", "C0G"),
    ("75p", "C_0603_1608Metric"): ("0603CG750J500NT", "C1681", "C0G (Samsung CL10C750JB8NNNC short)"),
    ("10p", "C_0603_1608Metric"): ("CL10C100JB8NNNC", "C1634", "C0G"),
    ("4.7u 50V", "C_1206_3216Metric"): ("1206B475K500NT", "C29823", "X7R"),
    ("10u 50V", "C_1206_3216Metric"): ("CL31A106KBHNNNE", "C13585", "X5R (the CN0531 uses X7R)"),
    ("1u 50V", "C_0603_1608Metric"): ("CL10A105KB8NNNC", "C15849", "X5R (the CN0531 uses X7R)"),
    ("10u", "L_Taiyo-Yuden_NR-40xx"): ("MNR4018T100MT", "C18222009",
                                        "MetalLions, same size and rating as the CN0531's NR4018T100M (not stocked)"),
    ("15u", "L_Taiyo-Yuden_NR-40xx"): ("MNR4018T150MT", "C20612490",
                                        "MetalLions, NR4018 size (the CN0531 uses NR4012T150M, same footprint)"),
    ("DFLS120L", "D_PowerDI-123"): ("DFLS120L-7", "C155287", "Diodes Incorporated"),
    ("BAT54", "D_SOD-323"): ("LBAT54HT1G", "C12744", "keeps VDD at or above VCC at power-up, as the CN0531"),
    ("LT3471", "DFN-10-1EP_3x3mm_P0.5mm_EP1.65x2.38mm"): ("LT3471EDD#TRPBF", "C514422", ""),
    ("LT3042xMSE", "MSOP-10-1EP_3x3mm_P0.5mm_EP1.68x1.88mm"): ("LT3042EMSE#TRPBF", "C461518", ""),
    ("LT3093xMSE", "MSOP-12-1EP_3x4.039mm_P0.65mm_EP1.651x2.845mm"): ("LT3093EMSE#PBF", "C670482", ""),
    ("LTC6655BHMS8-5", "MSOP-8_3x3mm_P0.65mm"): ("LTC6655BHMS8-5#PBF", "C689432", "B grade, 2 ppm/C"),
    ("AD8676BRZ", "SOIC-8_3.9x4.9mm_P1.27mm"): ("AD8676BRZ", "C206590", ""),
    ("AD8675ARZ", "SOIC-8_3.9x4.9mm_P1.27mm"): ("AD8675ARZ", "C207408", ""),
    ("AD5791BRUZ", "TSSOP-20_4.4x6.5mm_P0.65mm"): ("AD5791BRUZ", "C578299", ""),
    ("ISO7741DW", "SOIC-16W_7.5x10.3mm_P1.27mm"): ("ISO7741DWR", "C571196",
                                                    "TI, outputs high while the input side is open or unpowered"),
    ("Pmod to RFSoC 4x2 PMOD0", "Pmod_Header_2x06_P2.54mm_Horizontal"): (
        "2x6 male header, 2.54 mm, right angle", "", "soldered by hand"),
}


def lookup_v2(value: str, footprint: str):
    return PARTS_V2.get((value, footprint.split(":")[-1])) or lookup(value, footprint)


def dac(s: Schematic) -> None:
    """The CN0531's circuit, in the space the A2 sheet adds to the right of and below base_sch.py's parts."""
    p = s.place
    L0603, LNR = "Inductor_SMD:L_Taiyo-Yuden_NR-40xx", "Inductor_SMD:L_Taiyo-Yuden_NR-40xx"

    def vert(ref, x, y, value, fp, top, bottom="GND", lib=None):
        lib = lib or ("Device:R" if ref.startswith("R") else "Device:C")
        part = p(ref, lib, x * U, y * U, value=value, fp=fp)
        s.net(part["1"], top)
        s.net(part["2"], bottom)
        return part

    def horiz(ref, x, y, value, fp, left, right, lib=None):
        lib = lib or ("Device:R" if ref.startswith("R") else "Device:C")
        part = p(ref, lib, x * U, y * U, 90, value=value, fp=fp)
        s.net(part["1"], left)
        s.net(part["2"], right)
        return part

    # ------------------------------------------------------------ +-8 V from 3.3 V (LT3471), as the CN0531
    s.text(*g(6, 116), "DAC supplies, copied from the CN0531: LT3471 boost to +8 V and inverter to -8 V from +3V3_DAC")
    u6 = p("U6", "Regulator_Switching:LT3471", *g(30, 135))
    for pin, net in (("8", "+3V3_DAC"), ("9", "SS1"), ("7", "SS2"), ("10", "SW1"), ("6", "SW2"), ("1", "FB_P"),
                     ("2", "VREF_B"), ("3", "VREF_B"), ("4", "FB_N"), ("5", "GND"), ("11", "GND")):
        s.net(u6[pin], net)
    vert("C48", 8, 126, "10u 50V", C1206, "+3V3_DAC")
    horiz("R30", 12, 132, "4.7k", R0603, "+3V3_DAC", "SS1")
    vert("C40", 16, 136, "470n", C0603, "SS1")
    horiz("R31", 12, 142, "4.7k", R0603, "+3V3_DAC", "SS2")
    vert("C41", 16, 146, "470n", C0603, "SS2")
    horiz("L2", 46, 122, "10u", LNR, "+3V3_DAC", "SW1", lib="Device:L")
    d6 = p("D6", "Device:D_Schottky", *g(54, 122), 180, value="DFLS120L", fp="Diode_SMD:D_PowerDI-123",
           ref_at=(0, -2.54), value_at=(0, 2.54))
    s.net(d6["2"], "SW1")
    s.net(d6["1"], "+8V")
    vert("C42", 60, 126, "4.7u 50V", C1206, "+8V")
    horiz("R32", 66, 128, "105k", R0603, "+8V", "FB_P")
    horiz("C43", 66, 131, "33p", C0603, "+8V", "FB_P")
    vert("R33", 72, 132, "15k", R0603, "FB_P")
    vert("C44", 46, 132, "100n", C0603, "VREF_B")
    horiz("R34", 52, 136, "15k", R0603, "VREF_B", "FB_N")
    horiz("R35", 60, 139, "120k", R0603, "FB_N", "-8V")
    horiz("C45", 60, 142, "75p", C0603, "FB_N", "-8V")
    horiz("L3", 46, 148, "15u", LNR, "+3V3_DAC", "SW2", lib="Device:L")
    horiz("C46", 52, 151, "1u 50V", C0603, "SW2", "NX")
    d7 = p("D7", "Device:D_Schottky", *g(56, 155), 90, value="DFLS120L", fp="Diode_SMD:D_PowerDI-123",
           ref_at=(4, -1.27), value_at=(6, 1.27))
    s.net(d7["2"], "NX")   # anode at the switched node, cathode to ground (CN0531 D2)
    s.net(d7["1"], "GND")
    horiz("L4", 62, 151, "15u", LNR, "NX", "-8V", lib="Device:L")
    vert("C47", 70, 153, "10u 50V", C1206, "-8V")
    s.flag(*g(76, 122))
    s.power(*g(76, 122), "+8V", 270)
    s.flag(*g(80, 142))
    s.label(*g(80, 142), "-8V", 0)
    s.text(*g(6, 160), "R32/R33 set +8 V and R34/R35 set -8 V, as on the CN0531. L3, C46, D7 and L4 make the inverter.",
           1.27, False)

    # ------------------------------------------------------------ +7.5 V (LT3042) and -7.5 V (LT3093)
    s.text(*g(84, 116), "+7.5 V (LT3042) and -7.5 V (LT3093), copied from the CN0531")
    u7 = p("U7", "Regulator_Linear:LT3042xMSE", *g(100, 128))
    for pin, net in (("1", "+8V"), ("2", "+8V"), ("3", "+8V"), ("4", "PG_P"), ("5", "ILIM_P"), ("6", "PGFB_P"),
                     ("7", "SET_P"), ("8", "GND"), ("11", "GND"), ("9", "+7V5"), ("10", "+7V5")):
        s.net(u7[pin], net)
    vert("R37", 88, 134, "1.3k", R0603, "ILIM_P")
    vert("R40", 92, 138, "75k", R0603, "SET_P")
    vert("C49", 96, 138, "4.7u 50V", C1206, "SET_P")
    vert("R38", 112, 124, "1M", R0603, "+7V5", "PGFB_P")
    vert("R39", 112, 132, "51k", R0603, "PGFB_P")
    vert("C50", 116, 126, "4.7u 50V", C1206, "+7V5")
    vert("R36", 120, 126, "200k", R0603, "+3V3_DAC", "PG_P")
    u8 = p("U8", "Regulator_Linear:LT3093xMSE", *g(100, 150))
    for pin, net in (("1", "-8V"), ("2", "-8V"), ("13", "-8V"), ("3", "-8V"), ("4", "PG_N"), ("5", "PGFB_N"),
                     ("6", "ILIM_N"), ("8", "SET_N"), ("9", "GND"), ("10", "-7V5"), ("11", "-7V5"), ("12", "-7V5")):
        s.net(u8[pin], net)
    s.nc(u8["7"])
    vert("R44", 88, 156, "19.6k", R0603, "ILIM_N")
    vert("R45", 92, 158, "75k", R0603, "SET_N")
    vert("C51", 96, 158, "4.7u 50V", C1206, "SET_N")
    vert("R42", 112, 146, "1M", R0603, "-7V5", "PGFB_N")
    vert("R43", 112, 154, "51k", R0603, "PGFB_N")
    vert("C52", 116, 148, "4.7u 50V", C1206, "-7V5")
    vert("R41", 120, 148, "200k", R0603, "+3V3_DAC", "PG_N")

    # ------------------------------------------------------------ 5 V reference and its buffers
    s.text(*g(166, 10), "5 V reference (LTC6655-5) and reference buffers (AD8676)")
    u9 = p("U9", LTC6655[1], *g(176, 22))
    s.net(u9["2"], "+7V5")
    s.net(u9["1"], "REF_SHDN")
    for pin in ("3", "4", "5", "8"):
        s.net(u9[pin], "GND")
    s.net(u9["7"], "REF5")
    s.net(u9["6"], "REF5")
    vert("R46", 168, 16, "10k", R0603, "+7V5", "REF_SHDN")
    vert("C53", 188, 26, "10u 50V", C1206, "REF5")
    vert("C54", 168, 30, "100n", C0603, "+7V5")
    u10a = p("U10", "Amplifier_Operational:AD8676xR", *g(206, 22), unit=1, value="AD8676BRZ")
    u10b = p("U10", "Amplifier_Operational:AD8676xR", *g(206, 36), unit=2, value="AD8676BRZ")
    u10c = p("U10", "Amplifier_Operational:AD8676xR", *g(220, 26), unit=3, value="AD8676BRZ")
    s.net(u10a["3"], "REF5")
    s.net(u10a["2"], "VREFP_S")
    s.net(u10a["1"], "VREFP_F")
    s.net(u10b["5"], "GND")  # the negative reference is ground (CN0531 P10 fitted)
    s.net(u10b["6"], "VREFN_S")
    s.net(u10b["7"], "VREFN_F")
    s.net(u10c["8"], "+7V5")
    s.net(u10c["4"], "-7V5")
    vert("C55", 196, 46, "10u 50V", C1206, "+7V5")
    vert("C56", 202, 46, "100n", C0603, "+7V5")
    vert("C57", 208, 46, "100n", C0603, "+7V5", "-7V5")
    vert("C58", 214, 46, "100n", C0603, "-7V5")
    vert("C59", 220, 46, "10u 50V", C1206, "-7V5")

    # ------------------------------------------------------------ the DAC and its output buffer
    s.text(*g(166, 54), "AD5791 (RBUF = 1, unity gain, 0 to 5 V) and the AD8675 output buffer")
    u11 = p("U11", "Analog_DAC:AD5791xRUZ", *g(186, 72), value="AD5791BRUZ")
    for pin, net in (("3", "VREFP_S"), ("4", "VREFP_F"), ("16", "VREFN_F"), ("17", "VREFN_S"), ("1", "DAC_INV"),
                     ("20", "DAC_OUT"), ("2", "DAC_VOUT"), ("5", "+7V5"), ("18", "-7V5"), ("9", "+3V3_DAC"),
                     ("10", "+3V3_DAC"), ("15", "GND"), ("19", "GND"), ("12", "DAC_SDIN"), ("13", "DAC_SCLK"),
                     ("14", "DAC_SYNC"), ("11", "DAC_SDO"), ("8", "DAC_LDAC"), ("7", "DAC_CLR"), ("6", "DAC_RESET")):
        s.net(u11[pin], net)
    u12 = p("U12", "Amplifier_Operational:AD8610xR", *g(216, 74), value="AD8675ARZ")
    s.net(u12["3"], "DAC_VOUT")
    s.net(u12["2"], "DAC_INV")
    s.net(u12["6"], "DAC_OUT")
    s.net(u12["7"], "+7V5")
    s.net(u12["4"], "-7V5")
    for pin in ("1", "5", "8"):
        if pin in u12.pins:
            s.nc(u12[pin])
    horiz("C67", 216, 84, "10p", C0603, "DAC_INV", "DAC_OUT")
    d8 = p("D8", "Device:D_Schottky", *g(170, 92), 180, value="BAT54", fp="Diode_SMD:D_SOD-323",
           ref_at=(0, -2.54), value_at=(0, 2.54))
    s.net(d8["2"], "+3V3_DAC")
    s.net(d8["1"], "+7V5")
    for k, (ref, val, fp, net) in enumerate((("C60", "10u 50V", C1206, "+7V5"), ("C61", "100n", C0603, "+7V5"),
                                             ("C62", "100n", C0603, "-7V5"), ("C63", "10u 50V", C1206, "-7V5"),
                                             ("C64", "10u 10V", C0805, "+3V3_DAC"), ("C65", "100n", C0603, "+3V3_DAC"),
                                             ("C66", "100n", C0603, "+3V3_DAC"), ("C68", "10u 50V", C1206, "+7V5"),
                                             ("C69", "100n", C0603, "+7V5"), ("C70", "100n", C0603, "-7V5"),
                                             ("C71", "10u 50V", C1206, "-7V5"))):
        vert(ref, 168 + 5 * k, 106, val, fp, net)
    vert("C72", 224, 92, "100n", C0603, "+7V5", "-7V5")
    for k, (ref, net, pull) in enumerate((("R24", "DAC_SYNC", "+3V3_DAC"), ("R25", "DAC_RESET", "+3V3_DAC"),
                                          ("R26", "DAC_CLR", "+3V3_DAC"))):
        vert(ref, 196 + 6 * k, 92, "10k", R0603, pull, net)
    vert("R27", 214, 92, "10k", R0603, "DAC_LDAC")
    for k, (ref, net) in enumerate((("TP10", "+7V5"), ("TP11", "-7V5"), ("TP12", "REF5"), ("TP13", "DAC_OUT"))):
        tp = p(ref, "Connector:TestPoint", *g(170 + 8 * k, 120), 180, value=net, fp=TP_PAD, hide_value=True,
               ref_at=(3.81, 3.0))
        s.net(tp["1"], net)
    s.text(*g(166, 128), "Copied from EVAL-CN0531-PMDZ (02_058962 Rev B), with its jumpers set for a 0-5 V output:", 1.27, False)
    s.text(*g(166, 129.5), "positive reference from the LTC6655, negative reference ground, RFB to the buffer output.", 1.27, False)
    s.text(*g(166, 131), "The software sets RBUF = 1 for the 0 to 5 V range. LDAC stays low, CLR and RESET high.", 1.27, False)

    # ------------------------------------------------------------ SPI from the RFSoC 4x2, through the isolator
    s.text(*g(6, 50), "SPI from the RFSoC 4x2's PL (Pmod cable to PMOD0, lower row), through the ISO7741 isolator")
    j8 = p("J8", "Connector_Generic:Conn_02x06_Top_Bottom", *g(12, 66), value="Pmod to RFSoC 4x2 PMOD0",
           fp=f"{NAME}:Pmod_Header_2x06_P2.54mm_Horizontal")
    # The channels and Pmod pins are in the order the pins lie on the board, so that no two tracks cross: J8's
    # pins 7 to 10 under U13's pins 3 to 6, and U13's outputs 14 to 12 towards the AD5791's pins 12 to 14.
    pmod = {"7": "PMOD_SDIN", "8": "PMOD_SCLK", "9": "PMOD_SYNC", "10": "PMOD_SDO", "5": "ISO_GND", "11": "ISO_GND",
            "6": "ISO_3V3", "12": "ISO_3V3"}
    for n in map(str, range(1, 13)):
        if n in pmod:
            s.net(j8[n], pmod[n])
        else:
            s.nc(j8[n])
    u13 = p("U13", f"{NAME}:ISO7741DW", *g(36, 66), value="ISO7741DW")
    for pin, net in (("1", "ISO_3V3"), ("7", "ISO_3V3"), ("2", "ISO_GND"), ("8", "ISO_GND"), ("3", "PMOD_SDIN"),
                     ("4", "PMOD_SCLK"), ("5", "PMOD_SYNC"), ("6", "ISO_SDO"), ("16", "+3V3_DAC"), ("10", "+3V3_DAC"),
                     ("9", "GND"), ("15", "GND"), ("14", "DAC_SDIN"), ("13", "DAC_SCLK"), ("12", "DAC_SYNC"),
                     ("11", "DAC_SDO")):
        s.net(u13[pin], net)
    horiz("R10", 24, 76, "33", R0603, "ISO_SDO", "PMOD_SDO")
    vert("C73", 22, 58, "100n", C0603, "ISO_3V3", "ISO_GND")
    vert("C74", 26, 58, "10u 10V", C0805, "ISO_3V3", "ISO_GND")
    vert("C75", 50, 54, "100n", C0603, "+3V3_DAC")
    for x, net in ((22, "ISO_3V3"), (26, "ISO_GND")):
        s.flag(*g(x, 84))
        s.label(*g(x, 84), net, 0)
    s.text(*g(6, 88), "J8 is wired in Pmod numbering. The upper row's signal pins stay open, because the 4x2 uses", 1.27, False)
    s.text(*g(6, 89.5), "PMOD0_0 for the output PPS's validity line. ISO_3V3 and ISO_GND come from the 4x2 and touch", 1.27, False)
    s.text(*g(6, 91), "nothing else on the board. U13's outputs are high while the 4x2 side is off (SPI idle).", 1.27, False)
    s.text(*g(6, 92.5), "R10 drives the cable from U13. U13 drives the AD5791 over 15 mm of track directly.", 1.27, False)


def main() -> None:
    seed(NAME)
    s = build(name=NAME, title="Integrated OCXO board (plan 2)",
              comments=("Notes in hw/ocxo_board/DESIGN.md", "Generated by gen/gen_sch.py"))
    s.paper = "A2"
    Schematic.custom[LTC6655[1]] = LTC6655
    Schematic.custom[ISO7741[1]] = ISO7741
    dac(s)
    missing = add_part_fields(s, lookup_v2)
    if missing:
        print("no part number for", ", ".join(missing))
    left = s.unconnected()
    if left:
        sys.exit("pins not connected: " + ", ".join(left))
    s.write(BOARD / f"{NAME}.kicad_sch")
    pro = BOARD / f"{NAME}.kicad_pro"
    if not pro.exists():  # gen_pcb.py adds the board's design rules to it later, so a rerun keeps the file
        pro.write_text(json.dumps(
            {"meta": {"filename": f"{NAME}.kicad_pro", "version": 3}, "boards": [], "libraries": {}, "sheets": [],
             "text_variables": {}}, indent=2) + "\n", encoding="utf-8", newline="\n")
    syms = []
    for symbol in (TPS7A84, OCXO, LTC6655, ISO7741):
        sym = copy.deepcopy(symbol)
        sym[1] = Q(symbol[1].split(":")[1])
        syms.append(sym)
    (BOARD / f"{NAME}.kicad_sym").write_text(dump(["kicad_symbol_lib", ["version", 20251024],
                                                   ["generator", Q("gen_sch.py")], *syms]) + "\n",
                                              encoding="utf-8", newline="\n")
    (BOARD / "sym-lib-table").write_text(
        '(sym_lib_table\n\t(version 7)\n'
        f'\t(lib (name "{NAME}") (type "KiCad") (uri "${{KIPRJMOD}}/{NAME}.kicad_sym") (options "") '
        '(descr "Custom symbols of the OCXO board"))\n)\n', encoding="utf-8", newline="\n")
    print(f"wrote {BOARD / (NAME + '.kicad_sch')}")


if __name__ == "__main__":
    main()
