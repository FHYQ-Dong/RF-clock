"""Manufacturer part numbers (MPN) and LCSC numbers for the parts in base_sch.py, by value and footprint.

gen_sch.py writes them into each symbol's MPN and LCSC fields, and fab.py puts them into the JLCPCB BOM. Every
part JLCPCB places has the LCSC number of a part checked in stock in JLCPCB's parts library on 2026-11-20,
so that the BOM tool matches each line exactly instead of guessing from the value. Where the library had no
stock of the part first chosen, an equivalent with the same footprint replaced it, and the note says so. Parts
without an LCSC number are bought separately and soldered by hand (fab.py's HAND list).
"""

from __future__ import annotations

# (value, footprint name) -> (MPN, LCSC number, note)
PARTS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("10k", "R_0603_1608Metric"): ("0603WAF1002T5E", "C25804", ""),
    ("10k 1%", "R_0603_1608Metric"): ("0603WAF1002T5E", "C25804", ""),
    ("63.4k 1%", "R_0603_1608Metric"): ("FRC0603F6342TS", "C2999593", ""),
    ("12.4k", "R_0603_1608Metric"): ("RT0603BRD0712K4L", "C861102", "0.1 %, 25 ppm/K thin film"),
    ("2.37k", "R_0603_1608Metric"): ("RT0603BRD072K37L", "C705759", "0.1 %, 25 ppm/K thin film"),
    ("100 0.1%", "R_0603_1608Metric"): ("TNPW0603100RBEEA", "C844889",
                                        "0.1 %, 25 ppm/K thin film (Yageo RT0603BRD07100RL not stocked)"),
    ("2.2k", "R_0603_1608Metric"): ("0603WAF2201T5E", "C4190", ""),
    ("1k", "R_0603_1608Metric"): ("0603WAF1001T5E", "C21190", ""),
    ("33", "R_0603_1608Metric"): ("0603WAF330JT5E", "C23140", ""),
    ("0", "R_0603_1608Metric"): ("0603WAF0000T5E", "C21189", ""),
    ("22u 25V", "C_1206_3216Metric"): ("CL31A226KAHNNNE", "C12891", "X5R 25 V"),
    ("22u 16V", "C_1206_3216Metric"): ("CL31A226KAHNNNE", "C12891", "X5R 25 V"),
    ("22u 10V", "C_1206_3216Metric"): ("CL31A226KAHNNNE", "C12891", "X5R 25 V"),
    ("10u 25V", "C_1206_3216Metric"): ("CL31A106KBHNNNE", "C13585", "X5R 50 V"),
    ("100n 25V", "C_0603_1608Metric"): ("CC0603KRX7R9BB104", "C14663", "X7R 50 V"),
    ("100n", "C_0603_1608Metric"): ("CC0603KRX7R9BB104", "C14663", "X7R 50 V"),
    ("10u 10V", "C_0805_2012Metric"): ("CL21A106KAYNNNE", "C15850", "X5R 25 V"),
    ("47u 16V", "C_1210_3225Metric"): ("CL32A476MOJNNNE", "C3039702",
                                       "X5R, check: OUT of U2 needs 25 uF at 5 V (Murata GRM32ER61C476ME15L short)"),
    ("1u 10V", "C_0603_1608Metric"): ("CL10A105KB8NNNC", "C15849", "X5R 50 V"),
    ("10n", "C_0603_1608Metric"): ("0603B103K500NT", "C57112", "X7R 50 V"),
    ("1u C0G", "C_1210_3225Metric"): ("C3225C0G1E105J250AC", "",
                                      "C0G, not in JLCPCB's library, so bought separately and soldered by hand"),
    ("100n C0G", "C_0805_2012Metric"): ("GRM21B5C1H104JA01L", "",
                                        "C0G, not in JLCPCB's library, so bought separately and soldered by hand"),
    ("3.3u", "L_Bourns_SRP5030T"): ("SRP5030T-3R3M", "C189931", "Isat 8 A"),
    ("470R 4A", "L_1206_3216Metric"): ("BLM31KN471SN1L", "C703117",
                                       "rated 4 A (BLM31KN601SN1L, 600 ohm 2.9 A, not stocked)"),
    ("2 A hold", "Fuse_1812_4532Metric"): ("ASMD1812-200-30V", "C2982282", "2 A hold, 30 V"),
    ("SS34", "D_SMA"): ("SS34", "C8678", ""),
    ("SMAJ15A", "D_SMA"): ("SMAJ15A", "C5353166", "unidirectional"),
    ("green", "LED_0603_1608Metric"): ("19-217/G7C-AN1P2/6T", "C2986030", "any 0603 green LED"),
    ("BAV199", "SOT-23"): ("BAV199,215", "C40919", "Nexperia, low-leakage double diode"),
    ("TPS563208", "SOT-23-6"): ("TPS563208DDCR", "C468250", "Texas Instruments"),
    ("TPS7A8400A", "QFN-20-1EP_3.5x3.5mm_P0.5mm_EP2x2mm_ThermalVias"): ("TPS7A8400ARGRR", "C524802", ""),
    ("LP38693MP-3.3", "SOT-223-5"): ("LP38693MP-3.3/NOPB", "C130904", "Texas Instruments"),
    ("TPS7A2033PDBV", "SOT-23-5"): ("TPS7A2033PDBVR", "C2862740", "Texas Instruments"),
    ("74LVC2G34", "SOT-363_SC-70-6"): ("74LVC2G34GW,125", "C426709", "Nexperia"),
    ("12 V 2 A, floating", "BarrelJack_Horizontal"): ("DC-005-A200", "C720557",
                                                     "check: pins match KiCad BarrelJack_Horizontal, 5.5x2.1 mm"),
    ("SMA", "SMA_Amphenol_132289_EdgeMount"): ("132289", "",
                                               "Amphenol edge-mount SMA, not stocked by JLCPCB, so soldered by hand"),
    ("NI-10M-3660", "OCXO_Taitien_NI-10M-3600"): ("NI-10M-3660", "", "Taitien, bought separately"),
    ("GND loop", "TestPoint_Keystone_5000-5004_Miniature"): ("5001", "", "Keystone, black"),
}


def lookup(value: str, footprint: str) -> tuple[str, str, str]:
    return PARTS.get((value, footprint.split(":")[-1]), ("", "", ""))
