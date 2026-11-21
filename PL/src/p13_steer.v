`timescale 1ns / 1ps
// P13: the predictive steering algorithm through FC, run in the PL (docs/ABI.md
// 4.16, rf_clock.plmodel.steer, which defines every step to the bit).
//
// 1. Pairs.  P13 watches the entries P8 and P9 hand to the queue.  A locked OWN
//    entry and the PEER entry of the same window (PPS_COUNT and slot) with a
//    measurement make a pair, 2 theta = +-((T_rx - peer T_tx) - (peer T_rx -
//    this node's T_tx of the slot before)) in fs, + on node B (STEER_CONTROL.ROLE_B).
// 2. Steps.  The second is cut into steps of STEER_STEP.SLOTS slots from this
//    node's PPS edge.  A step's theta is the mean of its pairs whose slot lies in
//    its last STEER_STEP.LATE slots, in 2^-16 fs; a step without such a pair has
//    no theta.  Each step is taken at its end tick.
// 3. The algorithm (rf_clock.plmodel.steer).  Two dot products over the last L
//    changes of FC (the own increment, and S's part from them), one over the last
//    p changes of theta less B's own corrections, and four scalar terms, all
//    through one 48 x 48 multiplier into a 104-bit accumulator, from coefficient
//    tables the ARM loads (STEER_COEF_*).  The rounded and clipped result is FC's
//    new fine DAC value.
// 4. The command.  "<prefix><4 digits>\r" goes to P12 with the step's end tick
//    plus STEER_WRITE_DELAY as its start; P12 keeps the ARM's lines out of the
//    way.  From the first step with theta on, every step sends one, also while
//    the algorithm settles or leaves FC as it is.
// 5. The log.  Every step from the first with theta on puts a QE_TYPE_STEER entry
//    into the queue (with STEER_CONTROL.LOG).
//
// The registers at 0x440 .. 0x4FF are decoded here; P2 forwards that range.
module p13_steer #(
    parameter [31:0]  SLOT_TICKS = 32'd200000,
    parameter [31:0]  SLOTS      = 32'd500,
    parameter integer LAW        = 12,   // the change tables: 2^LAW entries
    parameter integer NAW        = 12,   // the mean's tables and the differences: 2^NAW entries
    parameter integer PAW        = 6,    // the AR tables: 2^PAW entries
    parameter integer CMD_BYTES  = 16
) (
    input  wire                    clk,
    input  wire                    rst,
    input  wire [63:0]             raw_tick,
    input  wire                    pps_event,
    input  wire [63:0]             pps_tick,
    input  wire [31:0]             pps_count,
    input  wire                    pps_locked,
    input  wire [31:0]             tx_offset,      // SLOT_TX_OFFSET
    // what P8 and P9 hand to the queue
    input  wire                    own_take,
    input  wire [511:0]            own_entry,
    input  wire                    peer_take,
    input  wire [511:0]            peer_entry,
    // the queue: QE_TYPE_STEER entries
    output reg                     steer_req,
    output reg  [511:0]            steer_entry,
    input  wire                    steer_ack,
    // P12
    output reg                     cmd_valid,
    output reg  [63:0]             cmd_tick,
    output reg  [4:0]              cmd_len,
    output reg  [8*CMD_BYTES-1:0]  cmd_bytes,
    output wire                    reserve_valid,
    output wire [63:0]             reserve_tick,
    input  wire                    cmd_started,
    input  wire                    cmd_late,
    input  wire [63:0]             cmd_start_tick,
    // registers (0x440 .. 0x4FF)
    input  wire                    reg_wr,         // registered write, after reg_wr_ok
    input  wire [11:0]             reg_waddr,
    input  wire [31:0]             reg_wdata,
    input  wire [11:0]             reg_waddr_live, // the write on the bus now, for reg_wr_ok
    input  wire [31:0]             reg_wdata_live,
    output reg                     reg_wr_ok,
    input  wire [11:0]             reg_raddr,      // the read on the bus now
    output reg  [31:0]             reg_rdata,
    output reg                     reg_rd_ok,
    input  wire                    reg_rd,         // pulse after a read of reg_rd_addr
    input  wire [11:0]             reg_rd_addr
);
    `include "src/p_regs.vh"
    `include "src/p_defs.vh"

    localparam integer LMAXI = (1 << LAW);
    localparam integer NMAXI = (1 << NAW);
    localparam integer PMAXI = (1 << PAW);

    // ------------------------------------------------------------------
    // Registers
    // ------------------------------------------------------------------
    reg         ctl_enable, ctl_role_b, ctl_steps, ctl_log;
    reg [8:0]   step_slots, late_slots;
    reg [31:0]  write_delay;
    reg [LAW-1:0] cfg_l;
    reg [NAW-1:0] cfg_n;
    reg [PAW:0]   cfg_p;
    reg [31:0]  cfg_settle;
    reg [15:0]  cfg_max_step;
    reg [11:0]  cfg_lo, cfg_hi, cfg_low0;
    reg [31:0]  cfg_max_sat;
    reg [15:0]  steps_size, steps_hold;
    reg [63:0]  prefix;
    reg [3:0]   prefix_len;
    reg [2:0]   coef_table;
    reg [12:0]  coef_index;
    reg [15:0]  coef_hi;
    reg signed [47:0] c_rho, c_kinf, c_r0, c_target;

    // status kept for the registers
    reg         started, stopped, settling, last_late, busy_flag;
    reg [31:0]  idx;               // steps since the first with theta
    reg [11:0]  low;               // FC's fine DAC value
    reg signed [15:0] last_change;
    reg signed [47:0] last_theta;
    reg [31:0]  missing_count, late_count, pairs_count, saturated_count;
    reg [63:0]  last_cmd_tick;
    reg [31:0]  theta_snap_hi, cmd_snap_hi;

    wire [12:0] coef_waddr = coef_index;
    reg         coef_we;
    reg  [47:0] coef_wdata;

    // write validation on the bus's current write
    always @* begin
        reg_wr_ok = 1'b1;
        case (reg_waddr_live)
            REG_STEER_CONTROL, REG_STEER_WRITE_DELAY, REG_STEER_SETTLE, REG_STEER_MAX_STEP,
            REG_STEER_MAX_SATURATED, REG_STEER_STEPS, REG_STEER_PREFIX_LO, REG_STEER_PREFIX_HI,
            REG_STEER_COEF_HI, REG_STEER_COEF_LO: reg_wr_ok = 1'b1;
            REG_STEER_STEP: reg_wr_ok = (reg_wdata_live[8:0] != 9'd0) && ({23'd0, reg_wdata_live[8:0]} <= SLOTS) &&
                                        (reg_wdata_live[24:16] != 9'd0) && (reg_wdata_live[24:16] <= reg_wdata_live[8:0]);
            REG_STEER_L: reg_wr_ok = (reg_wdata_live != 32'd0) && (reg_wdata_live <= LMAXI - 2);
            REG_STEER_N: reg_wr_ok = (reg_wdata_live != 32'd0) && (reg_wdata_live <= NMAXI - 2);
            REG_STEER_P: reg_wr_ok = (reg_wdata_live != 32'd0) && (reg_wdata_live <= PMAXI);
            REG_STEER_FC_LIMITS: reg_wr_ok = (reg_wdata_live[11:0] <= reg_wdata_live[27:16]);
            REG_STEER_FC_START: reg_wr_ok = 1'b1;
            REG_STEER_PREFIX_LEN: reg_wr_ok = (reg_wdata_live <= 32'd8);
            REG_STEER_COEF_ADDR: reg_wr_ok = (reg_wdata_live[15:13] <= 3'd6);
            default: reg_wr_ok = 1'b0;
        endcase
    end

    reg enable_rise;
    always @(posedge clk) begin
        coef_we     <= 1'b0;
        enable_rise <= 1'b0;
        if (rst) begin
            ctl_enable <= 1'b0; ctl_role_b <= 1'b1; ctl_steps <= 1'b0; ctl_log <= 1'b1;
            step_slots <= 9'd50; late_slots <= 9'd25;
            write_delay <= 32'd20000;
            cfg_l <= 400; cfg_n <= 600; cfg_p <= 40;
            cfg_settle <= 32'd300; cfg_max_step <= 16'd1000;
            cfg_lo <= 12'd1024; cfg_hi <= 12'd3072; cfg_low0 <= 12'd2048;
            cfg_max_sat <= 32'd1200;
            steps_size <= 16'd0; steps_hold <= 16'd1;
            prefix <= 64'd0; prefix_len <= 4'd0;
            coef_table <= 3'd0; coef_index <= 13'd0; coef_hi <= 16'd0;
            c_rho <= 48'sd0; c_kinf <= 48'sd0; c_r0 <= 48'sd0; c_target <= 48'sd0;
        end else if (reg_wr) begin
            case (reg_waddr)
                REG_STEER_CONTROL: begin
                    enable_rise <= reg_wdata[STEERCTL_ENABLE] && !ctl_enable;
                    ctl_enable  <= reg_wdata[STEERCTL_ENABLE];
                    ctl_role_b  <= reg_wdata[STEERCTL_ROLE_B];
                    ctl_steps   <= reg_wdata[STEERCTL_STEPS];
                    ctl_log     <= reg_wdata[STEERCTL_LOG];
                end
                REG_STEER_STEP: begin
                    step_slots <= reg_wdata[8:0];
                    late_slots <= reg_wdata[24:16];
                end
                REG_STEER_WRITE_DELAY:   write_delay  <= reg_wdata;
                REG_STEER_L:             cfg_l        <= reg_wdata[LAW-1:0];
                REG_STEER_N:             cfg_n        <= reg_wdata[NAW-1:0];
                REG_STEER_P:             cfg_p        <= reg_wdata[PAW:0];
                REG_STEER_SETTLE:        cfg_settle   <= reg_wdata;
                REG_STEER_MAX_STEP:      cfg_max_step <= reg_wdata[15:0];
                REG_STEER_FC_LIMITS: begin
                    cfg_lo <= reg_wdata[11:0];
                    cfg_hi <= reg_wdata[27:16];
                end
                REG_STEER_FC_START:      cfg_low0     <= reg_wdata[11:0];
                REG_STEER_MAX_SATURATED: cfg_max_sat  <= reg_wdata;
                REG_STEER_STEPS: begin
                    steps_size <= reg_wdata[15:0];
                    steps_hold <= reg_wdata[31:16];
                end
                REG_STEER_PREFIX_LO:  prefix[31:0]  <= reg_wdata;
                REG_STEER_PREFIX_HI:  prefix[63:32] <= reg_wdata;
                REG_STEER_PREFIX_LEN: prefix_len    <= reg_wdata[3:0];
                REG_STEER_COEF_ADDR: begin
                    coef_table <= reg_wdata[15:13];
                    coef_index <= reg_wdata[12:0];
                end
                REG_STEER_COEF_HI: coef_hi <= reg_wdata[15:0];
                REG_STEER_COEF_LO: begin
                    coef_we    <= 1'b1;
                    coef_wdata <= {coef_hi, reg_wdata};
                    if (coef_table == COEF_TABLE_SCALAR) begin
                        case (coef_index[1:0])
                            2'd0: c_rho    <= {coef_hi, reg_wdata};
                            2'd1: c_kinf   <= {coef_hi, reg_wdata};
                            2'd2: c_r0     <= {coef_hi, reg_wdata};
                            default: c_target <= {coef_hi, reg_wdata};
                        endcase
                    end
                end
                default: ;
            endcase
        end
        // the index moves on after each entry written (coef_we is the cycle after)
        if (coef_we) coef_index <= coef_index + 13'd1;
    end

    // ------------------------------------------------------------------
    // Coefficient tables and the engine's memories (one write port, one read port each)
    // ------------------------------------------------------------------
    reg [47:0] hmem  [0:LMAXI-1];
    reg [47:0] kmem  [0:LMAXI-1];
    reg [47:0] amem  [0:PMAXI-1];
    reg [47:0] armem [0:PMAXI-1];
    reg [47:0] gmem  [0:NMAXI-1];
    reg [47:0] m2mem [0:NMAXI-1];
    reg [15:0] umem  [0:LMAXI-1];
    reg [47:0] dfmem [0:NMAXI-1];

    reg [LAW-1:0] h_ra, k_ra, u_ra, u_wa;
    reg [PAW-1:0] a_ra, ar_ra;
    reg [NAW-1:0] g_ra, m2_ra, df_ra, df_wa;
    reg [47:0]    h_q, k_q, a_q, ar_q, g_q, m2_q, df_q, df_wd;
    reg [15:0]    u_q, u_wd;
    reg           u_we, df_we;

    always @(posedge clk) begin
        if (coef_we && coef_table == COEF_TABLE_H)     hmem[coef_waddr[LAW-1:0]]  <= coef_wdata;
        if (coef_we && coef_table == COEF_TABLE_K)     kmem[coef_waddr[LAW-1:0]]  <= coef_wdata;
        if (coef_we && coef_table == COEF_TABLE_ALPHA) amem[coef_waddr[PAW-1:0]]  <= coef_wdata;
        if (coef_we && coef_table == COEF_TABLE_AR)    armem[coef_waddr[PAW-1:0]] <= coef_wdata;
        if (coef_we && coef_table == COEF_TABLE_GAMMA) gmem[coef_waddr[NAW-1:0]]  <= coef_wdata;
        if (coef_we && coef_table == COEF_TABLE_MEAN2) m2mem[coef_waddr[NAW-1:0]] <= coef_wdata;
        if (u_we)  umem[u_wa]   <= u_wd;
        if (df_we) dfmem[df_wa] <= df_wd;
        h_q  <= hmem[h_ra];
        k_q  <= kmem[k_ra];
        a_q  <= amem[a_ra];
        ar_q <= armem[ar_ra];
        g_q  <= gmem[g_ra];
        m2_q <= m2mem[m2_ra];
        u_q  <= umem[u_ra];
        df_q <= dfmem[df_ra];
    end

    // ------------------------------------------------------------------
    // Pairs
    // ------------------------------------------------------------------
    reg         own_v;
    reg [31:0]  own_cnt;
    reg [8:0]   own_slot;
    reg [63:0]  own_trx;

    reg         pr_v1, pr_v2, pr_v3;
    reg [31:0]  pr_cnt;
    reg [8:0]   pr_slot;
    reg [63:0]  pr_min, pr_ptrx;
    reg [63:0]  pr_txt;            // ticks of this node's burst in the slot before
    reg [63:0]  pr_txfs;
    reg signed [63:0] pr_d2;

    wire [8:0]  peer_slot = peer_entry[32*QE_SLOT +: 9];
    wire [31:0] peer_cnt  = peer_entry[32*QE_PPS_COUNT +: 32];
    wire [63:0] peer_ttx  = {peer_entry[32*QE_P_TTX_HI +: 32], peer_entry[32*QE_P_TTX_LO +: 32]};
    wire [63:0] peer_trx  = {peer_entry[32*QE_P_TRX_HI +: 32], peer_entry[32*QE_P_TRX_LO +: 32]};
    wire        peer_meas = peer_entry[32*QE_P_HEAD + PKST_MEASUREMENT];

    // the step and lateness of the pair: slot / step_slots by subtraction
    reg         dv_busy, dv_done;
    reg [8:0]   dv_rem, dv_q;
    reg signed [63:0] dv_d2;
    reg [31:0]  dv_cnt;

    reg         acc_have;
    reg [31:0]  acc_cnt;
    reg [8:0]   acc_step;
    reg signed [63:0] acc_sum;
    reg [9:0]   acc_n;

    function signed [47:0] clamp48;
        input signed [103:0] v;
        begin
            if (v > 104'sd140737488355327)       clamp48 = 48'sh7FFF_FFFF_FFFF;
            else if (v < -104'sd140737488355328) clamp48 = 48'sh8000_0000_0000;
            else                                 clamp48 = v[47:0];
        end
    endfunction

    always @(posedge clk) begin
        pr_v1 <= 1'b0; pr_v2 <= 1'b0; pr_v3 <= 1'b0; dv_done <= 1'b0;
        if (rst) begin
            own_v <= 1'b0;
            dv_busy <= 1'b0;
            pairs_count <= 32'd0;
        end else begin
            if (own_take) begin
                own_v    <= own_entry[8 + QEF_LOCKED];
                own_cnt  <= own_entry[32*QE_PPS_COUNT +: 32];
                own_slot <= own_entry[32*QE_SLOT +: 9];
                own_trx  <= {own_entry[32*QE_TRX_HI +: 32], own_entry[32*QE_TRX_LO +: 32]};
            end
            if (peer_take && own_v && peer_meas && peer_cnt == own_cnt && peer_slot == own_slot) begin
                pr_v1   <= 1'b1;
                pr_cnt  <= peer_cnt;
                pr_slot <= peer_slot;
                pr_min  <= own_trx - peer_ttx;
                pr_ptrx <= peer_trx;
                pr_txt  <= ((peer_slot == 9'd0) ? (SLOTS - 32'd1) : {23'd0, peer_slot - 9'd1}) * SLOT_TICKS
                           + {32'd0, tx_offset};
            end
            if (pr_v1) begin
                pr_v2   <= 1'b1;
                pr_txfs <= pr_txt * 64'd10_000_000;
            end
            if (pr_v2) begin
                pr_v3 <= 1'b1;
                pr_d2 <= ctl_role_b ? $signed(pr_min - (pr_ptrx - pr_txfs))
                                    : $signed((pr_ptrx - pr_txfs) - pr_min);
            end
            if (pr_v3 && !dv_busy) begin
                dv_busy <= 1'b1;
                dv_rem  <= pr_slot;
                dv_q    <= 9'd0;
                dv_d2   <= pr_d2;
                dv_cnt  <= pr_cnt;
                pairs_count <= pairs_count + 32'd1;
            end
            if (dv_busy) begin
                if (dv_rem >= step_slots) begin
                    dv_rem <= dv_rem - step_slots;
                    dv_q   <= dv_q + 9'd1;
                end else begin
                    dv_busy <= 1'b0;
                    dv_done <= 1'b1;
                end
            end
        end
    end

    // ------------------------------------------------------------------
    // Seconds and steps
    // ------------------------------------------------------------------
    reg         sec_valid, sec_done;
    reg [31:0]  sec_count;
    reg [8:0]   step_j;
    reg [9:0]   slot_end;
    reg [63:0]  end_tick;
    reg [63:0]  step_ticks;
    always @(posedge clk) step_ticks <= {55'd0, step_slots} * {32'd0, SLOT_TICKS};

    reg         fin_req;
    reg         fin_ack;
    reg [31:0]  fin_cnt;
    reg [8:0]   fin_step;
    reg [63:0]  fin_end;

    assign reserve_valid = ctl_enable && sec_valid && !stopped;
    assign reserve_tick  = end_tick + {32'd0, write_delay};

    always @(posedge clk) begin
        if (rst) begin
            sec_valid <= 1'b0;
            sec_done  <= 1'b0;
            fin_req   <= 1'b0;
            acc_have  <= 1'b0;
        end else begin
            // a pair joins its step's sum when it lies in the step's last late_slots slots
            if (dv_done) begin
                if (!acc_have || acc_cnt != dv_cnt || acc_step != dv_q) begin
                    acc_have <= 1'b1;
                    acc_cnt  <= dv_cnt;
                    acc_step <= dv_q;
                    acc_sum  <= 64'sd0;
                    acc_n    <= 10'd0;
                    if (dv_rem >= step_slots - late_slots) begin
                        acc_sum <= dv_d2;
                        acc_n   <= 10'd1;
                    end
                end else if (dv_rem >= step_slots - late_slots) begin
                    acc_sum <= acc_sum + dv_d2;
                    acc_n   <= acc_n + 10'd1;
                end
            end
            if (fin_ack) fin_req <= 1'b0;
            if (pps_event) begin
                if (sec_valid && !sec_done && !fin_req) begin  // the last step not taken yet: take it now
                    fin_req  <= 1'b1;
                    fin_cnt  <= sec_count;
                    fin_step <= step_j;
                    fin_end  <= end_tick;
                end
                sec_valid <= pps_locked;
                sec_done  <= 1'b0;
                sec_count <= pps_count;
                step_j    <= 9'd0;
                slot_end  <= {1'b0, step_slots};
                end_tick  <= pps_tick + step_ticks;
            end else if (sec_valid && !sec_done && raw_tick >= end_tick) begin
                fin_req  <= 1'b1;
                fin_cnt  <= sec_count;
                fin_step <= step_j;
                fin_end  <= end_tick;
                if ({22'd0, slot_end} >= SLOTS) begin
                    sec_done <= 1'b1;
                end else begin
                    step_j   <= step_j + 9'd1;
                    slot_end <= slot_end + {1'b0, step_slots};
                    end_tick <= end_tick + step_ticks;
                end
            end
        end
    end

    // ------------------------------------------------------------------
    // The multiply-accumulate unit: 48 x 48 into 104 bits, products of FC data
    // shifted left by 16 (Q40 -> Q56)
    // ------------------------------------------------------------------
    reg               mac_clear;
    reg               mi_v, mi_sh;
    reg signed [47:0] mi_a, mi_b;
    reg               m1_v, m1_sh, m2_v, m2_sh, m3_v, m3_sh;
    reg signed [47:0] m1_a, m1_b;
    reg signed [95:0] m2_p, m3_p;
    reg signed [103:0] acc;
    always @(posedge clk) begin
        m1_v <= mi_v; m1_sh <= mi_sh; m1_a <= mi_a; m1_b <= mi_b;
        m2_v <= m1_v; m2_sh <= m1_sh; m2_p <= m1_a * m1_b;
        m3_v <= m2_v; m3_sh <= m2_sh; m3_p <= m2_p;
        if (mac_clear) acc <= 104'sd0;
        else if (m3_v) acc <= acc + (m3_sh ? ($signed({{8{m3_p[95]}}, m3_p}) <<< 16) : $signed({{8{m3_p[95]}}, m3_p}));
    end


    // ------------------------------------------------------------------
    // The engine: one step at a time, in the order of rf_clock.plmodel.steer
    // ------------------------------------------------------------------
    localparam [5:0] E_IDLE = 6'd0, E_DIV0 = 6'd1, E_DIV = 6'd2, E_START = 6'd3,
                     E_S0A = 6'd4, E_S0B = 6'd5, E_S0C = 6'd6, E_DOWN0 = 6'd7, E_DOWN1 = 6'd8,
                     E_LOOP = 6'd9, E_DRAIN = 6'd10, E_PRED0 = 6'd11, E_PRED1 = 6'd12, E_PRED2 = 6'd13,
                     E_PRED3 = 6'd14, E_PUSH0 = 6'd15, E_PUSH1 = 6'd16, E_PUSH2 = 6'd17,
                     E_SA = 6'd19, E_SB = 6'd20, E_SC = 6'd21, E_SD = 6'd22, E_SE = 6'd23, E_SF = 6'd24,
                     E_SG = 6'd25, E_DECIDE = 6'd27, E_APPLY = 6'd28, E_STEPS = 6'd29,
                     E_BCD0 = 6'd30, E_BCD = 6'd31, E_CMD = 6'd32, E_LOG = 6'd33;
    reg [5:0]  st, lp_ret;
    reg        reset_pending;           // ENABLE written 0 -> 1: start afresh at the next idle cycle
    localparam signed [103:0] S_MAX = (104'sd1 <<< 86) - 104'sd1;
    localparam signed [103:0] S_MIN = -(104'sd1 <<< 86);

    // the step being taken
    reg        have_theta;
    reg [31:0] s_cnt;
    reg [8:0]  s_step;
    reg [63:0] s_end;
    reg [9:0]  s_pairs;
    reg signed [63:0] s_sum;

    // floor((sum * 2^16 + n) / (2 n)), restoring division of the magnitude
    reg [79:0] q_num;
    reg [11:0] q_r;
    reg [6:0]  q_i;
    reg        q_neg;
    reg [10:0] q_den;

    reg signed [47:0] theta;          // Q16
    reg signed [47:0] theta_prev;
    reg signed [63:0] W;              // the sum of the last min(n, D) differences
    reg [31:0] D;                     // differences stored; dF_m sits at m mod 2^NAW
    reg [31:0] Dn;
    reg signed [31:0] s0;
    reg signed [57:0] rem;            // Q56
    reg [31:0] sat_since;
    reg        sat_on;
    reg signed [71:0] down;           // Q16
    reg signed [47:0] dF;
    reg signed [103:0] S;
    reg signed [15:0] change;
    reg        flag_missing, flag_settling, flag_saturated, flag_stopped, flag_first;
    reg [1:0]  steps_phase;
    reg [15:0] steps_cnt;
    reg [31:0] k;                     // this step's index

    // loops: A from a table at an index counting up, B from U or dF at one counting down
    reg [1:0]  lp_ta;                 // 0 H, 1 K, 2 ALPHA, 3 AR
    reg        lp_bdf;                // B from the differences (else the changes)
    reg        lp_sh;
    reg [12:0] lp_n, lp_i;
    reg [12:0] lp_ia;
    reg [31:0] lp_ib;
    reg        lp_v, lp_v1, lp_sh1, lp_bdf1;
    reg [1:0]  lp_ta1;
    reg [3:0]  drain;
    reg               sc_v, sc_sh;
    reg signed [47:0] sc_a, sc_b;

    reg [11:0] bcd_v;
    reg [3:0]  bcd_d3, bcd_d2, bcd_d1, bcd_d0;

    wire [31:0] cfg_l32 = {{(32-LAW){1'b0}}, cfg_l};
    wire [31:0] cfg_n32 = {{(32-NAW){1'b0}}, cfg_n};
    wire [31:0] cfg_p32 = {{(31-PAW){1'b0}}, cfg_p};
    wire [63:0] cmd_tick_now = s_end + {32'd0, write_delay};

    always @* begin
        mi_v = 1'b0; mi_sh = 1'b0; mi_a = 48'sd0; mi_b = 48'sd0;
        if (lp_v1) begin
            mi_v  = 1'b1;
            mi_sh = lp_sh1;
            case (lp_ta1)
                2'd0: mi_a = h_q;
                2'd1: mi_a = k_q;
                2'd2: mi_a = a_q;
                default: mi_a = ar_q;
            endcase
            mi_b = lp_bdf1 ? df_q : {{32{u_q[15]}}, u_q};
        end else if (sc_v) begin
            mi_v = 1'b1; mi_sh = sc_sh; mi_a = sc_a; mi_b = sc_b;
        end
    end

    // the wanted FC, its rounding and the two clips (rf_clock.plmodel.steer.Engine.step)
    wire signed [95:0] low_q56  = $signed({28'd0, low, 56'd0});
    wire signed [95:0] wanted   = low_q56 + {{38{rem[57]}}, rem} - S[95:0];
    wire signed [95:0] wanted_r = wanted + $signed({40'd0, 1'b1, 55'd0});
    wire signed [39:0] rounded  = wanted_r[95:56];
    wire signed [39:0] lo_step  = $signed({28'd0, low}) - $signed({24'd0, cfg_max_step});
    wire signed [39:0] hi_step  = $signed({28'd0, low}) + $signed({24'd0, cfg_max_step});
    wire signed [39:0] clip1    = (rounded < lo_step) ? lo_step : ((rounded > hi_step) ? hi_step : rounded);
    wire signed [39:0] clip2    = (clip1 < $signed({28'd0, cfg_lo})) ? $signed({28'd0, cfg_lo})
                                : ((clip1 > $signed({28'd0, cfg_hi})) ? $signed({28'd0, cfg_hi}) : clip1);
    wire               in_range = (rounded >= $signed({28'd0, cfg_lo})) && (rounded <= $signed({28'd0, cfg_hi}));
    wire signed [95:0] rem_new  = wanted - $signed({clip2, 56'd0});

    // the command: prefix, four digits, carriage return
    reg [8*CMD_BYTES-1:0] bytes_c;
    integer bi;
    always @* begin
        bytes_c = {8*CMD_BYTES{1'b0}};
        for (bi = 0; bi < CMD_BYTES; bi = bi + 1) begin
            if (bi < prefix_len)            bytes_c[8*bi +: 8] = prefix[8*(bi % 8) +: 8];
            else if (bi == prefix_len)      bytes_c[8*bi +: 8] = 8'h30 + {4'd0, bcd_d3};
            else if (bi == prefix_len + 1)  bytes_c[8*bi +: 8] = 8'h30 + {4'd0, bcd_d2};
            else if (bi == prefix_len + 2)  bytes_c[8*bi +: 8] = 8'h30 + {4'd0, bcd_d1};
            else if (bi == prefix_len + 3)  bytes_c[8*bi +: 8] = 8'h30 + {4'd0, bcd_d0};
            else if (bi == prefix_len + 4)  bytes_c[8*bi +: 8] = 8'h0D;
        end
    end

    wire signed [71:0] th72    = {{24{theta[47]}}, theta};
    wire signed [71:0] thp72   = {{24{theta_prev[47]}}, theta_prev};
    wire signed [71:0] df72    = {{24{dF[47]}}, dF};
    wire signed [71:0] tgt72   = {{24{c_target[47]}}, c_target};
    wire signed [71:0] dF_meas = th72 - thp72 - down;
    wire signed [71:0] th_pred = thp72 + down + df72;

    function signed [47:0] clamp48s;    // the same for 72-bit values
        input signed [71:0] v;
        begin
            if (v > 72'sd140737488355327)       clamp48s = 48'sh7FFF_FFFF_FFFF;
            else if (v < -72'sd140737488355328) clamp48s = 48'sh8000_0000_0000;
            else                                clamp48s = v[47:0];
        end
    endfunction

    always @(posedge clk) begin
        fin_ack   <= 1'b0;
        cmd_valid <= 1'b0;
        mac_clear <= 1'b0;
        sc_v      <= 1'b0;
        u_we      <= 1'b0;
        df_we     <= 1'b0;
        lp_v      <= 1'b0;
        lp_v1     <= lp_v;
        lp_sh1    <= lp_sh;
        lp_ta1    <= lp_ta;
        lp_bdf1   <= lp_bdf;
        if (steer_ack) steer_req <= 1'b0;
        if (enable_rise) reset_pending <= 1'b1;
        if (rst) begin
            st <= E_IDLE;
            reset_pending <= 1'b0;
            started <= 1'b0; stopped <= 1'b0; settling <= 1'b0; last_late <= 1'b0; busy_flag <= 1'b0;
            idx <= 32'd0; low <= 12'd0; last_change <= 16'sd0; last_theta <= 48'sd0;
            missing_count <= 32'd0; late_count <= 32'd0; saturated_count <= 32'd0;
            last_cmd_tick <= 64'd0;
            steer_req <= 1'b0;
            D <= 32'd0; W <= 64'sd0; s0 <= 32'sd0; rem <= 58'sd0; sat_on <= 1'b0; sat_since <= 32'd0;
            theta_prev <= 48'sd0;
            cmd_tick <= 64'd0; cmd_len <= 5'd0; cmd_bytes <= {8*CMD_BYTES{1'b0}};
        end else begin
            if (cmd_started) begin
                last_cmd_tick <= cmd_start_tick;
                last_late     <= cmd_late;
                if (cmd_late) late_count <= late_count + 32'd1;
            end
            case (st)
                E_IDLE: begin
                    busy_flag <= 1'b0;
                    if (reset_pending) begin
                        reset_pending <= 1'b0;
                        started <= 1'b0; stopped <= 1'b0; settling <= 1'b0;
                        idx <= 32'd0; low <= cfg_low0; rem <= 58'sd0; s0 <= 32'sd0;
                        W <= 64'sd0; D <= 32'd0; sat_on <= 1'b0; theta_prev <= 48'sd0;
                        last_change <= 16'sd0;
                    end else if (fin_req && !fin_ack) begin
                        fin_ack <= 1'b1;
                        s_cnt   <= fin_cnt;
                        s_step  <= fin_step;
                        s_end   <= fin_end;
                        have_theta <= acc_have && acc_cnt == fin_cnt && acc_step == fin_step && acc_n != 10'd0;
                        s_pairs <= (acc_have && acc_cnt == fin_cnt && acc_step == fin_step) ? acc_n : 10'd0;
                        s_sum   <= acc_sum;
                        flag_missing <= 1'b0; flag_settling <= 1'b0; flag_saturated <= 1'b0;
                        flag_stopped <= 1'b0; flag_first <= 1'b0;
                        change <= 16'sd0;
                        S      <= 104'sd0;
                        dF     <= 48'sd0;
                        k <= idx;
                        if (ctl_enable && !stopped) begin
                            busy_flag <= 1'b1;
                            st <= E_DIV0;
                        end
                    end
                end
                E_DIV0: begin
                    q_neg <= s_sum[63];
                    q_num <= s_sum[63] ? (({16'd0, -s_sum} << 16) + {70'd0, s_pairs} - 80'd1)
                                       : (({16'd0, s_sum} << 16) + {70'd0, s_pairs});
                    q_den <= {s_pairs, 1'b0};
                    q_r   <= 12'd0;
                    q_i   <= 7'd80;
                    st    <= have_theta ? E_DIV : E_START;
                end
                E_DIV: begin
                    if (q_i != 7'd0) begin
                        if ({q_r[10:0], q_num[79]} >= {1'b0, q_den}) begin
                            q_r   <= {q_r[10:0], q_num[79]} - {1'b0, q_den};
                            q_num <= {q_num[78:0], 1'b1};
                        end else begin
                            q_r   <= {q_r[10:0], q_num[79]};
                            q_num <= {q_num[78:0], 1'b0};
                        end
                        q_i <= q_i - 7'd1;
                    end else begin
                        theta <= clamp48(q_neg ? -$signed({24'd0, q_num}) : $signed({24'd0, q_num}));
                        st    <= E_START;
                    end
                end
                E_START: begin
                    if (!started) begin
                        if (have_theta) begin
                            started       <= 1'b1;
                            settling      <= 1'b1;
                            flag_first    <= 1'b1;
                            flag_settling <= 1'b1;
                            theta_prev    <= theta;
                            last_theta    <= theta;
                            u_we <= 1'b1; u_wa <= {LAW{1'b0}}; u_wd <= 16'd0;
                            idx  <= 32'd1;
                            dF   <= 48'sd0; down <= 72'sd0; S <= 104'sd0;
                            st   <= E_BCD0;
                        end else begin
                            st <= E_IDLE;
                        end
                    end else begin
                        flag_missing <= !have_theta;
                        if (!have_theta) missing_count <= missing_count + 32'd1;
                        st <= E_S0A;
                    end
                end
                // U_{k-L-1} leaves the table and joins s0
                E_S0A: if (k > cfg_l32) begin
                        u_ra <= k[LAW-1:0] - cfg_l - {{(LAW-1){1'b0}}, 1'b1};
                        st   <= E_S0B;
                    end else st <= E_DOWN0;
                E_S0B: st <= E_S0C;
                E_S0C: begin
                    s0 <= s0 + {{16{u_q[15]}}, u_q};
                    st <= E_DOWN0;
                end
                // the own increment: sum_d h(d) U_{k-d} + rho s0, Q32 -> Q16
                E_DOWN0: begin
                    mac_clear <= 1'b1;
                    sc_v <= 1'b1; sc_sh <= 1'b0; sc_a <= c_rho; sc_b <= {{16{s0[31]}}, s0};
                    lp_ta <= 2'd0; lp_bdf <= 1'b0; lp_sh <= 1'b0;
                    lp_n  <= (k < cfg_l32) ? k[12:0] : {{(13-LAW){1'b0}}, cfg_l};
                    lp_i  <= 13'd0; lp_ia <= 13'd1; lp_ib <= k - 32'd1;
                    lp_ret <= E_DOWN1;
                    st <= E_LOOP;
                end
                E_LOOP: begin
                    if (lp_i != lp_n) begin
                        h_ra  <= lp_ia[LAW-1:0];
                        k_ra  <= lp_ia[LAW-1:0];
                        a_ra  <= lp_ia[PAW-1:0];
                        ar_ra <= lp_ia[PAW-1:0];
                        u_ra  <= lp_ib[LAW-1:0];
                        df_ra <= lp_ib[NAW-1:0];
                        lp_v  <= 1'b1;
                        lp_ia <= lp_ia + 13'd1;
                        lp_ib <= lp_ib - 32'd1;
                        lp_i  <= lp_i + 13'd1;
                    end else begin
                        drain <= 4'd5;
                        st    <= E_DRAIN;
                    end
                end
                E_DRAIN: if (drain != 4'd0) drain <= drain - 4'd1; else st <= lp_ret;
                E_DOWN1: begin
                    down <= ((acc + $signed({88'd0, 1'b1, 15'd0})) >>> 16);
                    if (ctl_steps) st <= E_STEPS;
                    else if (have_theta) st <= E_PUSH0;
                    else if (D > cfg_p32) st <= E_PRED0;
                    else begin
                        dF <= 48'sd0;
                        st <= E_PUSH0;
                    end
                end
                // a step without theta: the algorithm's prediction of dF
                E_PRED0: begin
                    m2_ra <= (D < cfg_n32) ? D[NAW-1:0] : cfg_n;
                    st <= E_PRED1;
                end
                E_PRED1: st <= E_PRED2;
                E_PRED2: begin
                    mac_clear <= 1'b1;
                    sc_v <= 1'b1; sc_sh <= 1'b0; sc_a <= m2_q; sc_b <= clamp48({{40{W[63]}}, W});
                    lp_ta <= 2'd3; lp_bdf <= 1'b1; lp_sh <= 1'b0;
                    lp_n  <= cfg_p32[12:0];
                    lp_i  <= 13'd0; lp_ia <= 13'd0; lp_ib <= D;
                    lp_ret <= E_PRED3;
                    st <= E_LOOP;
                end
                E_PRED3: begin
                    dF <= clamp48((acc + $signed({64'd0, 1'b1, 39'd0})) >>> 40);
                    st <= E_PUSH0;
                end
                // dF joins the differences and W
                E_PUSH0: begin
                    if (have_theta) dF <= clamp48s(dF_meas);
                    Dn <= D + 32'd1;
                    if (D + 32'd1 > cfg_n32) begin
                        df_ra <= D[NAW-1:0] + {{(NAW-1){1'b0}}, 1'b1} - cfg_n;
                        st <= E_PUSH1;
                    end else st <= E_PUSH2;
                end
                E_PUSH1: st <= E_PUSH2;
                E_PUSH2: begin
                    if (Dn > cfg_n32) W <= W + {{16{dF[47]}}, dF} - {{16{df_q[47]}}, df_q};
                    else              W <= W + {{16{dF[47]}}, dF};
                    df_we <= 1'b1; df_wa <= Dn[NAW-1:0]; df_wd <= dF;
                    D <= Dn;
                    if (have_theta) begin
                        theta_prev <= theta;
                        last_theta <= theta;
                    end else begin
                        theta_prev <= clamp48s(th_pred);
                        last_theta <= clamp48s(th_pred);
                    end
                    if (!have_theta) st <= E_APPLY;
                    else if (k < cfg_settle) begin
                        flag_settling <= 1'b1;
                        st <= E_APPLY;
                    end else st <= E_SA;
                end
                // S = R0 (theta - target) + gamma(n_e) W + K_inf s0 + sum alpha dF + sum K U
                E_SA: begin
                    settling <= 1'b0;
                    g_ra <= (D < cfg_n32) ? D[NAW-1:0] : cfg_n;
                    st <= E_SB;
                end
                E_SB: st <= E_SC;
                E_SC: begin
                    mac_clear <= 1'b1;
                    sc_v <= 1'b1; sc_sh <= 1'b0; sc_a <= c_r0; sc_b <= clamp48s(th72 - tgt72);
                    st <= E_SD;
                end
                E_SD: begin
                    sc_v <= 1'b1; sc_sh <= 1'b0; sc_a <= g_q; sc_b <= clamp48({{40{W[63]}}, W});
                    st <= E_SE;
                end
                E_SE: begin
                    sc_v <= 1'b1; sc_sh <= 1'b1; sc_a <= c_kinf; sc_b <= {{16{s0[31]}}, s0};
                    lp_ta <= 2'd2; lp_bdf <= 1'b1; lp_sh <= 1'b0;
                    lp_n  <= cfg_p32[12:0];
                    lp_i  <= 13'd0; lp_ia <= 13'd0; lp_ib <= D;
                    lp_ret <= E_SF;
                    st <= E_LOOP;
                end
                E_SF: begin
                    lp_ta <= 2'd1; lp_bdf <= 1'b0; lp_sh <= 1'b1;
                    lp_n  <= (k < cfg_l32) ? k[12:0] : {{(13-LAW){1'b0}}, cfg_l};
                    lp_i  <= 13'd0; lp_ia <= 13'd1; lp_ib <= k - 32'd1;
                    lp_ret <= E_SG;
                    st <= E_LOOP;
                end
                E_SG: begin
                    if (acc > S_MAX)       S <= S_MAX;
                    else if (acc < S_MIN)  S <= S_MIN;
                    else                   S <= acc;
                    st <= E_DECIDE;
                end
                E_DECIDE: begin
                    if (in_range) begin
                        sat_on <= 1'b0;
                    end else begin
                        flag_saturated  <= 1'b1;
                        saturated_count <= saturated_count + 32'd1;
                        if (!sat_on) begin
                            sat_on    <= 1'b1;
                            sat_since <= k;
                        end
                    end
                    if (!in_range && (sat_on ? (k - sat_since >= cfg_max_sat) : (cfg_max_sat == 32'd0))) begin
                        // the algorithm gives up: FC stays, no command, steering stops
                        stopped      <= 1'b1;
                        flag_stopped <= 1'b1;
                        change       <= 16'sd0;
                    end else begin
                        rem    <= (clip2 == rounded) ? rem_new[57:0] : 58'sd0;
                        change <= clip2[15:0] - {4'd0, low};
                    end
                    st <= E_APPLY;
                end
                // FC steps instead of the algorithm (STEER_CONTROL.STEPS)
                E_STEPS: begin
                    if (k >= cfg_settle) begin
                        if (k == cfg_settle || steps_cnt == steps_hold) begin
                            steps_cnt   <= 16'd1;
                            steps_phase <= (k == cfg_settle) ? 2'd0 : steps_phase + 2'd1;
                            case ((k == cfg_settle) ? 2'd0 : steps_phase + 2'd1)
                                2'd0: change <= $signed({4'd0, cfg_low0}) + $signed(steps_size) - $signed({4'd0, low});
                                2'd2: change <= $signed({4'd0, cfg_low0}) - $signed(steps_size) - $signed({4'd0, low});
                                default: change <= $signed({4'd0, cfg_low0}) - $signed({4'd0, low});
                            endcase
                        end else begin
                            steps_cnt <= steps_cnt + 16'd1;
                        end
                    end else flag_settling <= 1'b1;
                    st <= E_APPLY;
                end
                E_APPLY: begin
                    u_we <= 1'b1; u_wa <= k[LAW-1:0]; u_wd <= change;
                    low  <= low + change[11:0];
                    last_change <= change;
                    idx  <= k + 32'd1;
                    st   <= flag_stopped ? E_LOG : E_BCD0;
                end
                E_BCD0: begin
                    bcd_v <= low;
                    bcd_d3 <= 4'd0; bcd_d2 <= 4'd0; bcd_d1 <= 4'd0; bcd_d0 <= 4'd0;
                    st <= E_BCD;
                end
                E_BCD: begin
                    if (bcd_v >= 12'd1000) begin
                        bcd_v <= bcd_v - 12'd1000; bcd_d3 <= bcd_d3 + 4'd1;
                    end else if (bcd_v >= 12'd100) begin
                        bcd_v <= bcd_v - 12'd100; bcd_d2 <= bcd_d2 + 4'd1;
                    end else if (bcd_v >= 12'd10) begin
                        bcd_v <= bcd_v - 12'd10; bcd_d1 <= bcd_d1 + 4'd1;
                    end else begin
                        bcd_d0 <= bcd_v[3:0];
                        st <= E_CMD;
                    end
                end
                E_CMD: begin
                    cmd_valid <= 1'b1;
                    cmd_tick  <= cmd_tick_now;
                    cmd_len   <= {1'b0, prefix_len} + 5'd5;
                    cmd_bytes <= bytes_c;
                    st <= E_LOG;
                end
                E_LOG: begin
                    if (ctl_log) begin
                        steer_req   <= 1'b1;
                        steer_entry <= {
                            32'd0,                                                  // 15
                            s0,                                                     // 14
                            {{6{rem[57]}}, rem[57:32]},                             // 13 remainder >> 32
                            {change, 4'd0, low},                                    // 12
                            S[95:64], S[63:32],                                     // 11, 10: S >> 32
                            {{16{dF[47]}}, dF[47:32]}, dF[31:0],                    // 9, 8
                            {{16{last_theta[47]}}, last_theta[47:32]}, last_theta[31:0], // 7, 6
                            k,                                                      // 5
                            cmd_tick_now[63:32], cmd_tick_now[31:0],                // 4, 3
                            {6'd0, s_pairs, 7'd0, s_step},                         // 2
                            s_cnt,                                                  // 1
                            {16'd0, 1'b0, ctl_steps, last_late, flag_first, flag_stopped, flag_saturated,
                             flag_settling, flag_missing, QE_TYPE_STEER}             // 0
                        };
                    end
                    st <= E_IDLE;
                end
                default: st <= E_IDLE;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Register reads
    // ------------------------------------------------------------------
    always @* begin
        reg_rd_ok = 1'b1;
        reg_rdata = 32'd0;
        case (reg_raddr)
            REG_STEER_CONTROL:       reg_rdata = {28'd0, ctl_log, ctl_steps, ctl_role_b, ctl_enable};
            REG_STEER_STATUS:        reg_rdata = {26'd0, busy_flag, last_late, settling, stopped, started, ctl_enable};
            REG_STEER_STEP:          reg_rdata = {7'd0, late_slots, 7'd0, step_slots};
            REG_STEER_WRITE_DELAY:   reg_rdata = write_delay;
            REG_STEER_L:             reg_rdata = cfg_l32;
            REG_STEER_N:             reg_rdata = cfg_n32;
            REG_STEER_P:             reg_rdata = cfg_p32;
            REG_STEER_SETTLE:        reg_rdata = cfg_settle;
            REG_STEER_MAX_STEP:      reg_rdata = {16'd0, cfg_max_step};
            REG_STEER_FC_LIMITS:     reg_rdata = {4'd0, cfg_hi, 4'd0, cfg_lo};
            REG_STEER_FC_START:      reg_rdata = {20'd0, cfg_low0};
            REG_STEER_MAX_SATURATED: reg_rdata = cfg_max_sat;
            REG_STEER_STEPS:         reg_rdata = {steps_hold, steps_size};
            REG_STEER_PREFIX_LO:     reg_rdata = prefix[31:0];
            REG_STEER_PREFIX_HI:     reg_rdata = prefix[63:32];
            REG_STEER_PREFIX_LEN:    reg_rdata = {28'd0, prefix_len};
            REG_STEER_COEF_ADDR:     reg_rdata = {16'd0, coef_table, coef_index};
            REG_STEER_COEF_HI:       reg_rdata = {16'd0, coef_hi};
            REG_STEER_COEF_LO:       reg_rdata = 32'd0;
            REG_STEER_INDEX:         reg_rdata = idx;
            REG_STEER_FC:            reg_rdata = {last_change, 4'd0, low};
            REG_STEER_THETA_LO:      reg_rdata = last_theta[31:0];
            REG_STEER_THETA_HI:      reg_rdata = theta_snap_hi;
            REG_STEER_MISSING_CNT:   reg_rdata = missing_count;
            REG_STEER_LATE_CNT:      reg_rdata = late_count;
            REG_STEER_CMD_TICK_LO:   reg_rdata = last_cmd_tick[31:0];
            REG_STEER_CMD_TICK_HI:   reg_rdata = cmd_snap_hi;
            REG_STEER_PAIRS_CNT:     reg_rdata = pairs_count;
            REG_STEER_SATURATED_CNT: reg_rdata = saturated_count;
            REG_STEER_LMAX:          reg_rdata = LMAXI;
            REG_STEER_NMAX:          reg_rdata = NMAXI;
            REG_STEER_PMAX:          reg_rdata = PMAXI;
            default:                 reg_rd_ok = 1'b0;
        endcase
    end

    always @(posedge clk) begin
        if (rst) begin
            theta_snap_hi <= 32'd0;
            cmd_snap_hi   <= 32'd0;
        end else if (reg_rd) begin
            if (reg_rd_addr == REG_STEER_THETA_LO)    theta_snap_hi <= {{16{last_theta[47]}}, last_theta[47:32]};
            if (reg_rd_addr == REG_STEER_CMD_TICK_LO) cmd_snap_hi   <= last_cmd_tick[63:32];
        end
    end
endmodule
