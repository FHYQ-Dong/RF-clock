`timescale 1ns/1ps

module tb_H04_pps_capture;
    localparam [63:0] PPS_PERIOD = 64'd20;
    localparam [63:0] PPS_TOLERANCE = 64'd2;
    localparam [63:0] PPS_TIMEOUT = 64'd30;
    localparam integer STABLE_CYCLES = 4;

    reg clk;
    reg rst;
    reg pps_in_async;
    reg [63:0] raw_tick;
    wire pps_event;
    wire pps_locked;
    integer event_count;

    H04_pps_capture #(
        .PPS_PERIOD(PPS_PERIOD),
        .PPS_TOLERANCE(PPS_TOLERANCE),
        .PPS_TIMEOUT(PPS_TIMEOUT),
        .STABLE_CYCLES(STABLE_CYCLES)
    ) dut (
        .clk(clk),
        .rst(rst),
        .pps_in_async(pps_in_async),
        .raw_tick(raw_tick),
        .pps_event(pps_event),
        .pps_locked(pps_locked)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst)
            raw_tick <= 64'd0;
        else
            raw_tick <= raw_tick + 64'd1;
    end

    always @(posedge pps_event)
        event_count = event_count + 1;

    task check;
        input condition;
        input [1023:0] message;
        begin
            if (!condition) begin
                $display("FAIL @%0t: %0s", $time, message);
                $finish;
            end
        end
    endtask

    task pulse_at;
        input [63:0] target_tick;
        begin
            while (raw_tick < target_tick)
                @(posedge clk);

            // Deliberately make the input asynchronous to clk.
            #1 pps_in_async = 1'b1;
            repeat (2) @(posedge clk);
            #1 pps_in_async = 1'b0;
        end
    endtask

    task wait_for_events;
        input integer expected_count;
        integer guard_cycles;
        begin
            guard_cycles = 0;
            while ((event_count < expected_count) && (guard_cycles < 20)) begin
                @(posedge clk);
                guard_cycles = guard_cycles + 1;
            end
            check(event_count == expected_count,
                   "each PPS pulse must produce exactly one pps_event");
            // pps_event is asserted from the synchronizer state; H04's
            // sequential qualification consumes it on the following edge.
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        clk = 1'b0;
        rst = 1'b1;
        pps_in_async = 1'b0;
        raw_tick = 64'd0;
        event_count = 0;

        repeat (2) @(posedge clk);
        #1;
        rst = 1'b0;

        // First pulse is only a baseline. It is deliberately outside the
        // expected interval from reset, so it must not lock the module.
        pulse_at(64'd40);
        wait_for_events(1);
        check(!pps_locked, "one PPS event must not assert pps_locked");

        // Four valid intervals are required for STABLE_CYCLES=4.
        pulse_at(64'd60);
        wait_for_events(2);
        pulse_at(64'd80);
        wait_for_events(3);
        pulse_at(64'd100);
        wait_for_events(4);
        check(!pps_locked, "three valid intervals must not lock PPS");

        pulse_at(64'd120);
        wait_for_events(5);
        check(pps_locked, "four valid PPS intervals must assert pps_locked");

        // A bad interval must remove the lock.
        pulse_at(64'd150);
        wait_for_events(6);
        check(!pps_locked, "an invalid PPS period must clear pps_locked");

        // Four subsequent valid intervals must reacquire the lock.
        pulse_at(64'd170);
        wait_for_events(7);
        pulse_at(64'd190);
        wait_for_events(8);
        pulse_at(64'd210);
        wait_for_events(9);
        pulse_at(64'd230);
        wait_for_events(10);
        check(pps_locked, "PPS lock must recover after valid intervals");

        // No PPS for longer than PPS_TIMEOUT must clear the lock.
        repeat (35) @(posedge clk);
        #1;
        check(!pps_locked, "PPS timeout must clear pps_locked");

        $display("PASS: tb_H04_pps_capture");
        $finish;
    end
endmodule
