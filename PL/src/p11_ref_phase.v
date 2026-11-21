`timescale 1ns / 1ps
// P11: the phase of the node's 10 MHz reference against its tick (since 2.4).
//
// The FS725's 10 MHz reaches the converters through the LMK04828, the LMX2594 and
// each RFDC tile's PLL, which lock again at every PL.open and do not come back to
// the same phase: against the PL's 100 MHz clock the 10 MHz moved by a few ps at
// each start and drifted by a few ps an hour (results/m5-10mhz-restarts-20261108_*),
// where the output PPS, made from the tick, did not.  A copy of the reference on
// ADC_D (ADC tile 0 block 0, which runs for MTS anyway; software sets its NCO to
// 0 Hz with phase 0, so the I stream carries the real samples) is correlated here
// with a 10 MHz sine and cosine on the tick grid: at 800 MS/s one period is 80
// samples, 10 ticks, so the sample of lane n in the beat of tick t has the angle
// 2 pi (8 (t mod 10) + n) / 80, exactly, with t = raw_tick (both start at 0 at
// reset and count every clock).
//
//   I = sum x[s] cos(2 pi k[s] / 80) x 32767,   Q = sum x[s] sin(2 pi k[s] / 80) x 32767
//
// over the beats between two PPS events.  For x = A cos(2 pi f (t - tau)) this gives
// tau = atan2(Q, I) / (2 pi f) modulo 100 ns: the reference's delay against the
// tick grid, plus the ADC path's fixed latency (MTS fixes it at every start).  At
// each PPS event the sums of the second just ended are kept (snap_*), with the
// beats summed, the largest |sample| (to see clipping) and that second's PPS_COUNT,
// and snap_seq counts the snapshots; the ARM reads them within the next second.
// Products: 16 x 16 bits; a second's sums stay within 63 bits (2^30 x 8 x 1e8).
module p11_ref_phase (
    input  wire               clk,
    input  wire               rst,
    // ADC tile 0 block 0, I: 8 samples a beat, lane n (n = 0 first) at bits 16 n.
    input  wire [127:0]       s_axis_tdata,
    input  wire               s_axis_tvalid,
    input  wire               pps_event,
    input  wire [31:0]        pps_count,
    output reg  [31:0]        snap_seq,
    output reg  [31:0]        snap_count,
    output reg  [31:0]        snap_beats,
    output reg  [15:0]        snap_peak,
    output reg  signed [63:0] snap_i,
    output reg  signed [63:0] snap_q
);
    function signed [15:0] cos80(input [6:0] k);
        case (k)
            7'd0: cos80 = 32767;
            7'd1: cos80 = 32666;
            7'd2: cos80 = 32364;
            7'd3: cos80 = 31862;
            7'd4: cos80 = 31163;
            7'd5: cos80 = 30273;
            7'd6: cos80 = 29196;
            7'd7: cos80 = 27938;
            7'd8: cos80 = 26509;
            7'd9: cos80 = 24916;
            7'd10: cos80 = 23170;
            7'd11: cos80 = 21280;
            7'd12: cos80 = 19260;
            7'd13: cos80 = 17121;
            7'd14: cos80 = 14876;
            7'd15: cos80 = 12539;
            7'd16: cos80 = 10126;
            7'd17: cos80 = 7649;
            7'd18: cos80 = 5126;
            7'd19: cos80 = 2571;
            7'd20: cos80 = 0;
            7'd21: cos80 = -2571;
            7'd22: cos80 = -5126;
            7'd23: cos80 = -7649;
            7'd24: cos80 = -10126;
            7'd25: cos80 = -12539;
            7'd26: cos80 = -14876;
            7'd27: cos80 = -17121;
            7'd28: cos80 = -19260;
            7'd29: cos80 = -21280;
            7'd30: cos80 = -23170;
            7'd31: cos80 = -24916;
            7'd32: cos80 = -26509;
            7'd33: cos80 = -27938;
            7'd34: cos80 = -29196;
            7'd35: cos80 = -30273;
            7'd36: cos80 = -31163;
            7'd37: cos80 = -31862;
            7'd38: cos80 = -32364;
            7'd39: cos80 = -32666;
            7'd40: cos80 = -32767;
            7'd41: cos80 = -32666;
            7'd42: cos80 = -32364;
            7'd43: cos80 = -31862;
            7'd44: cos80 = -31163;
            7'd45: cos80 = -30273;
            7'd46: cos80 = -29196;
            7'd47: cos80 = -27938;
            7'd48: cos80 = -26509;
            7'd49: cos80 = -24916;
            7'd50: cos80 = -23170;
            7'd51: cos80 = -21280;
            7'd52: cos80 = -19260;
            7'd53: cos80 = -17121;
            7'd54: cos80 = -14876;
            7'd55: cos80 = -12539;
            7'd56: cos80 = -10126;
            7'd57: cos80 = -7649;
            7'd58: cos80 = -5126;
            7'd59: cos80 = -2571;
            7'd60: cos80 = 0;
            7'd61: cos80 = 2571;
            7'd62: cos80 = 5126;
            7'd63: cos80 = 7649;
            7'd64: cos80 = 10126;
            7'd65: cos80 = 12539;
            7'd66: cos80 = 14876;
            7'd67: cos80 = 17121;
            7'd68: cos80 = 19260;
            7'd69: cos80 = 21280;
            7'd70: cos80 = 23170;
            7'd71: cos80 = 24916;
            7'd72: cos80 = 26509;
            7'd73: cos80 = 27938;
            7'd74: cos80 = 29196;
            7'd75: cos80 = 30273;
            7'd76: cos80 = 31163;
            7'd77: cos80 = 31862;
            7'd78: cos80 = 32364;
            7'd79: cos80 = 32666;
            default: cos80 = 16'sd0;
        endcase
    endfunction

    // the angle index of lane n in a beat at tick phase p: 8 p + n; sin(k) = cos(k - 20)
    function [6:0] index(input [3:0] p, input integer n, input integer shift);
        integer v;
        begin
            v = (8 * p + n + shift) % 80;
            index = v[6:0];
        end
    endfunction

    // the tick's phase in the reference's period, in step with raw_tick
    reg [3:0] tick10;
    always @(posedge clk)
        if (rst) tick10 <= 4'd0;
        else     tick10 <= (tick10 == 4'd9) ? 4'd0 : tick10 + 4'd1;

    // stage 1: the beat, its tick phase, the event and the valid flag, registered
    reg signed [15:0] x1 [0:7];
    reg        [3:0]  p1;
    reg               v1, e1;
    integer i;
    always @(posedge clk) begin
        for (i = 0; i < 8; i = i + 1) x1[i] <= s_axis_tdata[16 * i +: 16];
        p1 <= tick10;
        v1 <= !rst && s_axis_tvalid;
        e1 <= !rst && pps_event;
    end

    // stage 2: the products and each sample's magnitude
    reg signed [31:0] pc2 [0:7];
    reg signed [31:0] ps2 [0:7];
    reg        [15:0] m2  [0:7];
    reg               v2, e2;
    always @(posedge clk) begin
        for (i = 0; i < 8; i = i + 1) begin
            pc2[i] <= x1[i] * cos80(index(p1, i, 0));
            ps2[i] <= x1[i] * cos80(index(p1, i, 60));
            m2[i]  <= x1[i][15] ? -x1[i] : x1[i];  // |-32768| = 32768 still fits 16 bits unsigned
        end
        v2 <= v1;
        e2 <= e1;
    end

    // stage 3: the beat's sums and its largest magnitude
    reg signed [34:0] sc3, ss3;
    reg        [15:0] m3;
    reg               v3, e3;
    reg signed [34:0] ac, as;
    reg        [15:0] am;
    always @(*) begin
        ac = 35'sd0;
        as = 35'sd0;
        am = 16'd0;
        for (i = 0; i < 8; i = i + 1) begin
            ac = ac + pc2[i];
            as = as + ps2[i];
            if (m2[i] > am) am = m2[i];
        end
    end
    always @(posedge clk) begin
        sc3 <= v2 ? ac : 35'sd0;
        ss3 <= v2 ? as : 35'sd0;
        m3  <= v2 ? am : 16'd0;
        v3  <= v2;
        e3  <= e2;
    end

    // stage 4: the second's sums; at an event the beat that came with it starts the next
    reg signed [63:0] acc_i, acc_q;
    reg        [31:0] beats, count;
    reg        [15:0] peak;
    always @(posedge clk) begin
        if (rst) begin
            acc_i <= 64'sd0; acc_q <= 64'sd0; beats <= 32'd0; peak <= 16'd0; count <= 32'd0;
            snap_seq <= 32'd0; snap_count <= 32'd0; snap_beats <= 32'd0; snap_peak <= 16'd0;
            snap_i <= 64'sd0; snap_q <= 64'sd0;
        end else if (e3) begin
            snap_i     <= acc_i;
            snap_q     <= acc_q;
            snap_beats <= beats;
            snap_peak  <= peak;
            snap_count <= count;
            snap_seq   <= snap_seq + 32'd1;
            acc_i <= {{29{sc3[34]}}, sc3};
            acc_q <= {{29{ss3[34]}}, ss3};
            beats <= v3 ? 32'd1 : 32'd0;
            peak  <= m3;
            count <= pps_count;  // the second the event starts (P1 raised its count with the event)
        end else if (v3) begin
            acc_i <= acc_i + sc3;
            acc_q <= acc_q + ss3;
            beats <= beats + 32'd1;
            if (m3 > peak) peak <= m3;
        end
    end
endmodule
