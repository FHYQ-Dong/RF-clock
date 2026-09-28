`timescale 1ns/1ps

module tb_H01_clock_reset_guard;
    localparam integer STABLE_CYCLES = 4;

    reg clk;
    reg arst_n;
    reg clock_pll_locked_async;
    reg rfdc_locked_async;
    reg external_ref_locked_async;
    reg wr_locked_async;
    reg boot_valid_async;
    reg raw_tick_exhausted_async;
    reg sync_locked_async;
    reg servo_converged_async;

    wire rst;
    wire clock_ok;
    wire clock_reference_ok;
    wire local_time_valid;
    wire system_time_valid;

    H01_clock_reset_guard #(
        .STABLE_CYCLES(STABLE_CYCLES)
    ) dut (
        .clk(clk),
        .arst_n(arst_n),
        .clock_pll_locked_async(clock_pll_locked_async),
        .rfdc_locked_async(rfdc_locked_async),
        .external_ref_locked_async(external_ref_locked_async),
        .wr_locked_async(wr_locked_async),
        .boot_valid_async(boot_valid_async),
        .raw_tick_exhausted_async(raw_tick_exhausted_async),
        .sync_locked_async(sync_locked_async),
        .servo_converged_async(servo_converged_async),
        .rst(rst),
        .clock_ok(clock_ok),
        .clock_reference_ok(clock_reference_ok),
        .local_time_valid(local_time_valid),
        .system_time_valid(system_time_valid)
    );

    always #5 clk = ~clk;

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

    task wait_clocks;
        input integer count;
        begin
            repeat (count) @(posedge clk);
            #1;
        end
    endtask

    initial begin
        clk = 1'b0;
        arst_n = 1'b0;
        clock_pll_locked_async = 1'b0;
        rfdc_locked_async = 1'b0;
        external_ref_locked_async = 1'b0;
        wr_locked_async = 1'b0;
        boot_valid_async = 1'b0;
        raw_tick_exhausted_async = 1'b0;
        sync_locked_async = 1'b0;
        servo_converged_async = 1'b0;

        wait_clocks(2);
        check(rst, "arst_n=0 must assert rst");
        check(!clock_ok && !clock_reference_ok &&
               !local_time_valid && !system_time_valid,
               "all status outputs must be low during reset");

        arst_n = 1'b1;
        wait_clocks(1);
        check(rst, "rst must remain asserted for the first release cycle");
        wait_clocks(1);
        check(!rst, "rst must synchronously release after two clk edges");

        @(negedge clk);
        clock_pll_locked_async = 1'b1;
        rfdc_locked_async = 1'b1;
        external_ref_locked_async = 1'b1;
        wr_locked_async = 1'b1;
        boot_valid_async = 1'b1;
        sync_locked_async = 1'b1;
        servo_converged_async = 1'b1;

        wait_clocks(10);
        check(clock_ok, "clock_ok must assert after stable local locks");
        check(clock_reference_ok,
               "clock_reference_ok must assert after external reference lock");
        check(local_time_valid,
               "local_time_valid must assert after boot and non-exhausted tick");
        check(system_time_valid,
               "system_time_valid must assert after sync and servo convergence");

        @(negedge clk);
        raw_tick_exhausted_async = 1'b1;
        wait_clocks(6);
        check(clock_ok && clock_reference_ok,
               "tick exhaustion must not remove local clock/reference status");
        check(!local_time_valid && !system_time_valid,
               "tick exhaustion must invalidate local and system time");

        @(negedge clk);
        raw_tick_exhausted_async = 1'b0;
        wait_clocks(8);
        check(local_time_valid && system_time_valid,
               "time validity must recover after tick exhaustion clears");

        @(negedge clk);
        external_ref_locked_async = 1'b0;
        wait_clocks(6);
        check(clock_ok, "external reference loss must not remove clock_ok");
        check(!clock_reference_ok && !local_time_valid && !system_time_valid,
               "external reference loss must invalidate downstream status");

        @(negedge clk);
        external_ref_locked_async = 1'b1;
        wait_clocks(8);
        check(clock_reference_ok && local_time_valid && system_time_valid,
               "reference status must recover after external reference returns");

        @(negedge clk);
        sync_locked_async = 1'b0;
        wait_clocks(6);
        check(local_time_valid && !system_time_valid,
               "sync loss must invalidate only system_time_valid");

        @(negedge clk);
        sync_locked_async = 1'b1;
        wait_clocks(8);
        check(system_time_valid,
               "system_time_valid must recover after sync lock returns");

        @(negedge clk);
        arst_n = 1'b0;
        #1;
        check(rst, "asynchronous reset assertion must assert rst immediately");
        check(!clock_ok && !clock_reference_ok &&
               !local_time_valid && !system_time_valid,
               "asynchronous reset must clear all status outputs");

        $display("PASS: tb_H01_clock_reset_guard");
        $finish;
    end
endmodule
