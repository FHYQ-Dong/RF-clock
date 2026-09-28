`timescale 1ns/1ps

module tb_H03_raw_tick;
    reg clk;
    reg rst;
    wire [63:0] raw_tick_out;
    wire exhausted;

    H03_raw_tick dut (
        .clk(clk),
        .rst(rst),
        .raw_tick_out(raw_tick_out),
        .exhausted(exhausted)
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

    initial begin
        clk = 1'b0;
        rst = 1'b1;

        repeat (2) @(posedge clk);
        #1;
        check(raw_tick_out == 64'd0 && !exhausted,
               "reset must clear raw_tick and exhausted");

        rst = 1'b0;
        @(posedge clk);
        #1;
        check(raw_tick_out == 64'd1 && !exhausted,
               "raw_tick must increment once per clk");

        @(posedge clk);
        #1;
        check(raw_tick_out == 64'd2 && !exhausted,
               "raw_tick must continue incrementing");

        @(negedge clk);
        dut.raw_tick_out = 64'hFFFFFFFFFFFFFFFE;
        dut.exhausted = 1'b0;

        @(posedge clk);
        #1;
        check(raw_tick_out == 64'hFFFFFFFFFFFFFFFF && !exhausted,
               "counter must reach all ones before exhaustion");

        @(posedge clk);
        #1;
        check(raw_tick_out == 64'hFFFFFFFFFFFFFFFF && exhausted,
               "counter must report exhaustion at all ones");

        @(posedge clk);
        #1;
        check(raw_tick_out == 64'hFFFFFFFFFFFFFFFF && exhausted,
               "exhausted counter must remain saturated");

        rst = 1'b1;
        @(posedge clk);
        #1;
        check(raw_tick_out == 64'd0 && !exhausted,
               "reset must restart the raw timebase");

        $display("PASS: tb_H03_raw_tick");
        $finish;
    end
endmodule
