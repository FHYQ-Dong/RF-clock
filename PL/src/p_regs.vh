// P2 AXI4-Lite register map: byte offsets and bit fields.
//
// This is the single source for the ARM-visible register layout; the
// software-facing description is docs/ABI.md.  Included inside module
// bodies (no include guard), like p_defs.vh.
//
// Layout rules: 32-bit words; a 64-bit value is LO at +0 and HI at +4;
// blocks start at 0x000 (identity/control), 0x020 (time), 0x040 (TX),
// 0x060 (RX), 0x080 (counters/config), and since 2.0 0x100 (P7 slot engine),
// 0x180 (P3 bursts from chips), 0x200 (P8 correlator), 0x240 (P8 search
// report), 0x300 (P9 packet) and 0x380 (measurement queue); since 2.1 0x140
// (P10 output PPS; PPS_OUT_IDLE since 2.2, PPS_OUT_FRAC since 2.3).

// ---- identity, control, status ----
localparam [11:0] REG_ID              = 12'h000;  // RO
localparam [11:0] REG_VERSION         = 12'h004;  // RO
localparam [11:0] REG_CONTROL         = 12'h008;  // WO, write-1 pulses
localparam [11:0] REG_STATUS          = 12'h00C;  // RO
localparam [11:0] REG_TIME_EPOCH      = 12'h010;  // RO
localparam [11:0] REG_FAULT_BITS      = 12'h014;  // RO
localparam [11:0] REG_FAULT_INFO      = 12'h018;  // RO

// ---- time ----
localparam [11:0] REG_TICK_LO         = 12'h020;  // RO, read latches TICK_HI
localparam [11:0] REG_TICK_HI         = 12'h024;  // RO
localparam [11:0] REG_PPS_TICK_LO     = 12'h028;  // RO, read latches PPS_TICK_HI + PPS_COUNT
localparam [11:0] REG_PPS_TICK_HI     = 12'h02C;  // RO
localparam [11:0] REG_PPS_COUNT       = 12'h030;  // RO
localparam [11:0] REG_PPS_LAST_PERIOD = 12'h034;  // RO
localparam [11:0] REG_PPS_JITTER_CNT  = 12'h038;  // RO
localparam [11:0] REG_PPS_INPUTS      = 12'h03C;  // RO, board PPS copies (since 1.1)

// ---- TX ----
localparam [11:0] REG_TX_TARGET_LO    = 12'h040;  // RW shadow
localparam [11:0] REG_TX_TARGET_HI    = 12'h044;  // RW shadow
localparam [11:0] REG_TX_LENGTH       = 12'h048;  // RW shadow, beats
localparam [11:0] REG_TX_RESULT       = 12'h04C;  // RO
localparam [11:0] REG_TX_SEQ          = 12'h050;  // RO
localparam [11:0] REG_TX_ACTUAL_LO    = 12'h054;  // RO
localparam [11:0] REG_TX_ACTUAL_HI    = 12'h058;  // RO
localparam [11:0] REG_TX_REPEAT       = 12'h05C;  // RW shadow, passes over the burst (since 1.3)

// ---- RX ----
localparam [11:0] REG_RX_TARGET_LO    = 12'h060;  // RW shadow
localparam [11:0] REG_RX_TARGET_HI    = 12'h064;  // RW shadow
localparam [11:0] REG_RX_LENGTH       = 12'h068;  // RW shadow, beats
localparam [11:0] REG_RX_RESULT       = 12'h06C;  // RO
localparam [11:0] REG_RX_SEQ          = 12'h070;  // RO
localparam [11:0] REG_RX_BEATS        = 12'h074;  // RO
localparam [11:0] REG_RX_ACTUAL_LO    = 12'h078;  // RO
localparam [11:0] REG_RX_ACTUAL_HI    = 12'h07C;  // RO

// ---- counters, build configuration ----
localparam [11:0] REG_RX_INVALID_CNT  = 12'h080;  // RO
localparam [11:0] REG_TX_DEPTH        = 12'h084;  // RO, beats
localparam [11:0] REG_RX_MAX_BEATS    = 12'h088;  // RO
localparam [11:0] REG_MIN_LEAD_TICKS  = 12'h08C;  // RO
localparam [11:0] REG_PPS_CONFIG      = 12'h090;  // RW, PPS source/polarity (since 1.1)
localparam [11:0] REG_BUILD_VERSION   = 12'h094;  // RO, package version built for (since 1.2)

