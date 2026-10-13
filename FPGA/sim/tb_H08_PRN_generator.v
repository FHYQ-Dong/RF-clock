`timescale 1ns/1ps

`include "src/H08_protocol_defs.vh"

module tb_H08_PRN_generator;
    reg clk;
    reg rst;
    reg prn_enable;
    reg clear_fault;

    reg frame_valid;
    wire frame_ready;
    wire frame_accepted;
    reg [31:0] frame_id;
    reg [15:0] frame_seed;
    reg [31:0] frame_len;

    wire chip_valid;
    reg chip_ready;
    wire prn_chip;
    wire [31:0] chip_index;
    wire chip_last;

    wire frame_idle;
    wire frame_generating;
    wire frame_fault;
    wire [`H08_RETURN_CODE_WIDTH-1:0] frame_return_code;
    wire [31:0] active_frame_id;

    integer failures;
    integer i;
    reg [15:0] expected_lfsr;

    H08_PRN_generator dut (
        .clk(clk),
        .rst(rst),
        .prn_enable(prn_enable),
        .clear_fault(clear_fault),
        .frame_valid(frame_valid),
        .frame_ready(frame_ready),
        .frame_accepted(frame_accepted),
        .frame_id(frame_id),
        .frame_seed(frame_seed),
        .frame_len(frame_len),
        .chip_valid(chip_valid),
        .chip_ready(chip_ready),
        .prn_chip(prn_chip),
        .chip_index(chip_index),
        .chip_last(chip_last),
        .frame_idle(frame_idle),
        .frame_generating(frame_generating),
        .frame_fault(frame_fault),
        .frame_return_code(frame_return_code),
        .active_frame_id(active_frame_id)
    );

    always #5 clk = ~clk;

    function [15:0] lfsr_next;
        input [15:0] current;
        begin
            lfsr_next = {
                current[0] ^ current[2] ^ current[3] ^ current[5],
                current[15:1]
            };
        end
    endfunction

    task check;
        input condition;
        input [8*160-1:0] message;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL @%0t: %0s", $time, message);
                failures = failures + 1;
            end
        end
    endtask

    task send_frame;
        input [31:0] id;
        input [15:0] seed;
        input [31:0] length;
        begin
            @(negedge clk);
            frame_id = id;
            frame_seed = seed;
            frame_len = length;
            frame_valid = 1'b1;
            while (!frame_ready)
                @(posedge clk);
            @(posedge clk);
            #1;
            frame_valid = 1'b0;
        end
    endtask

    initial begin
        clk = 1'b0;
        rst = 1'b1;
        prn_enable = 1'b1;
        clear_fault = 1'b0;
        frame_valid = 1'b0;
        frame_id = 32'b0;
        frame_seed = 16'b0;
        frame_len = 32'b0;
        chip_ready = 1'b1;
        failures = 0;
        expected_lfsr = 16'b0;

        repeat (2) @(posedge clk);
        #1;
        check(frame_idle && !frame_generating && !frame_fault &&
              !frame_ready && !frame_accepted &&
              frame_return_code == `H08_RETURN_CODE_IDLE,
              "reset leaves H08 in idle with a known IDLE result");

        rst = 1'b0;
        repeat (1) @(posedge clk);
        #1;

        send_frame(32'h0000_0001, 16'hACE1, 32'd0);
        check(!frame_accepted && frame_idle && frame_ready && !frame_fault &&
              frame_return_code == `H08_RETURN_CODE_FRAME_LENGTH_INVALID,
              "zero frame length is rejected while remaining idle");

        send_frame(32'h0000_0002, 16'h0000, 32'd4);
        check(!frame_accepted && frame_idle && frame_ready && !frame_fault &&
              frame_return_code == `H08_RETURN_CODE_FRAME_SEED_INVALID,
              "zero seed is rejected while remaining idle");

        send_frame(32'h1234_5678, 16'hACE1, 32'd4);
        expected_lfsr = 16'hACE1;
        check(frame_accepted && frame_generating && !frame_idle &&
              !frame_fault && frame_return_code == `H08_RETURN_CODE_PENDING &&
              active_frame_id == 32'h1234_5678 &&
              chip_valid && chip_index == 32'd0 &&
              !chip_last && prn_chip == expected_lfsr[0],
              "valid frame is accepted with PENDING status and chip zero");

        // frame_accepted is an acceptance pulse, not a level status.
        chip_ready = 1'b0;
        @(posedge clk);
        #1;
        check(!frame_accepted && chip_valid &&
              chip_index == 32'd0 && prn_chip == expected_lfsr[0] &&
              frame_return_code == `H08_RETURN_CODE_PENDING,
              "backpressure holds chip zero and clears frame_accepted");

        repeat (1) @(posedge clk);
        #1;
        check(chip_valid && chip_index == 32'd0 &&
              prn_chip == expected_lfsr[0] &&
              frame_return_code == `H08_RETURN_CODE_PENDING,
              "backpressure keeps the pending frame stable");

        chip_ready = 1'b1;
        for (i = 0; i < 4; i = i + 1) begin
            #1;
            check(chip_valid && chip_index == i &&
                  prn_chip == expected_lfsr[0] &&
                  chip_last == (i == 3) &&
                  frame_return_code == `H08_RETURN_CODE_PENDING,
                  "chip stream index, data, last flag, and PENDING status");
            @(posedge clk);
            #1;
            if (i < 3)
                expected_lfsr = lfsr_next(expected_lfsr);
        end

        check(frame_idle && !frame_generating && !frame_fault &&
              frame_ready && !chip_valid && !chip_last &&
              frame_return_code == `H08_RETURN_CODE_SUCCESS &&
              active_frame_id == 32'b0,
              "last chip returns to idle with SUCCESS result");

        if (failures == 0)
            $display("PASS: tb_H08_PRN_generator");
        else begin
            $display("FAIL: tb_H08_PRN_generator failures=%0d", failures);
            $fatal(1, "tb_H08_PRN_generator failed");
        end
        $finish;
    end
endmodule
