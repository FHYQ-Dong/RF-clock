`timescale 1ns / 1ps
// RF-clock PL top (RTL part of the block design).
//
// Everything except the RX DMA side runs on `clk`, the 100 MHz fabric clock
// derived from the node's FS725 10 MHz; the RFDC DAC/ADC AXIS must be on the
// same clock.  `dma_clk` is the AXI DMA S2MM clock (>= 200 MHz for
// 3.2 GB/s through a 128-bit HP port after the BD width converter).
//
// P5 (RFDC IP, MTS/SYSREF, clocking) lives in the block design, not here.
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
    parameter integer BUILD_VERSION  = 0
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi:m_axis_dac:s_axis_adc, ASSOCIATED_RESET arst_n" *)
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
    input  wire [7:0]                s_axi_awaddr,
    input  wire                      s_axi_awvalid,
    output wire                      s_axi_awready,
    input  wire [31:0]               s_axi_wdata,
    input  wire [3:0]                s_axi_wstrb,
    input  wire                      s_axi_wvalid,
    output wire                      s_axi_wready,
    output wire [1:0]                s_axi_bresp,
    output wire                      s_axi_bvalid,
    input  wire                      s_axi_bready,
    input  wire [7:0]                s_axi_araddr,
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

    // RFDC DAC AXI4-Stream.
    output wire [DATA_W-1:0]         m_axis_dac_tdata,
    output wire                      m_axis_dac_tvalid,
    input  wire                      m_axis_dac_tready,

    // RFDC ADC AXI4-Stream (I/Q streams combined to DATA_W in the BD).
    input  wire [DATA_W-1:0]         s_axis_adc_tdata,
    input  wire                      s_axis_adc_tvalid,
    input  wire                      adc_overrange,  // RFDC status, asynchronous

    // AXI DMA S2MM stream (dma_clk).
    output wire [DATA_W-1:0]         m_axis_s2mm_tdata,
    output wire [DATA_W/8-1:0]       m_axis_s2mm_tkeep,
    output wire                      m_axis_s2mm_tvalid,
    input  wire                      m_axis_s2mm_tready,
    output wire                      m_axis_s2mm_tlast
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
        .BUILD_VERSION  (BUILD_VERSION)
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
        .rx_invalid_count   (rx_invalid_count)
    );

    // ---------------- P3 ----------------
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
        .m_axis_tdata  (m_axis_dac_tdata),
        .m_axis_tvalid (m_axis_dac_tvalid),
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

    // ---------------- P6 ----------------
    wire [3:0] fault_in;
    assign fault_in[FB_LOCK_LOST]      = lock_lost_event;
    assign fault_in[FB_PPS]            = pps_fault_event;
    assign fault_in[FB_TICK_EXHAUSTED] = raw_tick_exhausted;
    assign fault_in[FB_TX]             = tx_fault;

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