// ---- P7 slot engine (since 2.0): a 2 ms ping-pong from each PPS edge ----
localparam [11:0] REG_SLOT_CONTROL      = 12'h100;  // RW, taken at the next PPS edge
localparam [11:0] REG_SLOT_TX_OFFSET    = 12'h104;  // RW, ticks from the slot start to the burst
localparam [11:0] REG_SLOT_RX_OFFSET    = 12'h108;  // RW, ticks from the slot start to the window
localparam [11:0] REG_SLOT_STATUS       = 12'h10C;  // RO
localparam [11:0] REG_SLOT_SKIPPED_CNT  = 12'h110;  // RO, slots not started on time
localparam [11:0] REG_SLOT_COPY         = 12'h114;  // RW, 1: copy the next window to DDR
localparam [11:0] REG_SLOT_TICKS        = 12'h118;  // RO, ticks per slot
localparam [11:0] REG_SLOTS_PER_SECOND  = 12'h11C;  // RO
localparam [11:0] REG_RX_WINDOW_BEATS   = 12'h120;  // RO, the correlator's window
localparam [11:0] REG_RX_SPAN_BEATS     = 12'h124;  // RO, the window and the packet behind it

// ---- P10 output PPS (since 2.1): a step on DAC tile 0 once a second ----
localparam [11:0] REG_PPS_OUT_CONTROL      = 12'h140;  // RW; ENABLE taken at the next second
localparam [11:0] REG_PPS_OUT_OFFSET       = 12'h144;  // RW, ticks from the second's start
localparam [11:0] REG_PPS_OUT_SAMPLE       = 12'h148;  // RW, 0..7: the step's first sample in its beat
localparam [11:0] REG_PPS_OUT_WIDTH        = 12'h14C;  // RW, beats, 1..65535
localparam [11:0] REG_PPS_OUT_AMPLITUDE    = 12'h150;  // RW, i16 in [15:0]
localparam [11:0] REG_PPS_OUT_STATUS       = 12'h154;  // RO
localparam [11:0] REG_PPS_OUT_CNT          = 12'h158;  // RO, pulses sent
localparam [11:0] REG_PPS_OUT_FLYWHEEL_CNT = 12'h15C;  // RO, of them in seconds no locked edge started
localparam [11:0] REG_PPS_OUT_TICK_LO      = 12'h160;  // RO, the last pulse; read latches PPS_OUT_TICK_HI
localparam [11:0] REG_PPS_OUT_TICK_HI      = 12'h164;  // RO
localparam [11:0] REG_PPS_OUT_IDLE         = 12'h168;  // RW, i16 in [15:0]: the I samples between pulses (2.2)
localparam [11:0] REG_PPS_OUT_FRAC         = 12'h16C;  // RW, u16 in [15:0]: the edge's delay in 2^-16 of a sample, with FRAC (2.3)

// ---- P3 bursts from chips (since 2.0) ----
localparam [11:0] REG_BURST_PRN_CHIPS   = 12'h180;  // RW, PRN or acquisition code length
localparam [11:0] REG_BURST_GUARD_CHIPS = 12'h184;  // RW, zero chips before the packet
localparam [11:0] REG_BURST_BEATS       = 12'h188;  // RO, beats of a burst with its packet
localparam [11:0] REG_BURST_CNT         = 12'h18C;  // RO, bursts sent by the slot engine
localparam [11:0] REG_PACKET_SPREAD     = 12'h190;  // RO, L: chips per packet bit
localparam [11:0] REG_PACKET_BITS       = 12'h194;  // RO
localparam [11:0] REG_CHIP_MEM_CHIPS    = 12'h198;  // RO, capacity of each code memory
localparam [11:0] REG_BURST_MEM_REJ_CNT = 12'h19C;  // RO, burst memory writes refused

// ---- P8 correlator (since 2.0) ----
localparam [11:0] REG_P8_CONTROL        = 12'h200;  // WO, write-1 pulses
localparam [11:0] REG_P8_CONFIG         = 12'h204;  // RW
localparam [11:0] REG_P8_STATUS         = 12'h208;  // RO
localparam [11:0] REG_P8_LOCK_LAG       = 12'h20C;  // RO, the lag the tracker centres on
localparam [11:0] REG_P8_LOCK_MAG_LO    = 12'h210;  // RO, magnitude of the last lock
localparam [11:0] REG_P8_LOCK_MAG_HI    = 12'h214;  // RO
localparam [11:0] REG_P8_LOST_CNT       = 12'h218;  // RO
localparam [11:0] REG_P8_RELOCK_CNT     = 12'h21C;  // RO, locks the PL took by itself
localparam [11:0] REG_P8_SEARCH_CNT     = 12'h220;  // RO, searches finished
localparam [11:0] REG_P8_REFUSED_CNT    = 12'h224;  // RO, search requests refused while busy
localparam [11:0] REG_P8_TRACK_HALF     = 12'h228;  // RO, the tracker's lags each side
localparam [11:0] REG_P8_SEARCH_LANES   = 12'h22C;  // RO
localparam [11:0] REG_P8_SEARCH_LAGS    = 12'h230;  // RO, lags of a whole window
localparam [11:0] REG_QUALITY_OFFSET    = 12'h234;  // RW, signed, 1/16 log2 unit

