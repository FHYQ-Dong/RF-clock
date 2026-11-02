`timescale 1ns / 1ps
// The estimate of one correlation peak (rf_clock.plmodel.correlator.peak_at):
//
//   magnitude  floor(sqrt(I^2 + Q^2)) at the peak (c)
//   fraction   the parabola through the magnitudes of l, c, r in 2^-32 sample
//              (p_frac_div); 0 with `edge` (the peak at the end of its range)
//   phase      atan2(Q, I) at the peak in 2^-32 turn (p_cordic)
//   power      I^2 + Q^2 at the peak
//
// `start` while idle samples the six inputs; `done` pulses with all outputs
// valid about 90 clocks later.  The three powers go through one pipelined
// p_power on consecutive clocks; the three square roots and the CORDIC run in
// parallel, then the division.
module p_estimator #(
    parameter integer W = 47
) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 start,
    input  wire                 edge_in,
    input  wire signed [W-1:0]  l_i, l_q,
    input  wire signed [W-1:0]  c_i, c_q,
    input  wire signed [W-1:0]  r_i, r_q,
    output reg                  busy,
    output reg                  done,
    output reg  [W-1:0]         magnitude,
    output reg  signed [31:0]   fraction,
    output reg  signed [31:0]   phase,
    output reg  [2*W-1:0]       power
);
    localparam integer PW_LAT = 5;  // p_power latency with MUL_STAGES = 3

    // ---- inputs held for the run ----
    reg signed [W-1:0] hl_i, hl_q, hc_i, hc_q, hr_i, hr_q;
    reg                h_edge;

    // ---- powers: l, c, r on three consecutive clocks ----
    reg  [1:0]          feed;      // 0: l, 1: c, 2: r
    reg                 feeding;
    reg  signed [W-1:0] pw_i, pw_q;
    wire [2*W-1:0]      pw_out;
    reg  [PW_LAT+2:0]   pw_tag_v;  // which clock carries which power
    reg  [2*W-1:0]      p_l, p_c, p_r;

    p_power #(.W(W), .MUL_STAGES(3)) u_pw (
        .clk (clk), .in_i (pw_i), .in_q (pw_q), .power (pw_out)
    );

    // ---- square roots ----
    reg                 sq_start;
    wire [2:0]          sq_done;
    wire [W-1:0]        m_l, m_c, m_r;

    p_isqrt #(.W(2*W)) u_sl (.clk(clk), .rst(rst), .start(sq_start), .value(p_l),
                             .busy(), .done(sq_done[0]), .root(m_l));
    p_isqrt #(.W(2*W)) u_sc (.clk(clk), .rst(rst), .start(sq_start), .value(p_c),
                             .busy(), .done(sq_done[1]), .root(m_c));
    p_isqrt #(.W(2*W)) u_sr (.clk(clk), .rst(rst), .start(sq_start), .value(p_r),
                             .busy(), .done(sq_done[2]), .root(m_r));

    // ---- division ----
    reg                 dv_start;
    wire                dv_done;
    wire signed [31:0]  dv_frac;

    p_frac_div #(.MW(W)) u_dv (.clk(clk), .rst(rst), .start(dv_start),
                               .l(m_l), .c(m_c), .r(m_r),
                               .busy(), .done(dv_done), .frac(dv_frac));

    // ---- CORDIC ----
    reg                 cd_start;
    wire                cd_done;
    wire signed [31:0]  cd_phase;
    reg                 cd_have;

    p_cordic #(.IW(W)) u_cd (.clk(clk), .rst(rst), .start(cd_start),
                             .i_in(hc_i), .q_in(hc_q),
                             .busy(), .done(cd_done), .phase(cd_phase));

    localparam [2:0] S_IDLE = 3'd0, S_POWER = 3'd1, S_SQRT = 3'd2, S_DIV = 3'd3, S_END = 3'd4;
    reg [2:0] state;
    reg [3:0] wait_n;

    always @(posedge clk) begin
        done     <= 1'b0;
        sq_start <= 1'b0;
        dv_start <= 1'b0;
        cd_start <= 1'b0;
        pw_i     <= {W{1'b0}};
        pw_q     <= {W{1'b0}};
        if (cd_done) begin
            phase   <= cd_phase;
            cd_have <= 1'b1;
        end
        if (rst) begin
            state     <= S_IDLE;
            busy      <= 1'b0;
            feeding   <= 1'b0;
            feed      <= 2'd0;
            wait_n    <= 4'd0;
            cd_have   <= 1'b0;
            magnitude <= {W{1'b0}};
            fraction  <= 32'sd0;
            phase     <= 32'sd0;
            power     <= {(2*W){1'b0}};
        end else begin
            case (state)
                S_IDLE: if (start) begin
                    busy    <= 1'b1;
                    hl_i <= l_i; hl_q <= l_q;
                    hc_i <= c_i; hc_q <= c_q;
                    hr_i <= r_i; hr_q <= r_q;
                    h_edge  <= edge_in;
                    feed    <= 2'd0;
                    feeding <= 1'b1;
                    cd_have <= 1'b0;
                    wait_n  <= 4'd0;
                    state   <= S_POWER;
                end
                S_POWER: begin
                    // feed l, c, r on three clocks, then wait for the last power
                    if (feeding) begin
                        case (feed)
                            2'd0: begin pw_i <= hl_i; pw_q <= hl_q; end
                            2'd1: begin pw_i <= hc_i; pw_q <= hc_q; end
                            default: begin pw_i <= hr_i; pw_q <= hr_q; end
                        endcase
                        if (feed == 2'd1) cd_start <= 1'b1;
                        if (feed == 2'd2) feeding <= 1'b0;
                        feed <= feed + 2'd1;
                    end
                    wait_n <= wait_n + 4'd1;
                    // pw_i of clock n (wait_n = n) reaches pw_out PW_LAT + 1 clocks later
                    if (wait_n == PW_LAT + 1) p_l <= pw_out;
                    if (wait_n == PW_LAT + 2) p_c <= pw_out;
                    if (wait_n == PW_LAT + 3) begin
                        p_r      <= pw_out;
                        sq_start <= 1'b1;
                        state    <= S_SQRT;
                    end
                end
                S_SQRT: if (sq_done[1]) begin
                    magnitude <= m_c;
                    power     <= p_c;
                    if (h_edge) begin
                        fraction <= 32'sd0;
                        state    <= S_END;
                    end else begin
                        dv_start <= 1'b1;
                        state    <= S_DIV;
                    end
                end
                S_DIV: if (dv_done) begin
                    fraction <= dv_frac;
                    state    <= S_END;
                end
                S_END: if (cd_have || cd_done) begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
