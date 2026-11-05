`timescale 1ns / 1ps
// p8_correlator's re-lock (docs/ABI.md 4.10) with 16 search lanes: a window with
// the burst, searched and accepted by the "ARM"; a window where only a peak 40 dB
// weaker is left at the same lag: the tracker loses the lock and the search finds
// that peak within one lag of the lock, but too weak, so the PL must wait for the
// ARM instead of locking on it (the noise re-lock of 2026-11-03); then a window
// with the burst again: the PL locks by itself.
module tb_p8_relock;
    localparam integer N = 16, AW = 11, BW = 13, KW = 13, WB = 400;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;

    reg [31:0]  cfg [0:4];
    reg [31:0]  prn_words [0:9];
    reg [15:0]  tap_mem [0:80];
    reg [255:0] windows [0:3*WB-1];

    reg  [63:0]       tick = 64'd0;
    always @(posedge clk) tick <= tick + 64'd1;
    reg  [63:0]       win_start = 64'hFFFF_FFFF_FFFF_0000;
    reg  [31:0]       win_index = 32'd0;
    wire [63:0]       rel = tick - win_start;
    wire [255:0]      adc_d = (tick >= win_start && rel < WB) ? windows[win_index * WB + rel[31:0]] : 256'd0;

    reg               rx_arm = 1'b0;
    reg  [63:0]       ev_tick = 64'd0;
    reg               cmd_accept = 1'b0;
    reg  [3:0]        prn_we = 4'd0;
    reg  [AW-1:0]     waddr = {AW{1'b0}};
    reg  [31:0]       wdata = 32'd0;
    reg  [81*16-1:0]  taps;
    wire              own_req;

    wire        locked, search_busy, waiting, rx_busy;
    wire [15:0] lock_lag, sr_s_lag;
    wire [46:0] lock_mag, sr_s_magnitude;
    wire [31:0] lost_count, relock_count, search_count, sr_seq;

    p8_correlator #(.N(N), .AW(AW), .BW(BW), .KW(KW), .WB(WB)) dut (
        .clk (clk), .rst (rst), .time_valid (1'b1), .running (1'b1),
        .adc_d (adc_d), .adc_v (1'b1), .adc_or (1'b0), .tick_d (tick),
        .rx_arm (rx_arm), .ev_tick (ev_tick), .ev_slot (9'd1), .sec_count (32'd7), .sec_tick (64'd0),
        .span_beats (WB), .prn_chips (cfg[0][AW+5:0]), .packets (1'b0),
        .threshold (16'h8000), .lock_min (16'h8000), .quality_offset (32'sd0),
        .cmd_search (1'b0), .cmd_accept_strongest (cmd_accept), .cmd_accept_earliest (1'b0), .cmd_unlock (1'b0),
        .prn_we (prn_we), .mem_waddr (waddr), .mem_wdata (wdata), .taps (taps),
        .rx_busy (rx_busy), .locked (locked), .search_busy (search_busy), .waiting (waiting),
        .lock_lag (lock_lag), .lock_mag (lock_mag),
        .lost_count (lost_count), .relock_count (relock_count), .search_count (search_count),
        .refused_count (), .search_lags (),
        .sr_seq (sr_seq), .sr_pps_count (), .sr_slot (), .sr_flags (), .sr_tick (),
        .sr_s_lag (sr_s_lag), .sr_s_fraction (), .sr_s_phase (), .sr_s_magnitude (sr_s_magnitude),
        .sr_e_lag (), .sr_e_fraction (), .sr_e_phase (), .sr_e_magnitude (), .sr_sidelobe (),
        .span_v (), .span_m (), .span_d (), .span_dsp (), .span_lag (), .span_corr (),
        .span_pps_count (), .span_slot (), .span_tick (),
        .meas_valid (), .meas_trx (), .meas_phase (), .meas_quality (), .meas_flags (),
        .own_req (own_req), .own_entry (), .own_ack (own_req)
    );

    integer errors = 0;
    integer k;
    task check(input cond, input [8*72-1:0] msg);
        if (!cond) begin
            errors = errors + 1;
            $display("FAIL: %0s (t=%0t)", msg, $time);
        end
    endtask

    // one window: arm it 200 ticks ahead, play it, wait for its search to end
    task window(input integer n, input [31:0] searches_after);
        integer w;
        begin
            @(posedge clk);
            ev_tick <= tick + 64'd200; win_start <= tick + 64'd200; win_index <= n; rx_arm <= 1'b1;
            @(posedge clk);
            rx_arm <= 1'b0;
            w = 0;
            while ((search_count != searches_after || search_busy || dut.pstate != 0) && w < 400000) begin
                @(posedge clk);
                w = w + 1;
            end
            repeat (20) @(posedge clk);
            $display("window %0d: locked %b waiting %b lock lag %0d strongest %0d (magnitude %0d, lock %0d), lost %0d re-locks %0d",
                     n, locked, waiting, lock_lag, sr_s_lag, sr_s_magnitude, lock_mag, lost_count, relock_count);
        end
    endtask

    initial begin
        $readmemh("rl_cfg.hex", cfg);
        $readmemh("rl_prn.hex", prn_words);
        $readmemh("rl_taps.hex", tap_mem);
        $readmemh("rl_windows.hex", windows);
        for (k = 0; k < 81; k = k + 1) taps[16*k +: 16] = tap_mem[k];
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        for (k = 0; k < 10; k = k + 1) begin
            @(posedge clk);
            prn_we <= 4'hF; waddr <= k; wdata <= prn_words[k];
        end
        @(posedge clk); prn_we <= 4'h0;
        repeat (N + 40) @(posedge clk);

        // 1: the burst; searched, then accepted by the ARM
        window(0, 1);
        check(waiting && !locked && sr_s_lag == cfg[2], "a search report waits for the ARM");
        @(posedge clk); cmd_accept <= 1'b1; @(posedge clk); cmd_accept <= 1'b0;
        repeat (10) @(posedge clk);
        check(locked && lock_lag == cfg[2], "locked after ACCEPT_STRONGEST");

        // 2: only a weak peak at the lock lag: the tracker loses it, the search may not re-lock on it
        window(1, 2);
        check(lost_count == 1, "the tracker lost the lock");
        check(sr_s_lag + 1 >= lock_lag && sr_s_lag <= lock_lag + 1, "the search found the weak peak within one lag");
        check(!locked && waiting && relock_count == 0, "too weak to re-lock by itself: waiting for the ARM");

        // 3: the burst again: the PL locks by itself
        window(2, 3);
        check(locked && relock_count == 1 && !waiting, "re-locked by itself on the burst");
        check(lock_lag == cfg[4], "at the burst's lag");

        if (errors == 0) $display("PASS tb_p8_relock");
        else             $display("FAIL tb_p8_relock: %0d errors", errors);
        $finish;
    end
endmodule
