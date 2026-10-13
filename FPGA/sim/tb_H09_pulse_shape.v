`timescale 1ns/1ps

`include "src/H09_protocol_defs.vh"

// Directed H09 testbench.
//
// The testbench presents a continuous, deterministic PRN chip stream and
// keeps sample_ready asserted.  Every accepted output lane is written to a
// CSV file so that it can be compared with utils/sim_h09_rrc_fir.py.  Output
// file names can be overridden with +SAMPLE_FILE=... and +BEAT_FILE=... .

module tb_H09_pulse_shape;
    // Keep this aligned with utils/sim_h09_rrc_fir.py.  The run can be
    // shortened for a quick smoke test with +CHIP_COUNT=<n>.
    parameter integer CHIP_COUNT = 4096;
    parameter [15:0] LFSR_SEED = 16'hACE1;
    localparam integer SAMPLES_PER_CHIP = 8;
    localparam integer TAIL_SAMPLES = 73; // 81 taps - 8 samples/chip

    reg clk;
    reg rst;
    reg pulse_enable;
    reg clear_fault;

    reg chip_valid;
    wire chip_ready;
    reg prn_chip;
    reg chip_last;

    wire sample_valid;
    reg sample_ready;
    wire [127:0] sample_I;
    wire [127:0] sample_Q;
    wire [7:0] sample_mask;
    wire sample_first;
    wire sample_last;
    wire [31:0] sample_index;

    wire pulse_busy;
    wire pulse_fault;
    wire [3:0] pulse_return_code;

    integer failures;
    integer accepted_chips;
    integer captured_samples;
    integer captured_beats;
    integer lane;
    integer sample_value;
    integer sample_q_value;
    integer expected_value_index;
    integer simulation_cycle;
    integer sample_fd;
    integer beat_fd;
    integer input_fd;
    integer plusarg_result;
    integer done;
    integer timeout_cycles;
    integer chip_count;
    integer expected_sample_count;
    reg [15:0] lfsr_seed;
    // Icarus requires a SystemVerilog string for a variable $fopen path;
    // packed reg vectors are treated as VPI registers, not file-name strings.
    string sample_file_name;
    string beat_file_name;
    string input_file_name;

    H09_pulse_shape dut (
        .clk(clk),
        .rst(rst),
        .pulse_enable(pulse_enable),
        .clear_fault(clear_fault),
        .chip_valid(chip_valid),
        .chip_ready(chip_ready),
        .prn_chip(prn_chip),
        .chip_last(chip_last),
        .sample_valid(sample_valid),
        .sample_ready(sample_ready),
        .sample_I(sample_I),
        .sample_Q(sample_Q),
        .sample_mask(sample_mask),
        .sample_first(sample_first),
        .sample_last(sample_last),
        .sample_index(sample_index),
        .pulse_busy(pulse_busy),
        .pulse_fault(pulse_fault),
        .pulse_return_code(pulse_return_code)
    );

    always #5 clk = ~clk;

    // This is the H08 16-bit right-shift LFSR: output old state bit 0 and
    // advance only when a chip is accepted by the DUT.
    function [15:0] lfsr_next;
        input [15:0] state;
        begin
            lfsr_next = {
                state[0] ^ state[2] ^ state[3] ^ state[5],
                state[15:1]
            };
        end
    endfunction

    task check;
        input condition;
        input [8*180-1:0] message;
        begin
            if (condition !== 1'b1) begin
                $display("FAIL @%0t: %0s", $time, message);
                failures = failures + 1;
            end
        end
    endtask

    // Capture before the DUT's nonblocking state updates.  At a rising edge,
    // these are the values that were held stable during the just-finished
    // output beat and are being consumed by sample_ready.
    always @(posedge clk) begin
        simulation_cycle = simulation_cycle + 1;
        if (!rst && sample_valid && sample_ready) begin
            captured_beats = captured_beats + 1;
            $fwrite(
                beat_fd,
                "%0d,%0d,%0d,%0d,%0d\n",
                captured_beats - 1,
                sample_index,
                sample_mask,
                sample_first,
                sample_last
            );

            if (sample_first && (captured_samples != 0))
                check(1'b0, "sample_first asserted after the first output sample");
            if ((captured_samples == 0) && !sample_first)
                check(1'b0, "first output beat did not assert sample_first");

            for (lane = 0; lane < SAMPLES_PER_CHIP; lane = lane + 1) begin
                if (sample_mask[lane]) begin
                    sample_value = $signed(sample_I[lane*16 +: 16]);
                    sample_q_value = $signed(sample_Q[lane*16 +: 16]);
                    expected_value_index = sample_index + lane;
                    check(
                        expected_value_index == captured_samples,
                        "sample_index/sample_mask contains a gap or duplicate"
                    );
                    $fwrite(
                        sample_fd,
                        "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",
                        captured_beats - 1,
                        simulation_cycle,
                        expected_value_index,
                        lane,
                        sample_value,
                        sample_q_value,
                        sample_mask,
                        sample_first,
                        sample_last
                    );
                    captured_samples = captured_samples + 1;
                end
            end

            if (sample_last) begin
                check(
                    sample_mask == 8'b0000_0001,
                    "final tail beat must contain exactly one valid lane"
                );
                done = 1;
            end
        end

        if (!rst && pulse_fault)
            check(1'b0, "H09 entered FAULT during a continuous ready stream");
    end

    // Drive a chip only at the falling edge, then hold it until a rising edge
    // accepts it.  This models H08's valid/ready stream without introducing
    // bubbles between chips.
    task send_prn_frame;
        integer chip_number;
        reg bit_value;
        reg [15:0] lfsr_state;
        begin
            @(negedge clk);
            chip_number = 0;
            lfsr_state = lfsr_seed;
            bit_value = lfsr_state[0];
            prn_chip = bit_value;
            chip_last = (chip_number == chip_count - 1);
            chip_valid = 1'b1;

            while (chip_number < chip_count) begin
                @(posedge clk);
                if (chip_ready && chip_valid) begin
                    $fwrite(
                        input_fd,
                        "%0d,%0d,%0d\n",
                        chip_number,
                        prn_chip,
                        chip_last
                    );
                    accepted_chips = accepted_chips + 1;
                    chip_number = chip_number + 1;
                    if (chip_number < chip_count) begin
                        @(negedge clk);
                        lfsr_state = lfsr_next(lfsr_state);
                        bit_value = lfsr_state[0];
                        prn_chip = bit_value;
                        chip_last = (chip_number == chip_count - 1);
                    end
                end
            end

            @(negedge clk);
            chip_valid = 1'b0;
            prn_chip = 1'b0;
            chip_last = 1'b0;
        end
    endtask

    initial begin
        clk = 1'b0;
        rst = 1'b1;
        pulse_enable = 1'b1;
        clear_fault = 1'b0;
        chip_valid = 1'b0;
        prn_chip = 1'b0;
        chip_last = 1'b0;
        sample_ready = 1'b1;
        failures = 0;
        accepted_chips = 0;
        captured_samples = 0;
        captured_beats = 0;
        simulation_cycle = 0;
        done = 0;

        chip_count = CHIP_COUNT;
        lfsr_seed = LFSR_SEED;
        plusarg_result = $value$plusargs("CHIP_COUNT=%d", chip_count);
        plusarg_result = $value$plusargs("LFSR_SEED=%h", lfsr_seed);
        if (chip_count <= 0)
            $fatal(1, "CHIP_COUNT must be positive");
        if (lfsr_seed == 16'b0)
            $fatal(1, "LFSR_SEED must be non-zero");
        expected_sample_count =
            chip_count * SAMPLES_PER_CHIP + TAIL_SAMPLES;

        sample_file_name = "h09_rtl_samples.csv";
        beat_file_name = "h09_rtl_beats.csv";
        input_file_name = "h09_rtl_input.csv";
        plusarg_result = $value$plusargs("SAMPLE_FILE=%s", sample_file_name);
        plusarg_result = $value$plusargs("BEAT_FILE=%s", beat_file_name);
        plusarg_result = $value$plusargs("INPUT_FILE=%s", input_file_name);

        sample_fd = $fopen(sample_file_name, "w");
        beat_fd = $fopen(beat_file_name, "w");
        input_fd = $fopen(input_file_name, "w");
        if (!sample_fd || !beat_fd || !input_fd) begin
            $display("FAIL: cannot open one or more H09 output files");
            $fatal(1, "H09 output file open failed");
        end
        $fwrite(
            sample_fd,
            "beat,cycle,sample_index,lane,sample_I,sample_Q,sample_mask,sample_first,sample_last\n"
        );
        $fwrite(beat_fd, "beat_index,sample_index,sample_mask,sample_first,sample_last\n");
        $fwrite(input_fd, "chip_index,prn_chip,chip_last\n");

        repeat (3) @(posedge clk);
        @(negedge clk);
        rst = 1'b0;

        send_prn_frame;

        timeout_cycles = 0;
        while (!done && timeout_cycles < 1000) begin
            @(posedge clk);
            timeout_cycles = timeout_cycles + 1;
        end
        check(done, "H09 produces a final sample_last before the timeout");
        repeat (2) @(posedge clk);
        #1;

        check(accepted_chips == chip_count, "all PRN chips were accepted");
        check(
            captured_samples == expected_sample_count,
            "captured sample count matches chips plus the RRC tail"
        );
        check(captured_beats == chip_count + 10, "captured beat count is correct");
        check(!pulse_busy && !pulse_fault, "H09 returns to idle after the tail");
        check(
            pulse_return_code == `H09_RETURN_CODE_SUCCESS,
            "H09 returns SUCCESS after the tail"
        );

        $fclose(sample_fd);
        $fclose(beat_fd);
        $fclose(input_fd);

        if (failures == 0) begin
            $display("PASS: tb_H09_pulse_shape");
            $display("  input : %0s", input_file_name);
            $display("  beats : %0s", beat_file_name);
            $display("  samples: %0s", sample_file_name);
        end
        else begin
            $display("FAIL: tb_H09_pulse_shape failures=%0d", failures);
            $fatal(1, "tb_H09_pulse_shape failed");
        end
        $finish;
    end
endmodule
