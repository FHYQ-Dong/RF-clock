`timescale 1ns / 1ps
// P8 whole-window search (docs/ABI.md 4.10; rf_clock.plmodel.correlator.search).
//
//   R = 8 prn_chips + 73 reference samples, K = 8 window_beats - R + 1 lags.
//
// 1. The reference is made from the PRN chips by this search's own chip memory
//    and FIR into a reference RAM (prn_chips + 10 beats).
// 2. Passes p = 0 .. ceil(K / N) - 1 over N lanes, R + N clocks each: window
//    sample k0 + t (k0 = p N) is broadcast to every lane while reference sample
//    t moves along the lanes one per clock, so that lane j multiplies window
//    sample k0 + j + n with reference sample n, n = 0 .. R - 1, and accumulates
//    the correlation at lag k0 + j exactly (47 bits).  Markers travel with
//    reference samples 0 and R - 1; lane j finishes one clock after lane j - 1
//    and its result moves down a result chain to lane 0, one result every two
//    clocks in lag order, into the result RAM, while the largest power (the
//    first on a tie) is kept.
// 3. A scan of the result RAM finds the earliest peak, the first lag with
//    power not below either neighbour and power * 2^32 >= threshold^2 * the
//    strongest's power, and the sidelobe, the largest power more than GUARD
//    lags from the strongest.
// 4. The estimator gives the strongest and the earliest peak; a square root
//    the sidelobe's magnitude.
//
// `start` while idle; `done` pulses when the report is valid.  `bad` with
// done: the window holds fewer than 3 lags; no report.
module p8_search #(
    parameter integer N      = 1024,  // lanes
    parameter integer AW     = 11,    // code memory word address bits
    parameter integer BW     = 13,    // window buffer beat address bits
    parameter integer KW     = 16,    // lag bits: 8 MAXW < 2^KW
    parameter integer MAXW   = 5481,  // window beats the RAMs hold
    parameter integer RD_LAT = 2,     // window buffer read latency
    parameter integer GUARD  = 16
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               start,
    input  wire [AW+5:0]      prn_chips,
    input  wire [BW:0]        window_beats,
    input  wire [15:0]        threshold,
    // Window buffer read port.
    output reg  [BW-1:0]      rd_addr,
    input  wire [255:0]       rd_data,
    // PRN memory writes and taps (burst memory port).
    input  wire [3:0]         prn_we,
    input  wire [AW-1:0]      mem_waddr,
    input  wire [31:0]        mem_wdata,
    input  wire [81*16-1:0]   taps,
    // Report.
    output reg                busy,
    output reg                done,
    output reg                bad,
    output wire [KW-1:0]      lags,
    output reg  [KW-1:0]      s_lag,
    output reg  signed [31:0] s_fraction,
    output reg  signed [31:0] s_phase,
    output reg  [46:0]        s_magnitude,
    output reg  [93:0]        s_corr,       // {I, Q} at the strongest peak
    output reg  [KW-1:0]      e_lag,
    output reg  signed [31:0] e_fraction,
    output reg  signed [31:0] e_phase,
    output reg  [46:0]        e_magnitude,
    output reg  [93:0]        e_corr,
    output reg  [46:0]        sidelobe
);
    localparam integer MAXK = 8 * MAXW;

    // ---- sizes of this search ----
    reg  [31:0] r_len;      // R
    reg  [31:0] k_len;      // K
    reg  [31:0] passes;
    wire [31:0] r_calc = {{(26-AW){1'b0}}, prn_chips} * 32'd8 + 32'd73;
    wire signed [32:0] k_calc = $signed({1'b0, {(28-BW){1'b0}}, window_beats, 3'b000}) -
                                $signed({1'b0, r_calc}) + 33'sd1;
    assign lags = k_len[KW-1:0];

    localparam [3:0] S_IDLE = 4'd0, S_PREP = 4'd1, S_PASS = 4'd2, S_COLLECT = 4'd3,
                     S_LEVEL = 4'd4, S_SCAN = 4'd5, S_EST_S = 4'd6, S_EST_E = 4'd7,
                     S_SIDE = 4'd8;
    reg [3:0] state;

    // =================================================================
    // 1. Reference RAM from the chips.
    // =================================================================
    reg  [31:0] c_n;            // chip index during S_PREP
    reg         c_run;
    wire        prn_chip;
    p_chip_mem #(.AW(AW)) u_prn (
        .clk (clk), .we (prn_we), .waddr (mem_waddr), .wdata (mem_wdata),
        .rd_addr (c_n[AW+4:0]), .rd_chip (prn_chip), .rd_word ()
    );
    reg [1:0] c_nz_d;
    always @(posedge clk) c_nz_d <= {c_nz_d[0], c_run && (c_n < {{(26-AW){1'b0}}, prn_chips})};
    wire [127:0] fir_beat;
    p_chip_fir u_fir (
        .clk (clk), .rst (rst), .en (1'b1), .chip_nz (c_nz_d[1]), .chip_neg (~prn_chip),
        .taps (taps), .beat (fir_beat)
    );
    reg [7:0]      c_v;            // chip c_n's beat leaves the FIR 8 clocks later
    reg [KW-4:0]   c_a [0:7];
    integer s;
    always @(posedge clk) begin
        c_v[0] <= c_run;
        c_a[0] <= c_n[KW-4:0];
        for (s = 1; s < 8; s = s + 1) begin
            c_v[s] <= c_v[s-1];
            c_a[s] <= c_a[s-1];
        end
    end

    (* ram_style = "block" *)
    reg [127:0] ref_ram [0:MAXW-1];
    reg [KW-4:0] ref_raddr;
    reg [127:0]  ref_q1, ref_q2;
    always @(posedge clk) begin
        if (c_v[7]) ref_ram[c_a[7]] <= fir_beat;
        ref_q1 <= ref_ram[ref_raddr];
        ref_q2 <= ref_q1;
    end

    // =================================================================
    // 2. Passes: sources, broadcast, lanes, result chain.
    // =================================================================
    reg          p_run;
    reg [31:0]   t;
    reg [31:0]   k0;
    reg [31:0]   pass;
    wire [31:0]  sidx = k0 + t;

    // address stage: registered with the RAM addresses, then delayed to the data
    reg [2:0] a_rsel, a_fsel;
    reg       a_rok, a_fok, a_first, a_last, a_v;
    reg [2:0] d_rsel [0:RD_LAT-1];
    reg [2:0] d_fsel [0:RD_LAT-1];
    reg [RD_LAT-1:0] d_rok, d_fok, d_first, d_last, d_v;
    always @(posedge clk) begin
        rd_addr   <= sidx[BW+2:3];
        ref_raddr <= (state == S_PASS) ? t[KW-1:3] : ref_raddr;
        a_rsel    <= sidx[2:0];
        a_fsel    <= t[2:0];
        a_rok     <= p_run && (sidx < {{(28-BW){1'b0}}, window_beats, 3'b000});
        a_fok     <= p_run && (t < r_len);
        a_first   <= p_run && (t == 0);
        a_last    <= p_run && (t == r_len - 1);
        a_v       <= p_run;
        d_rsel[0] <= a_rsel; d_fsel[0] <= a_fsel;
        d_rok[0]  <= a_rok;  d_fok[0]  <= a_fok;
        d_first[0] <= a_first; d_last[0] <= a_last; d_v[0] <= a_v;
        for (s = 1; s < RD_LAT; s = s + 1) begin
            d_rsel[s] <= d_rsel[s-1]; d_fsel[s] <= d_fsel[s-1];
            d_rok[s]  <= d_rok[s-1];  d_fok[s]  <= d_fok[s-1];
            d_first[s] <= d_first[s-1]; d_last[s] <= d_last[s-1]; d_v[s] <= d_v[s-1];
        end
    end
    // the address stage and RD_LAT delays meet the data RD_LAT clocks after the addresses
    wire [2:0] rsel = d_rsel[RD_LAT-1];
    wire [2:0] fsel = d_fsel[RD_LAT-1];
    wire rok = d_rok[RD_LAT-1], fok = d_fok[RD_LAT-1];
    wire sfirst = d_first[RD_LAT-1], slast = d_last[RD_LAT-1];

    reg signed [15:0] src_i, src_q, src_r;
    reg               src_f, src_l;
    always @(posedge clk) begin
        src_i <= rok ? rd_data[16 * rsel +: 16]       : 16'sd0;
        src_q <= rok ? rd_data[128 + 16 * rsel +: 16] : 16'sd0;
        src_r <= fok ? ref_q2[16 * fsel +: 16]        : 16'sd0;
        src_f <= sfirst;
        src_l <= slast;
    end

    // broadcast in two stages; the reference reaches lane 0 in two stages too
    (* max_fanout = 64 *) reg signed [15:0] bc1_i, bc1_q;
    (* max_fanout = 32 *) reg signed [15:0] bc2_i, bc2_q;
    reg signed [15:0] r_pre;
    reg               f_pre, l_pre;
    always @(posedge clk) begin
        bc1_i <= src_i; bc1_q <= src_q;
        bc2_i <= bc1_i; bc2_q <= bc1_q;
        r_pre <= src_r; f_pre <= src_f; l_pre <= src_l;
    end

    reg signed [15:0] rc [0:N-1];
    reg [N-1:0]       rf, rl;
    reg [N:0]         ov;          // result chain valid, ov[N] = 0
    reg [93:0]        oc [0:N];    // {I, Q}
    genvar g;
    generate
        for (g = 0; g < N; g = g + 1) begin : g_lane
            reg signed [31:0] m_i, m_q;
            reg               mf, ml, ad;
            reg signed [46:0] acc_i, acc_q;
            if (g == 0) begin : g_head
                always @(posedge clk) begin
                    rc[g] <= r_pre;
                    rf[g] <= f_pre;
                    rl[g] <= l_pre;
                end
            end else begin : g_link
                always @(posedge clk) begin
                    rc[g] <= rc[g-1];
                    rf[g] <= rf[g-1];
                    rl[g] <= rl[g-1];
                end
            end
            always @(posedge clk) begin
                m_i <= bc2_i * rc[g];
                m_q <= bc2_q * rc[g];
                mf  <= rf[g];
                ml  <= rl[g];
                acc_i <= (mf ? 47'sd0 : acc_i) + m_i;   // all signed: m_i is sign-extended
                acc_q <= (mf ? 47'sd0 : acc_q) + m_q;
                ad  <= ml && !rst;
                oc[g] <= ad ? {acc_i, acc_q} : oc[g+1];
                ov[g] <= ad ? 1'b1 : ov[g+1];
            end
        end
    endgenerate
    always @(posedge clk) begin
        ov[N] <= 1'b0;
        oc[N] <= 94'd0;
    end

    // =================================================================
    // Result RAM, streaming maximum, scan, estimates.
    // =================================================================
    (* ram_style = "block" *)
    reg [93:0]   res_ram [0:MAXK-1];
    reg [KW-1:0] res_raddr;
    reg [93:0]   res_q1, res_q2;
    reg [31:0]   collected;
    wire         col_store = ov[0] && (collected < k_len);
    always @(posedge clk) begin
        if (col_store) res_ram[collected[KW-1:0]] <= oc[0];
        res_q1 <= res_ram[res_raddr];
        res_q2 <= res_q1;
    end

    // one power unit, fed by the collector or the scan
    reg  signed [46:0] pw_i, pw_q;
    wire [93:0]        pw_out;
    p_power #(.W(47), .MUL_STAGES(3)) u_pw (.clk (clk), .in_i (pw_i), .in_q (pw_q), .power (pw_out));
    localparam integer PL = 6;          // pw_i/pw_q set -> pw_out
    reg [PL-1:0]  pw_v;
    reg [KW-1:0]  pw_tag [0:PL-1];
    reg [KW-1:0]  feed_tag;
    reg           feed_v;
    always @(posedge clk) begin
        pw_v[0]   <= feed_v;
        pw_tag[0] <= feed_tag;
        for (s = 1; s < PL; s = s + 1) begin
            pw_v[s]   <= pw_v[s-1];
            pw_tag[s] <= pw_tag[s-1];
        end
    end
    wire          pv   = pw_v[PL-2];      // pw_out now carries the power of pv_tag
    wire [KW-1:0] ptag = pw_tag[PL-2];

    reg [93:0]   best_p;
    reg [KW-1:0] best_k;
    reg          have_best;
    reg [125:0]  level;
    reg [31:0]   t2;
    reg [5:0]    lv_n;
    reg [93:0]   p_m1, p_m2;     // powers of the two lags before ptag
    reg          found;
    reg [93:0]   side_p;
    reg [31:0]   scan_n;         // next lag to read
    reg [31:0]   scan_seen;      // powers seen
    reg [2:0]    rd_wait;
    reg [93:0]   el, ec, er;     // l, c, r for the estimator
    reg [1:0]    est_phase;
    reg [3:0]    est_cnt;

    reg         est_start, est_edge;
    wire        est_done;
    wire [46:0] est_mag;
    wire signed [31:0] est_frac, est_phase_v;
    p_estimator #(.W(47)) u_est (
        .clk (clk), .rst (rst), .start (est_start), .edge_in (est_edge),
        .l_i (el[93:47]), .l_q (el[46:0]), .c_i (ec[93:47]), .c_q (ec[46:0]),
        .r_i (er[93:47]), .r_q (er[46:0]),
        .busy (), .done (est_done), .magnitude (est_mag), .fraction (est_frac),
        .phase (est_phase_v), .power ()
    );
    reg         sq_start;
    wire        sq_done;
    wire [46:0] sq_root;
    p_isqrt #(.W(94)) u_sq (.clk (clk), .rst (rst), .start (sq_start), .value (side_p),
                            .busy (), .done (sq_done), .root (sq_root));

    wire [KW-1:0] cur_k = (state == S_EST_S) ? best_k : e_lag;

    // the scan's read tags: valid and lag, by clocks after the address is set
    reg [2:0]    scan_v;
    reg [KW-1:0] scan_t [0:2];
    always @(posedge clk) begin
        scan_v[0] <= (state == S_SCAN) && (scan_n < k_len);
        scan_t[0] <= scan_n[KW-1:0];
        scan_v[1] <= scan_v[0]; scan_t[1] <= scan_t[0];
        scan_v[2] <= scan_v[1]; scan_t[2] <= scan_t[1];
    end

    // the candidate for the earliest peak is ptag - 1, now that ptag's power is known
    wire [KW-1:0] cand = ptag - 1'b1;
    wire          cand_ok = (cand == 0 || p_m1 >= p_m2) && p_m1 >= pw_out &&
                            {p_m1, 32'd0} >= level && cand <= best_k;
    wire [KW-1:0] last_k = k_len[KW-1:0] - 1'b1;
    wire          last_ok = (last_k == 0 || pw_out >= p_m1) && {pw_out, 32'd0} >= level && last_k <= best_k;
    wire [KW:0]   lag_dist = (ptag > best_k) ? {1'b0, ptag} - {1'b0, best_k} : {1'b0, best_k} - {1'b0, ptag};

    always @(posedge clk) begin
        done      <= 1'b0;
        est_start <= 1'b0;
        sq_start  <= 1'b0;
        feed_v    <= 1'b0;
        pw_i      <= 47'sd0;
        pw_q      <= 47'sd0;
        if (rst) begin
            state     <= S_IDLE;
            busy      <= 1'b0;
            bad       <= 1'b0;
            c_run     <= 1'b0;
            c_n       <= 32'd0;
            p_run     <= 1'b0;
            t         <= 0;
            k0        <= 0;
            pass      <= 0;
            r_len     <= 0;
            k_len     <= 0;
            passes    <= 0;
            collected <= 0;
            have_best <= 1'b0;
            found     <= 1'b0;
            s_lag <= 0; s_fraction <= 0; s_phase <= 0; s_magnitude <= 0; s_corr <= 0;
            e_lag <= 0; e_fraction <= 0; e_phase <= 0; e_magnitude <= 0; e_corr <= 0;
            sidelobe  <= 0;
        end else begin
            case (state)
                S_IDLE: if (start) begin
                    busy  <= 1'b1;
                    bad   <= 1'b0;
                    r_len <= r_calc;
                    if (k_calc < 33'sd3 || k_calc > MAXK) begin
                        bad   <= 1'b1;
                        busy  <= 1'b0;
                        done  <= 1'b1;
                    end else begin
                        k_len  <= k_calc[31:0];
                        passes <= (k_calc[31:0] + N - 1) / N;
                        c_n    <= 32'd0;
                        c_run  <= 1'b1;
                        state  <= S_PREP;
                    end
                end
                S_PREP: begin
                    if (c_run) begin
                        c_n <= c_n + 32'd1;
                        if (c_n == {{(26-AW){1'b0}}, prn_chips} + 32'd9) c_run <= 1'b0;
                    end else if (c_v == 8'd0) begin
                        t         <= 0;
                        k0        <= 0;
                        pass      <= 0;
                        collected <= 0;
                        have_best <= 1'b0;
                        best_p    <= 94'd0;
                        best_k    <= 0;
                        p_run     <= 1'b1;
                        state     <= S_PASS;
                    end
                end
                S_PASS: begin
                    if (t == r_len + N - 1) begin
                        t <= 0;
                        if (pass + 1 == passes) begin
                            p_run <= 1'b0;
                            state <= S_COLLECT;
                        end else begin
                            pass <= pass + 1;
                            k0   <= k0 + N;
                        end
                    end else begin
                        t <= t + 1;
                    end
                end
                S_COLLECT: if (collected == passes * N && pw_v == {PL{1'b0}} && !feed_v) begin
                    t2    <= {16'd0, threshold} * {16'd0, threshold};
                    level <= 126'd0;
                    lv_n  <= 6'd0;
                    state <= S_LEVEL;
                end
                S_LEVEL: begin   // level = threshold^2 * best_p, one bit of t2 per clock
                    if (t2[lv_n[4:0]]) level <= level + ({32'd0, best_p} << lv_n[4:0]);
                    lv_n <= lv_n + 6'd1;
                    if (lv_n == 6'd31) begin
                        scan_n    <= 0;
                        scan_seen <= 0;
                        found     <= 1'b0;
                        side_p    <= 94'd0;
                        e_lag     <= best_k;
                        rd_wait   <= 3'd0;
                        state     <= S_SCAN;
                    end
                end
                S_SCAN: begin
                    if (scan_seen == k_len) begin
                        el       <= 94'd0;
                        est_cnt  <= 4'd0;
                        est_phase <= 2'd0;
                        state    <= S_EST_S;
                    end
                end
                S_EST_S, S_EST_E: begin
                    // read l, c, r of the peak, then run the estimator
                    est_cnt <= (est_cnt == 4'd15) ? est_cnt : est_cnt + 4'd1;
                    if (est_cnt == 4'd3) el <= (cur_k == 0) ? 94'd0 : res_q2;
                    if (est_cnt == 4'd4) ec <= res_q2;
                    if (est_cnt == 4'd5) begin
                        er <= (cur_k == last_k) ? 94'd0 : res_q2;
                        est_edge  <= (cur_k == 0) || (cur_k == last_k);
                        est_start <= 1'b1;
                    end
                    if (est_cnt > 4'd5 && est_done) begin
                        est_cnt <= 4'd0;
                        if (state == S_EST_S) begin
                            s_lag <= best_k; s_fraction <= est_frac; s_phase <= est_phase_v;
                            s_magnitude <= est_mag; s_corr <= ec;
                            state <= S_EST_E;
                        end else begin
                            e_fraction <= est_frac; e_phase <= est_phase_v; e_magnitude <= est_mag;
                            e_corr <= ec;
                            sq_start <= 1'b1;
                            state    <= S_SIDE;
                        end
                    end
                end
                S_SIDE: if (sq_done) begin
                    sidelobe <= sq_root;
                    busy     <= 1'b0;
                    done     <= 1'b1;
                    state    <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase

            // ---- collector: results into the RAM and their powers ----
            if (state == S_PASS || state == S_COLLECT) begin
                if (ov[0]) begin
                    collected <= collected + 1;
                    if (col_store) begin
                        pw_i     <= oc[0][93:47];
                        pw_q     <= oc[0][46:0];
                        feed_v   <= 1'b1;
                        feed_tag <= collected[KW-1:0];
                    end
                end
                if (pv && (!have_best || pw_out > best_p)) begin
                    have_best <= 1'b1;
                    best_p    <= pw_out;
                    best_k    <= ptag;
                end
            end

            // ---- scan: the RAM again, every lag's power in order ----
            if (state == S_SCAN) begin
                if (scan_n < k_len) begin
                    res_raddr <= scan_n[KW-1:0];
                    scan_n    <= scan_n + 1;
                end
                // res_raddr set in clock n is in res_q2 two clocks after it is visible
                if (scan_v[2]) begin
                    pw_i     <= res_q2[93:47];
                    pw_q     <= res_q2[46:0];
                    feed_v   <= 1'b1;
                    feed_tag <= scan_t[2];
                end
                if (pv) begin
                    scan_seen <= scan_seen + 1;
                    p_m2 <= p_m1;
                    p_m1 <= pw_out;
                    if (lag_dist > GUARD && pw_out > side_p) side_p <= pw_out;
                    if (!found && ptag != 0 && cand_ok) begin
                        found <= 1'b1;
                        e_lag <= cand;
                    end else if (!found && ptag == last_k && last_ok) begin
                        found <= 1'b1;
                        e_lag <= last_k;
                    end
                end
            end
            if ((state == S_EST_S || state == S_EST_E) && est_cnt < 4'd3)
                res_raddr <= (est_cnt == 4'd0) ? cur_k - 1'b1 : (est_cnt == 4'd1) ? cur_k : cur_k + 1'b1;
        end
    end
endmodule
