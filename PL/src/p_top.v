`timescale 1ns / 1ps
// RF-clock PL top (RTL part of the block design).
//
// Everything except the RX DMA side runs on `clk`, the 100 MHz fabric clock
// derived from the node's FS725 10 MHz; the RFDC DAC/ADC AXIS must be on the
// same clock.  `dma_clk` is the AXI DMA S2MM clock (>= 200 MHz for
// 3.2 GB/s through a 128-bit HP port after the BD width converter).
//
// P5 (RFDC IP, MTS/SYSREF, clocking) lives in the block design, not here.
//
// Since 2.0 the slot engine (P7) runs the ping-pong: bursts made from chips
// (p3_burst_gen) with the measurement packet (P9), windows into the
// correlator (P8), measurements and decoded packets into the queue.  The
// first version's P3 player and P4 window serve development tests with the
// engine off; P4 also copies a window to DDR on SLOT_COPY.
// Since 2.1 P10 sends the output PPS on DAC tile 0's stream (DAC_B) and drives
// its validity line (a Pmod pin, high = not valid) and an LED (lit = valid).
// Since 2.2 the pulse can start from an idle level (PPS_OUT_IDLE); since 2.3 its edge can be delayed by a
// fraction of a sample (PPS_OUT_FRAC with PPS_OUT_CONTROL.FRAC).
// The X_INTERFACE_* attributes let the block design (module reference) see
// the clock/reset associations and the BRAM port as interfaces.
module p_top #(
    parameter integer DATA_W         = 256,
    parameter integer TX_ADDR_W      = 17,       // TX buffer address bits: 4 MiB AXI window
    parameter integer TX_DEPTH       = 69632,    // beats = 17 URAM rows; fits a 65535-chip
                                                 // burst with RRC tails (65545 beats)
    parameter [31:0]  RX_MAX_BEATS   = 32'd2097151,  // < 2**26 bytes (DMA length limit)
    parameter integer RX_FIFO_ADDR_W = 9,
    parameter [31:0]  MIN_LEAD_TICKS = 32'd64,
    parameter integer STABLE_CYCLES  = 1024,
    parameter [31:0]  PPS_PERIOD     = 32'd100_000_000,
    parameter [31:0]  PPS_TOLERANCE  = 32'd1,
    parameter [31:0]  PPS_LOCK_COUNT = 32'd4,
    // rf-clock package version A.B.C.D (0xAABBCCDD) set by the build script
    // from pyproject.toml; software compares it with its own version.  An integer
    // so the block design can set it as a plain decimal CONFIG value.
    parameter integer BUILD_VERSION  = 0,
    // 2.0: slots, windows, the search, the queue
    parameter [31:0]  SLOT_TICKS     = 32'd200000,
    parameter [31:0]  SLOTS          = 32'd500,
    parameter integer WINDOW_BEATS   = 5481,     // 43848 samples: the correlator's window
    parameter integer SEARCH_LANES   = 1024,
    parameter integer QUEUE_ENTRIES  = 1024,
    parameter integer CHIP_AW        = 11,       // code memories: 2^(CHIP_AW+5) chips
    parameter integer WIN_BW         = 13,       // window beat address bits
    parameter integer LAG_KW         = 16        // lag bits
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi:m_axis_dac:m_axis_dac0:s_axis_adc:BURST_PORT:QUEUE_PORT, ASSOCIATED_RESET arst_n" *)
    input  wire                      clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 arst_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire                      arst_n,
    input  wire                      clocks_locked_async,
    input  wire                      pps_in,       // IRIG_COMP_OUT (comparator)
    input  wire                      pps_trig_in,  // IRIG_TRIG_OUT (Schmitt trigger)

    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 dma_clk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF m_axis_s2mm, ASSOCIATED_RESET dma_arst_n" *)
    input  wire                      dma_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 dma_arst_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire                      dma_arst_n,

    // AXI4-Lite control (through an AXI clock converter onto clk).
    input  wire [11:0]               s_axi_awaddr,
    input  wire                      s_axi_awvalid,
    output wire                      s_axi_awready,
    input  wire [31:0]               s_axi_wdata,
    input  wire [3:0]                s_axi_wstrb,
    input  wire                      s_axi_wvalid,
    output wire                      s_axi_wready,
    output wire [1:0]                s_axi_bresp,
    output wire                      s_axi_bvalid,
    input  wire                      s_axi_bready,
    input  wire [11:0]               s_axi_araddr,
    input  wire                      s_axi_arvalid,
    output wire                      s_axi_arready,
    output wire [31:0]               s_axi_rdata,
    output wire [1:0]                s_axi_rresp,
    output wire                      s_axi_rvalid,
    input  wire                      s_axi_rready,

    // TX waveform buffer, AXI BRAM Controller port A (clocked by clk).
    // MEM_SIZE = 2**(TX_ADDR_W+5) bytes, the tx_bram_ctrl window in the BD.
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA EN" *)
    (* X_INTERFACE_PARAMETER = "MASTER_TYPE BRAM_CTRL, MEM_ECC NONE, MEM_WIDTH 256, MEM_SIZE 4194304, READ_WRITE_MODE READ_WRITE, READ_LATENCY 1" *)
    input  wire                      bram_en,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA WE" *)
    input  wire [DATA_W/8-1:0]       bram_we,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA ADDR" *)
    input  wire [TX_ADDR_W+4:0]      bram_addr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA DIN" *)
    input  wire [DATA_W-1:0]         bram_wrdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BRAM_PORTA DOUT" *)
    output wire [DATA_W-1:0]         bram_rddata,

    // Burst memory (2.0), AXI BRAM Controller port, 32-bit, 64 KiB (docs/ABI.md 4.9).
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BURST_PORT EN" *)
    (* X_INTERFACE_PARAMETER = "MASTER_TYPE BRAM_CTRL, MEM_ECC NONE, MEM_WIDTH 32, MEM_SIZE 65536, READ_WRITE_MODE READ_WRITE, READ_LATENCY 1" *)
    input  wire                      burst_en,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BURST_PORT WE" *)
    input  wire [3:0]                burst_we,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BURST_PORT ADDR" *)
    input  wire [15:0]               burst_addr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BURST_PORT DIN" *)
    input  wire [31:0]               burst_wrdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 BURST_PORT DOUT" *)
    output wire [31:0]               burst_rddata,

    // Measurement queue (2.0), AXI BRAM Controller port, 32-bit, 64 KiB, read only (4.12).
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 QUEUE_PORT EN" *)
    (* X_INTERFACE_PARAMETER = "MASTER_TYPE BRAM_CTRL, MEM_ECC NONE, MEM_WIDTH 32, MEM_SIZE 65536, READ_WRITE_MODE READ_WRITE, READ_LATENCY 1" *)
    input  wire                      queue_en,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 QUEUE_PORT WE" *)
    input  wire [3:0]                queue_we,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 QUEUE_PORT ADDR" *)
    input  wire [15:0]               queue_addr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 QUEUE_PORT DIN" *)
    input  wire [31:0]               queue_wrdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:bram:1.0 QUEUE_PORT DOUT" *)
    output wire [31:0]               queue_rddata,

    // RFDC DAC AXI4-Stream.
    output wire [DATA_W-1:0]         m_axis_dac_tdata,
    output wire                      m_axis_dac_tvalid,
    input  wire                      m_axis_dac_tready,

    // RFDC DAC tile 0 AXI4-Stream (2.1): the output PPS.
    output wire [DATA_W-1:0]         m_axis_dac0_tdata,
    output wire                      m_axis_dac0_tvalid,
    input  wire                      m_axis_dac0_tready,

    // RFDC ADC AXI4-Stream (I/Q streams combined to DATA_W in the BD).
    input  wire [DATA_W-1:0]         s_axis_adc_tdata,
    input  wire                      s_axis_adc_tvalid,
    input  wire                      adc_overrange,  // RFDC status, asynchronous

    // AXI DMA S2MM stream (dma_clk).
    output wire [DATA_W-1:0]         m_axis_s2mm_tdata,
    output wire [DATA_W/8-1:0]       m_axis_s2mm_tkeep,
    output wire                      m_axis_s2mm_tvalid,
    input  wire                      m_axis_s2mm_tready,
    output wire                      m_axis_s2mm_tlast,

    // The output PPS's validity line (Pmod, high = not valid) and LED (2.1).
    output wire                      pps_out_invalid,
    output wire                      pps_out_led
);
    `include "src/p_defs.vh"
    `include "src/p_regs.vh"

    // ---------------- P0 ----------------
    wire        rst;
    wire        clocks_locked;
    wire        time_valid;
    wire [31:0] time_epoch;
    wire        lock_lost_event;
    wire        epoch_start_event;

    p0_clock_reset #(
        .STABLE_CYCLES (STABLE_CYCLES)
    ) u_p0 (
        .clk                 (clk),
        .arst_n              (arst_n),
        .clocks_locked_async (clocks_locked_async),
        .rst                 (rst),
        .clocks_locked       (clocks_locked),
        .time_valid          (time_valid),
        .time_epoch          (time_epoch),
        .lock_lost_event     (lock_lost_event),
        .epoch_start_event   (epoch_start_event)
    );

    (* ASYNC_REG = "TRUE" *) reg [1:0] dma_rst_sync;
    always @(posedge dma_clk or negedge dma_arst_n) begin
        if (!dma_arst_n) dma_rst_sync <= 2'b11;
        else             dma_rst_sync <= {dma_rst_sync[0], 1'b0};
    end
    wire dma_rst = dma_rst_sync[1];

    (* ASYNC_REG = "TRUE" *) reg [1:0] overrange_sync;
    always @(posedge clk) overrange_sync <= {overrange_sync[0], adc_overrange};

    // ---------------- P1 ----------------
    wire [63:0] raw_tick;
    wire        raw_tick_exhausted;
    wire        pps_event;
    wire [63:0] pps_tick;
    wire [31:0] pps_count;
    wire [31:0] pps_last_period;
    wire [31:0] pps_jitter_count;
    wire        pps_locked;
    wire        pps_fault_event;
    wire [1:0]  pps_config;
    wire        pps_comp_level, pps_trig_level;
    wire [7:0]  pps_comp_edges, pps_trig_edges;

    p1_timebase_pps #(
        .PPS_PERIOD     (PPS_PERIOD),
        .PPS_TOLERANCE  (PPS_TOLERANCE),
        .PPS_LOCK_COUNT (PPS_LOCK_COUNT)
    ) u_p1 (
        .clk                (clk),
        .rst                (rst),
        .pps_comp_async     (pps_in),
        .pps_trig_async     (pps_trig_in),
        .pps_src_trig       (pps_config[PPSCFG_SRC_TRIG]),
        .pps_invert         (pps_config[PPSCFG_INVERT]),
        .pps_comp_level     (pps_comp_level),
        .pps_trig_level     (pps_trig_level),
        .pps_comp_edges     (pps_comp_edges),
        .pps_trig_edges     (pps_trig_edges),
        .raw_tick           (raw_tick),
        .raw_tick_exhausted (raw_tick_exhausted),
        .pps_event          (pps_event),
        .pps_tick           (pps_tick),
        .pps_count          (pps_count),
        .pps_last_period    (pps_last_period),
        .pps_jitter_count   (pps_jitter_count),
        .pps_locked         (pps_locked),
        .pps_fault_event    (pps_fault_event)
    );

    // ---------------- 2.0 signals ----------------
    localparam integer SPREAD = 64;
    localparam integer PBITS  = 288;
    localparam [31:0]  PACKET_CHIPS = PBITS * SPREAD;

    wire [2:0]  slot_control;
    wire [31:0] slot_tx_offset, slot_rx_offset;
    wire        slot_copy, slots_running, slot_copy_pending;
    wire [8:0]  slot;
    wire [31:0] slot_skipped_count;
    wire [16:0] burst_prn_chips;
    wire [15:0] burst_guard_chips;
    reg  [31:0] burst_count, packet_tx_count;
    wire [31:0] burst_mem_rej_count;
    wire        p8_cmd_search, p8_cmd_accept_s, p8_cmd_accept_e, p8_cmd_unlock;
    wire [31:0] p8_config, quality_offset, packet_config_id, queue_tail;
    wire        p8_locked, p8_search_busy, p8_waiting;
    wire [15:0] p8_lock_lag;
    wire [46:0] p8_lock_mag;
    wire [31:0] p8_lost_count, p8_relock_count, p8_search_count, p8_refused_count, p8_search_lags;
    wire [31:0] sr_seq, sr_pps_count;
    wire [8:0]  sr_slot;
    wire [3:0]  sr_flags;
    wire [63:0] sr_tick;
    wire [15:0] sr_s_lag, sr_e_lag;
    wire [31:0] sr_s_fraction, sr_s_phase, sr_e_fraction, sr_e_phase;
    wire [46:0] sr_s_magnitude, sr_e_magnitude, sr_sidelobe;
    wire [31:0] packet_rx_count, packet_crc_count;
    wire [31:0] queue_head, queue_dropped_count;

    // P7 and its units
    wire               burst_busy, burst_fault, burst_started;
    wire [63:0]        burst_actual_tick;
    wire               rx_busy_p8;
    wire               role_b, packets_active;
    wire [CHIP_AW+5:0] prn_active;
    wire [15:0]        guard_active;
    wire [31:0]        burst_beats, span_beats;
    wire               tx_arm, rx_arm, rx_copy;
    wire [63:0]        ev_tick, sec_tick;
    wire [8:0]         ev_slot;
    wire [31:0]        sec_count;

    // the burst and the span as the registers configure them now (the engine
    // takes them at the next PPS edge)
    wire [31:0] burst_beats_cfg = {15'd0, burst_prn_chips} + 32'd10 +
                                  (slot_control[SLOTCTL_PACKETS] ? {16'd0, burst_guard_chips} + PACKET_CHIPS : 32'd0);
    wire [31:0] span_beats_cfg  = WINDOW_BEATS + {16'd0, burst_guard_chips} + PACKET_CHIPS + 32'd1;
    // the engine owns the DAC, the windows and the burst memory
    wire        engine_lock = slot_control[SLOTCTL_ENABLE] || slots_running;

    // ---------------- 2.1 signals: P10 ----------------
    wire [2:0]  pps_out_control;
    wire [31:0] pps_out_offset;
    wire [2:0]  pps_out_sample;
    wire [15:0] pps_out_width, pps_out_amplitude, pps_out_idle, pps_out_frac;
    wire        pps_out_have_ref, pps_out_running, pps_out_flywheel;
    wire [31:0] pps_out_count, pps_out_flywheel_count;
    wire [63:0] pps_out_tick;

    // ---------------- P2 ----------------
    wire        tx_doorbell, tx_cancel, rx_doorbell, rx_cancel, clear_fault;
    wire [63:0] tx_target, rx_target;
    wire [31:0] tx_length, rx_length;
    wire [31:0] tx_repeat;

    wire [2:0]  tx_state;
    wire [7:0]  tx_result;
    wire [31:0] tx_seq;
    wire [63:0] tx_actual_tick;
    wire        tx_fault;

    wire [2:0]  rx_state;
    wire [7:0]  rx_result;
    wire [3:0]  rx_flags;
    wire [31:0] rx_seq;
    wire [31:0] rx_beats;
    wire [63:0] rx_actual_tick;
    wire [31:0] rx_invalid_count;
    wire        dma_ready;

    wire [3:0]  fault_bits;
    wire [3:0]  fault_first_source;
    wire [15:0] fault_count;
    wire        any_fault;

    p2_control_regs #(
        .TX_DEPTH_BEATS (TX_DEPTH),
        .RX_MAX_BEATS   (RX_MAX_BEATS),
        .MIN_LEAD_TICKS (MIN_LEAD_TICKS),
        .BUILD_VERSION  (BUILD_VERSION),
        .SLOT_TICKS     (SLOT_TICKS),
        .SLOTS          (SLOTS),
        .WINDOW_BEATS   (WINDOW_BEATS),
        .SPREAD         (SPREAD),
        .PACKET_BITS    (PBITS),
        .CHIP_CHIPS     (32'd1 << (CHIP_AW + 5)),
        .TRACK_HALF     (32'd4),
        .SEARCH_LANES   (SEARCH_LANES),
        .QUEUE_ENTRIES  (QUEUE_ENTRIES),
        .PPS_PERIOD     (PPS_PERIOD)
    ) u_p2 (
        .clk                (clk),
        .rst                (rst),
        .s_axi_awaddr       (s_axi_awaddr),
        .s_axi_awvalid      (s_axi_awvalid),
        .s_axi_awready      (s_axi_awready),
        .s_axi_wdata        (s_axi_wdata),
        .s_axi_wstrb        (s_axi_wstrb),
        .s_axi_wvalid       (s_axi_wvalid),
        .s_axi_wready       (s_axi_wready),
        .s_axi_bresp        (s_axi_bresp),
        .s_axi_bvalid       (s_axi_bvalid),
        .s_axi_bready       (s_axi_bready),
        .s_axi_araddr       (s_axi_araddr),
        .s_axi_arvalid      (s_axi_arvalid),
        .s_axi_arready      (s_axi_arready),
        .s_axi_rdata        (s_axi_rdata),
        .s_axi_rresp        (s_axi_rresp),
        .s_axi_rvalid       (s_axi_rvalid),
        .s_axi_rready       (s_axi_rready),
        .tx_doorbell        (tx_doorbell),
        .tx_cancel          (tx_cancel),
        .rx_doorbell        (rx_doorbell),
        .rx_cancel          (rx_cancel),
        .clear_fault        (clear_fault),
        .tx_target          (tx_target),
        .tx_length          (tx_length),
        .tx_repeat          (tx_repeat),
        .rx_target          (rx_target),
        .rx_length          (rx_length),
        .pps_config         (pps_config),
        .time_valid         (time_valid),
        .clocks_locked      (clocks_locked),
        .pps_locked         (pps_locked),
        .raw_tick_exhausted (raw_tick_exhausted),
        .any_fault          (any_fault),
        .dma_ready          (dma_ready),
        .time_epoch         (time_epoch),
        .fault_bits         (fault_bits),
        .fault_first_source (fault_first_source),
        .fault_count        (fault_count),
        .raw_tick           (raw_tick),
        .pps_tick           (pps_tick),
        .pps_count          (pps_count),
        .pps_last_period    (pps_last_period),
        .pps_jitter_count   (pps_jitter_count),
        .pps_comp_level     (pps_comp_level),
        .pps_trig_level     (pps_trig_level),
        .pps_comp_edges     (pps_comp_edges),
        .pps_trig_edges     (pps_trig_edges),
        .tx_state           (tx_state),
        .tx_result          (tx_result),
        .tx_seq             (tx_seq),
        .tx_actual_tick     (tx_actual_tick),
        .rx_state           (rx_state),
        .rx_result          (rx_result),
        .rx_flags           (rx_flags),
        .rx_seq             (rx_seq),
        .rx_beats           (rx_beats),
        .rx_actual_tick     (rx_actual_tick),
        .rx_invalid_count   (rx_invalid_count),
        .slot_control       (slot_control),
        .slot_tx_offset     (slot_tx_offset),
        .slot_rx_offset     (slot_rx_offset),
        .slot_copy          (slot_copy),
        .slots_running      (slots_running),
        .slot               (slot),
        .slot_skipped_count (slot_skipped_count),
        .slot_copy_pending  (slot_copy_pending),
        .rx_span_beats      (span_beats_cfg),
        .burst_prn_chips    (burst_prn_chips),
        .burst_guard_chips  (burst_guard_chips),
        .burst_beats        (burst_beats_cfg),
        .burst_count        (burst_count),
        .burst_mem_rej_count(burst_mem_rej_count),
        .p8_search          (p8_cmd_search),
        .p8_accept_strongest(p8_cmd_accept_s),
        .p8_accept_earliest (p8_cmd_accept_e),
        .p8_unlock          (p8_cmd_unlock),
        .p8_config          (p8_config),
        .quality_offset     (quality_offset),
        .p8_locked          (p8_locked),
        .p8_search_busy     (p8_search_busy),
        .p8_waiting         (p8_waiting),
        .p8_lock_lag        (p8_lock_lag),
        .p8_lock_mag        (p8_lock_mag),
        .p8_lost_count      (p8_lost_count),
        .p8_relock_count    (p8_relock_count),
        .p8_search_count    (p8_search_count),
        .p8_refused_count   (p8_refused_count),
        .p8_search_lags     (p8_search_lags),
        .sr_seq             (sr_seq),
        .sr_pps_count       (sr_pps_count),
        .sr_slot            (sr_slot),
        .sr_flags           (sr_flags),
        .sr_tick            (sr_tick),
        .sr_s_lag           (sr_s_lag),
        .sr_s_fraction      (sr_s_fraction),
        .sr_s_phase         (sr_s_phase),
        .sr_s_magnitude     (sr_s_magnitude),
        .sr_e_lag           (sr_e_lag),
        .sr_e_fraction      (sr_e_fraction),
        .sr_e_phase         (sr_e_phase),
        .sr_e_magnitude     (sr_e_magnitude),
        .sr_sidelobe        (sr_sidelobe),
        .packet_config_id   (packet_config_id),
        .packet_tx_count    (packet_tx_count),
        .packet_rx_count    (packet_rx_count),
        .packet_crc_count   (packet_crc_count),
        .queue_tail         (queue_tail),
        .queue_head         (queue_head),
        .queue_dropped_count(queue_dropped_count),
        .pps_out_control    (pps_out_control),
        .pps_out_offset     (pps_out_offset),
        .pps_out_sample     (pps_out_sample),
        .pps_out_width      (pps_out_width),
        .pps_out_amplitude  (pps_out_amplitude),
        .pps_out_idle       (pps_out_idle),
        .pps_out_frac       (pps_out_frac),
        .pps_out_status     ({pps_out_invalid, pps_out_flywheel, pps_out_running, pps_out_have_ref}),
        .pps_out_count      (pps_out_count),
        .pps_out_flywheel_count(pps_out_flywheel_count),
        .pps_out_tick       (pps_out_tick)
    );

    // ---------------- P3 ----------------
    wire [DATA_W-1:0] player_tdata, burst_tdata;
    wire              player_tvalid, burst_tvalid;
    assign m_axis_dac_tdata  = player_tdata | burst_tdata;   // each is zero outside its bursts
    assign m_axis_dac_tvalid = player_tvalid | burst_tvalid;

    p3_tx_player #(
        .DATA_W         (DATA_W),
        .ADDR_W         (TX_ADDR_W),
        .DEPTH          (TX_DEPTH),
        .BRAM_ADDR_W    (TX_ADDR_W + 5),
        .MIN_LEAD_TICKS ({32'd0, MIN_LEAD_TICKS})
    ) u_p3 (
        .clk           (clk),
        .rst           (rst),
        .raw_tick      (raw_tick),
        .time_valid    (time_valid),
        .ext_busy      (engine_lock),
        .doorbell      (tx_doorbell),
        .cancel        (tx_cancel),
        .clear_fault   (clear_fault),
        .cmd_target    (tx_target),
        .cmd_length    (tx_length),
        .cmd_repeat    (tx_repeat),
        .bram_en       (bram_en),
        .bram_we       (bram_we),
        .bram_addr     (bram_addr),
        .bram_wrdata   (bram_wrdata),
        .bram_rddata   (bram_rddata),
        .m_axis_tdata  (player_tdata),
        .m_axis_tvalid (player_tvalid),
        .m_axis_tready (m_axis_dac_tready),
        .state         (tx_state),
        .result        (tx_result),
        .seq           (tx_seq),
        .actual_tick   (tx_actual_tick),
        .fault         (tx_fault)
    );

    // ---------------- P4 ----------------
    p4_rx_capture #(
        .DATA_W         (DATA_W),
        .MAX_BEATS      (RX_MAX_BEATS),
        .FIFO_ADDR_W    (RX_FIFO_ADDR_W),
        .MIN_LEAD_TICKS ({32'd0, MIN_LEAD_TICKS})
    ) u_p4 (
        .clk               (clk),
        .rst               (rst),
        .raw_tick          (raw_tick),
        .time_valid        (time_valid),
        .doorbell          (rx_doorbell),
        .cancel            (rx_cancel),
        .cmd_target        (rx_target),
        .cmd_length        (rx_length),
        .ext_busy          (engine_lock),
        .int_doorbell      (rx_arm && rx_copy),
        .int_target        (ev_tick),
        .int_length        (span_beats),
        .s_axis_adc_tdata  (s_axis_adc_tdata),
        .s_axis_adc_tvalid (s_axis_adc_tvalid),
        .adc_overrange     (overrange_sync[1]),
        .dma_clk           (dma_clk),
        .dma_rst           (dma_rst),
        .m_axis_tdata      (m_axis_s2mm_tdata),
        .m_axis_tvalid     (m_axis_s2mm_tvalid),
        .m_axis_tready     (m_axis_s2mm_tready),
        .m_axis_tlast      (m_axis_s2mm_tlast),
        .state             (rx_state),
        .result            (rx_result),
        .flags             (rx_flags),
        .seq               (rx_seq),
        .beats             (rx_beats),
        .actual_tick       (rx_actual_tick),
        .invalid_count     (rx_invalid_count),
        .dma_ready         (dma_ready)
    );
    assign m_axis_s2mm_tkeep = {(DATA_W/8){1'b1}};

    // ---------------- burst memory ----------------
    wire [3:0]         prn_we, code_we;
    wire [CHIP_AW-1:0] mem_waddr;
    wire [31:0]        mem_wdata;
    wire [81*16-1:0]   taps;

    p_burst_mem #(.AW(CHIP_AW)) u_bmem (
        .clk (clk), .rst (rst), .lock (engine_lock),
        .bram_en (burst_en), .bram_we (burst_we), .bram_addr (burst_addr),
        .bram_wrdata (burst_wrdata), .bram_rddata (burst_rddata),
        .prn_we (prn_we), .code_we (code_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata),
        .taps (taps), .rej_count (burst_mem_rej_count)
    );

    // ---------------- P7 ----------------

    p7_slot_engine #(
        .SLOT_TICKS (SLOT_TICKS),
        .SLOTS      (SLOTS),
        .AW         (CHIP_AW)
    ) u_p7 (
        .clk (clk), .rst (rst), .raw_tick (raw_tick), .time_valid (time_valid), .pps_locked (pps_locked),
        .pps_event (pps_event), .pps_tick (pps_tick), .pps_count (pps_count),
        .cfg_control (slot_control), .cfg_tx_offset (slot_tx_offset), .cfg_rx_offset (slot_rx_offset),
        .cfg_prn_chips (burst_prn_chips[CHIP_AW+5:0]), .cfg_guard_chips (burst_guard_chips),
        .window_beats (WINDOW_BEATS), .packet_chips (PACKET_CHIPS), .copy_request (slot_copy),
        .tx_busy (burst_busy), .tx_fault (burst_fault), .rx_busy (rx_busy_p8),
        .running (slots_running), .slot (slot), .skipped_count (slot_skipped_count),
        .copy_pending (slot_copy_pending), .role_b (role_b), .packets (packets_active),
        .prn_chips (prn_active), .guard_chips (guard_active),
        .burst_beats (burst_beats), .span_beats (span_beats),
        .tx_arm (tx_arm), .rx_arm (rx_arm), .rx_copy (rx_copy),
        .ev_tick (ev_tick), .ev_slot (ev_slot), .sec_count (sec_count), .sec_tick (sec_tick)
    );

    // ---------------- P3 bursts from chips ----------------
    wire [PBITS-1:0] packet;

    p3_burst_gen #(.AW(CHIP_AW), .SPREAD(SPREAD), .PBITS(PBITS)) u_burst (
        .clk (clk), .rst (rst), .raw_tick (raw_tick), .clear_fault (clear_fault),
        .arm (tx_arm), .target (ev_tick), .prn_chips (prn_active), .guard_chips (guard_active),
        .packets (packets_active), .beats (burst_beats), .packet (packet),
        .prn_we (prn_we), .code_we (code_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata), .taps (taps),
        .m_axis_tdata (burst_tdata), .m_axis_tvalid (burst_tvalid), .m_axis_tready (m_axis_dac_tready),
        .busy (burst_busy), .fault (burst_fault), .started (burst_started), .actual_tick (burst_actual_tick)
    );

    always @(posedge clk) begin
        if (rst) begin
            burst_count     <= 32'd0;
            packet_tx_count <= 32'd0;
        end else if (burst_started) begin
            burst_count <= burst_count + 32'd1;
            if (packets_active) packet_tx_count <= packet_tx_count + 32'd1;
        end
    end

    // ---------------- P8 ----------------
    // ADC beats with the raw_tick of their bus cycle, as P4 takes them
    reg  [DATA_W-1:0] adc_r;
    reg               adc_rv, adc_ror;
    reg  [63:0]       tick_r;
    always @(posedge clk) begin
        adc_r   <= s_axis_adc_tdata;
        adc_rv  <= rst ? 1'b0 : s_axis_adc_tvalid;
        adc_ror <= rst ? 1'b0 : overrange_sync[1];
        tick_r  <= raw_tick;
    end

    wire               span_v, span_dsp;
    wire [31:0]        span_m, span_pps_count;
    wire [DATA_W-1:0]  span_d;
    wire [15:0]        span_lag;
    wire [93:0]        span_corr;
    wire [8:0]         span_slot;
    wire [63:0]        span_tick;
    wire               meas_valid;
    wire [63:0]        meas_trx;
    wire [31:0]        meas_phase;
    wire [7:0]         meas_quality;
    wire [3:0]         meas_flags;
    wire               own_req, own_ack, peer_req, peer_ack;
    wire [511:0]       own_entry, peer_entry;

    p8_correlator #(
        .N  (SEARCH_LANES),
        .AW (CHIP_AW),
        .BW (WIN_BW),
        .KW (LAG_KW),
        .WB (WINDOW_BEATS)
    ) u_p8 (
        .clk (clk), .rst (rst), .time_valid (time_valid), .running (slots_running),
        .adc_d (adc_r), .adc_v (adc_rv), .adc_or (adc_ror), .tick_d (tick_r),
        .rx_arm (rx_arm), .ev_tick (ev_tick), .ev_slot (ev_slot), .sec_count (sec_count), .sec_tick (sec_tick),
        .span_beats (span_beats), .prn_chips (prn_active), .packets (packets_active),
        .threshold (p8_config[P8CFG_THRESHOLD_LSB +: 16]), .lock_min (p8_config[P8CFG_LOCK_MIN_LSB +: 16]),
        .quality_offset (quality_offset),
        .cmd_search (p8_cmd_search), .cmd_accept_strongest (p8_cmd_accept_s),
        .cmd_accept_earliest (p8_cmd_accept_e), .cmd_unlock (p8_cmd_unlock),
        .prn_we (prn_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata), .taps (taps),
        .rx_busy (rx_busy_p8), .locked (p8_locked), .search_busy (p8_search_busy), .waiting (p8_waiting),
        .lock_lag (p8_lock_lag), .lock_mag (p8_lock_mag),
        .lost_count (p8_lost_count), .relock_count (p8_relock_count), .search_count (p8_search_count),
        .refused_count (p8_refused_count), .search_lags (p8_search_lags),
        .sr_seq (sr_seq), .sr_pps_count (sr_pps_count), .sr_slot (sr_slot), .sr_flags (sr_flags), .sr_tick (sr_tick),
        .sr_s_lag (sr_s_lag), .sr_s_fraction (sr_s_fraction), .sr_s_phase (sr_s_phase), .sr_s_magnitude (sr_s_magnitude),
        .sr_e_lag (sr_e_lag), .sr_e_fraction (sr_e_fraction), .sr_e_phase (sr_e_phase), .sr_e_magnitude (sr_e_magnitude),
        .sr_sidelobe (sr_sidelobe),
        .span_v (span_v), .span_m (span_m), .span_d (span_d), .span_dsp (span_dsp), .span_lag (span_lag),
        .span_corr (span_corr), .span_pps_count (span_pps_count), .span_slot (span_slot), .span_tick (span_tick),
        .meas_valid (meas_valid), .meas_trx (meas_trx), .meas_phase (meas_phase),
        .meas_quality (meas_quality), .meas_flags (meas_flags),
        .own_req (own_req), .own_entry (own_entry), .own_ack (own_ack)
    );

    // ---------------- P9 and the queue ----------------
    p9_packet #(.AW(CHIP_AW), .SPREAD(SPREAD), .PBITS(PBITS)) u_p9 (
        .clk (clk), .rst (rst),
        .tx_arm (tx_arm), .ev_tick (ev_tick), .ev_slot (ev_slot), .sec_count (sec_count), .sec_tick (sec_tick),
        .time_epoch (time_epoch[7:0]), .config_id (packet_config_id), .locked (p8_locked),
        .pps_locked (pps_locked), .clocks_locked (clocks_locked),
        .meas_valid (meas_valid), .meas_trx (meas_trx), .meas_phase (meas_phase),
        .meas_quality (meas_quality), .meas_flags (meas_flags),
        .packet (packet), .packet_ready (),
        .span_v (span_v), .span_m (span_m), .span_d (span_d), .span_dsp (span_dsp), .span_lag (span_lag),
        .span_corr (span_corr), .span_pps_count (span_pps_count), .span_slot (span_slot), .span_tick (span_tick),
        .prn_chips (prn_active), .guard_chips (guard_active),
        .code_we (code_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata), .taps (taps),
        .rx_count (packet_rx_count), .crc_count (packet_crc_count),
        .peer_req (peer_req), .peer_entry (peer_entry), .peer_ack (peer_ack)
    );

    p9_queue #(.ENTRIES(QUEUE_ENTRIES)) u_queue (
        .clk (clk), .rst (rst),
        .own_req (own_req), .own_entry (own_entry), .own_ack (own_ack),
        .peer_req (peer_req), .peer_entry (peer_entry), .peer_ack (peer_ack),
        .tail (queue_tail), .head (queue_head), .dropped_count (queue_dropped_count),
        .bram_en (queue_en), .bram_addr (queue_addr), .bram_rddata (queue_rddata)
    );

    // ---------------- P10 output PPS (2.1) ----------------
    p10_pps_out #(
        .DATA_W     (DATA_W),
        .PPS_PERIOD (PPS_PERIOD),
        .MIN_OFFSET (PPSOUT_MIN_OFFSET)
    ) u_p10 (
        .clk             (clk),
        .rst             (rst),
        .raw_tick        (raw_tick),
        .pps_event       (pps_event),
        .pps_tick        (pps_tick),
        .pps_locked      (pps_locked),
        .clocks_locked   (clocks_locked),
        .cfg_enable      (pps_out_control[PPSOUTCTL_ENABLE]),
        .cfg_synced      (pps_out_control[PPSOUTCTL_SYNCED]),
        .cfg_offset      (pps_out_offset),
        .cfg_sample      (pps_out_sample),
        .cfg_width       (pps_out_width),
        .cfg_amplitude   (pps_out_amplitude),
        .cfg_idle        (pps_out_idle),
        .cfg_frac_en     (pps_out_control[PPSOUTCTL_FRAC]),
        .cfg_frac        (pps_out_frac),
        .m_axis_tdata    (m_axis_dac0_tdata),
        .m_axis_tvalid   (m_axis_dac0_tvalid),
        .m_axis_tready   (m_axis_dac0_tready),
        .have_ref        (pps_out_have_ref),
        .running         (pps_out_running),
        .last_flywheel   (pps_out_flywheel),
        .pulse_count     (pps_out_count),
        .flywheel_count  (pps_out_flywheel_count),
        .last_tick       (pps_out_tick),
        .pps_out_invalid (pps_out_invalid)
    );
    assign pps_out_led = !pps_out_invalid;

    // ---------------- P6 ----------------
    wire [3:0] fault_in;
    assign fault_in[FB_LOCK_LOST]      = lock_lost_event;
    assign fault_in[FB_PPS]            = pps_fault_event;
    assign fault_in[FB_TICK_EXHAUSTED] = raw_tick_exhausted;
    assign fault_in[FB_TX]             = tx_fault || burst_fault;

    p6_status #(
        .N (4)
    ) u_p6 (
        .clk          (clk),
        .rst          (rst),
        .clear_fault  (clear_fault),
        .epoch_start  (epoch_start_event),
        .fault_in     (fault_in),
        .fault_bits   (fault_bits),
        .first_source (fault_first_source),
        .fault_count  (fault_count),
        .any_fault    (any_fault)
    );
endmodule
