// P2 AXI4-Lite register map: byte offsets and bit fields.
//
// This is the single source for the ARM-visible register layout; the
// software-facing description is docs/ABI.md.  Included inside module
// bodies (no include guard), like p_defs.vh.
//
// Layout rules: 32-bit words; a 64-bit value is LO at +0 and HI at +4;
// blocks start at 0x00 (identity/control), 0x20 (time), 0x40 (TX),
// 0x60 (RX), 0x80 (counters/config).

// ---- identity, control, status ----
localparam [7:0] REG_ID              = 8'h00;  // RO
localparam [7:0] REG_VERSION         = 8'h04;  // RO
localparam [7:0] REG_CONTROL         = 8'h08;  // WO, write-1 pulses
localparam [7:0] REG_STATUS          = 8'h0C;  // RO
localparam [7:0] REG_TIME_EPOCH      = 8'h10;  // RO
localparam [7:0] REG_FAULT_BITS      = 8'h14;  // RO
localparam [7:0] REG_FAULT_INFO      = 8'h18;  // RO

// ---- time ----
localparam [7:0] REG_TICK_LO         = 8'h20;  // RO, read latches TICK_HI
localparam [7:0] REG_TICK_HI         = 8'h24;  // RO
localparam [7:0] REG_PPS_TICK_LO     = 8'h28;  // RO, read latches PPS_TICK_HI + PPS_COUNT
localparam [7:0] REG_PPS_TICK_HI     = 8'h2C;  // RO
localparam [7:0] REG_PPS_COUNT       = 8'h30;  // RO
localparam [7:0] REG_PPS_LAST_PERIOD = 8'h34;  // RO
localparam [7:0] REG_PPS_JITTER_CNT  = 8'h38;  // RO
localparam [7:0] REG_PPS_INPUTS      = 8'h3C;  // RO, board PPS copies (since 1.1)

// ---- TX ----
localparam [7:0] REG_TX_TARGET_LO    = 8'h40;  // RW shadow
localparam [7:0] REG_TX_TARGET_HI    = 8'h44;  // RW shadow
localparam [7:0] REG_TX_LENGTH       = 8'h48;  // RW shadow, beats
localparam [7:0] REG_TX_RESULT       = 8'h4C;  // RO
localparam [7:0] REG_TX_SEQ          = 8'h50;  // RO
localparam [7:0] REG_TX_ACTUAL_LO    = 8'h54;  // RO
localparam [7:0] REG_TX_ACTUAL_HI    = 8'h58;  // RO
localparam [7:0] REG_TX_REPEAT       = 8'h5C;  // RW shadow, passes over the burst (since 1.3)

// ---- RX ----
localparam [7:0] REG_RX_TARGET_LO    = 8'h60;  // RW shadow
localparam [7:0] REG_RX_TARGET_HI    = 8'h64;  // RW shadow
localparam [7:0] REG_RX_LENGTH       = 8'h68;  // RW shadow, beats
localparam [7:0] REG_RX_RESULT       = 8'h6C;  // RO
localparam [7:0] REG_RX_SEQ          = 8'h70;  // RO
localparam [7:0] REG_RX_BEATS        = 8'h74;  // RO
localparam [7:0] REG_RX_ACTUAL_LO    = 8'h78;  // RO
localparam [7:0] REG_RX_ACTUAL_HI    = 8'h7C;  // RO

// ---- counters, build configuration ----
localparam [7:0] REG_RX_INVALID_CNT  = 8'h80;  // RO
localparam [7:0] REG_TX_DEPTH        = 8'h84;  // RO, beats
localparam [7:0] REG_RX_MAX_BEATS    = 8'h88;  // RO
localparam [7:0] REG_MIN_LEAD_TICKS  = 8'h8C;  // RO
localparam [7:0] REG_PPS_CONFIG      = 8'h90;  // RW, PPS source/polarity (since 1.1)
localparam [7:0] REG_BUILD_VERSION   = 8'h94;  // RO, package version built for (since 1.2)

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

// ---- RX_RESULT / FAULT_INFO fields ----
localparam integer RX_RESULT_FLAGS_LSB   = 8;   // RX_RESULT [11:8]
localparam integer FAULT_INFO_COUNT_LSB  = 16;  // FAULT_INFO [31:16]

localparam [31:0] P_DESIGN_ID = 32'h5243_4C4B;  // "RCLK"
localparam [31:0] P_VERSION   = 32'h0001_0003;  // major.minor = 1.3
