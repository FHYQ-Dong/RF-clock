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
    parameter [31:0] BUILD_VERSION  = 32'd0,
    // 2.0 build parameters (read-only registers)
    parameter [31:0] SLOT_TICKS     = 32'd200000,
    parameter [31:0] SLOTS          = 32'd500,
    parameter [31:0] WINDOW_BEATS   = 32'd5481,
    parameter [31:0] SPREAD         = 32'd64,
    parameter [31:0] PACKET_BITS    = 32'd288,
    parameter [31:0] CHIP_CHIPS     = 32'd65536,
    parameter [31:0] TRACK_HALF     = 32'd4,
    parameter [31:0] SEARCH_LANES   = 32'd1024,
    parameter [31:0] QUEUE_ENTRIES  = 32'd1024,
    // 2.1: the output PPS's offset must lie inside one PPS period
    parameter [31:0] PPS_PERIOD     = 32'd100_000_000
) (
    input  wire        clk,
    input  wire        rst,
    // AXI4-Lite slave.
    input  wire [11:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [11:0] s_axi_araddr,
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
    input  wire [31:0] rx_invalid_count,
    // P7 slot engine (since 2.0).
    output reg  [2:0]  slot_control,
    output reg  [31:0] slot_tx_offset,
    output reg  [31:0] slot_rx_offset,
    output reg         slot_copy,        // pulse: SLOT_COPY written with 1
    input  wire        slots_running,
    input  wire [8:0]  slot,
    input  wire [31:0] slot_skipped_count,
    input  wire        slot_copy_pending,
    input  wire [31:0] rx_span_beats,
    // P3 bursts from chips.
    output reg  [16:0] burst_prn_chips,
    output reg  [15:0] burst_guard_chips,
    input  wire [31:0] burst_beats,
    input  wire [31:0] burst_count,
    input  wire [31:0] burst_mem_rej_count,
    // P8 correlator.
    output reg         p8_search,
    output reg         p8_accept_strongest,
    output reg         p8_accept_earliest,
    output reg         p8_unlock,
    output reg  [31:0] p8_config,
    output reg  [31:0] quality_offset,
    input  wire        p8_locked,
    input  wire        p8_search_busy,
    input  wire        p8_waiting,
    input  wire [15:0] p8_lock_lag,
    input  wire [46:0] p8_lock_mag,
    input  wire [31:0] p8_lost_count,
    input  wire [31:0] p8_relock_count,
    input  wire [31:0] p8_search_count,
    input  wire [31:0] p8_refused_count,
    input  wire [31:0] p8_search_lags,
    input  wire [31:0] sr_seq,
    input  wire [31:0] sr_pps_count,
    input  wire [8:0]  sr_slot,
    input  wire [3:0]  sr_flags,
    input  wire [63:0] sr_tick,
    input  wire [15:0] sr_s_lag,
    input  wire [31:0] sr_s_fraction,
    input  wire [31:0] sr_s_phase,
    input  wire [46:0] sr_s_magnitude,
    input  wire [15:0] sr_e_lag,
    input  wire [31:0] sr_e_fraction,
    input  wire [31:0] sr_e_phase,
    input  wire [46:0] sr_e_magnitude,
    input  wire [46:0] sr_sidelobe,
    // P9 packet and the measurement queue.
    output reg  [31:0] packet_config_id,
    input  wire [31:0] packet_tx_count,
    input  wire [31:0] packet_rx_count,
    input  wire [31:0] packet_crc_count,
    output reg  [31:0] queue_tail,
    input  wire [31:0] queue_head,
    input  wire [31:0] queue_dropped_count,
    // P10 output PPS (since 2.1).
    output reg  [1:0]  pps_out_control,  // {PPSOUTCTL_SYNCED, PPSOUTCTL_ENABLE}
    output reg  [31:0] pps_out_offset,
    output reg  [2:0]  pps_out_sample,
    output reg  [15:0] pps_out_width,
    output reg  [15:0] pps_out_amplitude,
    output reg  [15:0] pps_out_idle,      // 2.2
    input  wire [3:0]  pps_out_status,
    input  wire [31:0] pps_out_count,
    input  wire [31:0] pps_out_flywheel_count,
    input  wire [63:0] pps_out_tick
);
    `include "src/p_regs.vh"
    `include "src/p_defs.vh"

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
    wire [11:0] wr_addr = {s_axi_awaddr[11:2], 2'b00};

    always @(posedge clk) begin
        tx_doorbell <= 1'b0;
        tx_cancel   <= 1'b0;
        rx_doorbell <= 1'b0;
        rx_cancel   <= 1'b0;
        clear_fault <= 1'b0;
        slot_copy   <= 1'b0;
        p8_search           <= 1'b0;
        p8_accept_strongest <= 1'b0;
        p8_accept_earliest  <= 1'b0;
        p8_unlock           <= 1'b0;
        if (rst) begin
            s_axi_bvalid <= 1'b0;
            s_axi_bresp  <= RESP_OKAY;
            tx_target    <= 64'd0;
            tx_length    <= 32'd0;
            tx_repeat    <= 32'd1;
            rx_target    <= 64'd0;
            rx_length    <= 32'd0;
            pps_config   <= 2'b00;
            slot_control      <= 3'd0;
            slot_tx_offset    <= 32'd1000;
            slot_rx_offset    <= 32'd0;
            burst_prn_chips   <= 17'd4096;
            burst_guard_chips <= 16'd10;
            p8_config         <= 32'h8000_8000;
            quality_offset    <= 32'd0;
            packet_config_id  <= 32'd0;
            queue_tail        <= 32'd0;
            pps_out_control   <= 2'd0;
            pps_out_offset    <= PPSOUT_MIN_OFFSET;
            pps_out_sample    <= 3'd0;
            pps_out_width     <= 16'd4;
            pps_out_amplitude <= 16'd0;
            pps_out_idle      <= 16'd0;
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
                        REG_SLOT_CONTROL:      slot_control   <= s_axi_wdata[2:0];
                        REG_SLOT_TX_OFFSET:    slot_tx_offset <= s_axi_wdata;
                        REG_SLOT_RX_OFFSET:    slot_rx_offset <= s_axi_wdata;
                        REG_SLOT_COPY:         slot_copy      <= s_axi_wdata[0];
                        REG_BURST_PRN_CHIPS: begin
                            if (s_axi_wdata == 32'd0 || s_axi_wdata > CHIP_CHIPS)
                                s_axi_bresp <= RESP_SLVERR;
                            else
                                burst_prn_chips <= s_axi_wdata[16:0];
                        end
                        REG_BURST_GUARD_CHIPS: burst_guard_chips <= s_axi_wdata[15:0];
                        REG_P8_CONTROL: begin
                            p8_search           <= s_axi_wdata[P8CTL_SEARCH];
                            p8_accept_strongest <= s_axi_wdata[P8CTL_ACCEPT_STRONGEST];
                            p8_accept_earliest  <= s_axi_wdata[P8CTL_ACCEPT_EARLIEST];
                            p8_unlock           <= s_axi_wdata[P8CTL_UNLOCK];
                        end
                        REG_P8_CONFIG:         p8_config        <= s_axi_wdata;
                        REG_QUALITY_OFFSET:    quality_offset   <= s_axi_wdata;
                        REG_PACKET_CONFIG_ID:  packet_config_id <= s_axi_wdata;
                        REG_QUEUE_TAIL: begin
                            if (s_axi_wdata >= QUEUE_ENTRIES)
                                s_axi_bresp <= RESP_SLVERR;
                            else
                                queue_tail <= s_axi_wdata;
                        end
                        REG_PPS_OUT_CONTROL: pps_out_control <= s_axi_wdata[1:0];
                        REG_PPS_OUT_OFFSET: begin
                            if (s_axi_wdata < PPSOUT_MIN_OFFSET || s_axi_wdata >= PPS_PERIOD)
                                s_axi_bresp <= RESP_SLVERR;
                            else
                                pps_out_offset <= s_axi_wdata;
                        end
                        REG_PPS_OUT_SAMPLE: begin
                            if (s_axi_wdata > 32'd7)
                                s_axi_bresp <= RESP_SLVERR;
                            else
                                pps_out_sample <= s_axi_wdata[2:0];
                        end
                        REG_PPS_OUT_WIDTH: begin
                            if (s_axi_wdata == 32'd0 || s_axi_wdata > 32'd65535)
                                s_axi_bresp <= RESP_SLVERR;
                            else
                                pps_out_width <= s_axi_wdata[15:0];
                        end
                        REG_PPS_OUT_AMPLITUDE: pps_out_amplitude <= s_axi_wdata[15:0];
                        REG_PPS_OUT_IDLE:      pps_out_idle      <= s_axi_wdata[15:0];
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
    wire [11:0] rd_addr = {s_axi_araddr[11:2], 2'b00};

    reg [31:0] tick_snap_hi;
    reg [31:0] pps_snap_hi;
    reg [31:0] pps_count_snap;
    reg [31:0] pps_out_snap_hi;

    wire [31:0] pps_inputs_word;
    assign pps_inputs_word = {8'd0, pps_trig_edges, pps_comp_edges, 6'd0,
                              pps_trig_level, pps_comp_level};

    wire [31:0] status_word;
    assign status_word = {17'd0, rx_state, 1'b0, tx_state, p8_locked, slots_running,
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
            REG_SLOT_CONTROL:      rd_value = {29'd0, slot_control};
            REG_SLOT_TX_OFFSET:    rd_value = slot_tx_offset;
            REG_SLOT_RX_OFFSET:    rd_value = slot_rx_offset;
            REG_SLOT_STATUS:       rd_value = {7'd0, slot, 15'd0, slots_running};
            REG_SLOT_SKIPPED_CNT:  rd_value = slot_skipped_count;
            REG_SLOT_COPY:         rd_value = {31'd0, slot_copy_pending};
            REG_SLOT_TICKS:        rd_value = SLOT_TICKS;
            REG_SLOTS_PER_SECOND:  rd_value = SLOTS;
            REG_RX_WINDOW_BEATS:   rd_value = WINDOW_BEATS;
            REG_RX_SPAN_BEATS:     rd_value = rx_span_beats;
            REG_BURST_PRN_CHIPS:   rd_value = {15'd0, burst_prn_chips};
            REG_BURST_GUARD_CHIPS: rd_value = {16'd0, burst_guard_chips};
            REG_BURST_BEATS:       rd_value = burst_beats;
            REG_BURST_CNT:         rd_value = burst_count;
            REG_PACKET_SPREAD:     rd_value = SPREAD;
            REG_PACKET_BITS:       rd_value = PACKET_BITS;
            REG_CHIP_MEM_CHIPS:    rd_value = CHIP_CHIPS;
            REG_BURST_MEM_REJ_CNT: rd_value = burst_mem_rej_count;
            REG_P8_CONTROL:        rd_value = 32'd0;
            REG_P8_CONFIG:         rd_value = p8_config;
            REG_P8_STATUS:         rd_value = {29'd0, p8_waiting, p8_search_busy, p8_locked};
            REG_P8_LOCK_LAG:       rd_value = {16'd0, p8_lock_lag};
            REG_P8_LOCK_MAG_LO:    rd_value = p8_lock_mag[31:0];
            REG_P8_LOCK_MAG_HI:    rd_value = {17'd0, p8_lock_mag[46:32]};
            REG_P8_LOST_CNT:       rd_value = p8_lost_count;
            REG_P8_RELOCK_CNT:     rd_value = p8_relock_count;
            REG_P8_SEARCH_CNT:     rd_value = p8_search_count;
            REG_P8_REFUSED_CNT:    rd_value = p8_refused_count;
            REG_P8_TRACK_HALF:     rd_value = TRACK_HALF;
            REG_P8_SEARCH_LANES:   rd_value = SEARCH_LANES;
            REG_P8_SEARCH_LAGS:    rd_value = p8_search_lags;
            REG_QUALITY_OFFSET:    rd_value = quality_offset;
            REG_SR_SEQ:            rd_value = sr_seq;
            REG_SR_PPS_COUNT:      rd_value = sr_pps_count;
            REG_SR_SLOT:           rd_value = {23'd0, sr_slot};
            REG_SR_FLAGS:          rd_value = {28'd0, sr_flags};
            REG_SR_TICK_LO:        rd_value = sr_tick[31:0];
            REG_SR_TICK_HI:        rd_value = sr_tick[63:32];
            REG_SR_STRONG_LAG:     rd_value = {16'd0, sr_s_lag};
            REG_SR_STRONG_FRAC:    rd_value = sr_s_fraction;
            REG_SR_STRONG_PHASE:   rd_value = sr_s_phase;
            REG_SR_STRONG_MAG_LO:  rd_value = sr_s_magnitude[31:0];
            REG_SR_STRONG_MAG_HI:  rd_value = {17'd0, sr_s_magnitude[46:32]};
            REG_SR_EARLY_LAG:      rd_value = {16'd0, sr_e_lag};
            REG_SR_EARLY_FRAC:     rd_value = sr_e_fraction;
            REG_SR_EARLY_PHASE:    rd_value = sr_e_phase;
            REG_SR_EARLY_MAG_LO:   rd_value = sr_e_magnitude[31:0];
            REG_SR_EARLY_MAG_HI:   rd_value = {17'd0, sr_e_magnitude[46:32]};
            REG_SR_SIDELOBE_LO:    rd_value = sr_sidelobe[31:0];
            REG_SR_SIDELOBE_HI:    rd_value = {17'd0, sr_sidelobe[46:32]};
            REG_PACKET_CONFIG_ID:  rd_value = packet_config_id;
            REG_PACKET_VERSION:    rd_value = {24'd0, PACKET_FORMAT_VERSION};
            REG_PACKET_TX_CNT:     rd_value = packet_tx_count;
            REG_PACKET_RX_CNT:     rd_value = packet_rx_count;
            REG_PACKET_CRC_CNT:    rd_value = packet_crc_count;
            REG_QUEUE_HEAD:        rd_value = queue_head;
            REG_QUEUE_TAIL:        rd_value = queue_tail;
            REG_QUEUE_ENTRIES:     rd_value = QUEUE_ENTRIES;
            REG_QUEUE_DROPPED_CNT: rd_value = queue_dropped_count;
            REG_PPS_OUT_CONTROL:      rd_value = {30'd0, pps_out_control};
            REG_PPS_OUT_OFFSET:       rd_value = pps_out_offset;
            REG_PPS_OUT_SAMPLE:       rd_value = {29'd0, pps_out_sample};
            REG_PPS_OUT_WIDTH:        rd_value = {16'd0, pps_out_width};
            REG_PPS_OUT_AMPLITUDE:    rd_value = {16'd0, pps_out_amplitude};
            REG_PPS_OUT_IDLE:         rd_value = {16'd0, pps_out_idle};
            REG_PPS_OUT_STATUS:       rd_value = {28'd0, pps_out_status};
            REG_PPS_OUT_CNT:          rd_value = pps_out_count;
            REG_PPS_OUT_FLYWHEEL_CNT: rd_value = pps_out_flywheel_count;
            REG_PPS_OUT_TICK_LO:      rd_value = pps_out_tick[31:0];
            REG_PPS_OUT_TICK_HI:      rd_value = pps_out_snap_hi;
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
            pps_out_snap_hi <= 32'd0;
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
                if (rd_addr == REG_PPS_OUT_TICK_LO)
                    pps_out_snap_hi <= pps_out_tick[63:32];
            end
        end
    end
endmodule
