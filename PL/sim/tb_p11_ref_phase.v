`timescale 1ns / 1ps
// p11_ref_phase on a 10 MHz sine of known delay and amplitude, 8 samples a beat at
// 800 MS/s, with a PPS event every 1000 beats: the delay from atan2(Q, I) / (2 pi f)
// to 0.5 ps, a step of 3 ps to 0.1 ps, a delay near the period's end (the angle
// wraps), the beats, the largest |sample| and the PPS_COUNT of each snapshot, beats
// without TVALID left out, and a second of another length.
module tb_p11_ref_phase;
    localparam integer SECOND = 1000;  // beats between PPS events
    localparam real    PI     = 3.14159265358979;
    localparam real    F_HZ   = 10.0e6;
    localparam real    FS_HZ  = 800.0e6;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg [63:0] t = 64'd0;  // the TB's tick, in step with the DUT's: both start at reset and count every clock
    always @(posedge clk) t <= rst ? 64'd0 : t + 64'd1;

    real    tau_ns = 12.3456;
    real    amp    = 20000.0;
    reg     tvalid = 1'b1;
    reg     pps_event = 1'b0;
    reg [31:0] pps_count = 32'd0;
    reg [127:0] tdata;
    integer n;
    real    v;
    always @(t or tau_ns or amp) begin
        for (n = 0; n < 8; n = n + 1) begin
            v = amp * $cos(2.0 * PI * F_HZ * ((8.0 * t + n) / FS_HZ - tau_ns * 1.0e-9));
            tdata[16 * n +: 16] = $rtoi($floor(v + 0.5));
        end
    end

    wire [31:0]        seq, count, beats;
    wire [15:0]        peak;
    wire signed [63:0] si, sq;
    p11_ref_phase dut (.clk (clk), .rst (rst), .s_axis_tdata (tdata), .s_axis_tvalid (tvalid),
                       .pps_event (pps_event), .pps_count (pps_count),
                       .snap_seq (seq), .snap_count (count), .snap_beats (beats), .snap_peak (peak),
                       .snap_i (si), .snap_q (sq));

    integer errors = 0;
    real    got, prev_tau;

    // one second of `len` beats (a multiple of 10: whole periods of the reference, as the
    // board's 1e8; a part period leaks into the sums), the event (and P1's count) at its start
    task second(input integer len);
        begin
            @(posedge clk);
            pps_event <= 1'b1;
            pps_count <= pps_count + 32'd1;
            @(posedge clk);
            pps_event <= 1'b0;
            repeat (len - 2) @(posedge clk);
        end
    endtask

    function real delay_ns(input signed [63:0] i, input signed [63:0] q);
        real a;
        begin
            a = $atan2(1.0 * q, 1.0 * i);
            if (a < 0.0) a = a + 2.0 * PI;
            delay_ns = a / (2.0 * PI * F_HZ) * 1.0e9;
        end
    endfunction

    // the snapshot of the second that the last event closed (taken a few clocks after it;
    // check runs at the end of the next second, before its event)
    task check(input real want_ns, input real tol_ps, input integer want_beats, input integer want_peak,
               input [31:0] want_count, input [31:0] want_seq);
        begin
            got = delay_ns(si, sq);
            if ((got - want_ns) * 1000.0 > tol_ps || (want_ns - got) * 1000.0 > tol_ps) begin
                errors = errors + 1;
                $display("delay %f ns, want %f ns (%f ps off)", got, want_ns, (got - want_ns) * 1000.0);
            end
            if (beats != want_beats) begin errors = errors + 1; $display("beats %0d, want %0d", beats, want_beats); end
            if (want_peak >= 0 && (peak > want_peak || 1000 * peak < 999 * want_peak)) begin  // the nearest sample to the crest
                errors = errors + 1; $display("peak %0d, want about %0d", peak, want_peak);
            end
            if (count != want_count) begin errors = errors + 1; $display("count %0d, want %0d", count, want_count); end
            if (seq != want_seq) begin errors = errors + 1; $display("seq %0d, want %0d", seq, want_seq); end
        end
    endtask

    initial begin
        repeat (5) @(posedge clk);
        rst <= 1'b0;
        repeat (17) @(posedge clk);
        second(SECOND);                                   // event 1: the first, partial second ends
        second(SECOND);                                   // event 2: closes second 1
        check(12.3456, 0.5, SECOND, 20000, 32'd1, 32'd2);
        prev_tau = got;
        tau_ns = 12.3486;                                 // 3 ps later
        second(SECOND);                                   // event 3: second 2 had the old delay
        check(12.3456, 0.5, SECOND, 20000, 32'd2, 32'd3);
        second(SECOND);                                   // event 4: second 3, all at the new delay
        check(12.3486, 0.5, SECOND, -1, 32'd3, 32'd4);
        if ((got - prev_tau) * 1000.0 - 3.0 > 0.1 || 3.0 - (got - prev_tau) * 1000.0 > 0.1) begin
            errors = errors + 1;
            $display("step %f ps, want 3", (got - prev_tau) * 1000.0);
        end
        tau_ns = 99.9990;                                 // 1 ps before the period's end: the angle wraps
        amp = 8000.0;
        second(SECOND);
        second(SECOND);
        check(99.9990, 0.5, SECOND, 8000, 32'd5, 32'd6);
        @(posedge clk);                                   // a second whose first 100 beats have no TVALID
        pps_event <= 1'b1;
        pps_count <= pps_count + 32'd1;
        tvalid    <= 1'b0;
        @(posedge clk);
        pps_event <= 1'b0;
        repeat (99) @(posedge clk);
        tvalid <= 1'b1;
        repeat (SECOND - 101) @(posedge clk);
        second(SECOND);
        check(99.9990, 0.5, SECOND - 100, 8000, 32'd7, 32'd8);
        second(1500);                                     // a longer second
        second(SECOND);
        check(99.9990, 0.5, 1500, 8000, 32'd9, 32'd10);
        if (errors == 0) $display("PASS tb_p11_ref_phase");
        else             $display("FAIL tb_p11_ref_phase: %0d errors", errors);
        $finish;
    end
endmodule
