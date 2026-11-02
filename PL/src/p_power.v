`timescale 1ns / 1ps
// I^2 + Q^2 of two signed W-bit values, exact in 2W bits, one per clock with
// LATENCY = MUL_STAGES + 2 clocks.  The registers after the products let the
// synthesis tool pipeline the wide multipliers inside the DSPs.
module p_power #(
    parameter integer W          = 47,
    parameter integer MUL_STAGES = 3
) (
    input  wire                 clk,
    input  wire signed [W-1:0]  in_i,
    input  wire signed [W-1:0]  in_q,
    output wire [2*W-1:0]       power
);
    localparam integer LATENCY = MUL_STAGES + 2;

    reg [W-1:0]   a, b;          // |in_i|, |in_q|: 2^(W-1) fits unsigned
    reg [2*W-1:0] sa [0:MUL_STAGES-1];
    reg [2*W-1:0] sb [0:MUL_STAGES-1];
    reg [2*W-1:0] sum;
    integer s;

    always @(posedge clk) begin
        a <= in_i[W-1] ? -in_i : in_i;
        b <= in_q[W-1] ? -in_q : in_q;
        sa[0] <= a * a;
        sb[0] <= b * b;
        for (s = 1; s < MUL_STAGES; s = s + 1) begin
            sa[s] <= sa[s-1];
            sb[s] <= sb[s-1];
        end
        sum <= sa[MUL_STAGES-1] + sb[MUL_STAGES-1];
    end
    assign power = sum;
endmodule
