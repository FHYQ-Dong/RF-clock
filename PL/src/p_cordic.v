`timescale 1ns / 1ps
// atan2(q, i) in 2^-32 turn by CORDIC vectoring (rf_clock.plmodel.correlator.phase):
//
//   normalize: shift both so the larger magnitude has 46 bits (left, or right
//   arithmetically if it has more); into the right half-plane by -/+90 degrees;
//   30 iterations k = 0 .. 29 on the values before the iteration:
//     y > 0: x += y >>> k, y -= x >>> k, z += A[k];  else the opposite
//   A[k] = round(atan(2^-k) / 2 pi * 2^32); z wraps in 32 bits.
//
// `start` while idle; `done` pulses with `phase` valid 32 clocks later.
module p_cordic #(
    parameter integer IW = 48   // signed input width
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  start,
    input  wire signed [IW-1:0]  i_in,
    input  wire signed [IW-1:0]  q_in,
    output reg                   busy,
    output reg                   done,
    output reg  signed [31:0]    phase
);
    localparam integer NORM = 46;
    localparam integer XW   = 50;  // |x|, |y| < 1.65 * sqrt(2) * 2^46 < 2^48

    function [31:0] atan_k(input [4:0] k);
        case (k)
            5'd0:  atan_k = 32'd536870912;
            5'd1:  atan_k = 32'd316933406;
            5'd2:  atan_k = 32'd167458907;
            5'd3:  atan_k = 32'd85004756;
            5'd4:  atan_k = 32'd42667331;
            5'd5:  atan_k = 32'd21354465;
            5'd6:  atan_k = 32'd10679838;
            5'd7:  atan_k = 32'd5340245;
            5'd8:  atan_k = 32'd2670163;
            5'd9:  atan_k = 32'd1335087;
            5'd10: atan_k = 32'd667544;
            5'd11: atan_k = 32'd333772;
            5'd12: atan_k = 32'd166886;
            5'd13: atan_k = 32'd83443;
            5'd14: atan_k = 32'd41722;
            5'd15: atan_k = 32'd20861;
            5'd16: atan_k = 32'd10430;
            5'd17: atan_k = 32'd5215;
            5'd18: atan_k = 32'd2608;
            5'd19: atan_k = 32'd1304;
            5'd20: atan_k = 32'd652;
            5'd21: atan_k = 32'd326;
            5'd22: atan_k = 32'd163;
            5'd23: atan_k = 32'd81;
            5'd24: atan_k = 32'd41;
            5'd25: atan_k = 32'd20;
            5'd26: atan_k = 32'd10;
            5'd27: atan_k = 32'd5;
            5'd28: atan_k = 32'd3;
            5'd29: atan_k = 32'd1;
            default: atan_k = 32'd0;
        endcase
    endfunction

    // Bit length of the larger magnitude (that of their OR).
    wire [IW-1:0] i_abs = i_in[IW-1] ? -i_in : i_in;
    wire [IW-1:0] q_abs = q_in[IW-1] ? -q_in : q_in;
    wire [IW-1:0] m_or  = i_abs | q_abs;
    reg  [7:0]    bitlen;
    integer       b;
    always @* begin
        bitlen = 8'd0;
        for (b = 0; b < IW; b = b + 1)
            if (m_or[b]) bitlen = b + 1;
    end

    localparam [1:0] S_IDLE = 2'd0, S_PRE = 2'd1, S_ITER = 2'd2;
    reg [1:0]               state;
    reg signed [XW-1:0]     x, y;
    reg [31:0]              z;
    reg [4:0]               k;

    wire signed [XW-1:0] i_ext = i_in;
    wire signed [XW-1:0] q_ext = q_in;
    wire signed [XW-1:0] xs = x >>> k;
    wire signed [XW-1:0] ys = y >>> k;

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            state <= S_IDLE;
            busy  <= 1'b0;
            phase <= 32'sd0;
            x <= {XW{1'b0}};
            y <= {XW{1'b0}};
            z <= 32'd0;
            k <= 5'd0;
        end else begin
            case (state)
                S_IDLE: if (start) begin
                    if (m_or == {IW{1'b0}}) begin
                        phase <= 32'sd0;
                        done  <= 1'b1;
                    end else begin
                        busy <= 1'b1;
                        if (bitlen <= NORM) begin
                            x <= i_ext <<< (NORM - bitlen);
                            y <= q_ext <<< (NORM - bitlen);
                        end else begin
                            x <= i_ext >>> (bitlen - NORM);
                            y <= q_ext >>> (bitlen - NORM);
                        end
                        state <= S_PRE;
                    end
                end
                S_PRE: begin
                    if (x < 0) begin
                        if (y >= 0) begin
                            x <= y;
                            y <= -x;
                            z <= 32'h4000_0000;   // +90 degrees
                        end else begin
                            x <= -y;
                            y <= x;
                            z <= 32'hC000_0000;   // -90 degrees
                        end
                    end else begin
                        z <= 32'd0;
                    end
                    k     <= 5'd0;
                    state <= S_ITER;
                end
                S_ITER: begin
                    if (y > 0) begin
                        x <= x + ys;
                        y <= y - xs;
                        z <= z + atan_k(k);
                    end else begin
                        x <= x - ys;
                        y <= y + xs;
                        z <= z - atan_k(k);
                    end
                    k <= k + 5'd1;
                    if (k == 5'd29) begin
                        phase <= (y > 0) ? $signed(z + atan_k(k)) : $signed(z - atan_k(k));
                        busy  <= 1'b0;
                        done  <= 1'b1;
                        state <= S_IDLE;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
