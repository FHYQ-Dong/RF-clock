`timescale 1ns / 1ps
// P0: reset release, lock qualification, epoch counting, lock-loss event.
module tb_p0_clock_reset;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg arst_n = 1'b0;
    reg locked = 1'b0;

    wire        rst, clocks_locked, time_valid, lock_lost_event, epoch_start_event;
    wire [31:0] time_epoch;

    p0_clock_reset #(.STABLE_CYCLES(8)) dut (
        .clk                 (clk),
        .arst_n              (arst_n),
        .clocks_locked_async (locked),
        .rst                 (rst),
        .clocks_locked       (clocks_locked),
        .time_valid          (time_valid),
        .time_epoch          (time_epoch),
        .lock_lost_event     (lock_lost_event),
        .epoch_start_event   (epoch_start_event)
    );

    initial begin
        #20_000;
        $display("FAIL tb_p0_clock_reset (timeout)");
        $finish;
    end

    integer errors = 0;
    integer lost_pulses = 0;
    integer epoch_pulses = 0;
    always @(posedge clk) begin
        if (lock_lost_event)   lost_pulses  = lost_pulses + 1;
        if (epoch_start_event) epoch_pulses = epoch_pulses + 1;
    end

    task check(input cond, input [8*64-1:0] msg);
        if (!cond) begin
            errors = errors + 1;
            $display("FAIL: %0s", msg);
        end
    endtask

    integer n;
    initial begin
        repeat (4) @(posedge clk);
        check(rst === 1'b1, "rst asserted while arst_n low");
        arst_n = 1'b1;
        repeat (4) @(posedge clk);
        check(rst === 1'b0, "rst released");
        check(time_valid === 1'b0 && time_epoch == 0, "not valid before lock");

        locked = 1'b1;
        // 2 sync stages + 8 stable cycles.
        n = 0;
        while (!time_valid && n < 50) begin @(posedge clk); n = n + 1; end
        check(time_valid === 1'b1, "time_valid after lock");
        check(n >= 9 && n <= 12, "qualification takes ~STABLE_CYCLES");
        check(time_epoch == 1, "first epoch is 1");

        locked = 1'b0;
        repeat (5) @(posedge clk);
        check(time_valid === 1'b0, "time_valid drops on lock loss");
        check(lost_pulses == 1, "one lock_lost_event");
        check(time_epoch == 1, "epoch kept while unlocked");

        // A short glitch of lock must not qualify.
        locked = 1'b1; repeat (4) @(posedge clk); locked = 1'b0;
        repeat (20) @(posedge clk);
        check(time_valid === 1'b0 && time_epoch == 1, "short lock does not qualify");

        locked = 1'b1;
        repeat (20) @(posedge clk);
        check(time_valid === 1'b1 && time_epoch == 2, "relock starts epoch 2");
        check(epoch_pulses == 2, "two epoch_start_event pulses");
        check(lost_pulses == 1, "no extra lock_lost_event");

        if (errors == 0) $display("PASS tb_p0_clock_reset");
        else             $display("FAIL tb_p0_clock_reset (%0d errors)", errors);
        $finish;
    end
endmodule
