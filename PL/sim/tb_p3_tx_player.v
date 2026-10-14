`timescale 1ns / 1ps
// P3: buffer load/readback, exact start tick, gap-free burst, repeated
// bursts, rejects, and the three FAULT causes.
module tb_p3_tx_player;
    `include "src/p_defs.vh"
    `include "src/p_regs.vh"

    localparam integer DATA_W = 256;
    localparam integer ADDR_W = 4;   // 16-beat window
    localparam integer DEPTH  = 12;  // not a power of two, like the real 69632
    localparam integer BANK_W = 2;   // three 4-beat banks (the real ones are 4096)
    localparam integer BA_W   = ADDR_W + 5;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg         rst = 1'b1;
    reg  [63:0] raw_tick = 64'd0;
    reg         time_valid = 1'b1;
    always @(posedge clk) raw_tick <= rst ? 64'd0 : raw_tick + 64'd1;

    reg         doorbell = 0, cancel = 0, clear_fault = 0;
    reg  [63:0] cmd_target = 0;
    reg  [31:0] cmd_length = 0;
    reg  [31:0] cmd_repeat = 1;
    reg                bram_en = 0;
    reg [DATA_W/8-1:0] bram_we = 0;
    reg [BA_W-1:0]     bram_addr = 0;
    reg [DATA_W-1:0]   bram_wrdata = 0;
    wire [DATA_W-1:0]  bram_rddata;
    wire [DATA_W-1:0]  tdata;
    wire               tvalid;
    reg                tready = 1'b1;
    wire [2:0]  state;
    wire [7:0]  result;
    wire [31:0] seq;
    wire [63:0] actual_tick;
    wire        fault;

    p3_tx_player #(
        .DATA_W(DATA_W), .ADDR_W(ADDR_W), .DEPTH(DEPTH), .BANK_ADDR_W(BANK_W),
        .BRAM_ADDR_W(BA_W), .MIN_LEAD_TICKS(64'd16)
    ) dut (
        .clk(clk), .rst(rst), .raw_tick(raw_tick), .time_valid(time_valid),
        .doorbell(doorbell), .cancel(cancel), .clear_fault(clear_fault),
        .cmd_target(cmd_target), .cmd_length(cmd_length), .cmd_repeat(cmd_repeat),
        .bram_en(bram_en), .bram_we(bram_we), .bram_addr(bram_addr),
        .bram_wrdata(bram_wrdata), .bram_rddata(bram_rddata),
        .m_axis_tdata(tdata), .m_axis_tvalid(tvalid), .m_axis_tready(tready),
        .state(state), .result(result), .seq(seq), .actual_tick(actual_tick),
        .fault(fault)
    );

    initial begin
        #1_000_000;  // the TX_REPEAT_MAX case alone takes 65535 clocks
        $display("FAIL tb_p3_tx_player (timeout)");
        $finish;
    end

    integer errors = 0;
    task check(input cond, input [8*64-1:0] msg);
        if (!cond) begin
            errors = errors + 1;
            $display("FAIL: %0s (t=%0t)", msg, $time);
        end
    endtask

    function [DATA_W-1:0] beat(input integer i);
        beat = {8{32'hB000_0000 + i}};
    endfunction

    task bram_write(input integer i, input [DATA_W-1:0] d);
        begin
            @(negedge clk);
            bram_en = 1; bram_we = {(DATA_W/8){1'b1}}; bram_addr = i << 5; bram_wrdata = d;
            @(negedge clk);
            bram_en = 0; bram_we = 0;
        end
    endtask

    task submit(input [63:0] target, input [31:0] len);
        begin
            @(negedge clk);
            cmd_target = target; cmd_length = len; doorbell = 1;
            @(negedge clk);
            doorbell = 0;
        end
    endtask

    task pulse_clear;
        begin
            @(negedge clk); clear_fault = 1; @(negedge clk); clear_fault = 0;
        end
    endtask

    // Check the output beat by beat around the burst window: passes x len
    // beats, pass after pass without a gap.
    task expect_passes(input [63:0] target, input integer len, input integer passes);
        integer k;
        begin
            while (raw_tick != target - 2) @(negedge clk);
            check(tdata == 0, "zero two ticks before target");
            @(negedge clk);
            check(tdata == 0, "zero one tick before target");
            for (k = 0; k < len * passes; k = k + 1) begin
                @(negedge clk);
                check(raw_tick == target + k, "tick alignment");
                check(tdata == beat(k % len), "burst beat content");
            end
            @(negedge clk);
            check(tdata == 0, "zero after burst");
        end
    endtask

    task expect_burst(input [63:0] target, input integer len);
        expect_passes(target, len, 1);
    endtask

    integer i;
    reg [63:0] tgt;
    initial begin
        repeat (4) @(posedge clk);
        rst = 1'b0;
        repeat (2) @(negedge clk);
        check(tvalid === 1'b1, "tvalid held high");
        check(tdata == 0, "idle output is zero");

        for (i = 0; i < DEPTH; i = i + 1) bram_write(i, beat(i));

        // Port A readback (latency 1), from the first and the last bank.
        @(negedge clk); bram_en = 1; bram_we = 0; bram_addr = 3 << 5;
        @(negedge clk); bram_en = 0;
        check(bram_rddata == beat(3), "port A readback");
        @(negedge clk); bram_en = 1; bram_we = 0; bram_addr = 9 << 5;
        @(negedge clk); bram_en = 0;
        check(bram_rddata == beat(9), "port A readback, last bank");
        // A write above DEPTH reaches no bank.
        bram_write(DEPTH, {DATA_W{1'b1}});
        @(negedge clk); bram_en = 1; bram_we = 0; bram_addr = 0;
        @(negedge clk); bram_en = 0;
        check(bram_rddata == beat(0), "write above DEPTH dropped");

        // Normal burst.
        tgt = raw_tick + 40;
        submit(tgt, 5);
        check(state == ST_ARMED && result == RC_ARMED, "armed");
        expect_burst(tgt, 5);
        @(negedge clk);
        check(state == ST_DONE && result == RC_DONE, "done");
        check(seq == 1, "seq incremented");
        check(actual_tick == tgt, "actual tick = target");

        // Full-depth burst back to back from DONE.
        tgt = raw_tick + 30;
        submit(tgt, DEPTH);
        expect_burst(tgt, DEPTH);
        @(negedge clk);
        check(state == ST_DONE && seq == 2, "full-depth burst done");

        // Rejects.
        submit(raw_tick + 3, 4);
        check(result == RC_REJ_STALE && state == ST_DONE, "stale target rejected");
        submit(raw_tick + 100, 0);
        check(result == RC_REJ_LENGTH, "zero length rejected");
        submit(raw_tick + 100, DEPTH + 1);
        check(result == RC_REJ_LENGTH, "over-depth rejected");
        time_valid = 0;
        submit(raw_tick + 100, 4);
        check(result == RC_REJ_TIME_INVALID, "invalid time rejected");
        time_valid = 1;

        // Busy and cancel.
        tgt = raw_tick + 100;
        submit(tgt, 4);
        submit(tgt + 50, 4);
        check(result == RC_REJ_BUSY && state == ST_ARMED, "busy doorbell rejected");
        @(negedge clk); cancel = 1; @(negedge clk); cancel = 0;
        check(state == ST_IDLE && result == RC_CANCELLED, "cancel from ARMED");
        repeat (120) @(negedge clk);
        check(tdata == 0, "cancelled burst never plays");

        // FAULT: buffer write while ARMED.
        submit(raw_tick + 100, 4);
        bram_write(0, beat(0));
        check(state == ST_FAULT && result == RC_FAULT_BUF_WRITE && fault, "buffer write fault");
        submit(raw_tick + 100, 4);
        check(state == ST_FAULT && result == RC_FAULT_BUF_WRITE, "doorbell ignored in FAULT");
        pulse_clear;
        check(state == ST_IDLE && !fault, "clear_fault -> IDLE");

        // FAULT: tready drop in the middle of a burst; output goes to zero.
        tgt = raw_tick + 40;
        submit(tgt, 8);
        while (raw_tick != tgt + 3) @(negedge clk);
        tready = 0;
        @(negedge clk);
        tready = 1;
        check(state == ST_FAULT && result == RC_FAULT_TREADY, "tready drop is a fault");
        repeat (3) @(negedge clk);
        check(tdata == 0, "output zero after fault");
        pulse_clear;

        // FAULT: time_valid lost while running.
        tgt = raw_tick + 40;
        submit(tgt, 8);
        while (raw_tick != tgt + 2) @(negedge clk);
        time_valid = 0;
        @(negedge clk);
        time_valid = 1;
        check(state == ST_FAULT && result == RC_FAULT_TIME, "time loss is a fault");
        pulse_clear;
        check(seq == 2, "faulted bursts do not count as done");

        // Repeated bursts: passes back to back, one DONE, TX_ACTUAL = first pass.
        cmd_repeat = 3;
        tgt = raw_tick + 30;
        submit(tgt, 5);
        expect_passes(tgt, 5, 3);
        @(negedge clk);
        check(state == ST_DONE && seq == 3 && actual_tick == tgt, "repeated burst done once");
        cmd_repeat = 2;
        tgt = raw_tick + 30;
        submit(tgt, DEPTH);
        expect_passes(tgt, DEPTH, 2);  // wraps from the last bank to the first
        @(negedge clk);
        check(state == ST_DONE && seq == 4, "repeated full-depth burst done");
        cmd_repeat = TX_REPEAT_MAX;
        tgt = raw_tick + 30;
        submit(tgt, 1);
        expect_passes(tgt, 1, TX_REPEAT_MAX);  // the whole pass counter
        @(negedge clk);
        check(state == ST_DONE && seq == 5, "maximum repeat done");
        cmd_repeat = 0;
        submit(raw_tick + 100, 4);
        check(result == RC_REJ_LENGTH, "zero repeat rejected");
        cmd_repeat = TX_REPEAT_MAX + 1;
        submit(raw_tick + 100, 4);
        check(result == RC_REJ_LENGTH, "repeat above TX_REPEAT_MAX rejected");
        cmd_repeat = 1;

        if (errors == 0) $display("PASS tb_p3_tx_player");
        else             $display("FAIL tb_p3_tx_player (%0d errors)", errors);
        $finish;
    end
endmodule
