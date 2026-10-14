"""Clock-chip register file tools for the RFSoC 4x2."""

from .lmk04828 import LMKConfig, RegisterFile as LMKRegisterFile, decode_lmk, encode_lmk, load_lmk, make_100mhz_config, validate_sysref_frequency
from .lmx2594 import LMXConfig, RegisterFile as LMXRegisterFile, decode_lmx, encode_lmx, load_lmx

__all__ = ["LMKConfig", "LMKRegisterFile", "decode_lmk", "encode_lmk", "load_lmk",
           "make_100mhz_config", "validate_sysref_frequency", "LMXConfig", "LMXRegisterFile", "decode_lmx", "encode_lmx", "load_lmx"]
