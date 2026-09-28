`timescale 1ns/1ps

`include "../src/H05_protocol_defs.vh"

module tb_H05_scheduled_execute;
    reg clk;
    reg rst;
    reg [63:0] raw_tick;
    reg cmd_valid;
    wire cmd_ready;
    reg [31:0] cmd_id;
    reg [63:0] cmd_target_tick;
    reg [31:0] cmd_control_value;
    reg schedule_enable;
    reg raw_tick_exhausted;
    reg clear_fault;

    wire cmd_accepted;
    wire cmd_executed;
    wire [`H05_RETURN_CODE_WIDTH-1:0] cmd_return_code;
    wire cmd_waiting_for_execute;
    wire cmd_fault;
    wire apply_now;
    wire [31:0] active_control_value;

    integer failures;
    reg [63:0] first_target;
    reg apply_seen;

    H05_scheduled_execute #(
        .ARRIVE_TICKS_BEFORE_EXECUTE(4)
    ) dut (
        .clk(clk),
        .rst(rst),
        .raw_tick(raw_tick),
        .cmd_valid(cmd_valid),
        .cmd_ready(cmd_ready),
        .cmd_id(cmd_id),
        .cmd_target_tick(cmd_target_tick),
        .cmd_control_value(cmd_control_value),
        .schedule_enable(schedule_enable),
        .raw_tick_exhausted(raw_tick_exhausted),
        .clear_fault(clear_fault),
        .cmd_accepted(cmd_accepted),
        .cmd_executed(cmd_executed),
        .cmd_return_code(cmd_return_code),
        .cmd_waiting_for_execute(cmd_waiting_for_execute),
        .cmd_fault(cmd_fault),
        .apply_now(apply_now),
        .active_control_value(active_control_value)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (rst)
            raw_tick <= 64'd0;
        else
            raw_tick <= raw_tick + 64'd1;
        if (apply_now)
            apply_seen <= 1'b1;
    end

    task check;
        input condition;
        input [8*120-1:0] message;
        begin
            if (!condition) begin
                $display("FAIL @%0t: %0s", $time, message);
                failures = failures + 1;
            end
        end
    endtask

    task send_command;
        input [31:0] id;
        input [63:0] target;
        input [31:0] value;
        begin
            @(negedge clk);
            cmd_id = id;
            cmd_target_tick = target;
            cmd_control_value = value;
            cmd_valid = 1'b1;
            while (!cmd_ready)
                @(posedge clk);
            @(posedge clk);
            #1;
            cmd_valid = 1'b0;
        end
    endtask

    initial begin
        clk = 1'b0;
        rst = 1'b1;
        raw_tick = 64'd0;
        cmd_valid = 1'b0;
        cmd_id = 32'd0;
        cmd_target_tick = 64'd0;
        cmd_control_value = 32'd0;
        schedule_enable = 1'b1;
        raw_tick_exhausted = 1'b0;
        clear_fault = 1'b0;
        failures = 0;
        apply_seen = 1'b0;

        repeat (2) @(posedge clk);
        #1;
        rst = 1'b0;
        repeat (3) @(posedge clk);
        #1;
        check(cmd_ready && !cmd_waiting_for_execute && !cmd_fault &&
              cmd_return_code == `H05_RETURN_CODE_IDLE,
              "reset must leave a healthy idle scheduler");

        // Valid command: fields must be latched at acceptance and remain
        // stable until the target edge.
        first_target = raw_tick + 64'd12;
        send_command(32'd1, first_target, 32'h1234_5678);
        #1;
        check(cmd_accepted && cmd_waiting_for_execute && !cmd_ready &&
              cmd_return_code == `H05_RETURN_CODE_PENDING &&
              active_control_value == 32'h1234_5678,
              "valid command acceptance and control-value latch");

        while (raw_tick < first_target)
            @(posedge clk);
        #1;
        check(apply_seen && cmd_executed && !cmd_waiting_for_execute &&
              cmd_return_code == `H05_RETURN_CODE_SUCCESS &&
              active_control_value == 32'h1234_5678,
              "apply_now at target tick");
        @(posedge clk);
        #1;
        check(!cmd_waiting_for_execute && cmd_ready,
              "scheduler returns to idle after apply_now");

        // Invalid ID and late target are rejected without poisoning the idle
        // scheduler.
        send_command(32'd0, raw_tick + 64'd20, 32'h0);
        #1;
        check(!cmd_accepted &&
              cmd_return_code == `H05_RETURN_CODE_ID_INVALID && cmd_ready,
              "zero command ID is rejected");
        send_command(32'd2, raw_tick, 32'h0);
        #1;
        check(!cmd_accepted &&
              cmd_return_code == `H05_RETURN_CODE_CMD_MISSED && cmd_ready,
              "past target tick is rejected");

        // A disabled scheduling policy backpressures H02 through cmd_ready.
        schedule_enable = 1'b0;
        @(negedge clk);
        cmd_id = 32'd3;
        cmd_target_tick = raw_tick + 64'd12;
        cmd_control_value = 32'hcafebabe;
        cmd_valid = 1'b1;
        repeat (2) @(posedge clk);
        #1;
        check(!cmd_ready && !cmd_accepted,
              "disabled scheduling policy blocks acceptance");
        schedule_enable = 1'b1;
        while (!cmd_ready)
            @(posedge clk);
        @(posedge clk);
        #1;
        cmd_valid = 1'b0;
        check(cmd_waiting_for_execute && !cmd_ready &&
              active_control_value == 32'hcafebabe,
              "held valid command transfers after cmd_ready returns");

        // Losing the scheduling authority while waiting creates a sticky
        // fault, which prevents a new command until explicit recovery.
        schedule_enable = 1'b0;
        @(posedge clk);
        #1;
        check(cmd_fault && !cmd_ready && !apply_now &&
              cmd_return_code == `H05_RETURN_CODE_TIME_INVALID,
              "time-validity loss enters sticky fault");
        schedule_enable = 1'b1;
        repeat (2) @(posedge clk);
        #1;
        check(cmd_fault && !cmd_ready,
              "fault remains latched until clear_fault");
        clear_fault = 1'b1;
        @(posedge clk);
        #1;
        clear_fault = 1'b0;
        check(!cmd_fault && cmd_ready &&
              cmd_return_code == `H05_RETURN_CODE_IDLE,
              "clear_fault restores idle acceptance");

        if (failures == 0)
            $display("PASS: tb_H05_scheduled_execute");
        else
            $display("FAIL: tb_H05_scheduled_execute failures=%0d", failures);
        $finish;
    end
endmodule
