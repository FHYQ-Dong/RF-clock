`timescale 1ns / 1ps
// P8 tracker (docs/ABI.md 4.10; rf_clock.plmodel.correlator.track): the
// correlation of a window with the reference at the 2H+1 lags lo = lag0 - H
// .. lag0 + H, window samples outside 0 .. 8 window_beats - 1 taken as zero,
// then the estimate at the lag of the largest power (the first on a tie).
//
// The window is read from its buffer one beat per clock while the reference
// is made beat by beat from the PRN chips by this tracker's own chip memory
// and FIR.  With off = lo mod 8 and bb = floor(lo / 8), step j multiplies
// window beat bb + j with the 16 reference samples 8 j - off - 8 .. 8 j -
// off + 7 (from reference beats j - 2 .. j) and adds 8 products per lag:
// lag lo + d, sample p takes reference sample 8 j + p - off - d.  Steps j = 0
// .. prn_chips + 10 cover every reference sample (8 prn_chips + 73).
//
// `start` while idle; `done` pulses with the result about prn_chips + 120
// clocks later.  `interior` is 0 when the largest power is at either end of
// the range: the peak may lie outside it, and the fraction is 0.
module p8_tracker #(
    parameter integer AW   = 11,   // code memory word address bits
    parameter integer BW   = 13,   // window buffer beat address bits
    parameter integer H    = 4,
    parameter integer RD_LAT = 2   // window buffer read latency
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               start,
    input  wire [15:0]        lag0,
    input  wire [AW+5:0]      prn_chips,
    input  wire [BW:0]        window_beats,
    // Window buffer read port.
    output reg  [BW-1:0]      rd_addr,
    input  wire [255:0]       rd_data,
    // PRN memory writes and taps (burst memory port).
    input  wire [3:0]         prn_we,
    input  wire [AW-1:0]      mem_waddr,
    input  wire [31:0]        mem_wdata,
    input  wire [81*16-1:0]   taps,
    // Result.
    output reg                busy,
    output reg                done,
    output reg  [15:0]        lag,
    output reg                interior,
    output wire [46:0]        magnitude,
    output wire signed [31:0] fraction,
    output wire signed [31:0] phase,
    output wire [93:0]        power,
    output reg  signed [46:0] c_i,
    output reg  signed [46:0] c_q
);
    localparam integer NL  = 2 * H + 1;   // lags
    localparam integer FIR_PIPE = 8;      // chip address to its FIR beat

    // ---- run state ----
    reg               running;
    reg signed [17:0] lo;
    reg [2:0]         off;
    reg signed [17:0] bb;
    reg [31:0]        j, steps;

    // ---- reference: chips j = 0 .. prn-1, then zeros ----
    wire prn_chip;
    p_chip_mem #(.AW(AW)) u_prn (
        .clk (clk), .we (prn_we), .waddr (mem_waddr), .wdata (mem_wdata),
        .rd_addr (j[AW+4:0]), .rd_chip (prn_chip), .rd_word ()
    );
    reg [1:0] nz_d;
    always @(posedge clk) nz_d <= {nz_d[0], running && (j < {{(26-AW){1'b0}}, prn_chips})};
    wire [127:0] ref_beat;
    p_chip_fir u_fir (
        .clk (clk), .rst (rst), .en (1'b1), .chip_nz (nz_d[1]), .chip_neg (~prn_chip),
        .taps (taps), .beat (ref_beat)
    );
    reg [127:0] ref1, ref2;   // reference beats j-1, j-2
    always @(posedge clk) begin
        ref1 <= ref_beat;
        ref2 <= ref1;
    end

    // ---- window beat bb + j, read so that it arrives with reference beat j ----
    reg [FIR_PIPE-1:0] v_pipe;       // step j valid, by cycles after its chip address
    reg [FIR_PIPE-1:0] in_pipe;      // window beat inside the window
    reg signed [18:0]  wb_pipe [0:FIR_PIPE-1];
    integer s;
    wire signed [18:0] wbeat = bb + $signed({1'b0, j[17:0]});
    always @(posedge clk) begin
        v_pipe[0]  <= running;
        wb_pipe[0] <= wbeat;
        for (s = 1; s < FIR_PIPE; s = s + 1) begin
            v_pipe[s]  <= v_pipe[s-1];
            wb_pipe[s] <= wb_pipe[s-1];
        end
    end
    // wb_pipe[s] holds step j's beat s + 1 cycles after j; rd_addr takes it from
    // wb_pipe[RI], so the data arrives RI + 2 + RD_LAT = FIR_PIPE cycles after j
    localparam integer RI = FIR_PIPE - 2 - RD_LAT;
    wire inside_ri = (wb_pipe[RI] >= 0) && (wb_pipe[RI] < $signed({1'b0, window_beats}));
    always @(posedge clk) begin
        rd_addr <= wb_pipe[RI][BW-1:0];
        in_pipe[0] <= inside_ri;
        for (s = 1; s < FIR_PIPE; s = s + 1) in_pipe[s] <= in_pipe[s-1];
    end
    // at this cycle: ref_beat = reference beat j, rd_data = window beat bb + j
    wire step_v  = v_pipe[FIR_PIPE-1];
    wire step_in = in_pipe[RD_LAT];

    // ---- stage A: the 16 aligned reference samples and the window samples ----
    reg [15:0]         ra [0:15];
    reg signed [15:0]  xi [0:7];
    reg signed [15:0]  xq [0:7];
    reg                a_v;
    wire [383:0] refwin = {ref_beat, ref1, ref2};  // sample q at [16 q +: 16], q = 0 .. 23
    integer u, p, d;
    always @(posedge clk) begin
        a_v <= step_v;
        for (u = 0; u < 16; u = u + 1)
            ra[u] <= refwin[16 * (8 - off + u) +: 16];
        for (p = 0; p < 8; p = p + 1) begin
            xi[p] <= step_in ? rd_data[16 * p +: 16] : 16'sd0;
            xq[p] <= step_in ? rd_data[128 + 16 * p +: 16] : 16'sd0;
        end
    end

    // ---- products, sums of 8, accumulators ----
    reg signed [31:0] pi [0:NL-1][0:7];
    reg signed [31:0] pq [0:NL-1][0:7];
    reg signed [32:0] s1i [0:NL-1][0:3];
    reg signed [32:0] s1q [0:NL-1][0:3];
    reg signed [33:0] s2i [0:NL-1][0:1];
    reg signed [33:0] s2q [0:NL-1][0:1];
    reg signed [34:0] s3i [0:NL-1];
    reg signed [34:0] s3q [0:NL-1];
    reg signed [46:0] acc_i [0:NL-1];
    reg signed [46:0] acc_q [0:NL-1];
    reg [3:0]         sv;      // valid through the 4 stages after A
    always @(posedge clk) begin
        sv <= {sv[2:0], a_v};
        for (d = 0; d < NL; d = d + 1) begin
            for (p = 0; p < 8; p = p + 1) begin
                pi[d][p] <= xi[p] * $signed(ra[8 + p - d]);
                pq[d][p] <= xq[p] * $signed(ra[8 + p - d]);
            end
            for (p = 0; p < 4; p = p + 1) begin
                s1i[d][p] <= pi[d][2*p] + pi[d][2*p+1];
                s1q[d][p] <= pq[d][2*p] + pq[d][2*p+1];
            end
            for (p = 0; p < 2; p = p + 1) begin
                s2i[d][p] <= s1i[d][2*p] + s1i[d][2*p+1];
                s2q[d][p] <= s1q[d][2*p] + s1q[d][2*p+1];
            end
            s3i[d] <= s2i[d][0] + s2i[d][1];
            s3q[d] <= s2q[d][0] + s2q[d][1];
            if (start && !busy) begin
                acc_i[d] <= 47'sd0;
                acc_q[d] <= 47'sd0;
            end else if (sv[3]) begin
                acc_i[d] <= acc_i[d] + s3i[d];
                acc_q[d] <= acc_q[d] + s3q[d];
            end
        end
    end

    // ---- after the steps: powers of the lags, the largest, the estimate ----
    wire [93:0] pw_out;
    reg  signed [46:0] pw_i, pw_q;
    p_power #(.W(47), .MUL_STAGES(3)) u_pw (.clk (clk), .in_i (pw_i), .in_q (pw_q), .power (pw_out));

    reg         est_start, est_edge;
    reg signed [46:0] el_i, el_q, er_i, er_q;
    wire        est_done;
    p_estimator #(.W(47)) u_est (
        .clk (clk), .rst (rst), .start (est_start), .edge_in (est_edge),
        .l_i (el_i), .l_q (el_q), .c_i (c_i), .c_q (c_q), .r_i (er_i), .r_q (er_q),
        .busy (), .done (est_done), .magnitude (magnitude), .fraction (fraction),
        .phase (phase), .power (power)
    );

    localparam [2:0] T_IDLE = 3'd0, T_RUN = 3'd1, T_DRAIN = 3'd2, T_POWER = 3'd3, T_EST = 3'd4;
    reg [2:0]  tstate;
    reg [7:0]  cnt;
    reg [93:0] best;
    reg [3:0]  kbest;

    always @(posedge clk) begin
        done      <= 1'b0;
        est_start <= 1'b0;
        pw_i      <= 47'sd0;
        pw_q      <= 47'sd0;
        if (rst) begin
            tstate   <= T_IDLE;
            busy     <= 1'b0;
            running  <= 1'b0;
            j        <= 32'd0;
            steps    <= 32'd0;
            lo       <= 18'sd0;
            off      <= 3'd0;
            bb       <= 18'sd0;
            lag      <= 16'd0;
            interior <= 1'b0;
            cnt      <= 8'd0;
            best     <= 94'd0;
            kbest    <= 4'd0;
            c_i      <= 47'sd0;
            c_q      <= 47'sd0;
        end else begin
            case (tstate)
                T_IDLE: if (start) begin
                    busy    <= 1'b1;
                    lo      <= $signed({2'b00, lag0}) - H;
                    off     <= lag0[2:0] - H[2:0];
                    bb      <= ($signed({2'b00, lag0}) - H) >>> 3;
                    j       <= 32'd0;
                    steps   <= {{(26-AW){1'b0}}, prn_chips} + 32'd11;
                    running <= 1'b1;
                    tstate  <= T_RUN;
                end
                T_RUN: begin
                    j <= j + 32'd1;
                    if (j + 32'd1 == steps) begin
                        running <= 1'b0;
                        cnt     <= 8'd0;
                        tstate  <= T_DRAIN;
                    end
                end
                T_DRAIN: begin   // the last step through the read, stage A and 4 sum stages
                    cnt <= cnt + 8'd1;
                    if (cnt == FIR_PIPE + 6) begin
                        cnt    <= 8'd0;
                        best   <= 94'd0;
                        kbest  <= 4'd0;
                        tstate <= T_POWER;
                    end
                end
                T_POWER: begin
                    // lag d enters at cnt = d; its power leaves 6 clocks later
                    if (cnt < NL) begin
                        pw_i <= acc_i[cnt];
                        pw_q <= acc_q[cnt];
                    end
                    if (cnt >= 6 && cnt < 6 + NL && (cnt == 6 || pw_out > best)) begin
                        best  <= pw_out;
                        kbest <= cnt - 6;
                    end
                    cnt <= cnt + 8'd1;
                    if (cnt == 6 + NL) begin
                        interior <= (kbest != 0) && (kbest != NL - 1);
                        est_edge <= (kbest == 0) || (kbest == NL - 1);
                        lag      <= lo[15:0] + {12'd0, kbest};
                        c_i      <= acc_i[kbest];
                        c_q      <= acc_q[kbest];
                        el_i     <= (kbest == 0) ? 47'sd0 : acc_i[kbest - 1];
                        el_q     <= (kbest == 0) ? 47'sd0 : acc_q[kbest - 1];
                        er_i     <= (kbest == NL - 1) ? 47'sd0 : acc_i[kbest + 1];
                        er_q     <= (kbest == NL - 1) ? 47'sd0 : acc_q[kbest + 1];
                        est_start <= 1'b1;
                        tstate   <= T_EST;
                    end
                end
                T_EST: if (est_done) begin
                    busy   <= 1'b0;
                    done   <= 1'b1;
                    tstate <= T_IDLE;
                end
                default: tstate <= T_IDLE;
            endcase
        end
    end
endmodule
