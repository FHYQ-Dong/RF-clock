`timescale 1ns / 1ps
// P1: raw tick counting, PPS edge timestamp, period/jitter/missing checks,
// source/polarity selection and the per-copy input probes.
module tb_p1_timebase_pps;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg rst = 1'b1;
    reg pps = 1'b0;    // comparator copy
    reg trig = 1'b0;   // Schmitt-trigger copy
    reg src_trig = 1'b0;
    reg invert = 1'b0;

    wire [63:0] raw_tick, pps_tick;
    wire [31:0] pps_count, pps_last_period, pps_jitter_count;
    wire        raw_tick_exhausted, pps_event, pps_locked, pps_fault_event;
    wire        comp_level, trig_level;
    wire [7:0]  comp_edges, trig_edges;

    localparam [31:0] PERIOD = 32'd100;

    p1_timebase_pps #(
        .PPS_PERIOD     (PERIOD),
        .PPS_TOLERANCE  (32'd1),
        .PPS_LOCK_COUNT (32'd3)
    ) dut (
        .clk                (clk),
        .rst                (rst),
        .pps_comp_async     (pps),
        .pps_trig_async     (trig),
        .pps_src_trig       (src_trig),
        .pps_invert         (invert),
        .pps_comp_level     (comp_level),
        .pps_trig_level     (trig_level),
        .pps_comp_edges     (comp_edges),
        .pps_trig_edges     (trig_edges),
        .raw_tick           (raw_tick),
        .raw_tick_exhausted (raw_tick_exhausted),
        .pps_event          (pps_event),
        .pps_tick           (pps_tick),
        .pps_count          (pps_count),
        .pps_last_period    (pps_last_period),
        .pps_jitter_count   (pps_jitter_count),
        .pps_locked         (pps_locked),
        .pps_fault_event    (pps_fault_event)
    );

    initial begin
        #50_000;
        $display("FAIL tb_p1_timebase_pps (timeout)");
        $finish;
    end

    integer errors = 0;
    integer faults = 0;
    always @(posedge clk) if (pps_fault_event) faults = faults + 1;

    task check(input cond, input [8*64-1:0] msg);
        if (!cond) begin
            errors = errors + 1;
            $display("FAIL: %0s (t=%0t)", msg, $time);
        end
    endtask

    // Raise PPS in the middle of the cycle whose raw_tick == t; the expected
    // pps_tick is t.  Hold it high for 10 cycles.
    task pps_at(input [63:0] t);
        begin
            while (raw_tick != t) @(negedge clk);
            pps = 1'b1;
            repeat (10) @(negedge clk);
            pps = 1'b0;
            repeat (4) @(negedge clk);
            check(pps_tick == t, "pps_tick equals the edge tick");
        end
    endtask

    // Idle-high Schmitt copy pulsing low; with invert the falling edge at
    // raw_tick == t is the timed edge.
    task trig_low_at(input [63:0] t);
        begin
            while (raw_tick != t) @(negedge clk);
            trig = 1'b0;
            repeat (10) @(negedge clk);
            trig = 1'b1;
            repeat (4) @(negedge clk);
            check(pps_tick == t, "inverted trig: pps_tick equals the falling-edge tick");
        end
    endtask

    reg [63:0] t0;
    reg [63:0] t1;
    integer    faults_before;
    reg [63:0] prev;
    initial begin
        repeat (4) @(posedge clk);
        rst = 1'b0;

        // raw_tick increments by exactly one per clock.
        @(negedge clk); prev = raw_tick;
        repeat (10) @(negedge clk);
        check(raw_tick == prev + 10, "raw_tick +1 per clock");
        check(!raw_tick_exhausted, "not exhausted");

        t0 = 64'd50;
        pps_at(t0);
        check(pps_count == 1 && !pps_locked, "first PPS, not locked");
        pps_at(t0 + PERIOD);
        pps_at(t0 + 2*PERIOD);
        check(!pps_locked, "two good periods: not yet locked");
        pps_at(t0 + 3*PERIOD);
        check(pps_locked, "locked after 3 good periods");
        check(pps_last_period == PERIOD, "exact period");
        check(pps_jitter_count == 0 && faults == 0, "no jitter or fault yet");

        // +1 tick: within tolerance, counted as jitter, lock kept.
        pps_at(t0 + 4*PERIOD + 1);
        check(pps_locked && pps_jitter_count == 1 && faults == 0, "1-tick jitter tolerated");

        // 5 ticks late: the missing check fires first, then the late edge
        // is a period fault; lock is dropped.
        pps_at(t0 + 5*PERIOD + 6);
        check(!pps_locked && faults == 2, "late PPS: missing + period fault");

        // Missing pulse: no edge for > PERIOD + tolerance.
        while (raw_tick != t0 + 7*PERIOD + 6) @(negedge clk);
        check(faults == 3, "missing PPS is a fault");

        // Probes: 6 comparator pulses so far, nothing on the other copy.
        check(comp_edges == 8'd6 && trig_edges == 8'd0, "probe edge counts");
        check(!comp_level && !trig_level, "probe levels idle low");

        // Switch to the Schmitt copy, inverted.  The copy idles high; the
        // configuration change restarts acquisition without a fault.
        trig = 1'b1;
        repeat (4) @(negedge clk);
        check(trig_level && trig_edges == 8'd1, "trig probe follows the pin");
        faults_before = faults;
        src_trig = 1'b1;
        invert   = 1'b1;
        repeat (4) @(negedge clk);
        check(faults == faults_before && !pps_locked, "config change: no fault, not locked");
        t1 = raw_tick + 20;
        trig_low_at(t1);
        trig_low_at(t1 + PERIOD);
        trig_low_at(t1 + 2*PERIOD);
        trig_low_at(t1 + 3*PERIOD);
        check(pps_locked && pps_last_period == PERIOD, "locked on the inverted trig copy");
        check(faults == faults_before, "no faults on the inverted trig copy");
        check(comp_edges == 8'd6 && trig_edges == 8'd5, "probe edge counts after trig pulses");

        if (errors == 0) $display("PASS tb_p1_timebase_pps");
        else             $display("FAIL tb_p1_timebase_pps (%0d errors)", errors);
        $finish;
    end
endmodule
