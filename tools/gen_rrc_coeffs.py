# Python script to generate RRC FIR coefficients
# Using the following RRC parameters:
#   - RRC beta: 0.25
#   - RRC span: 10
#   - Sample per symbol: 8
#   - FIR tap: 81 (10 * 8 + 1)
#   - Coefficients: float64, stored in config/RRC_config.db for the ARM waveform generator

from rf_clock.generate_rrc_coefficients import generate_rrc_coefficients
from pathlib import Path
import hashlib
import sqlite3


# Configs
RRC_SAMPLES_PER_SYMBOL = 8
RRC_SPAN_CHIPS = 10
RRC_TAP_COUNT = RRC_SAMPLES_PER_SYMBOL * RRC_SPAN_CHIPS + 1
RRC_TAIL_SAMPLES = RRC_TAP_COUNT - RRC_SAMPLES_PER_SYMBOL
RRC_BETA = 0.25

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DATABASE = CONFIG_DIR / "RRC_config.db"


# RRC coefficient generation
RRC_COEFF = generate_rrc_coefficients(
    beta = RRC_BETA,
    span_chips = RRC_SPAN_CHIPS,
    samples_per_symbol = RRC_SAMPLES_PER_SYMBOL,
)

# Validate the coefficients (symmetry is checked by the generator)
if len(RRC_COEFF) != RRC_TAP_COUNT:
    raise ValueError("wrong coefficient count")


# Calculate the config ID using the sha256 hash of the RRC parameters.  The
# float coefficients are not hashed: their last bits may differ between
# machines, while the parameters identify the configuration exactly.
config_text = repr((
    RRC_SAMPLES_PER_SYMBOL,
    RRC_SPAN_CHIPS,
    RRC_TAP_COUNT,
    RRC_TAIL_SAMPLES,
    RRC_BETA,
))
RRC_CONFIG_ID = int(hashlib.sha256(config_text.encode("utf-8")).hexdigest(), 16) % (2**32)


# Save to config database
with sqlite3.connect(CONFIG_DATABASE) as conn:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS RRC_config (
            RRC_CONFIG_ID INTEGER PRIMARY KEY NOT NULL, 
            RRC_SAMPLES_PER_SYMBOL INTEGER NOT NULL,
            RRC_SPAN_CHIPS INTEGER NOT NULL,
            RRC_TAP_COUNT INTEGER NOT NULL,
            RRC_TAIL_SAMPLES INTEGER NOT NULL,
            RRC_BETA REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS RRC_coeff (
            RRC_CONFIG_ID INTEGER NOT NULL,
            TAP_INDEX INTEGER NOT NULL,
            COEFFICIENT REAL NOT NULL,
            
            PRIMARY KEY (RRC_CONFIG_ID, TAP_INDEX),
            FOREIGN KEY (RRC_CONFIG_ID) 
                REFERENCES RRC_config(RRC_CONFIG_ID)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO RRC_config (
            RRC_CONFIG_ID, 
            RRC_SAMPLES_PER_SYMBOL,
            RRC_SPAN_CHIPS,
            RRC_TAP_COUNT,
            RRC_TAIL_SAMPLES,
            RRC_BETA
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (RRC_CONFIG_ID) DO UPDATE SET
            RRC_SAMPLES_PER_SYMBOL = excluded.RRC_SAMPLES_PER_SYMBOL,
            RRC_SPAN_CHIPS = excluded.RRC_SPAN_CHIPS,
            RRC_TAP_COUNT = excluded.RRC_TAP_COUNT,
            RRC_TAIL_SAMPLES = excluded.RRC_TAIL_SAMPLES,
            RRC_BETA = excluded.RRC_BETA
        """, 
        (
            RRC_CONFIG_ID,
            RRC_SAMPLES_PER_SYMBOL,
            RRC_SPAN_CHIPS,
            RRC_TAP_COUNT,
            RRC_TAIL_SAMPLES,
            RRC_BETA
        )
    )
    conn.execute("DELETE FROM RRC_coeff WHERE RRC_CONFIG_ID = ?", (RRC_CONFIG_ID,))
    conn.executemany(
        """
        INSERT INTO RRC_coeff (RRC_CONFIG_ID, TAP_INDEX, COEFFICIENT)
        VALUES (?, ?, ?)
        """, 
        [
            (RRC_CONFIG_ID, i, float(coeff))
            for i, coeff in enumerate(RRC_COEFF)
        ]
    )

print(f"RRC_CONFIG_ID = {RRC_CONFIG_ID} -> {CONFIG_DATABASE}")