// ---- P8 search report (since 2.0): the latest finished search ----
localparam [11:0] REG_SR_SEQ            = 12'h240;  // RO, read first and last
localparam [11:0] REG_SR_PPS_COUNT      = 12'h244;  // RO, the window's second
localparam [11:0] REG_SR_SLOT           = 12'h248;  // RO, the window's slot
localparam [11:0] REG_SR_FLAGS          = 12'h24C;  // RO, the window's RX flags
localparam [11:0] REG_SR_TICK_LO        = 12'h250;  // RO, the window's start tick
localparam [11:0] REG_SR_TICK_HI        = 12'h254;  // RO
localparam [11:0] REG_SR_STRONG_LAG     = 12'h258;  // RO
localparam [11:0] REG_SR_STRONG_FRAC    = 12'h25C;  // RO, i32, 2^-32 sample
localparam [11:0] REG_SR_STRONG_PHASE   = 12'h260;  // RO, i32, 2^-32 turn
localparam [11:0] REG_SR_STRONG_MAG_LO  = 12'h264;  // RO
localparam [11:0] REG_SR_STRONG_MAG_HI  = 12'h268;  // RO
localparam [11:0] REG_SR_EARLY_LAG      = 12'h26C;  // RO
localparam [11:0] REG_SR_EARLY_FRAC     = 12'h270;  // RO
localparam [11:0] REG_SR_EARLY_PHASE    = 12'h274;  // RO
localparam [11:0] REG_SR_EARLY_MAG_LO   = 12'h278;  // RO
localparam [11:0] REG_SR_EARLY_MAG_HI   = 12'h27C;  // RO
localparam [11:0] REG_SR_SIDELOBE_LO    = 12'h280;  // RO
localparam [11:0] REG_SR_SIDELOBE_HI    = 12'h284;  // RO

// ---- P9 measurement packet (since 2.0) ----
localparam [11:0] REG_PACKET_CONFIG_ID  = 12'h300;  // RW, config_id sent in every packet
localparam [11:0] REG_PACKET_VERSION    = 12'h304;  // RO, packet format version
localparam [11:0] REG_PACKET_TX_CNT     = 12'h308;  // RO
localparam [11:0] REG_PACKET_RX_CNT     = 12'h30C;  // RO, decoded with a good CRC
localparam [11:0] REG_PACKET_CRC_CNT    = 12'h310;  // RO, failed CRC

// ---- measurement queue (since 2.0): a ring of entries in its own window ----
localparam [11:0] REG_QUEUE_HEAD        = 12'h380;  // RO, the entry the PL writes next
localparam [11:0] REG_QUEUE_TAIL        = 12'h384;  // RW, the entry the ARM reads next
localparam [11:0] REG_QUEUE_ENTRIES     = 12'h388;  // RO
localparam [11:0] REG_QUEUE_DROPPED_CNT = 12'h38C;  // RO, entries lost to a full queue

// ---- CONTROL bits (write 1 to pulse; several bits may be set at once) ----
localparam integer CTRL_TX_DOORBELL = 0;
localparam integer CTRL_TX_CANCEL   = 1;
localparam integer CTRL_RX_DOORBELL = 2;
localparam integer CTRL_RX_CANCEL   = 3;
localparam integer CTRL_CLEAR_FAULT = 4;

// ---- STATUS bits ----
localparam integer STAT_TIME_VALID     = 0;
localparam integer STAT_CLOCKS_LOCKED  = 1;
localparam integer STAT_PPS_LOCKED     = 2;
localparam integer STAT_TICK_EXHAUSTED = 3;
localparam integer STAT_ANY_FAULT      = 4;
localparam integer STAT_DMA_READY      = 5;
localparam integer STAT_SLOTS_RUNNING  = 6;   // since 2.0
localparam integer STAT_LOCKED         = 7;   // since 2.0: P8's tracker is locked
localparam integer STAT_TX_STATE_LSB   = 8;   // [10:8]
localparam integer STAT_RX_STATE_LSB   = 12;  // [14:12]

// ---- PPS_CONFIG bits (reset 0: comparator copy, not inverted) ----
localparam integer PPSCFG_SRC_TRIG = 0;  // 0 = IRIG_COMP_OUT, 1 = IRIG_TRIG_OUT
localparam integer PPSCFG_INVERT   = 1;  // time the falling edge of the source

// ---- PPS_INPUTS fields ----
localparam integer PPSIN_COMP_LEVEL     = 0;   // synchronized level
localparam integer PPSIN_TRIG_LEVEL     = 1;
localparam integer PPSIN_COMP_EDGES_LSB = 8;   // [15:8] rising edges, wraps
localparam integer PPSIN_TRIG_EDGES_LSB = 16;  // [23:16] rising edges, wraps

