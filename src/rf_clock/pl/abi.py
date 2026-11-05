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
        SLOT_CONTROL = 0x100
        SLOT_TX_OFFSET = 0x104
        SLOT_RX_OFFSET = 0x108
        SLOT_STATUS = 0x10C
        SLOT_SKIPPED_CNT = 0x110
        SLOT_COPY = 0x114
        SLOT_TICKS = 0x118
        SLOTS_PER_SECOND = 0x11C
        RX_WINDOW_BEATS = 0x120
        RX_SPAN_BEATS = 0x124
        PPS_OUT_CONTROL = 0x140
        PPS_OUT_OFFSET = 0x144
        PPS_OUT_SAMPLE = 0x148
        PPS_OUT_WIDTH = 0x14C
        PPS_OUT_AMPLITUDE = 0x150
        PPS_OUT_STATUS = 0x154
        PPS_OUT_CNT = 0x158
        PPS_OUT_FLYWHEEL_CNT = 0x15C
        PPS_OUT_TICK_LO = 0x160
        PPS_OUT_TICK_HI = 0x164
        PPS_OUT_IDLE = 0x168
        BURST_PRN_CHIPS = 0x180
        BURST_GUARD_CHIPS = 0x184
        BURST_BEATS = 0x188
        BURST_CNT = 0x18C
        PACKET_SPREAD = 0x190
        PACKET_BITS = 0x194
        CHIP_MEM_CHIPS = 0x198
        BURST_MEM_REJ_CNT = 0x19C
        P8_CONTROL = 0x200
        P8_CONFIG = 0x204
        P8_STATUS = 0x208
        P8_LOCK_LAG = 0x20C
        P8_LOCK_MAG_LO = 0x210
        P8_LOCK_MAG_HI = 0x214
        P8_LOST_CNT = 0x218
        P8_RELOCK_CNT = 0x21C
        P8_SEARCH_CNT = 0x220
        P8_REFUSED_CNT = 0x224
        P8_TRACK_HALF = 0x228
        P8_SEARCH_LANES = 0x22C
        P8_SEARCH_LAGS = 0x230
        QUALITY_OFFSET = 0x234
        SR_SEQ = 0x240
        SR_PPS_COUNT = 0x244
        SR_SLOT = 0x248
        SR_FLAGS = 0x24C
        SR_TICK_LO = 0x250
        SR_TICK_HI = 0x254
        SR_STRONG_LAG = 0x258
        SR_STRONG_FRAC = 0x25C
        SR_STRONG_PHASE = 0x260
        SR_STRONG_MAG_LO = 0x264
        SR_STRONG_MAG_HI = 0x268
        SR_EARLY_LAG = 0x26C
        SR_EARLY_FRAC = 0x270
        SR_EARLY_PHASE = 0x274
        SR_EARLY_MAG_LO = 0x278
        SR_EARLY_MAG_HI = 0x27C
        SR_SIDELOBE_LO = 0x280
        SR_SIDELOBE_HI = 0x284
        PACKET_CONFIG_ID = 0x300
        PACKET_VERSION = 0x304
        PACKET_TX_CNT = 0x308
        PACKET_RX_CNT = 0x30C
        PACKET_CRC_CNT = 0x310
        QUEUE_HEAD = 0x380
        QUEUE_TAIL = 0x384
        QUEUE_ENTRIES = 0x388
        QUEUE_DROPPED_CNT = 0x38C

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
        SLOTS_RUNNING = 0x6
        LOCKED = 0x7
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

    class SLOTCTL:
        ENABLE = 0x0
        ROLE_B = 0x1
        PACKETS = 0x2

    class SLOTST:
        RUNNING = 0x0
        SLOT_LSB = 0x10

    class P8CTL:
        SEARCH = 0x0
        ACCEPT_STRONGEST = 0x1
        ACCEPT_EARLIEST = 0x2
        UNLOCK = 0x3

    class P8CFG:
        THRESHOLD_LSB = 0x0
        LOCK_MIN_LSB = 0x10

    class P8ST:
        LOCKED = 0x0
        SEARCH_BUSY = 0x1
        WAITING = 0x2

    class BMEM:
        PRN_OFFSET = 0x0
        PACKET_OFFSET = 0x2000
        TAPS_OFFSET = 0x4000
        TAP_COUNT = 0x51

    class QE:
        TYPE_OWN = 0x1
        TYPE_PEER = 0x2
        HEADER = 0x0
        PPS_COUNT = 0x1
        SLOT = 0x2
        TICK_LO = 0x3
        TICK_HI = 0x4
        LAG = 0x5
        FRACTION = 0x6
        PHASE = 0x7
        MAG_LO = 0x8
        MAG_HI = 0x9
        TRX_LO = 0xA
        TRX_HI = 0xB
        QUALITY = 0xC
        POWER_LO = 0xD
        POWER_HI = 0xE
        P_HEAD = 0x5
        P_CONFIG_ID = 0x6
        P_PPS_COUNT = 0x7
        P_SLOT = 0x8
        P_TTX_LO = 0x9
        P_TTX_HI = 0xA
        P_TRX_LO = 0xB
        P_TRX_HI = 0xC
        P_PHASE = 0xD

    class QEF:
        LOCKED = 0x0
        WINDOW_BAD = 0x1
        SEARCHED = 0x2

    class PKST:
        LOCKED = 0x0
        MEASUREMENT = 0x1
        WINDOW_VALID = 0x2
        OVERFLOW = 0x3
        PPS_LOCKED = 0x4
        CLOCKS_LOCKED = 0x5

    class PPSOUTCTL:
        ENABLE = 0x0
        SYNCED = 0x1

    class PPSOUTST:
        HAVE_REF = 0x0
        RUNNING = 0x1
        FLYWHEEL = 0x2
        INVALID = 0x3

    TX_REPEAT_MAX = 0xFFFF
    PPSOUT_MIN_OFFSET = 0x8
    QUEUE_ENTRY_WORDS = 0x10
    PACKET_FORMAT_VERSION = 0x1
    RX_RESULT_FLAGS_LSB = 0x8
    FAULT_INFO_COUNT_LSB = 0x10
    DESIGN_ID = 0x52434C4B
    VERSION = 0x00020002

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
