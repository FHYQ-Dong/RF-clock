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
