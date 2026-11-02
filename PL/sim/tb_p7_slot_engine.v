`timescale 1ns / 1ps
// p7_slot_engine with short slots: arms at the right ticks for both roles,
// the configuration taken at the PPS edge, the skip rules, SLOT_COPY, and
// idling without pps_locked or after a burst FAULT.
module tb_p7_slot_engine;
    localparam [31:0] SLOT_TICKS = 32'd2000;
    localparam [31:0] SLOTS      = 32'd6;
    localparam [31:0] LEAD       = 32'd128;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg [63:0] raw_tick = 64'd0;
    always @(posedge clk) raw_tick <= raw_tick + 64'd1;

    reg         time_valid = 1'b1, pps_locked = 1'b1;
    reg         pps_event = 1'b0;
    reg  [63:0] pps_tick = 64'd0;
    reg  [31:0] pps_count = 32'd0;
    reg  [2:0]  control = 3'd0;
    reg  [31:0] tx_offset = 32'd300, rx_offset = 32'd500;
    reg  [16:0] prn_chips = 17'd100;
    reg  [15:0] guard_chips = 16'd10;
    reg         copy_request = 1'b0;
    reg         tx_busy = 1'b0, tx_fault = 1'b0, rx_busy = 1'b0;

    wire        running, copy_pending, role_b, packets, tx_arm, rx_arm, rx_copy;
    wire [8:0]  slot, ev_slot;
    wire [31:0] skipped, burst_beats, span_beats, sec_count;
    wire [16:0] a_prn;
    wire [15:0] a_guard;
    wire [63:0] ev_tick, sec_tick;

    p7_slot_engine #(.SLOT_TICKS(SLOT_TICKS), .SLOTS(SLOTS), .LEAD(LEAD)) dut (
        .clk (clk), .rst (rst), .raw_tick (raw_tick), .time_valid (time_valid), .pps_locked (pps_locked),
        .pps_event (pps_event), .pps_tick (pps_tick), .pps_count (pps_count),
        .cfg_control (control), .cfg_tx_offset (tx_offset), .cfg_rx_offset (rx_offset),
        .cfg_prn_chips (prn_chips), .cfg_guard_chips (guard_chips),
        .window_beats (32'd200), .packet_chips (32'd50), .copy_request (copy_request),
        .tx_busy (tx_busy), .tx_fault (tx_fault), .rx_busy (rx_busy),
        .running (running), .slot (slot), .skipped_count (skipped), .copy_pending (copy_pending),
        .role_b (role_b), .packets (packets), .prn_chips (a_prn), .guard_chips (a_guard),
        .burst_beats (burst_beats), .span_beats (span_beats),
        .tx_arm (tx_arm), .rx_arm (rx_arm), .rx_copy (rx_copy),
        .ev_tick (ev_tick), .ev_slot (ev_slot), .sec_count (sec_count), .sec_tick (sec_tick)
    );

    // Arms seen in a second: the slot, the type (1 TX, 2 RX, 3 RX with copy),
    // and whether ev_tick and the arm time were right.
    integer errors = 0;
    integer n_arms = 0;
    reg [1:0] kind [0:15];
    reg [8:0] kslot [0:15];
    always @(posedge clk) if (tx_arm || rx_arm) begin
        kind[n_arms]  = tx_arm ? 2'd1 : (rx_copy ? 2'd3 : 2'd2);
        kslot[n_arms] = ev_slot;
        if (ev_tick !== sec_tick + ev_slot * SLOT_TICKS + (tx_arm ? dut.tx_offset : dut.rx_offset)) begin
            errors = errors + 1;
            $display("slot %0d: ev_tick %0d", ev_slot, ev_tick);
        end
        if (raw_tick + LEAD !== ev_tick + 1) begin   // armed in the cycle raw_tick + LEAD == ev_tick
            errors = errors + 1;
            $display("slot %0d armed at %0d for %0d", ev_slot, raw_tick, ev_tick);
        end
        n_arms = n_arms + 1;
    end

    task pps;
        begin
            @(posedge clk);
            pps_tick  <= raw_tick - 64'd3;
            pps_count <= pps_count + 32'd1;
            pps_event <= 1'b1;
            @(posedge clk);
            pps_event <= 1'b0;
        end
    endtask

    // One second of SLOTS slots; then compare the arms with `plan`, one
    // character per slot: T (burst), R (window), C (window copied), - (none).
    task second(input [8*6-1:0] plan, input integer skips);
        integer k, a, s0;
        reg [7:0] ch;
        begin
            n_arms = 0;
            s0 = skipped;
            pps;
            repeat (SLOTS * SLOT_TICKS - 40) @(posedge clk);
            a = 0;
            for (k = 0; k < SLOTS; k = k + 1) begin
                ch = plan[8*(SLOTS-1-k) +: 8];
                if (ch != "-") begin
                    if (a >= n_arms || kslot[a] != k ||
                        kind[a] != (ch == "T" ? 2'd1 : (ch == "C" ? 2'd3 : 2'd2))) begin
                        errors = errors + 1;
                        $display("second %0d slot %0d: expected %s", pps_count, k, ch);
                    end
                    a = a + 1;
                end
            end
            if (a != n_arms) begin
                errors = errors + 1;
                $display("second %0d: %0d arms, expected %0d", pps_count, n_arms, a);
            end
            if (skipped - s0 != skips) begin
                errors = errors + 1;
                $display("second %0d: %0d skipped, expected %0d", pps_count, skipped - s0, skips);
            end
        end
    endtask

    initial begin
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        repeat (10) @(posedge clk);
        second("------", 0);                       // disabled
        if (running) begin errors = errors + 1; $display("running while disabled"); end
        control <= 3'b001;                         // node A
        second("TRTRTR", 0);
        if (!running || burst_beats != 110 || span_beats != 261) begin
            errors = errors + 1;
            $display("running %b beats %0d span %0d", running, burst_beats, span_beats);
        end
        control <= 3'b111;                         // node B with packets: only taken at the PPS edge
        copy_request <= 1'b1; @(posedge clk); copy_request <= 1'b0;
        second("CTRTRT", 0);
        if (burst_beats != 170 || copy_pending) begin
            errors = errors + 1; $display("beats %0d copy_pending %b", burst_beats, copy_pending);
        end
        copy_request <= 1'b1; @(posedge clk); copy_request <= 1'b0;
        control <= 3'b001;
        second("TCTRTR", 0);
        // too late for slot 0's burst
        tx_offset <= 32'd50;
        second("-RTRTR", 1);
        // a window that would still be open at the next burst: peer slots skipped
        tx_offset <= 32'd300; rx_offset <= 32'd2100;
        second("T-T-T-", 3);
        // a burst that runs into the next slot: own slots skipped
        rx_offset <= 32'd500; tx_offset <= 32'd1900;
        second("-R-R-R", 3);
        // window capture busy: its slots are skipped
        tx_offset <= 32'd300; rx_busy <= 1'b1;
        second("T-T-T-", 3);
        rx_busy <= 1'b0;
        // no PPS lock: idle
        pps_locked <= 1'b0;
        second("------", 0);
        if (running) begin errors = errors + 1; $display("running without pps_locked"); end
        pps_locked <= 1'b1;
        // a burst FAULT stops the engine until the next PPS edge after it is cleared
        n_arms = 0;
        pps;
        repeat (2 * SLOT_TICKS + 10) @(posedge clk);
        tx_fault <= 1'b1;
        repeat (SLOT_TICKS) @(posedge clk);
        if (running) begin errors = errors + 1; $display("running after a FAULT"); end
        tx_fault <= 1'b0;
        repeat ((SLOTS - 3) * SLOT_TICKS - 60) @(posedge clk);
        if (n_arms != 2) begin errors = errors + 1; $display("%0d arms before the FAULT", n_arms); end
        second("TRTRTR", 0);

        if (errors == 0) $display("PASS tb_p7_slot_engine");
        else             $display("FAIL tb_p7_slot_engine: %0d errors", errors);
        $finish;
    end
endmodule
