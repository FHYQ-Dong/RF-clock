`timescale 1ns / 1ps
// P12 through its registers, the transmit line looped back to receive pin A
// (pin B held high, as the empty Pmod hole is): an ARM line comes back byte for
// byte; a P13 command starts on its tick, and on time while an ARM line waits;
// an ARM line that would run into the next reserved command waits until after
// it; a command due while a line is on the wire starts late, right after it; the
// bit timing on the wire; a break counts as a framing error.  20 ticks a bit.
module tb_p12_serial;
    localparam integer BT = 20;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg [63:0] raw_tick = 64'd0;
    always @(posedge clk) raw_tick <= raw_tick + 64'd1;

    reg         reg_wr = 1'b0, reg_rd = 1'b0;
    reg  [11:0] reg_waddr = 12'd0, reg_waddr_live = 12'd0, reg_raddr = 12'd0, reg_rd_addr = 12'd0;
    reg  [31:0] reg_wdata = 32'd0, reg_wdata_live = 32'd0;
    wire        reg_wr_ok, reg_rd_ok;
    wire [31:0] reg_rdata;
    reg         cmd_valid = 1'b0, reserve_valid = 1'b0;
    reg  [63:0] cmd_tick = 64'd0, reserve_tick = 64'd0;
    reg  [4:0]  cmd_len = 5'd0;
    reg  [127:0] cmd_bytes = 128'd0;
    wire        cmd_pending, cmd_started, cmd_late;
    wire [63:0] cmd_start_tick;
    wire        txd;
    reg         rxd_b = 1'b1;
    reg         brk = 1'b0;

    p12_regs dut (
        .clk (clk), .rst (rst), .raw_tick (raw_tick),
        .reg_wr (reg_wr), .reg_waddr (reg_waddr), .reg_wdata (reg_wdata),
        .reg_waddr_live (reg_waddr_live), .reg_wdata_live (reg_wdata_live), .reg_wr_ok (reg_wr_ok),
        .reg_raddr (reg_raddr), .reg_rdata (reg_rdata), .reg_rd_ok (reg_rd_ok),
        .reg_rd (reg_rd), .reg_rd_addr (reg_rd_addr),
        .cmd_valid (cmd_valid), .cmd_tick (cmd_tick), .cmd_len (cmd_len), .cmd_bytes (cmd_bytes),
        .reserve_valid (reserve_valid), .reserve_tick (reserve_tick),
        .cmd_pending (cmd_pending), .cmd_started (cmd_started), .cmd_late (cmd_late),
        .cmd_start_tick (cmd_start_tick),
        .txd (txd), .rxd_a (txd & !brk), .rxd_b (rxd_b)
    );

    integer errors = 0;
    integer i, n;
    reg [31:0] v;
    reg [7:0] got [0:63];

    task wr(input [11:0] a, input [31:0] d);
        begin
            @(negedge clk);
            reg_waddr_live = a; reg_wdata_live = d;
            #1;
            if (!reg_wr_ok) begin
                $display("FAIL: write %h to %h refused", d, a);
                errors = errors + 1;
            end
            @(negedge clk);
            reg_wr = 1'b1; reg_waddr = a; reg_wdata = d;
            @(negedge clk);
            reg_wr = 1'b0;
        end
    endtask

    task rd(input [11:0] a, output [31:0] d);
        begin
            @(negedge clk);
            reg_raddr = a;
            #1;
            d = reg_rdata;
            @(negedge clk);
            reg_rd = 1'b1; reg_rd_addr = a;
            @(negedge clk);
            reg_rd = 1'b0;
        end
    endtask

    task send_line(input [8*16-1:0] text, input integer len);
        integer j;
        begin
            for (j = 0; j < len; j = j + 1) wr(12'h40C, {24'd0, text[8*j +: 8]});
            wr(12'h400, 32'd1);
        end
    endtask

    task drain_rx(output integer count);
        begin
            count = 0;
            rd(12'h410, v);
            while (v[8]) begin
                got[count] = v[7:0];
                count = count + 1;
                rd(12'h410, v);
            end
        end
    endtask

    // the wire: each frame's start tick and the bit-time check
    reg [63:0] fall_tick;
    reg        prev_txd = 1'b1;
    always @(posedge clk) prev_txd <= txd;

    task wait_idle;
        begin
            @(negedge clk);
            while (dut.u_serial.tx_active || dut.u_serial.line_q || cmd_pending) @(negedge clk);
            repeat (3 * BT) @(negedge clk);
        end
    endtask

    reg [63:0] t0;
    initial begin
        repeat (5) @(posedge clk);
        rst = 1'b0;
        wr(12'h404, 32'h0001_0000 | BT);          // ENABLE, 20 ticks a bit
        // 1. an ARM line comes back
        send_line("\015?DI", 4);                     // "ID?\r", byte 0 in the low bits
        wait_idle;
        drain_rx(n);
        if (n != 4 || got[0] != "I" || got[1] != "D" || got[2] != "?" || got[3] != 8'h0D) begin
            $display("FAIL: line came back as %0d bytes %h %h %h %h", n, got[0], got[1], got[2], got[3]);
            errors = errors + 1;
        end
        // 2. a command starts on its tick, while an ARM line waits behind it
        t0 = raw_tick + 300;
        @(negedge clk);
        cmd_tick = t0; cmd_len = 5'd12; cmd_bytes = {32'd0, "\0151002,5761CF"};
        cmd_valid = 1'b1;
        @(negedge clk);
        cmd_valid = 1'b0;
        send_line("\015?CF", 4);
        @(posedge cmd_started);
        if (cmd_start_tick != t0 || cmd_late) begin
            $display("FAIL: command started at %0d (late %b), want %0d", cmd_start_tick, cmd_late, t0);
            errors = errors + 1;
        end
        // the start bit falls at the same clock edge as cmd_started rises
        #1;
        fall_tick = raw_tick;
        if (txd !== 1'b0 || fall_tick != t0 + 1) begin
            $display("FAIL: the start bit fell at %0d, want %0d", fall_tick, t0 + 1);
            errors = errors + 1;
        end
        // the first data bit (F = 0x46: bit 0 is 0, bit 1 is 1) changes BT ticks later
        @(posedge txd);
        if (raw_tick != fall_tick + 2 * BT) begin
            $display("FAIL: first rising edge at %0d, want %0d", raw_tick, fall_tick + 2 * BT);
            errors = errors + 1;
        end
        wait_idle;
        drain_rx(n);
        if (n != 16 || got[0] != "F" || got[6] != "," || got[10] != "1" || got[11] != 8'h0D || got[12] != "F"
            || got[15] != 8'h0D) begin
            $display("FAIL: command and line came back as %0d bytes", n);
            errors = errors + 1;
        end
        // 3. a line that would run into the reserved command waits until after it
        t0 = raw_tick + 600;                        // a 4-byte line takes 800 ticks
        reserve_tick = t0; reserve_valid = 1'b1;
        send_line("\015?TS", 4);
        repeat (100) @(negedge clk);
        if (dut.u_serial.tx_active) begin
            $display("FAIL: the line started although it would run into the reserved command");
            errors = errors + 1;
        end
        @(negedge clk);
        cmd_tick = t0; cmd_len = 5'd4; cmd_bytes = {96'd0, "\015?CF"};
        cmd_valid = 1'b1;
        @(negedge clk);
        cmd_valid = 1'b0;
        @(posedge cmd_started);
        if (cmd_start_tick != t0 || cmd_late) begin
            $display("FAIL: reserved command started at %0d late %b, want %0d", cmd_start_tick, cmd_late, t0);
            errors = errors + 1;
        end
        reserve_tick = t0 + 100000;
        wait_idle;
        drain_rx(n);
        if (n != 8 || got[0] != "F" || got[4] != "S") begin
            $display("FAIL: reserved order came back as %0d bytes, first %h, fifth %h", n, got[0], got[4]);
            errors = errors + 1;
        end
        reserve_valid = 1'b0;
        // 4. a command due while a line is on the wire starts late, right after it
        send_line("\015?TS", 4);
        repeat (50) @(negedge clk);
        t0 = raw_tick + 10;
        cmd_tick = t0; cmd_len = 5'd4; cmd_bytes = {96'd0, "\015?CF"};
        cmd_valid = 1'b1;
        @(negedge clk);
        cmd_valid = 1'b0;
        @(posedge cmd_started);
        if (!cmd_late || cmd_start_tick <= t0) begin
            $display("FAIL: a command behind a line was not late (%0d, due %0d)", cmd_start_tick, t0);
            errors = errors + 1;
        end
        wait_idle;
        drain_rx(n);
        // 5. a break counts as a framing error, a full ten bits low
        brk = 1'b1;
        repeat (15 * BT) @(negedge clk);
        brk = 1'b0;
        repeat (15 * BT) @(negedge clk);
        rd(12'h420, v);
        if (v[15:0] == 16'd0) begin
            $display("FAIL: no framing error counted for a break");
            errors = errors + 1;
        end
        rd(12'h424, v);
        if (v[7:0] == 8'd0 || v[15:8] != 8'd0) begin
            $display("FAIL: edge counts A %0d B %0d", v[7:0], v[15:8]);
            errors = errors + 1;
        end
        rd(12'h414, v);
        if (v != 32'd4) begin
            $display("FAIL: %0d lines sent, want 4", v);
            errors = errors + 1;
        end
        rd(12'h418, v);
        if (v != 32'd3) begin
            $display("FAIL: %0d commands sent, want 3", v);
            errors = errors + 1;
        end
        if (errors == 0) $display("PASS tb_p12_serial");
        else $display("FAIL: %0d errors", errors);
        $finish;
    end
endmodule
