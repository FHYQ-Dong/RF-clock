`timescale 1ns / 1ps
// P2: AXI4-Lite control and status registers (fabric clock domain; the
// block design puts an AXI clock converter in front of this slave).
//
// The register map is defined in p_regs.vh and documented for software in
// docs/ABI.md.  Writes must use wstrb = 4'hF.  Partial writes, writes to
// read-only words and accesses to unmapped words return SLVERR and change
// nothing.
module p2_control_regs #(
    parameter [31:0] TX_DEPTH_BEATS = 32'd8192,
    parameter [31:0] RX_MAX_BEATS   = 32'd2097151,
    parameter [31:0] MIN_LEAD_TICKS = 32'd64,
    parameter [31:0] BUILD_VERSION  = 32'd0
) (
    input  wire        clk,
    input  wire        rst,
    // AXI4-Lite slave.
    input  wire [7:0]  s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [7:0]  s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    // Commands (one-cycle pulses) and shadow fields.
    output reg         tx_doorbell,
    output reg         tx_cancel,
    output reg         rx_doorbell,
    output reg         rx_cancel,
    output reg         clear_fault,
    output reg  [63:0] tx_target,
    output reg  [31:0] tx_length,
    output reg  [31:0] tx_repeat,
    output reg  [63:0] rx_target,
    output reg  [31:0] rx_length,
    output reg  [1:0]  pps_config,   // {PPSCFG_INVERT, PPSCFG_SRC_TRIG}
    // Status.
    input  wire        time_valid,
    input  wire        clocks_locked,
    input  wire        pps_locked,
    input  wire        raw_tick_exhausted,
    input  wire        any_fault,
    input  wire        dma_ready,
    input  wire [31:0] time_epoch,
    input  wire [3:0]  fault_bits,
    input  wire [3:0]  fault_first_source,
    input  wire [15:0] fault_count,
    input  wire [63:0] raw_tick,
    input  wire [63:0] pps_tick,
    input  wire [31:0] pps_count,
    input  wire [31:0] pps_last_period,
    input  wire [31:0] pps_jitter_count,
    input  wire        pps_comp_level,
    input  wire        pps_trig_level,
    input  wire [7:0]  pps_comp_edges,
    input  wire [7:0]  pps_trig_edges,
    input  wire [2:0]  tx_state,
    input  wire [7:0]  tx_result,
    input  wire [31:0] tx_seq,
    input  wire [63:0] tx_actual_tick,
    input  wire [2:0]  rx_state,
    input  wire [7:0]  rx_result,
    input  wire [3:0]  rx_flags,
    input  wire [31:0] rx_seq,
    input  wire [31:0] rx_beats,
    input  wire [63:0] rx_actual_tick,
    input  wire [31:0] rx_invalid_count
);
    `include "src/p_regs.vh"

    localparam [1:0] RESP_OKAY   = 2'b00;
    localparam [1:0] RESP_SLVERR = 2'b10;

    // ---------------------------------------------------------------
    // Write channel: accept AW and W together, one outstanding write.
    // No ready during rst: the AXI side may leave reset before P0's
    // synchronized rst, and an accepted-then-dropped access hangs the bus.
    // ---------------------------------------------------------------
    wire wr_fire = !rst && s_axi_awvalid && s_axi_wvalid && !s_axi_bvalid;
    assign s_axi_awready = wr_fire;
    assign s_axi_wready  = wr_fire;
    wire [7:0] wr_addr = {s_axi_awaddr[7:2], 2'b00};

    always @(posedge clk) begin
        tx_doorbell <= 1'b0;
        tx_cancel   <= 1'b0;
        rx_doorbell <= 1'b0;
        rx_cancel   <= 1'b0;
        clear_fault <= 1'b0;
        if (rst) begin
            s_axi_bvalid <= 1'b0;
            s_axi_bresp  <= RESP_OKAY;
            tx_target    <= 64'd0;
            tx_length    <= 32'd0;
            tx_repeat    <= 32'd1;
            rx_target    <= 64'd0;
            rx_length    <= 32'd0;
            pps_config   <= 2'b00;
        end else begin
            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;
            if (wr_fire) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= RESP_OKAY;
                if (s_axi_wstrb != 4'hF) begin
                    s_axi_bresp <= RESP_SLVERR;
                end else begin
                    case (wr_addr)
                        REG_CONTROL: begin
                            tx_doorbell <= s_axi_wdata[CTRL_TX_DOORBELL];
                            tx_cancel   <= s_axi_wdata[CTRL_TX_CANCEL];
                            rx_doorbell <= s_axi_wdata[CTRL_RX_DOORBELL];
                            rx_cancel   <= s_axi_wdata[CTRL_RX_CANCEL];
                            clear_fault <= s_axi_wdata[CTRL_CLEAR_FAULT];
                        end
                        REG_TX_TARGET_LO: tx_target[31:0]  <= s_axi_wdata;
                        REG_TX_TARGET_HI: tx_target[63:32] <= s_axi_wdata;
                        REG_TX_LENGTH:    tx_length        <= s_axi_wdata;
                        REG_TX_REPEAT:    tx_repeat        <= s_axi_wdata;
                        REG_RX_TARGET_LO: rx_target[31:0]  <= s_axi_wdata;
                        REG_RX_TARGET_HI: rx_target[63:32] <= s_axi_wdata;
                        REG_RX_LENGTH:    rx_length        <= s_axi_wdata;
                        REG_PPS_CONFIG: begin
                            pps_config[PPSCFG_SRC_TRIG] <= s_axi_wdata[PPSCFG_SRC_TRIG];
                            pps_config[PPSCFG_INVERT]   <= s_axi_wdata[PPSCFG_INVERT];
                        end
                        default:          s_axi_bresp      <= RESP_SLVERR;
                    endcase
                end
            end
        end
    end

    // ---------------------------------------------------------------
    // Read channel: one outstanding read.  Reading a *_LO word of a value
    // that keeps changing latches its HI word in the same cycle, so LO then
    // HI always forms one 64-bit snapshot regardless of bus width.
    // ---------------------------------------------------------------
    wire rd_fire = !rst && s_axi_arvalid && !s_axi_rvalid;
    assign s_axi_arready = rd_fire;
    wire [7:0] rd_addr = {s_axi_araddr[7:2], 2'b00};

    reg [31:0] tick_snap_hi;
    reg [31:0] pps_snap_hi;
    reg [31:0] pps_count_snap;

    wire [31:0] pps_inputs_word;
    assign pps_inputs_word = {8'd0, pps_trig_edges, pps_comp_edges, 6'd0,
                              pps_trig_level, pps_comp_level};

    wire [31:0] status_word;
    assign status_word = {17'd0, rx_state, 1'b0, tx_state, 2'b00,
                          dma_ready, any_fault, raw_tick_exhausted,
                          pps_locked, clocks_locked, time_valid};

    reg [31:0] rd_value;
    reg        rd_ok;
    always @* begin
        rd_ok    = 1'b1;
        rd_value = 32'd0;
        case (rd_addr)
            REG_ID:              rd_value = P_DESIGN_ID;
            REG_VERSION:         rd_value = P_VERSION;
            REG_CONTROL:         rd_value = 32'd0;
            REG_STATUS:          rd_value = status_word;
            REG_TIME_EPOCH:      rd_value = time_epoch;
            REG_FAULT_BITS:      rd_value = {28'd0, fault_bits};
            REG_FAULT_INFO:      rd_value = {fault_count, 12'd0, fault_first_source};
            REG_TICK_LO:         rd_value = raw_tick[31:0];
            REG_TICK_HI:         rd_value = tick_snap_hi;
            REG_PPS_TICK_LO:     rd_value = pps_tick[31:0];
            REG_PPS_TICK_HI:     rd_value = pps_snap_hi;
            REG_PPS_COUNT:       rd_value = pps_count_snap;
            REG_PPS_LAST_PERIOD: rd_value = pps_last_period;
            REG_PPS_JITTER_CNT:  rd_value = pps_jitter_count;
            REG_PPS_INPUTS:      rd_value = pps_inputs_word;
            REG_TX_TARGET_LO:    rd_value = tx_target[31:0];
            REG_TX_TARGET_HI:    rd_value = tx_target[63:32];
            REG_TX_LENGTH:       rd_value = tx_length;
            REG_TX_REPEAT:       rd_value = tx_repeat;
            REG_TX_RESULT:       rd_value = {24'd0, tx_result};
            REG_TX_SEQ:          rd_value = tx_seq;
            REG_TX_ACTUAL_LO:    rd_value = tx_actual_tick[31:0];
            REG_TX_ACTUAL_HI:    rd_value = tx_actual_tick[63:32];
            REG_RX_TARGET_LO:    rd_value = rx_target[31:0];
            REG_RX_TARGET_HI:    rd_value = rx_target[63:32];
            REG_RX_LENGTH:       rd_value = rx_length;
            REG_RX_RESULT:       rd_value = {20'd0, rx_flags, rx_result};
            REG_RX_SEQ:          rd_value = rx_seq;
            REG_RX_BEATS:        rd_value = rx_beats;
            REG_RX_ACTUAL_LO:    rd_value = rx_actual_tick[31:0];
            REG_RX_ACTUAL_HI:    rd_value = rx_actual_tick[63:32];
            REG_RX_INVALID_CNT:  rd_value = rx_invalid_count;
            REG_TX_DEPTH:        rd_value = TX_DEPTH_BEATS;
            REG_RX_MAX_BEATS:    rd_value = RX_MAX_BEATS;
            REG_MIN_LEAD_TICKS:  rd_value = MIN_LEAD_TICKS;
            REG_PPS_CONFIG:      rd_value = {30'd0, pps_config};
            REG_BUILD_VERSION:   rd_value = BUILD_VERSION;
            default:             rd_ok    = 1'b0;
        endcase
    end

    always @(posedge clk) begin
        if (rst) begin
            s_axi_rvalid   <= 1'b0;
            s_axi_rdata    <= 32'd0;
            s_axi_rresp    <= RESP_OKAY;
            tick_snap_hi   <= 32'd0;
            pps_snap_hi    <= 32'd0;
            pps_count_snap <= 32'd0;
        end else begin
            if (s_axi_rvalid && s_axi_rready)
                s_axi_rvalid <= 1'b0;
            if (rd_fire) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= rd_value;
                s_axi_rresp  <= rd_ok ? RESP_OKAY : RESP_SLVERR;
                if (rd_addr == REG_TICK_LO)
                    tick_snap_hi <= raw_tick[63:32];
                if (rd_addr == REG_PPS_TICK_LO) begin
                    pps_snap_hi    <= pps_tick[63:32];
                    pps_count_snap <= pps_count;
                end
            end
        end
    end
endmodule
