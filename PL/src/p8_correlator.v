`timescale 1ns / 1ps
// P8: the correlator (docs/ABI.md 4.10).
//
//   ADC beats -> window capture -> buffer 0 / 1 -> tracker (every window, while locked)
//                     |                       \-> search (not locked, or on request)
//                     \-> span stream to P9 (the window and the packet behind it)
//
// The slot engine arms a window LEAD ticks ahead; its beat 0 is the ADC beat
// of raw_tick == ev_tick (tick_d is the raw_tick of the cycle adc_d was on the
// bus).  The first WB beats go to a window buffer, with the sum of I^2 + Q^2
// and the RX flags; the whole span goes to P9.  A search freezes its buffer;
// windows meanwhile go to the other one.
//
// After each window: the tracker (while locked) gives the measurement; the
// lock holds while the peak is interior and its magnitude * 2^16 >= LOCK_MIN *
// the last lock's magnitude.  Without a lock, or on a SEARCH request, the
// window is searched.  An OWN entry goes to the queue for every window.  A
// search's report is latched with its window's second, slot, tick and flags;
// not locked, the PL re-locks by itself only on a strongest peak within one
// lag of the last lock, otherwise it waits for ACCEPT_* from the ARM.
module p8_correlator #(
    parameter integer N     = 1024,  // search lanes
    parameter integer AW    = 11,
    parameter integer BW    = 13,
    parameter integer KW    = 16,
    parameter integer WB    = 5481,  // window beats
    parameter integer H     = 4
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               time_valid,
    input  wire               running,          // the slot engine runs
    // ADC beats, registered: tick_d is the raw_tick of adc_d's bus cycle.
    input  wire [255:0]       adc_d,
    input  wire               adc_v,
    input  wire               adc_or,
    input  wire [63:0]        tick_d,
    // Slot engine.
    input  wire               rx_arm,
    input  wire [63:0]        ev_tick,
    input  wire [8:0]         ev_slot,
    input  wire [31:0]        sec_count,
    input  wire [63:0]        sec_tick,
    input  wire [31:0]        span_beats,
    input  wire [AW+5:0]      prn_chips,
    input  wire               packets,
    // Configuration and commands (P2).
    input  wire [15:0]        threshold,
    input  wire [15:0]        lock_min,
    input  wire signed [31:0] quality_offset,
    input  wire               cmd_search,
    input  wire               cmd_accept_strongest,
    input  wire               cmd_accept_earliest,
    input  wire               cmd_unlock,
    // Burst memory: PRN writes and taps.
    input  wire [3:0]         prn_we,
    input  wire [AW-1:0]      mem_waddr,
    input  wire [31:0]        mem_wdata,
    input  wire [81*16-1:0]   taps,
    // Status.
    output wire               rx_busy,
    output reg                locked,
    output wire               search_busy,
    output reg                waiting,
    output reg  [15:0]        lock_lag,
    output reg  [46:0]        lock_mag,
    output reg  [31:0]        lost_count,
    output reg  [31:0]        relock_count,
    output reg  [31:0]        search_count,
    output reg  [31:0]        refused_count,
    output wire [31:0]        search_lags,
    // Search report.
    output reg  [31:0]        sr_seq,
    output reg  [31:0]        sr_pps_count,
    output reg  [8:0]         sr_slot,
    output reg  [3:0]         sr_flags,
    output reg  [63:0]        sr_tick,
    output reg  [15:0]        sr_s_lag,
    output reg  signed [31:0] sr_s_fraction,
    output reg  signed [31:0] sr_s_phase,
    output reg  [46:0]        sr_s_magnitude,
    output reg  [15:0]        sr_e_lag,
    output reg  signed [31:0] sr_e_fraction,
    output reg  signed [31:0] sr_e_phase,
    output reg  [46:0]        sr_e_magnitude,
    output reg  [46:0]        sr_sidelobe,
    // The span to P9: the beats, and what the despreader needs, taken at arm.
    output reg                span_v,
    output reg  [31:0]        span_m,
    output reg  [255:0]       span_d,
    output reg                span_dsp,         // locked with packets: despread this span
    output reg  [15:0]        span_lag,
    output reg  [93:0]        span_corr,        // {I, Q} of the lock's correlation
    output reg  [31:0]        span_pps_count,
    output reg  [8:0]         span_slot,
    output reg  [63:0]        span_tick,
    // The latest measurement, for this node's packet.
    output reg                meas_valid,
    output reg  [63:0]        meas_trx,
    output reg  signed [31:0] meas_phase,
    output reg  [7:0]         meas_quality,
    output reg  [3:0]         meas_flags,
    // OWN queue entry.
    output reg                own_req,
    output reg  [511:0]       own_entry,
    input  wire               own_ack
);
    `include "src/p_defs.vh"

    localparam [BW:0] WBV = WB;

    // K = 8 WB - (8 prn + 73) + 1; a search needs K >= 3
    wire signed [33:0] k_signed = $signed({2'b00, 32'd8 * WB}) - $signed({2'b00, {(26-AW){1'b0}}, prn_chips} * 32'd8) - 34'sd72;
    assign search_lags = (k_signed < 34'sd3) ? 32'd0 : k_signed[31:0];
    wire prn_fits = (k_signed >= 34'sd3);

    // =================================================================
    // Window capture.
    // =================================================================
    reg        cap_armed, cap_open;
    reg [63:0] cap_target;
    reg [31:0] cap_m, cap_span;
    reg        cap_buf, last_buf;
    reg [31:0] cap_count;
    reg [8:0]  cap_slot;
    reg [63:0] cap_sec_tick;
    reg [3:0]  cap_flags;
    reg        search_buf;

    wire        at_start = cap_armed && (tick_d == cap_target);
    wire        in_span  = at_start || cap_open;
    wire [31:0] m_cur    = at_start ? 32'd0 : cap_m;
    wire        in_win   = in_span && (m_cur < WB);
    assign rx_busy = cap_armed || cap_open;

    // power of the window: 16 squares, a tree, the sum
    reg [30:0] sq [0:15];
    reg [32:0] sq4 [0:3];
    reg [34:0] sq1;
    reg [2:0]  pv;           // in_win through the three stages
    reg [47:0] win_power;
    integer    k;
    always @(posedge clk) begin
        for (k = 0; k < 8; k = k + 1) begin
            sq[k]     <= $signed(adc_d[16*k +: 16]) * $signed(adc_d[16*k +: 16]);
            sq[8 + k] <= $signed(adc_d[128 + 16*k +: 16]) * $signed(adc_d[128 + 16*k +: 16]);
        end
        for (k = 0; k < 4; k = k + 1)
            sq4[k] <= sq[4*k] + sq[4*k+1] + sq[4*k+2] + sq[4*k+3];
        sq1 <= sq4[0] + sq4[1] + sq4[2] + sq4[3];
        pv  <= {pv[1:0], in_win};
        if (at_start)    win_power <= 48'd0;
        else if (pv[2])  win_power <= win_power + {13'd0, sq1};
    end

    // the two window buffers
    (* ram_style = "block" *) reg [255:0] buf0 [0:WB-1];
    (* ram_style = "block" *) reg [255:0] buf1 [0:WB-1];
    reg  [255:0]   b0_q1, b0_q2, b1_q1, b1_q2;
    wire [BW-1:0]  t_rd_addr, s_rd_addr;
    reg            track_buf;
    wire [BW-1:0]  b0_addr = (search_busy && !search_buf) ? s_rd_addr : t_rd_addr;
    wire [BW-1:0]  b1_addr = (search_busy &&  search_buf) ? s_rd_addr : t_rd_addr;
    always @(posedge clk) begin
        if (in_win && !cap_buf) buf0[m_cur[BW-1:0]] <= adc_d;
        if (in_win &&  cap_buf) buf1[m_cur[BW-1:0]] <= adc_d;
        b0_q1 <= buf0[b0_addr]; b0_q2 <= b0_q1;
        b1_q1 <= buf1[b1_addr]; b1_q2 <= b1_q1;
    end
    wire [255:0] t_rd_data = track_buf  ? b1_q2 : b0_q2;
    wire [255:0] s_rd_data = search_buf ? b1_q2 : b0_q2;

    // window done: after the last window beat's power is summed
    reg [3:0]  wdone_d;
    wire       wdone = wdone_d[3];
    reg [93:0] lock_corr;

    always @(posedge clk) begin
        span_v  <= in_span;
        span_m  <= m_cur;
        span_d  <= adc_d;
        wdone_d <= {wdone_d[2:0], in_span && m_cur == WB - 1};
        if (rst) begin
            cap_armed  <= 1'b0;
            cap_open   <= 1'b0;
            cap_target <= 64'd0;
            cap_m      <= 32'd0;
            cap_span   <= 32'd0;
            cap_buf    <= 1'b0;
            last_buf   <= 1'b1;
            cap_flags  <= 4'd0;
            span_dsp   <= 1'b0;
            span_v     <= 1'b0;
            wdone_d    <= 4'd0;
        end else begin
            if (rx_arm && !cap_armed && !cap_open) begin
                cap_armed      <= 1'b1;
                cap_target     <= ev_tick;
                cap_span       <= span_beats;
                cap_count      <= sec_count;
                cap_slot       <= ev_slot;
                cap_sec_tick   <= sec_tick;
                cap_flags      <= 4'd0;
                cap_buf        <= (search_busy && search_buf == !last_buf) ? last_buf : !last_buf;
                span_dsp       <= locked && packets;
                span_lag       <= lock_lag;
                span_corr      <= lock_corr;
                span_pps_count <= sec_count;
                span_slot      <= ev_slot;
                span_tick      <= ev_tick;
            end
            if (cap_armed && !time_valid) cap_flags[RXF_TIME] <= 1'b1;
            if (in_win) begin
                if (!adc_v)      cap_flags[RXF_GAP]       <= 1'b1;
                if (adc_or)      cap_flags[RXF_OVERRANGE] <= 1'b1;
                if (!time_valid) cap_flags[RXF_TIME]      <= 1'b1;
            end
            if (at_start) begin
                cap_armed <= 1'b0;
                cap_open  <= 1'b1;
                last_buf  <= cap_buf;
            end
            if (in_span) begin
                cap_m <= m_cur + 32'd1;
                if (m_cur + 32'd1 == cap_span) cap_open <= 1'b0;
            end
        end
    end

    // =================================================================
    // Tracker and search.
    // =================================================================
    reg                t_start;
    wire               t_done, t_interior;
    wire [15:0]        t_lag;
    wire [46:0]        t_mag;
    wire signed [31:0] t_frac, t_phase;
    wire [93:0]        t_power;
    wire signed [46:0] t_ci, t_cq;

    p8_tracker #(.AW(AW), .BW(BW), .H(H), .RD_LAT(2)) u_trk (
        .clk (clk), .rst (rst), .start (t_start), .lag0 (lock_lag),
        .prn_chips (prn_chips), .window_beats (WBV),
        .rd_addr (t_rd_addr), .rd_data (t_rd_data),
        .prn_we (prn_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata), .taps (taps),
        .busy (), .done (t_done), .lag (t_lag), .interior (t_interior),
        .magnitude (t_mag), .fraction (t_frac), .phase (t_phase), .power (t_power),
        .c_i (t_ci), .c_q (t_cq)
    );

    reg                s_start;
    wire               s_done, s_bad;
    wire [KW-1:0]      s_lag, e_lag;
    wire signed [31:0] s_frac, s_phase, e_frac, e_phase;
    wire [46:0]        s_mag, e_mag, s_side;
    wire [93:0]        s_corr, e_corr;

    p8_search #(.N(N), .AW(AW), .BW(BW), .KW(KW), .MAXW(WB), .RD_LAT(2)) u_srch (
        .clk (clk), .rst (rst), .start (s_start), .prn_chips (prn_chips),
        .window_beats (WBV), .threshold (threshold),
        .rd_addr (s_rd_addr), .rd_data (s_rd_data),
        .prn_we (prn_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata), .taps (taps),
        .busy (search_busy), .done (s_done), .bad (s_bad), .lags (),
        .s_lag (s_lag), .s_fraction (s_frac), .s_phase (s_phase), .s_magnitude (s_mag), .s_corr (s_corr),
        .e_lag (e_lag), .e_fraction (e_frac), .e_phase (e_phase), .e_magnitude (e_mag), .e_corr (e_corr),
        .sidelobe (s_side)
    );

    // =================================================================
    // Measurement arithmetic: quality and T_rx.
    // =================================================================
    function [10:0] log2m94(input [93:0] v);   // 16 floor(log2 v) + 4 bits after the leading one
        integer b;
        reg [6:0]  n;
        reg [93:0] sh;
        begin
            n = 7'd0;
            for (b = 0; b < 94; b = b + 1) if (v[b]) n = b;
            sh = (n >= 4) ? (v >> (n - 4)) : (v << (4 - n));
            log2m94 = (v == 94'd0) ? 11'd0 : {n, sh[3:0]};
        end
    endfunction

    // =================================================================
    // Window processing and the lock.
    // =================================================================
    localparam [2:0] P_IDLE = 3'd0, P_TRACK = 3'd1, P_MEAS = 3'd2, P_DECIDE = 3'd3, P_ENTRY = 3'd4;
    reg [2:0]  pstate;
    reg [2:0]  mstep;
    reg        had_lock, search_pending, sdec_pending;
    reg        acc_s_pending, acc_e_pending, unlock_pending;
    // the window being processed
    reg        pr_buf;
    reg [31:0] pr_count;
    reg [8:0]  pr_slot;
    reg [63:0] pr_tick, pr_sec_tick;
    reg [3:0]  pr_flags;
    reg [47:0] pr_power;
    reg        pr_ran, pr_ok, pr_searched;
    // the search's window, taken when it starts
    reg [31:0] ss_count;
    reg [8:0]  ss_slot;
    reg [63:0] ss_tick;
    reg [3:0]  ss_flags;
    reg [93:0] sr_s_corr, sr_e_corr;
    // arithmetic
    reg [10:0]        lp, ls;
    reg [63:0]        dtick;
    reg signed [53:0] fprod;
    reg signed [33:0] qsum;
    reg signed [44:0] qmul;
    reg [63:0]        t1, t2;
    reg signed [21:0] t3;
    reg [63:0]        trx;
    reg [7:0]         quality;

    wire [62:0] lock_lhs = {t_mag, 16'd0};
    wire [62:0] lock_rhs = {31'd0, lock_min} * {16'd0, lock_mag};
    wire        relock_near = (sr_s_lag <= lock_lag + 16'd1) && (sr_s_lag + 16'd1 >= lock_lag);

    always @(posedge clk) begin
        t_start <= 1'b0;
        s_start <= 1'b0;
        if (cmd_search) begin
            if (search_busy || !prn_fits) refused_count <= refused_count + 32'd1;
            else                          search_pending <= 1'b1;
        end
        if (cmd_accept_strongest) acc_s_pending  <= 1'b1;
        if (cmd_accept_earliest)  acc_e_pending  <= 1'b1;
        if (cmd_unlock)           unlock_pending <= 1'b1;
        if (s_done && !s_bad) begin
            sr_pps_count   <= ss_count;
            sr_slot        <= ss_slot;
            sr_tick        <= ss_tick;
            sr_flags       <= ss_flags;
            sr_s_lag       <= {{(16-KW){1'b0}}, s_lag};
            sr_s_fraction  <= s_frac;
            sr_s_phase     <= s_phase;
            sr_s_magnitude <= s_mag;
            sr_s_corr      <= s_corr;
            sr_e_lag       <= {{(16-KW){1'b0}}, e_lag};
            sr_e_fraction  <= e_frac;
            sr_e_phase     <= e_phase;
            sr_e_magnitude <= e_mag;
            sr_e_corr      <= e_corr;
            sr_sidelobe    <= s_side;
            sr_seq         <= sr_seq + 32'd1;
            search_count   <= search_count + 32'd1;
            sdec_pending   <= 1'b1;
        end
        if (rst) begin
            pstate         <= P_IDLE;
            mstep          <= 3'd0;
            locked         <= 1'b0;
            had_lock       <= 1'b0;
            waiting        <= 1'b0;
            lock_lag       <= 16'd0;
            lock_mag       <= 47'd0;
            lock_corr      <= 94'd0;
            lost_count     <= 32'd0;
            relock_count   <= 32'd0;
            search_count   <= 32'd0;
            refused_count  <= 32'd0;
            search_pending <= 1'b0;
            sdec_pending   <= 1'b0;
            acc_s_pending  <= 1'b0;
            acc_e_pending  <= 1'b0;
            unlock_pending <= 1'b0;
            search_buf     <= 1'b0;
            track_buf      <= 1'b0;
            own_req        <= 1'b0;
            meas_valid     <= 1'b0;
            meas_trx       <= 64'd0;
            meas_phase     <= 32'sd0;
            meas_quality   <= 8'd0;
            meas_flags     <= 4'd0;
            sr_seq         <= 32'd0;
            sr_pps_count   <= 32'd0;
            sr_slot        <= 9'd0;
            sr_tick        <= 64'd0;
            sr_flags       <= 4'd0;
            sr_s_lag <= 16'd0; sr_s_fraction <= 32'sd0; sr_s_phase <= 32'sd0; sr_s_magnitude <= 47'd0;
            sr_e_lag <= 16'd0; sr_e_fraction <= 32'sd0; sr_e_phase <= 32'sd0; sr_e_magnitude <= 47'd0;
            sr_s_corr <= 94'd0; sr_e_corr <= 94'd0; sr_sidelobe <= 47'd0;
        end else if (!running) begin
            // the engine stopped: no lock, no requests; a running search finishes
            pstate         <= P_IDLE;
            locked         <= 1'b0;
            had_lock       <= 1'b0;
            waiting        <= 1'b0;
            search_pending <= 1'b0;
            sdec_pending   <= 1'b0;
            acc_s_pending  <= 1'b0;
            acc_e_pending  <= 1'b0;
            unlock_pending <= 1'b0;
            own_req        <= 1'b0;
            meas_valid     <= 1'b0;
        end else begin
            case (pstate)
                P_IDLE: begin
                    if (wdone) begin
                        pr_buf      <= last_buf;
                        pr_count    <= cap_count;
                        pr_slot     <= cap_slot;
                        pr_tick     <= cap_target;
                        pr_sec_tick <= cap_sec_tick;
                        pr_flags    <= cap_flags;
                        pr_power    <= win_power;
                        pr_ran      <= 1'b0;
                        pr_ok       <= 1'b0;
                        pr_searched <= 1'b0;
                        if (locked) begin
                            track_buf <= last_buf;
                            t_start   <= 1'b1;
                            pstate    <= P_TRACK;
                        end else begin
                            mstep  <= 3'd0;
                            pstate <= P_MEAS;
                        end
                    end else if (unlock_pending) begin
                        unlock_pending <= 1'b0;
                        acc_s_pending  <= 1'b0;
                        acc_e_pending  <= 1'b0;
                        locked   <= 1'b0;
                        had_lock <= 1'b0;
                        waiting  <= 1'b0;
                    end else if (acc_s_pending || acc_e_pending) begin
                        acc_s_pending <= 1'b0;
                        acc_e_pending <= 1'b0;
                        if (sr_seq != 32'd0) begin
                            locked    <= 1'b1;
                            had_lock  <= 1'b1;
                            waiting   <= 1'b0;
                            lock_lag  <= acc_s_pending ? sr_s_lag : sr_e_lag;
                            lock_mag  <= acc_s_pending ? sr_s_magnitude : sr_e_magnitude;
                            lock_corr <= acc_s_pending ? sr_s_corr : sr_e_corr;
                        end
                    end else if (sdec_pending) begin
                        sdec_pending <= 1'b0;
                        if (!locked) begin
                            if (had_lock && relock_near) begin
                                locked       <= 1'b1;
                                waiting      <= 1'b0;
                                lock_lag     <= sr_s_lag;
                                lock_mag     <= sr_s_magnitude;
                                lock_corr    <= sr_s_corr;
                                relock_count <= relock_count + 32'd1;
                            end else begin
                                waiting <= 1'b1;
                            end
                        end
                    end
                end
                P_TRACK: if (t_done) begin
                    pr_ran <= 1'b1;
                    mstep  <= 3'd0;
                    pstate <= P_MEAS;
                    if (t_interior && lock_lhs >= lock_rhs) begin
                        pr_ok     <= 1'b1;
                        lock_lag  <= t_lag;
                        lock_mag  <= t_mag;
                        lock_corr <= {t_ci, t_cq};
                    end else begin
                        locked     <= 1'b0;
                        lost_count <= lost_count + 32'd1;
                    end
                end
                P_MEAS: begin
                    mstep <= mstep + 3'd1;
                    case (mstep)
                        3'd0: begin
                            lp    <= log2m94(t_power);
                            ls    <= log2m94({46'd0, pr_power});
                            dtick <= pr_tick - pr_sec_tick;
                            fprod <= t_frac * $signed(22'sd1250000);
                        end
                        3'd1: begin
                            qsum <= $signed({23'd0, lp}) - $signed({23'd0, ls}) + quality_offset;
                            t1   <= dtick * 64'd10000000;
                            t2   <= {48'd0, t_lag} * 64'd1250000;
                            t3   <= (fprod + 54'sd2147483648) >>> 32;
                        end
                        3'd2: begin
                            qmul <= qsum * $signed(11'sd771);
                            trx  <= t1 + t2 + {{42{t3[21]}}, t3};
                        end
                        default: begin
                            if (qmul[44])                       quality <= 8'd0;
                            else if ((qmul >>> 10) > 45'sd255)  quality <= 8'd255;
                            else                                quality <= qmul[17:10];
                            pstate <= P_DECIDE;
                        end
                    endcase
                end
                P_DECIDE: begin
                    // search this window: without a lock, or on request
                    if ((!locked || search_pending) && !search_busy && prn_fits) begin
                        s_start        <= 1'b1;
                        search_buf     <= pr_buf;
                        search_pending <= 1'b0;
                        pr_searched    <= 1'b1;
                        ss_count       <= pr_count;
                        ss_slot        <= pr_slot;
                        ss_tick        <= pr_tick;
                        ss_flags       <= pr_flags;
                    end
                    meas_valid   <= pr_ok;
                    meas_trx     <= pr_ok ? trx : 64'd0;
                    meas_phase   <= pr_ok ? t_phase : 32'sd0;
                    meas_quality <= pr_ok ? quality : 8'd0;
                    meas_flags   <= pr_flags;
                    pstate       <= P_ENTRY;
                end
                P_ENTRY: begin
                    if (!own_req) begin
                        own_entry <= {32'd0,
                                      {16'd0, pr_power[47:32]}, pr_power[31:0],
                                      {24'd0, pr_ran ? quality : 8'd0},
                                      pr_ran ? trx[63:32] : 32'd0, pr_ran ? trx[31:0] : 32'd0,
                                      pr_ran ? {17'd0, t_mag[46:32]} : 32'd0, pr_ran ? t_mag[31:0] : 32'd0,
                                      pr_ran ? t_phase : 32'sd0, pr_ran ? t_frac : 32'sd0,
                                      pr_ran ? {16'd0, t_lag} : 32'd0,
                                      pr_tick[63:32], pr_tick[31:0], {23'd0, pr_slot}, pr_count,
                                      {16'd0, 5'd0, pr_searched, pr_flags != 4'd0, pr_ok, QE_TYPE_OWN}};
                        own_req <= 1'b1;
                    end else if (own_ack) begin
                        own_req <= 1'b0;
                        pstate  <= P_IDLE;
                    end
                end
                default: pstate <= P_IDLE;
            endcase
        end
    end
endmodule
