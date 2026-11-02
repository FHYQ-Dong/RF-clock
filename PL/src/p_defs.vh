// Shared encodings for the P-series PL modules.
//
// This file is included inside module bodies, so it has no include guard:
// every module that includes it gets its own copy of these localparams.

// TX/RX scheduler states (P2 STATUS register, P3/P4 state outputs).
localparam [2:0] ST_IDLE    = 3'd0;
localparam [2:0] ST_ARMED   = 3'd1;  // command accepted, waiting for target tick
localparam [2:0] ST_RUNNING = 3'd2;  // TX playing / RX window open
localparam [2:0] ST_DRAIN   = 3'd3;  // RX only: window closed, waiting for DMA TLAST
localparam [2:0] ST_DONE    = 3'd4;
localparam [2:0] ST_FAULT   = 3'd5;  // TX only; cleared by CLEAR_FAULT

// Result codes (P2 TX_RESULT / RX_RESULT [7:0]).
localparam [7:0] RC_NONE              = 8'h00;
localparam [7:0] RC_ARMED             = 8'h01;
localparam [7:0] RC_RUNNING           = 8'h02;
localparam [7:0] RC_DONE              = 8'h03;
localparam [7:0] RC_CANCELLED         = 8'h04;
localparam [7:0] RC_REJ_BUSY          = 8'h10;
localparam [7:0] RC_REJ_STALE         = 8'h11;
localparam [7:0] RC_REJ_LENGTH        = 8'h12;
localparam [7:0] RC_REJ_TIME_INVALID  = 8'h13;
localparam [7:0] RC_REJ_DMA_NOT_READY = 8'h14;
localparam [7:0] RC_FAULT_TREADY      = 8'h20;
localparam [7:0] RC_FAULT_BUF_WRITE   = 8'h21;
localparam [7:0] RC_FAULT_TIME        = 8'h22;
localparam [7:0] RC_RX_INVALID        = 8'h30;

// RX window flag bits (P2 RX_RESULT [11:8]).  Any set flag makes the
// window RC_RX_INVALID: the measurement is discarded, the system keeps
// running.
localparam integer RXF_OVERFLOW  = 0;  // FIFO full inside the window, beat lost
localparam integer RXF_GAP       = 1;  // ADC tvalid low inside the window
localparam integer RXF_TIME      = 2;  // time_valid low between arm and done
localparam integer RXF_OVERRANGE = 3;  // ADC over-range inside the window

// P6 fault bits (P2 FAULT_BITS).  FAULT_INFO first_source = bit index + 1.
localparam integer FB_LOCK_LOST      = 0;
localparam integer FB_PPS            = 1;
localparam integer FB_TICK_EXHAUSTED = 2;
localparam integer FB_TX             = 3;

// Measurement queue entries (since 2.0).  Entry i holds QUEUE_ENTRY_WORDS
// 32-bit words at byte 64 i of the queue window; the PL writes entries at
// QUEUE_HEAD, the ARM reads them up to it and then writes QUEUE_TAIL.
localparam [31:0] QUEUE_ENTRY_WORDS = 32'd16;
localparam [7:0]  QE_TYPE_OWN  = 8'd1;  // this node's tracker result of a peer burst
localparam [7:0]  QE_TYPE_PEER = 8'd2;  // a peer packet decoded with a good CRC
// Words of both types.
localparam integer QE_HEADER    = 0;   // [7:0] type, [15:8] flags, [31:16] entry sequence
localparam integer QE_PPS_COUNT = 1;   // the window's second (this node's PPS_COUNT)
localparam integer QE_SLOT      = 2;   // the window's slot
localparam integer QE_TICK_LO   = 3;   // the window's start tick
localparam integer QE_TICK_HI   = 4;
// QE_TYPE_OWN.
localparam integer QE_LAG       = 5;   // integer lag of the peak
localparam integer QE_FRACTION  = 6;   // i32, 2^-32 sample
localparam integer QE_PHASE     = 7;   // i32, 2^-32 turn
localparam integer QE_MAG_LO    = 8;   // floor(sqrt(I^2 + Q^2)) at the peak
localparam integer QE_MAG_HI    = 9;
localparam integer QE_TRX_LO    = 10;  // T_rx, i64 fs after this node's PPS
localparam integer QE_TRX_HI    = 11;
localparam integer QE_QUALITY   = 12;  // [7:0] 0.25 dB
localparam integer QE_POWER_LO  = 13;  // the window's sum of I^2 + Q^2
localparam integer QE_POWER_HI  = 14;
// QE_TYPE_PEER: the packet's fields.
localparam integer QE_P_HEAD      = 5;   // [31:24] version, [23:16] TIME_EPOCH, [15:8] quality, [7:0] status
localparam integer QE_P_CONFIG_ID = 6;
localparam integer QE_P_PPS_COUNT = 7;   // the peer's second
localparam integer QE_P_SLOT      = 8;   // the peer's slot of this burst
localparam integer QE_P_TTX_LO    = 9;   // T_tx, i64 fs after the peer's PPS
localparam integer QE_P_TTX_HI    = 10;
localparam integer QE_P_TRX_LO    = 11;  // T_rx of this node's previous burst, i64 fs after the peer's PPS
localparam integer QE_P_TRX_HI    = 12;
localparam integer QE_P_PHASE     = 13;  // i32, 2^-32 turn
// QE_HEADER flags of QE_TYPE_OWN.
localparam integer QEF_LOCKED     = 0;  // the tracker was locked: the fields are a measurement
localparam integer QEF_WINDOW_BAD = 1;  // the window has RX flags
localparam integer QEF_SEARCHED   = 2;  // a search ran on this window

// Measurement packet (since 2.0): format version and status bits.
localparam [7:0] PACKET_FORMAT_VERSION = 8'd1;
localparam integer PKST_LOCKED        = 0;  // the sender's tracker is locked
localparam integer PKST_MEASUREMENT   = 1;  // T_rx, phase and quality are a measurement
localparam integer PKST_WINDOW_VALID  = 2;  // that window had no RX flags
localparam integer PKST_OVERFLOW      = 3;  // that window lost beats
localparam integer PKST_PPS_LOCKED    = 4;
localparam integer PKST_CLOCKS_LOCKED = 5;
