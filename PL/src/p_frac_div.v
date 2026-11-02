`timescale 1ns / 1ps
// Sub-sample position of the parabola through three magnitudes
// (rf_clock.plmodel.correlator.subsample), in 2^-32 sample:
//
//   D = l - 2c + r;  D >= 0: 0
//   q = floor(|l - r| 2^31 / -D) by 31 steps of restoring division
//   (|l - r| <= -D because c is the largest), +1 when 2 rem >= -D
//   (half away from zero), negated when l > r (the peak lies before c);
//   +2^31 (half a sample after c) is kept in i32 as 2^31 - 1.
//
// `start` while idle; `done` pulses with `frac` valid about 33 clocks later.
module p_frac_div #(
    parameter integer MW = 47
) (
    input  wire                  clk,
    input  wire                  rst,
    input  wire                  start,
    input  wire [MW-1:0]         l,
    input  wire [MW-1:0]         c,
    input  wire [MW-1:0]         r,
    output reg                   busy,
    output reg                   done,
    output reg  signed [31:0]    frac
);
    localparam [1:0] S_IDLE = 2'd0, S_DIV = 2'd1, S_ROUND = 2'd2;

    reg [1:0]    state;
    reg [MW+2:0] b;       // -D, positive
    reg [MW+3:0] rem;     // < 2 b
    reg [31:0]   q;
    reg          neg;     // l > r
    reg [5:0]    n;

    wire signed [MW+2:0] d_s = $signed({3'b000, l}) - $signed({2'b00, c, 1'b0}) + $signed({3'b000, r});
    wire signed [MW+1:0] n_s = $signed({2'b00, l}) - $signed({2'b00, r});
    wire [MW+1:0]        n_abs = n_s[MW+1] ? -n_s : n_s;
    wire [MW+3:0]        rem2 = {rem[MW+2:0], 1'b0};
    wire [MW+2:0]        b_w  = -d_s;   // -D, in its own width: no unsigned mixing
    wire [32:0]          q_round = {1'b0, q} + ((rem2 >= {1'b0, b}) ? 33'd1 : 33'd0);

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            state <= S_IDLE;
            busy  <= 1'b0;
            frac  <= 32'sd0;
            b     <= {(MW+3){1'b0}};
            rem   <= {(MW+4){1'b0}};
            q     <= 32'd0;
            neg   <= 1'b0;
            n     <= 6'd0;
        end else begin
            case (state)
                S_IDLE: if (start) begin
                    busy <= 1'b1;
                    if (!d_s[MW+2]) begin           // D >= 0: no downward curvature
                        frac  <= 32'sd0;
                        busy  <= 1'b0;
                        done  <= 1'b1;
                    end else begin
                        b   <= b_w;
                        neg <= (n_s > 0);
                        if ({1'b0, n_abs} == b_w) begin
                            q     <= 32'h8000_0000;
                            rem   <= {(MW+4){1'b0}};
                            state <= S_ROUND;
                        end else begin
                            q     <= 32'd0;
                            rem   <= {2'b00, n_abs};
                            n     <= 6'd31;
                            state <= S_DIV;
                        end
                    end
                end
                S_DIV: begin
                    if (rem2 >= {1'b0, b}) begin
                        rem <= rem2 - {1'b0, b};
                        q   <= {q[30:0], 1'b1};
                    end else begin
                        rem <= rem2;
                        q   <= {q[30:0], 1'b0};
                    end
                    n <= n - 6'd1;
                    if (n == 6'd1) state <= S_ROUND;
                end
                S_ROUND: begin
                    if (neg)
                        frac <= -q_round[31:0];          // -2^31 fits
                    else if (q_round[32] || q_round[31])   // +2^31
                        frac <= 32'sh7FFF_FFFF;
                    else
                        frac <= q_round[31:0];
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
