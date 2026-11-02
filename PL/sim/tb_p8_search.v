`timescale 1ns / 1ps
// p8_search with 16 lanes against rf_clock.plmodel.correlator.search (vectors
// from gen_vectors.py): multipath at two thresholds, peaks at the first and
// the last lag, noise only.  Every report field to the bit, and the clocks.
module tb_p8_search;
    localparam integer N = 16, AW = 11, BW = 13, KW = 13, MAXW = 400;
    localparam integer F = 10;
    localparam integer NCASE = 5;
    localparam integer NWIN = 4;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;

    reg [31:0]  cfg [0:3];
    reg [31:0]  prn_words [0:9];
    reg [15:0]  tap_mem [0:80];
    reg [31:0]  plan [0:NCASE-1];
    reg [255:0] windows [0:NWIN*MAXW-1];
    reg [46:0]  cases [0:NCASE*F-1];

    reg               start = 1'b0;
    reg  [15:0]       threshold = 16'd0;
    reg  [3:0]        prn_we = 4'd0;
    reg  [AW-1:0]     waddr = {AW{1'b0}};
    reg  [31:0]       wdata = 32'd0;
    reg  [81*16-1:0]  taps;
    reg  [31:0]       wsel = 32'd0;
    wire [BW-1:0]     rd_addr;
    reg  [255:0]      q1, q2;
    always @(posedge clk) begin
        q1 <= windows[wsel * MAXW + rd_addr];
        q2 <= q1;
    end

    wire               busy, done, bad;
    wire [KW-1:0]      lags, s_lag, e_lag;
    wire signed [31:0] s_fraction, s_phase, e_fraction, e_phase;
    wire [46:0]        s_magnitude, e_magnitude, sidelobe;

    p8_search #(.N(N), .AW(AW), .BW(BW), .KW(KW), .MAXW(MAXW), .RD_LAT(2)) dut (
        .clk (clk), .rst (rst), .start (start), .prn_chips (cfg[0][AW+5:0]),
        .window_beats (cfg[1][BW:0]), .threshold (threshold),
        .rd_addr (rd_addr), .rd_data (q2),
        .prn_we (prn_we), .mem_waddr (waddr), .mem_wdata (wdata), .taps (taps),
        .busy (busy), .done (done), .bad (bad), .lags (lags),
        .s_lag (s_lag), .s_fraction (s_fraction), .s_phase (s_phase), .s_magnitude (s_magnitude),
        .e_lag (e_lag), .e_fraction (e_fraction), .e_phase (e_phase), .e_magnitude (e_magnitude),
        .s_corr (), .e_corr (), .sidelobe (sidelobe)
    );

    integer errors = 0;
    integer k, n, b, cycles;
    initial begin
        $readmemh("src_cfg.hex", cfg);
        $readmemh("src_prn.hex", prn_words);
        $readmemh("src_taps.hex", tap_mem);
        $readmemh("src_plan.hex", plan);
        $readmemh("src_windows.hex", windows);
        $readmemh("src_cases.hex", cases);
        for (k = 0; k < 81; k = k + 1) taps[16*k +: 16] = tap_mem[k];
        if (cfg[2] != NCASE || cfg[1] != MAXW) begin
            $display("FAIL tb_p8_search: %0d cases of %0d beats, expected %0d of %0d", cfg[2], cfg[1], NCASE, MAXW);
            $finish;
        end
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        for (k = 0; k < 10; k = k + 1) begin
            @(posedge clk);
            prn_we <= 4'hF; waddr <= k; wdata <= prn_words[k];
        end
        @(posedge clk); prn_we <= 4'h0;
        repeat (N + 40) @(posedge clk);
        for (n = 0; n < NCASE; n = n + 1) begin
            b = n * F;
            wsel <= plan[n];
            threshold <= cases[b][15:0];
            start <= 1'b1;
            @(posedge clk);
            start <= 1'b0;
            cycles = 0;
            while (!done) begin
                @(posedge clk);
                cycles = cycles + 1;
                if (cycles > 400000) begin
                    $display("FAIL tb_p8_search: case %0d timed out in state %0d", n, dut.state);
                    $finish;
                end
            end
            if (bad || lags != cfg[3]) begin
                errors = errors + 1;
                $display("case %0d: bad %b lags %0d/%0d", n, bad, lags, cfg[3]);
            end
            if (s_lag !== cases[b+1][KW-1:0] || s_fraction !== cases[b+2][31:0] ||
                s_phase !== cases[b+3][31:0] || s_magnitude !== cases[b+4]) begin
                errors = errors + 1;
                $display("case %0d strongest: lag %0d/%0d frac %h/%h phase %h/%h mag %0d/%0d", n,
                         s_lag, cases[b+1][KW-1:0], s_fraction, cases[b+2][31:0], s_phase, cases[b+3][31:0],
                         s_magnitude, cases[b+4]);
            end
            if (e_lag !== cases[b+5][KW-1:0] || e_fraction !== cases[b+6][31:0] ||
                e_phase !== cases[b+7][31:0] || e_magnitude !== cases[b+8]) begin
                errors = errors + 1;
                $display("case %0d earliest: lag %0d/%0d frac %h/%h phase %h/%h mag %0d/%0d", n,
                         e_lag, cases[b+5][KW-1:0], e_fraction, cases[b+6][31:0], e_phase, cases[b+7][31:0],
                         e_magnitude, cases[b+8]);
            end
            if (sidelobe !== cases[b+9]) begin
                errors = errors + 1;
                $display("case %0d sidelobe %0d/%0d", n, sidelobe, cases[b+9]);
            end
            $display("case %0d: strongest %0d earliest %0d, %0d clocks", n, s_lag, e_lag, cycles);
            @(posedge clk);
        end
        if (errors == 0) $display("PASS tb_p8_search (%0d cases)", NCASE);
        else             $display("FAIL tb_p8_search: %0d errors", errors);
        $finish;
    end
endmodule
