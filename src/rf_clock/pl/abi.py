"""Generated from PL/src/p_regs.vh and p_defs.vh; do not edit."""

from enum import IntEnum, IntFlag

class ABI:
    """ARM-visible register offsets and bit positions from the RTL ABI."""

    class REG:
        ID = 0x0
        VERSION = 0x4
        CONTROL = 0x8
        STATUS = 0xC
        TIME_EPOCH = 0x10
        FAULT_BITS = 0x14
        FAULT_INFO = 0x18
        TICK_LO = 0x20
        TICK_HI = 0x24
        PPS_TICK_LO = 0x28
        PPS_TICK_HI = 0x2C
        PPS_COUNT = 0x30
        PPS_LAST_PERIOD = 0x34
        PPS_JITTER_CNT = 0x38
        PPS_INPUTS = 0x3C
        TX_TARGET_LO = 0x40
        TX_TARGET_HI = 0x44
        TX_LENGTH = 0x48
        TX_RESULT = 0x4C
        TX_SEQ = 0x50
        TX_ACTUAL_LO = 0x54
        TX_ACTUAL_HI = 0x58
        TX_REPEAT = 0x5C
        RX_TARGET_LO = 0x60
        RX_TARGET_HI = 0x64
        RX_LENGTH = 0x68
        RX_RESULT = 0x6C
        RX_SEQ = 0x70
        RX_BEATS = 0x74
        RX_ACTUAL_LO = 0x78
        RX_ACTUAL_HI = 0x7C
        RX_INVALID_CNT = 0x80
        TX_DEPTH = 0x84
        RX_MAX_BEATS = 0x88
        MIN_LEAD_TICKS = 0x8C
        PPS_CONFIG = 0x90
        BUILD_VERSION = 0x94

    class CTRL:
        TX_DOORBELL = 0x0
        TX_CANCEL = 0x1
        RX_DOORBELL = 0x2
        RX_CANCEL = 0x3
        CLEAR_FAULT = 0x4

    class STAT:
        TIME_VALID = 0x0
        CLOCKS_LOCKED = 0x1
        PPS_LOCKED = 0x2
        TICK_EXHAUSTED = 0x3
        ANY_FAULT = 0x4
        DMA_READY = 0x5
        TX_STATE_LSB = 0x8
        RX_STATE_LSB = 0xC

    class PPSCFG:
        SRC_TRIG = 0x0
        INVERT = 0x1

    class PPSIN:
        COMP_LEVEL = 0x0
        TRIG_LEVEL = 0x1
        COMP_EDGES_LSB = 0x8
        TRIG_EDGES_LSB = 0x10

    class BUILDVER:
        ABI_MAJOR_LSB = 0x18
        ABI_MINOR_LSB = 0x10
        BUILD_LSB = 0x8
        PATCH_LSB = 0x0

    TX_REPEAT_MAX = 0xFFFF
    RX_RESULT_FLAGS_LSB = 0x8
    FAULT_INFO_COUNT_LSB = 0x10
    DESIGN_ID = 0x52434C4B
    VERSION = 0x00010003

class TxState(IntEnum):
    IDLE = 0x0
    ARMED = 0x1
    RUNNING = 0x2
    DRAIN = 0x3
    DONE = 0x4
    FAULT = 0x5


RxState = TxState


class ResultCode(IntEnum):
    NONE = 0x0
    ARMED = 0x1
    RUNNING = 0x2
    DONE = 0x3
    CANCELLED = 0x4
    REJ_BUSY = 0x10
    REJ_STALE = 0x11
    REJ_LENGTH = 0x12
    REJ_TIME_INVALID = 0x13
    REJ_DMA_NOT_READY = 0x14
    FAULT_TREADY = 0x20
    FAULT_BUF_WRITE = 0x21
    FAULT_TIME = 0x22
    RX_INVALID = 0x30


class RxFlag(IntFlag):
    OVERFLOW = 1 << 0
    GAP = 1 << 1
    TIME = 1 << 2
    OVERRANGE = 1 << 3


class FaultBit(IntFlag):
    LOCK_LOST = 1 << 0
    PPS = 1 << 1
    TICK_EXHAUSTED = 1 << 2
    TX = 1 << 3
