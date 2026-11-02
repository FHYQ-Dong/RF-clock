`timescale 1ns / 1ps
// P3 (2.0): a burst made from chips through the RRC FIR (docs/ABI.md 4.9).
//
//   chip n = 0 .. beats-1:  n < prn_chips              PRN chip n
//                           n < pstart = prn + guard   0 (guard)
//                           n < pstart + 288 L          packet chip, with `packets`:
//                                                       code[m] for a 0 bit, -code[m] for a 1,
//                                                       m = n - pstart, bit m / L, MSB first
//                           otherwise                   0 (the 10 chips that flush the FIR)
//
// One chip per clock, one beat per chip: beat 0 is on m_axis_tdata in the
// cycle raw_tick == target and the beats follow without a gap.  The slot
// engine arms the generator at least LEAD_MIN ticks before the target.  A low
// tready during the burst enters FAULT, which only clear_fault leaves; the
// output is zero outside bursts and in FAULT.
//
// DAC lane layout (TX_LAYOUT): sample p of a beat is I in bits [32p +: 16],
// Q (zero) in bits [32p + 16 +: 16].
module p3_burst_gen #(
    parameter integer AW     = 11,   // code memory word address bits
    parameter integer SPREAD = 64,   // L: chips per packet bit
    parameter integer PBITS  = 288
) (
    input  wire               clk,
    input  wire               rst,
    input  wire [63:0]        raw_tick,
    input  wire               clear_fault,
    // From the slot engine: configuration (taken at arm) and the arm pulse.
    input  wire               arm,
    input  wire [63:0]        target,
    input  wire [AW+5:0]      prn_chips,
    input  wire [15:0]        guard_chips,
    input  wire               packets,
    input  wire [31:0]        beats,      // the whole burst, flush chips included
    // The packet, MSB first in bit PBITS-1; taken when its first chip is read.
    input  wire [PBITS-1:0]   packet,
    // Code memory and tap writes (burst memory port).
    input  wire [3:0]         prn_we,
    input  wire [3:0]         code_we,
    input  wire [AW-1:0]      mem_waddr,
    input  wire [31:0]        mem_wdata,
    input  wire [81*16-1:0]   taps,
    // RFDC DAC AXI4-Stream.
    output wire [255:0]       m_axis_tdata,
    output reg                m_axis_tvalid,
    input  wire               m_axis_tready,
    // Status.
    output reg                busy,       // armed or sending
    output reg                fault,
    output reg                started,    // pulse: beat 0 is on the bus
    output reg  [63:0]        actual_tick
);
    localparam integer PIPE  = 8;  // n_q = 0 to beat 0 on the bus
    localparam integer PCHIP = PBITS * SPREAD;
    localparam integer LB    = $clog2(SPREAD);

    // ---- chip counter ----
    reg              armed, active;
    reg [63:0]       start_tick;   // raw_tick in the cycle before n_q = 0
    reg [31:0]       n_q, left;
    reg [AW+5:0]     c_prn;
    reg [31:0]       c_pstart, c_pend;
    reg              c_packets;
    reg [PBITS-1:0]  pkt;

    wire in_prn = active && (n_q < {{(31-AW-5){1'b0}}, c_prn});
    wire in_pkt = active && c_packets && (n_q >= c_pstart) && (n_q < c_pend);
    wire [31:0] m = n_q - c_pstart;
    wire [31:0] bit_index = m >> LB;
    wire        data_bit = in_pkt ? pkt[PBITS - 1 - bit_index] : 1'b0;

    // ---- code memories ----
    wire prn_chip, code_chip;
    p_chip_mem #(.AW(AW)) u_prn (
        .clk (clk), .we (prn_we), .waddr (mem_waddr), .wdata (mem_wdata),
        .rd_addr (n_q[AW+4:0]), .rd_chip (prn_chip), .rd_word ()
    );
    p_chip_mem #(.AW(AW)) u_code (
        .clk (clk), .we (code_we), .waddr (mem_waddr), .wdata (mem_wdata),
        .rd_addr (m[AW+4:0]), .rd_chip (code_chip), .rd_word ()
    );

    reg [1:0] prn_d, pkt_d, bit_d;
    always @(posedge clk) begin
        prn_d <= {prn_d[0], in_prn};
        pkt_d <= {pkt_d[0], in_pkt};
        bit_d <= {bit_d[0], data_bit};
    end
    wire chip_nz  = prn_d[1] | pkt_d[1];
    wire chip_neg = prn_d[1] ? ~prn_chip : ~(code_chip ^ bit_d[1]);

    wire [127:0] fir_beat;
    p_chip_fir u_fir (
        .clk (clk), .rst (rst), .en (1'b1), .chip_nz (chip_nz && !fault), .chip_neg (chip_neg),
        .taps (taps), .beat (fir_beat)
    );

    genvar g;
    generate
        for (g = 0; g < 8; g = g + 1) begin : g_lane
            assign m_axis_tdata[32*g +: 16]      = fault ? 16'd0 : fir_beat[16*g +: 16];
            assign m_axis_tdata[32*g + 16 +: 16] = 16'd0;
        end
    endgenerate

    // ---- which bus cycles carry the burst ----
    reg [PIPE-1:0] air_v, air_f;  // [PIPE-1]: the beat now on the bus
    always @(posedge clk) begin
        air_v <= {air_v[PIPE-2:0], active};
        air_f <= {air_f[PIPE-2:0], active && n_q == 32'd0};
    end
    wire on_air = air_v[PIPE-1] && !fault;

    always @(posedge clk) begin
        started <= 1'b0;
        if (rst) begin
            armed         <= 1'b0;
            active        <= 1'b0;
            busy          <= 1'b0;
            fault         <= 1'b0;
            n_q           <= 32'd0;
            left          <= 32'd0;
            start_tick    <= 64'd0;
            actual_tick   <= 64'd0;
            m_axis_tvalid <= 1'b0;
            c_prn         <= {(AW+6){1'b0}};
            c_pstart      <= 32'd0;
            c_pend        <= 32'd0;
            c_packets     <= 1'b0;
            pkt           <= {PBITS{1'b0}};
        end else begin
            m_axis_tvalid <= 1'b1;
            if (arm && !busy && !fault) begin
                armed      <= 1'b1;
                busy       <= 1'b1;
                start_tick <= target - (PIPE + 1);
                c_prn      <= prn_chips;
                c_pstart   <= {{(26-AW){1'b0}}, prn_chips} + {16'd0, guard_chips};
                c_pend     <= {{(26-AW){1'b0}}, prn_chips} + {16'd0, guard_chips} + PCHIP;
                c_packets  <= packets;
                left       <= beats;
            end
            if (armed && raw_tick == start_tick) begin
                armed  <= 1'b0;
                active <= 1'b1;
                n_q    <= 32'd0;
            end
            if (active) begin
                n_q  <= n_q + 32'd1;
                left <= left - 32'd1;
                if (left == 32'd1) active <= 1'b0;
                if (n_q + 32'd1 == c_pstart) pkt <= packet;
            end
            if (on_air && air_f[PIPE-1]) begin
                actual_tick <= raw_tick;
                started     <= 1'b1;
            end
            if (busy && !armed && !active && air_v == {PIPE{1'b0}})
                busy <= 1'b0;
            if (on_air && !m_axis_tready) begin
                fault  <= 1'b1;
                armed  <= 1'b0;
                active <= 1'b0;
            end
            if (fault && clear_fault) begin
                fault <= 1'b0;
                busy  <= 1'b0;
            end
        end
    end
endmodule