// ---- BUILD_VERSION fields: the rf-clock package version A.B.C.D in
// pyproject.toml when the bitstream was built.  A.B is this register map's
// VERSION, C counts bitstreams built for it, D counts Python-only releases. ----
localparam integer BUILDVER_ABI_MAJOR_LSB = 24;  // [31:24] A
localparam integer BUILDVER_ABI_MINOR_LSB = 16;  // [23:16] B
localparam integer BUILDVER_BUILD_LSB     = 8;   // [15:8]  C
localparam integer BUILDVER_PATCH_LSB     = 0;   // [7:0]   D

// ---- TX_REPEAT: 1 .. TX_REPEAT_MAX passes, played back to back (reset 1) ----
localparam [31:0] TX_REPEAT_MAX = 32'd65535;

// ---- SLOT_CONTROL bits (taken at the next PPS edge) ----
localparam integer SLOTCTL_ENABLE  = 0;  // run the slot engine; P2's TX and RX commands are refused
localparam integer SLOTCTL_ROLE_B  = 1;  // 0: node A, sends in even slots; 1: node B, in odd slots
localparam integer SLOTCTL_PACKETS = 2;  // a measurement packet behind every burst

// ---- SLOT_STATUS fields ----
localparam integer SLOTST_RUNNING  = 0;   // the engine runs (from the PPS edge after ENABLE)
localparam integer SLOTST_SLOT_LSB = 16;  // [24:16] the current slot

// ---- PPS_OUT_CONTROL bits (reset 0) ----
localparam integer PPSOUTCTL_ENABLE = 0;  // a pulse every second, from the next second on
localparam integer PPSOUTCTL_SYNCED = 1;  // the ARM vouches for the time scale (validity line, at once)
localparam integer PPSOUTCTL_FRAC   = 2;  // delay the edge by PPS_OUT_FRAC, taken at the next second (2.3)

// ---- PPS_OUT_STATUS bits ----
localparam integer PPSOUTST_HAVE_REF = 0;  // a locked PPS edge has set the phase
localparam integer PPSOUTST_RUNNING  = 1;  // enabled for the current second
localparam integer PPSOUTST_FLYWHEEL = 2;  // the last pulse came from the flywheel
localparam integer PPSOUTST_INVALID  = 3;  // the validity line (1: not valid)

// ---- PPS_OUT_OFFSET: PPSOUT_MIN_OFFSET .. PPS period - 1 (reset PPSOUT_MIN_OFFSET) ----
localparam [31:0] PPSOUT_MIN_OFFSET = 32'd8;

// ---- P8_CONTROL bits (write 1 to pulse) ----
localparam integer P8CTL_SEARCH           = 0;  // search the next window
localparam integer P8CTL_ACCEPT_STRONGEST = 1;  // lock on the reported strongest peak
localparam integer P8CTL_ACCEPT_EARLIEST  = 2;  // lock on the reported earliest peak
localparam integer P8CTL_UNLOCK           = 3;  // drop the lock

// ---- P8_CONFIG fields (reset 0x8000_8000) ----
localparam integer P8CFG_THRESHOLD_LSB = 0;   // [15:0] earliest peak: 2^-16 of the strongest magnitude
localparam integer P8CFG_LOCK_MIN_LSB  = 16;  // [31:16] lock: 2^-16 of the last lock's magnitude

// ---- P8_STATUS bits ----
localparam integer P8ST_LOCKED      = 0;
localparam integer P8ST_SEARCH_BUSY = 1;
localparam integer P8ST_WAITING     = 2;  // a search found a peak the PL may not lock on by itself

// ---- burst memory window (since 2.0): byte offsets ----
localparam [15:0] BMEM_PRN_OFFSET    = 16'h0000;  // chip n: bit n%32 of word n/32, 1 = +1
localparam [15:0] BMEM_PACKET_OFFSET = 16'h2000;  // the packet code, packed alike
localparam [15:0] BMEM_TAPS_OFFSET   = 16'h4000;  // tap k: signed 16 bits in word k
localparam [31:0] BMEM_TAP_COUNT     = 32'd81;

// ---- RX_RESULT / FAULT_INFO fields ----
localparam integer RX_RESULT_FLAGS_LSB   = 8;   // RX_RESULT [11:8]
localparam integer FAULT_INFO_COUNT_LSB  = 16;  // FAULT_INFO [31:16]

localparam [31:0] P_DESIGN_ID = 32'h5243_4C4B;  // "RCLK"
localparam [31:0] P_VERSION   = 32'h0002_0003;  // major.minor = 2.3
