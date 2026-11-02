`timescale 1ns / 1ps
// p8_tracker against rf_clock.plmodel.correlator (vectors from gen_vectors.py):
// the 9 correlations, the chosen lag, interior, magnitude, fraction, phase and
// power, for lag0 at the peak, off it and at both ends of the window.
module tb_p8_tracker;
    localparam integer AW = 11, BW = 13;
    localparam integer F = 25;      // lines per case
    localparam integer NCASE = 15;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;

    reg [31:0]  cfg [0:2];
    reg [31:0]  prn_words [0:9];
    reg [15:0]  tap_mem [0:80];
    reg [255:0] window [0:399];
    reg [93:0]  cases [0:NCASE*F-1];

    reg               start = 1'b0;
    reg  [15:0]       lag0 = 16'd0;
    reg  [3:0]        prn_we = 4'd0;
    reg  [AW-1:0]     waddr = {AW{1'b0}};
    reg  [31:0]       wdata = 32'd0;
    reg  [81*16-1:0]  taps;
    wire [BW-1:0]     rd_addr;
    reg  [255:0]      q1, q2;
    always @(posedge clk) begin
        q1 <= window[rd_addr];
        q2 <= q1;
    end

    wire               busy, done, interior;
    wire [15:0]        lag;
    wire [46:0]        magnitude;
    wire signed [31:0] fraction, phase;
    wire [93:0]        power;
    wire signed [46:0] c_i, c_q;

    p8_tracker #(.AW(AW), .BW(BW), .H(4), .RD_LAT(2)) dut (
        .clk (clk), .rst (rst), .start (start), .lag0 (lag0),
        .prn_chips (cfg[0][AW+5:0]), .window_beats (cfg[1][BW:0]),
        .rd_addr (rd_addr), .rd_data (q2),
        .prn_we (prn_we), .mem_waddr (waddr), .mem_wdata (wdata), .taps (taps),
        .busy (busy), .done (done), .lag (lag), .interior (interior),
        .magnitude (magnitude), .fraction (fraction), .phase (phase), .power (power),
        .c_i (c_i), .c_q (c_q)
    );

    integer errors = 0;
    integer k, n, b, d, cycles;
    initial begin
        $readmemh("trk_cfg.hex", cfg);
        $readmemh("trk_prn.hex", prn_words);
        $readmemh("trk_taps.hex", tap_mem);
        $readmemh("trk_window.hex", window);
        $readmemh("trk_cases.hex", cases);
        for (k = 0; k < 81; k = k + 1) taps[16*k +: 16] = tap_mem[k];
        if (cfg[2] != NCASE) begin
            $display("FAIL tb_p8_tracker: %0d cases, expected %0d", cfg[2], NCASE);
            $finish;
        end
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        for (k = 0; k < 10; k = k + 1) begin
            @(posedge clk);
            prn_we <= 4'hF; waddr <= k; wdata <= prn_words[k];
        end
        @(posedge clk); prn_we <= 4'h0;
        repeat (20) @(posedge clk);
        for (n = 0; n < NCASE; n = n + 1) begin
            b = n * F;
            lag0 <= cases[b][15:0];
            start <= 1'b1;
            @(posedge clk);
            start <= 1'b0;
            cycles = 0;
            while (!done) begin
                @(posedge clk);
                cycles = cycles + 1;
                if (cycles > 5000) begin
                    $display("FAIL tb_p8_tracker: case %0d timed out", n);
                    $finish;
                end
            end
            for (d = 0; d < 9; d = d + 1) begin
                if (dut.acc_i[d] !== cases[b+1+2*d][46:0] || dut.acc_q[d] !== cases[b+2+2*d][46:0]) begin
                    errors = errors + 1;
                    $display("case %0d (lag0 %0d) lag %0d: %h %h, expected %h %h", n, lag0, d,
                             dut.acc_i[d], dut.acc_q[d], cases[b+1+2*d][46:0], cases[b+2+2*d][46:0]);
                end
            end
            if (lag !== cases[b+19][15:0] || interior !== cases[b+20][0] || magnitude !== cases[b+21][46:0] ||
                fraction !== cases[b+22][31:0] || phase !== cases[b+23][31:0] || power !== cases[b+24]) begin
                errors = errors + 1;
                $display("case %0d (lag0 %0d): lag %0d/%0d int %b/%b mag %h/%h frac %h/%h phase %h/%h", n, lag0,
                         lag, cases[b+19][15:0], interior, cases[b+20][0], magnitude, cases[b+21][46:0],
                         fraction, cases[b+22][31:0], phase, cases[b+23][31:0]);
            end
            @(posedge clk);
        end
        if (errors == 0) $display("PASS tb_p8_tracker (%0d cases, %0d clocks each)", NCASE, cycles);
        else             $display("FAIL tb_p8_tracker: %0d errors", errors);
        $finish;
    end
endmodule
