`timescale 1ns / 1ps
// floor(sqrt(value)) of an unsigned W-bit value (W even), one result bit per
// clock by restoring square root (rf_clock.plmodel.correlator.magnitude):
//
//   rem <- 4 rem + next two bits of value;  t <- 4 root + 1
//   rem >= t ? (rem <- rem - t, root <- 2 root + 1) : root <- 2 root
//
// `start` while idle begins; `done` pulses with `root` valid W/2 clocks later.
module p_isqrt #(
    parameter integer W = 94
) (
    input  wire           clk,
    input  wire           rst,
    input  wire           start,
    input  wire [W-1:0]   value,
    output reg            busy,
    output reg            done,
    output reg  [W/2-1:0] root
);
    localparam integer R = W / 2;

    reg [W-1:0]  v;     // bits not yet brought down, the next two at the top
    reg [R+2:0]  rem;   // < 2 root + 2 <= 2^(R+1)
    reg [7:0]    n;

    wire [R+2:0] rem4 = {rem[R:0], v[W-1:W-2]};
    wire [R+2:0] t    = {1'b0, root, 2'b01};

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            busy <= 1'b0;
            root <= {R{1'b0}};
            rem  <= {(R+3){1'b0}};
            v    <= {W{1'b0}};
            n    <= 8'd0;
        end else if (!busy) begin
            if (start) begin
                busy <= 1'b1;
                v    <= value;
                rem  <= {(R+3){1'b0}};
                root <= {R{1'b0}};
                n    <= R[7:0];
            end
        end else begin
            if (rem4 >= t) begin
                rem  <= rem4 - t;
                root <= {root[R-2:0], 1'b1};
            end else begin
                rem  <= rem4;
                root <= {root[R-2:0], 1'b0};
            end
            v <= v << 2;
            n <= n - 8'd1;
            if (n == 8'd1) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end
    end
endmodule
