`timescale 1ns / 1ps
// P9: the measurement packet (docs/ABI.md 4.11; rf_clock.plmodel.packet).
//
// Assembler: at each burst's arm, this node's fields and the latest
// measurement into the 288-bit packet, MSB first:
//   version 8 | config_id 32 | PPS_COUNT 32 | slot 16 | TIME_EPOCH 8 |
//   T_tx 64 | T_rx 64 | phase 32 | quality 8 | status 8 | CRC 16
// the CRC-16/CCITT-FALSE of the first 34 bytes, one byte per clock.
//
// Despreader: in a span armed with a lock, the packet starts at window sample
// P = lag + 8 (prn + guard).  The span is realigned to it: realigned beat i
// holds window samples P + 8 i .. P + 8 i + 7 (from span beats Pb + i and
// Pb + i + 1, Pb = P / 8), multiplied with beat i of the code reference (the
// packet code through this despreader's own chip memory and FIR).  Bit b sums
// the products of samples 512 b + 36 .. 512 b + 547 (L = 64): beats 64 b + 4
// .. 64 b + 68, the halves of the boundary beats split between two bits.  The
// bit is 1 when Re(z) Re(c) + Im(z) Im(c) < 0, c the lock's correlation.
// After 288 bits a good CRC gives a QE_TYPE_PEER entry, a bad one a count.
module p9_packet #(
    parameter integer AW     = 11,
    parameter integer SPREAD = 64,
    parameter integer PBITS  = 288
) (
    input  wire               clk,
    input  wire               rst,
    // Assembler.
    input  wire               tx_arm,
    input  wire [63:0]        ev_tick,
    input  wire [8:0]         ev_slot,
    input  wire [31:0]        sec_count,
    input  wire [63:0]        sec_tick,
    input  wire [7:0]         time_epoch,
    input  wire [31:0]        config_id,
    input  wire               locked,
    input  wire               pps_locked,
    input  wire               clocks_locked,
    input  wire               meas_valid,
    input  wire [63:0]        meas_trx,
    input  wire [31:0]        meas_phase,
    input  wire [7:0]         meas_quality,
    input  wire [3:0]         meas_flags,
    output reg  [PBITS-1:0]   packet,
    output reg                packet_ready,
    // Despreader: the span from P8.
    input  wire               span_v,
    input  wire [31:0]        span_m,
    input  wire [255:0]       span_d,
    input  wire               span_dsp,
    input  wire [15:0]        span_lag,
    input  wire [93:0]        span_corr,
    input  wire [31:0]        span_pps_count,
    input  wire [8:0]         span_slot,
    input  wire [63:0]        span_tick,
    input  wire [AW+5:0]      prn_chips,
    input  wire [15:0]        guard_chips,
    input  wire [3:0]         code_we,
    input  wire [AW-1:0]      mem_waddr,
    input  wire [31:0]        mem_wdata,
    input  wire [81*16-1:0]   taps,
    output reg  [31:0]        rx_count,
    output reg  [31:0]        crc_count,
    // PEER queue entry.
    output reg                peer_req,
    output reg  [511:0]       peer_entry,
    input  wire               peer_ack
);
    `include "src/p_defs.vh"

    function [15:0] crc_byte(input [15:0] crc, input [7:0] data);
        integer b;
        reg [15:0] c;
        begin
            c = crc ^ {data, 8'd0};
            for (b = 0; b < 8; b = b + 1)
                c = c[15] ? ((c << 1) ^ 16'h1021) : (c << 1);
            crc_byte = c;
        end
    endfunction

    localparam integer NBYTES = PBITS / 8 - 2;   // 34 bytes under the CRC

    // =================================================================
    // Assembler.
    // =================================================================
    reg [PBITS-17:0] body;
    reg [63:0]       a_dtick;
    reg [5:0]        a_n;
    reg [15:0]       a_crc;
    reg [1:0]        a_state;
    reg [31:0]       a_count, a_cfg;
    reg [8:0]        a_slot;
    reg [7:0]        a_epoch, a_status;
    always @(posedge clk) begin
        if (rst) begin
            a_state      <= 2'd0;
            packet_ready <= 1'b0;
            packet       <= {PBITS{1'b0}};
        end else begin
            case (a_state)
                2'd0: if (tx_arm) begin
                    packet_ready <= 1'b0;
                    a_dtick  <= ev_tick - sec_tick;
                    a_count  <= sec_count;
                    a_slot   <= ev_slot;
                    a_epoch  <= time_epoch;
                    a_cfg    <= config_id;
                    a_status <= {2'b00,
                                 clocks_locked, pps_locked,
                                 meas_flags[RXF_GAP] | meas_flags[RXF_OVERFLOW],
                                 meas_flags == 4'd0, meas_valid, locked};
                    a_state  <= 2'd1;
                end
                2'd1: begin
                    body  <= {PACKET_FORMAT_VERSION, a_cfg, a_count, {7'd0, a_slot}, a_epoch,
                              a_dtick * 64'd10000000, meas_trx, meas_phase, meas_quality, a_status};
                    a_crc <= 16'hFFFF;
                    a_n   <= 6'd0;
                    a_state <= 2'd2;
                end
                2'd2: begin
                    a_crc <= crc_byte(a_crc, body[PBITS-17 - 8*a_n -: 8]);
                    a_n   <= a_n + 6'd1;
                    if (a_n == NBYTES - 1) a_state <= 2'd3;
                end
                default: begin
                    packet       <= {body, a_crc};
                    packet_ready <= 1'b1;
                    a_state      <= 2'd0;
                end
            endcase
        end
    end

    // =================================================================
    // Despreader.
    // =================================================================
    localparam integer LB   = $clog2(SPREAD);
    localparam integer BEAT_LAST = PBITS * SPREAD + 4;   // the last beat: its lower half ends bit PBITS-1
    localparam integer CODE_CHIPS = PBITS * SPREAD;

    reg        d_on;          // this span is despread
    reg [31:0] d_pb;
    reg [2:0]  d_po;
    reg signed [46:0] c_i, c_q;
    wire [31:0] p_start = {16'd0, span_lag} + 32'd8 * ({{(26-AW){1'b0}}, prn_chips} + {16'd0, guard_chips});

    // code reference: chip i is read when span beat i + Pb + 1 - 8 is here
    wire signed [32:0] chip_i = $signed({1'b0, span_m}) + 33'sd7 - $signed({1'b0, d_pb});
    wire               chip_in = span_v && d_on && chip_i >= 0 && chip_i < CODE_CHIPS;
    wire               code_chip;
    p_chip_mem #(.AW(AW)) u_code (
        .clk (clk), .we (code_we), .waddr (mem_waddr), .wdata (mem_wdata),
        .rd_addr (chip_i[AW+4:0]), .rd_chip (code_chip), .rd_word ()
    );
    reg [1:0] nz_d;
    always @(posedge clk) nz_d <= {nz_d[0], chip_in};
    wire [127:0] cref;
    p_chip_fir u_fir (
        .clk (clk), .rst (rst), .en (1'b1), .chip_nz (nz_d[1]), .chip_neg (~code_chip),
        .taps (taps), .beat (cref)
    );

    // realigned beat i = span_m - Pb - 1 from the previous and the current span beat
    reg  [255:0] prev_d;
    always @(posedge clk) prev_d <= span_d;
    wire signed [32:0] beat_i = $signed({1'b0, span_m}) - $signed({1'b0, d_pb}) - 33'sd1;
    reg  signed [15:0] ru_i [0:7];
    reg  signed [15:0] ru_q [0:7];
    reg  signed [15:0] cr [0:7];
    reg  [31:0]        s1_i;
    reg                s1_v;
    integer p;
    always @(posedge clk) begin
        for (p = 0; p < 8; p = p + 1) begin
            if (d_po + p < 8) begin
                ru_i[p] <= prev_d[16 * (d_po + p) +: 16];
                ru_q[p] <= prev_d[128 + 16 * (d_po + p) +: 16];
            end else begin
                ru_i[p] <= span_d[16 * (d_po + p - 8) +: 16];
                ru_q[p] <= span_d[128 + 16 * (d_po + p - 8) +: 16];
            end
            cr[p] <= cref[16 * p +: 16];
        end
        s1_v <= span_v && d_on && beat_i >= 4 && beat_i <= BEAT_LAST;
        s1_i <= beat_i[31:0];
    end

    // products, then the two half sums
    reg signed [31:0] pr_i [0:7];
    reg signed [31:0] pr_q [0:7];
    reg        s2_v, s3_v;
    reg [31:0] s2_i, s3_i;
    reg signed [33:0] zl_i, zl_q, zh_i, zh_q;
    always @(posedge clk) begin
        for (p = 0; p < 8; p = p + 1) begin
            pr_i[p] <= ru_i[p] * cr[p];
            pr_q[p] <= ru_q[p] * cr[p];
        end
        s2_v <= s1_v; s2_i <= s1_i;
        zl_i <= pr_i[0] + pr_i[1] + pr_i[2] + pr_i[3];
        zl_q <= pr_q[0] + pr_q[1] + pr_q[2] + pr_q[3];
        zh_i <= pr_i[4] + pr_i[5] + pr_i[6] + pr_i[7];
        zh_q <= pr_q[4] + pr_q[5] + pr_q[6] + pr_q[7];
        s3_v <= s2_v; s3_i <= s2_i;
    end

    // bit sums: a boundary beat (i = 64 b + 4) ends bit b - 1 with its lower half
    // and starts bit b with its upper half
    reg signed [41:0] z_i, z_q;
    reg signed [41:0] f_i, f_q;   // a finished bit
    reg               f_v;
    wire              boundary = (s3_i[LB-1:0] == 4);
    always @(posedge clk) begin
        f_v <= 1'b0;
        if (s3_v) begin
            if (boundary) begin
                if (s3_i != 4) begin
                    f_i <= z_i + zl_i;
                    f_q <= z_q + zl_q;
                    f_v <= 1'b1;
                end
                z_i <= zh_i;
                z_q <= zh_q;
            end else begin
                z_i <= z_i + zl_i + zh_i;
                z_q <= z_q + zl_q + zh_q;
            end
        end
    end

    // the bit: the sign of the projection on the lock's correlation
    reg signed [88:0] m_i, m_q, m_i2, m_q2;
    reg signed [89:0] proj;
    reg [3:0]         m_v;
    always @(posedge clk) begin
        m_i  <= f_i * c_i;
        m_q  <= f_q * c_q;
        m_i2 <= m_i;
        m_q2 <= m_q;
        proj <= m_i2 + m_q2;
        m_v  <= {m_v[2:0], f_v};
    end
    wire bit_v   = m_v[3];
    wire bit_val = proj[89];

    // bits into the packet, then the CRC and the entry
    reg [PBITS-1:0] rx_pkt;
    reg [8:0]       rx_bits;
    reg [1:0]       r_state;
    reg [5:0]       r_n;
    reg [15:0]      r_crc;
    reg [31:0]      r_count, r_tick_lo, r_tick_hi;
    reg [8:0]       r_slot;
    reg             span_v_d;

    always @(posedge clk) begin
        span_v_d <= span_v;
        if (rst) begin
            d_on      <= 1'b0;
            d_pb      <= 32'd0;
            d_po      <= 3'd0;
            c_i       <= 47'sd0;
            c_q       <= 47'sd0;
            rx_bits   <= 9'd0;
            r_state   <= 2'd0;
            rx_count  <= 32'd0;
            crc_count <= 32'd0;
            peer_req  <= 1'b0;
        end else begin
            // a span begins: take the despreader's configuration
            if (span_v && span_m == 32'd0) begin
                d_on      <= span_dsp;
                d_pb      <= p_start >> 3;
                d_po      <= p_start[2:0];
                c_i       <= span_corr[93:47];
                c_q       <= span_corr[46:0];
                rx_bits   <= 9'd0;
                r_count   <= span_pps_count;
                r_slot    <= span_slot;
                r_tick_lo <= span_tick[31:0];
                r_tick_hi <= span_tick[63:32];
            end else if (span_v_d && !span_v) begin
                d_on <= 1'b0;   // the span ended
            end
            if (bit_v && r_state == 2'd0 && rx_bits < PBITS) begin
                rx_pkt  <= {rx_pkt[PBITS-2:0], bit_val};
                rx_bits <= rx_bits + 9'd1;
                if (rx_bits == PBITS - 1) begin
                    r_crc   <= 16'hFFFF;
                    r_n     <= 6'd0;
                    r_state <= 2'd1;
                end
            end
            case (r_state)
                2'd1: begin
                    r_crc <= crc_byte(r_crc, rx_pkt[PBITS-1 - 8*r_n -: 8]);
                    r_n   <= r_n + 6'd1;
                    if (r_n == NBYTES - 1) r_state <= 2'd2;
                end
                2'd2: begin
                    if (r_crc == rx_pkt[15:0]) begin
                        rx_count   <= rx_count + 32'd1;
                        peer_entry <= {32'd0, 32'd0,
                                       rx_pkt[63:32],                       // 13 phase
                                       rx_pkt[127:96], rx_pkt[95:64],       // 12, 11 T_rx HI, LO
                                       rx_pkt[191:160], rx_pkt[159:128],    // 10, 9 T_tx HI, LO
                                       {16'd0, rx_pkt[215:200]},            // 8 the peer's slot
                                       rx_pkt[247:216],                     // 7 the peer's PPS_COUNT
                                       rx_pkt[279:248],                     // 6 config_id
                                       {rx_pkt[287:280], rx_pkt[199:192], rx_pkt[31:24], rx_pkt[23:16]},
                                       r_tick_hi, r_tick_lo, {23'd0, r_slot}, r_count,
                                       {16'd0, 8'd0, QE_TYPE_PEER}};
                        peer_req   <= 1'b1;
                        r_state    <= 2'd3;
                    end else begin
                        crc_count <= crc_count + 32'd1;
                        r_state   <= 2'd0;
                    end
                end
                2'd3: if (peer_ack) begin
                    peer_req <= 1'b0;
                    r_state  <= 2'd0;
                end
                default: ;
            endcase
        end
    end
endmodule
