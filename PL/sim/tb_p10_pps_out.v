`timescale 1ns / 1ps
// p10_pps_out with a 1000-tick second: the pulse at the second's start + OFFSET with
// its step and width, the flywheel over a missing edge, no second pulse when the PPS
// moves later, a PPS moved earlier with a new configuration taken at its early edge,
// the configuration taken at the next second, disabling, and the validity line (no
// blink at the second's start).  A nonzero idle level (2.2); from second 8 on idle
// and amplitude of opposite sign.  From second 13 the fractional edge (2.3): FRAC set but
// not enabled leaves the plain step; enabled, the step's first sample is
// amp + (idle - amp) x FRAC / 2^16, also with amp above idle.
module tb_p10_pps_out;
    localparam [31:0] PERIOD = 32'd1000;
    localparam [31:0] MINOFF = 32'd8;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg [63:0] raw_tick = 64'd0;
    always @(posedge clk) raw_tick <= raw_tick + 64'd1;

    reg         pps_event = 1'b0, pps_locked = 1'b1, clocks_locked = 1'b1;
    reg  [63:0] pps_tick = 64'd0;
    reg         enable = 1'b1, synced = 1'b1, tready = 1'b1;
    reg  [31:0] offset = 32'd20;
    reg  [2:0]  sample = 3'd3;
    reg  [15:0] width = 16'd4, amp = 16'd1234, idle = 16'd3000;
    reg         frac_en = 1'b0;
    reg  [15:0] frac = 16'd0;

    wire [255:0] tdata;
    wire         tvalid, have_ref, running, last_flywheel, invalid;
    wire [31:0]  pulses, flywheels;
    wire [63:0]  last_tick;

    p10_pps_out #(.PPS_PERIOD(PERIOD), .MIN_OFFSET(MINOFF)) dut (
        .clk (clk), .rst (rst), .raw_tick (raw_tick), .pps_event (pps_event), .pps_tick (pps_tick),
        .pps_locked (pps_locked), .clocks_locked (clocks_locked),
        .cfg_enable (enable), .cfg_synced (synced), .cfg_offset (offset), .cfg_sample (sample),
        .cfg_width (width), .cfg_amplitude (amp), .cfg_idle (idle), .cfg_frac_en (frac_en), .cfg_frac (frac),
        .m_axis_tdata (tdata), .m_axis_tvalid (tvalid), .m_axis_tready (tready),
        .have_ref (have_ref), .running (running), .last_flywheel (last_flywheel),
        .pulse_count (pulses), .flywheel_count (flywheels), .last_tick (last_tick),
        .pps_out_invalid (invalid)
    );

    integer errors = 0;

    // An edge latched at tick t: like P1, pps_tick = t and pps_event high in the cycle at t + 3.
    task edge_at(input [63:0] t);
        begin
            while (raw_tick != t + 64'd2) @(posedge clk);
            pps_event <= 1'b1;
            pps_tick  <= t;
            @(posedge clk);
            pps_event <= 1'b0;
        end
    endtask

    // A beat: I = id before sample `first`, e at it, a after it; Q = 0.
    function [255:0] beat_e(input integer first, input [15:0] a, input [15:0] id, input [15:0] e);
        integer n;
        begin
            beat_e = 256'd0;
            for (n = 0; n < 8; n = n + 1)
                beat_e[32 * n +: 16] = (n > first) ? a : (n == first) ? e : id;
        end
    endfunction

    function [255:0] beat(input integer first, input [15:0] a, input [15:0] id);
        beat = beat_e(first, a, id, a);
    endfunction

    // Pulses seen (beats other than idle; TVALID stays high): their start ticks, and the
    // beats checked against the configuration in force.
    integer    n_pulses = 0;
    reg [63:0] starts [0:63];
    reg        prev_valid = 1'b0;
    integer    beat_i = 0;
    reg [2:0]  exp_sample;
    reg [15:0] exp_width, exp_amp, exp_idle;
    reg [15:0] exp_edge;  // the step's first sample: exp_amp unless FRAC
    wire       in_pulse = tdata != beat(0, exp_idle, exp_idle);
    reg        rst_d = 1'b1;  // TVALID is registered: high from the second cycle out of reset
    always @(posedge clk) rst_d <= rst;
    always @(negedge clk) begin
        if (!rst_d && !tvalid) begin errors = errors + 1; $display("TVALID low at %0d", raw_tick); end
        if (in_pulse && !prev_valid) begin
            starts[n_pulses] = raw_tick;
            n_pulses = n_pulses + 1;
            beat_i = 0;
            if (last_tick !== raw_tick) begin errors = errors + 1; $display("last_tick %0d at %0d", last_tick, raw_tick); end
        end
        if (in_pulse) begin
            if (tdata !== (beat_i == 0 ? beat_e(exp_sample, exp_amp, exp_idle, exp_edge) : beat(0, exp_amp, exp_amp))) begin
                errors = errors + 1; $display("beat %0d of the pulse at %0d wrong", beat_i, starts[n_pulses - 1]);
            end
            beat_i = beat_i + 1;
        end else if (prev_valid && beat_i != exp_width) begin
            errors = errors + 1; $display("pulse of %0d beats, expected %0d", beat_i, exp_width);
        end
        prev_valid = in_pulse;
    end

    // The validity line must not rise inside a normal second.
    reg watch_valid = 1'b0;
    always @(negedge clk) if (watch_valid && invalid) begin
        errors = errors + 1; $display("validity line high at %0d", raw_tick);
    end

    task expect_pulse(input integer index, input [63:0] at);
        begin
            if (n_pulses <= index) begin errors = errors + 1; $display("pulse %0d missing (expected at %0d)", index, at); end
            else if (starts[index] !== at) begin errors = errors + 1; $display("pulse %0d at %0d, expected %0d", index, starts[index], at); end
        end
    endtask

    reg [63:0] s;
    integer k, n_before;
    initial begin
        exp_sample = sample; exp_width = width; exp_amp = amp; exp_idle = 16'd0; exp_edge = amp;
        repeat (5) @(posedge clk);
        rst <= 1'b0;
        // Out of reset the output is zero until the first locked edge takes IDLE.
        @(posedge clk); #1;

        // Nothing before the first locked edge, even enabled.
        repeat (1500) @(posedge clk);
        if (n_pulses != 0 || have_ref) begin errors = errors + 1; $display("a pulse before any edge"); end

        // Normal seconds: an edge every PERIOD from s; a pulse at each second's start + offset.
        s = 64'd2000;
        edge_at(s);
        @(posedge clk); #1 exp_idle = idle;  // the edge took IDLE: idle ticks from the next cycle
        repeat (MINOFF + 3) @(posedge clk);
        watch_valid = 1'b1;
        for (k = 1; k <= 3; k = k + 1) edge_at(s + k * PERIOD);
        repeat (PERIOD / 2) @(posedge clk);
        watch_valid = 1'b0;
        for (k = 0; k <= 3; k = k + 1) expect_pulse(k, s + k * PERIOD + offset);
        if (flywheels != 0 || last_flywheel) begin errors = errors + 1; $display("flywheel in normal seconds"); end

        // A missing edge (second 4): the pulse comes from the flywheel, the line goes high.
        repeat (PERIOD / 2 - 10) @(posedge clk);   // into second 4, no edge
        repeat (MINOFF + offset + 5) @(posedge clk);
        if (!invalid) begin errors = errors + 1; $display("validity line low without the second's edge"); end
        expect_pulse(4, s + 4 * PERIOD + offset);
        if (flywheels != 1 || !last_flywheel) begin errors = errors + 1; $display("flywheel count %0d", flywheels); end
        edge_at(s + 5 * PERIOD);                    // back: anchored again
        repeat (offset + 4) @(posedge clk);
        if (invalid) begin errors = errors + 1; $display("validity line high after the edge came back"); end
        expect_pulse(5, s + 5 * PERIOD + offset);

        // The PPS moves 25 ticks later (as after a PP and a new lock): the flywheel pulse of
        // second 6 is not repeated when the late edge re-anchors; second 7 follows the new edge.
        edge_at(s + 6 * PERIOD + 25);
        repeat (offset + 40) @(posedge clk);
        if (n_pulses != 7) begin errors = errors + 1; $display("%0d pulses after the late edge (a repeat?)", n_pulses); end
        expect_pulse(6, s + 6 * PERIOD + offset);
        s = s + 25;
        edge_at(s + 7 * PERIOD);
        repeat (offset + 4) @(posedge clk);
        expect_pulse(7, s + 7 * PERIOD + offset);

        // A new configuration in the middle of second 7, then the PPS 30 ticks earlier:
        // second 8 starts at its new edge, before the counter wraps, and takes the new
        // configuration there.
        repeat (200) @(posedge clk);
        offset <= 32'd100; sample <= 3'd1; width <= 16'd2; amp <= 16'hFC00; idle <= 16'h0400;
        s = s - 30;
        edge_at(s + 8 * PERIOD);
        @(posedge clk); #1;  // the new idle level from the cycle after the edge
        exp_sample = 3'd1; exp_width = 16'd2; exp_amp = 16'hFC00; exp_idle = 16'h0400; exp_edge = 16'hFC00;
        repeat (120) @(posedge clk);
        expect_pulse(8, s + 8 * PERIOD + 100);

        // Second 9: a normal edge, the same configuration.
        edge_at(s + 9 * PERIOD);
        repeat (120) @(posedge clk);
        expect_pulse(9, s + 9 * PERIOD + 100);

        // Not synchronized: the line is high, the pulses go on.
        synced <= 1'b0;
        repeat (5) @(posedge clk);
        if (!invalid) begin errors = errors + 1; $display("validity line low while not synchronized"); end
        synced <= 1'b1;
        tready <= 1'b0;  // the DAC not taking the stream
        repeat (5) @(posedge clk);
        if (!invalid) begin errors = errors + 1; $display("validity line low without TREADY"); end
        tready <= 1'b1;

        // Disabled in second 10: no pulse from second 11 on, the line high.
        edge_at(s + 10 * PERIOD);
        enable <= 1'b0;
        repeat (120) @(posedge clk);
        expect_pulse(10, s + 10 * PERIOD + 100);
        n_before = n_pulses;
        edge_at(s + 11 * PERIOD);
        edge_at(s + 12 * PERIOD);
        repeat (200) @(posedge clk);
        if (n_pulses != n_before || running) begin errors = errors + 1; $display("pulses while disabled"); end
        if (!invalid) begin errors = errors + 1; $display("validity line low while disabled"); end

        // FRAC set but not enabled (second 13): the plain step.
        enable <= 1'b1; frac <= 16'h4000;
        edge_at(s + 13 * PERIOD);
        repeat (120) @(posedge clk);
        expect_pulse(n_before, s + 13 * PERIOD + 100);
        // Enabled (second 14): amp -1024, idle +1024, a quarter: -1024 + 512 = -512.
        frac_en <= 1'b1;
        exp_edge = 16'hFE00;
        edge_at(s + 14 * PERIOD);
        repeat (120) @(posedge clk);
        expect_pulse(n_before + 1, s + 14 * PERIOD + 100);
        // FRAC 0xFFFF (second 15): -1024 + floor(2048 x 65535 / 65536) = 1023.
        frac <= 16'hFFFF;
        exp_edge = 16'h03FF;
        edge_at(s + 15 * PERIOD);
        repeat (120) @(posedge clk);
        expect_pulse(n_before + 2, s + 15 * PERIOD + 100);
        // Amp above idle (second 16, idle unchanged): amp 3072, idle 1024, a half: 3072 - 1024 = 2048.
        repeat (200) @(posedge clk);
        amp <= 16'h0C00; frac <= 16'h8000;
        edge_at(s + 16 * PERIOD);
        exp_amp = 16'h0C00; exp_edge = 16'h0800;
        repeat (120) @(posedge clk);
        expect_pulse(n_before + 3, s + 16 * PERIOD + 100);

        if (pulses != n_pulses) begin errors = errors + 1; $display("pulse_count %0d, seen %0d", pulses, n_pulses); end
        if (errors == 0) $display("PASS tb_p10_pps_out");
        else             $display("FAIL tb_p10_pps_out: %0d errors", errors);
        $finish;
    end
endmodule
