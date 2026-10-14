`timescale 1ns / 1ps
// P0: clock/reset qualification and time epoch.
//
// arst_n is the PS/board reset.  clocks_locked_async is the AND of every
// lock/ready signal the timebase depends on (board PLLs, clocking wizard,
// RFDC tiles).  Losing lock does not reset the PL: status must survive so
// the ARM can read why timing became invalid.  It clears time_valid, and
// the next qualified lock starts a new time_epoch (0 = never valid).
module p0_clock_reset #(
    parameter integer STABLE_CYCLES = 1024
) (
    input  wire        clk,
    input  wire        arst_n,
    input  wire        clocks_locked_async,
    output wire        rst,
    output wire        clocks_locked,
    output reg         time_valid,
    output reg  [31:0] time_epoch,
    output reg         lock_lost_event,
    output reg         epoch_start_event
);
    // Asynchronous assert, synchronous release.
    (* ASYNC_REG = "TRUE" *) reg [1:0] rst_sync;
    always @(posedge clk or negedge arst_n) begin
        if (!arst_n) rst_sync <= 2'b11;
        else         rst_sync <= {rst_sync[0], 1'b0};
    end
    assign rst = rst_sync[1];

    (* ASYNC_REG = "TRUE" *) reg [1:0] lock_sync;
    always @(posedge clk) lock_sync <= {lock_sync[0], clocks_locked_async};
    assign clocks_locked = lock_sync[1];

    reg [31:0] stable_cnt;

    always @(posedge clk) begin
        lock_lost_event   <= 1'b0;
        epoch_start_event <= 1'b0;
        if (rst) begin
            stable_cnt <= 32'd0;
            time_valid <= 1'b0;
            time_epoch <= 32'd0;
        end else if (!clocks_locked) begin
            stable_cnt <= 32'd0;
            if (time_valid) begin
                time_valid      <= 1'b0;
                lock_lost_event <= 1'b1;
            end
        end else if (!time_valid) begin
            if (stable_cnt == STABLE_CYCLES - 1) begin
                time_valid        <= 1'b1;
                time_epoch        <= time_epoch + 32'd1;
                epoch_start_event <= 1'b1;
            end else begin
                stable_cnt <= stable_cnt + 32'd1;
            end
        end
    end
endmodule
