`timescale 1ns / 1ps
// p3_burst_gen against rf_clock.plmodel.fir.shape (vectors from gen_vectors.py):
// a burst of the PRN alone and one with a packet, every DAC sample to the bit,
// beat 0 in the cycle raw_tick == target, zeros outside the bursts; then a
// low tready during a burst enters FAULT, which clear_fault leaves.
module tb_p3_burst_gen;
    localparam integer AW = 11;
    localparam integer MAXS = 8 * (300 + 10 + 288 * 64 + 10);

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg [63:0] raw_tick = 64'd0;
    always @(posedge clk) raw_tick <= raw_tick + 64'd1;

    reg [31:0]  cfg [0:3];
    reg [31:0]  prn_words [0:9];
    reg [31:0]  code_words [0:575];
    reg [15:0]  tap_mem [0:80];
    reg [287:0] packet_mem [0:0];
    reg [15:0]  plain [0:8*310-1];
    reg [15:0]  full [0:MAXS-1];

    reg               arm = 1'b0;
    reg  [63:0]       target = 64'd0;
    reg               packets = 1'b0;
    reg  [31:0]       beats = 32'd0;
    reg  [3:0]        prn_we = 4'd0, code_we = 4'd0;
    reg  [AW-1:0]     waddr = {AW{1'b0}};
    reg  [31:0]       wdata = 32'd0;
    reg  [81*16-1:0]  taps;
    reg               tready = 1'b1;
    reg               clear_fault = 1'b0;
    wire [255:0]      tdata;
    wire              tvalid, busy, fault, started;
    wire [63:0]       actual_tick;

    p3_burst_gen #(.AW(AW)) dut (
        .clk (clk), .rst (rst), .raw_tick (raw_tick), .clear_fault (clear_fault),
        .arm (arm), .target (target), .prn_chips (cfg[0][AW+5:0]), .guard_chips (cfg[1][15:0]),
        .packets (packets), .beats (beats), .packet (packet_mem[0]),
        .prn_we (prn_we), .code_we (code_we), .mem_waddr (waddr), .mem_wdata (wdata), .taps (taps),
        .m_axis_tdata (tdata), .m_axis_tvalid (tvalid), .m_axis_tready (tready),
        .busy (busy), .fault (fault), .started (started), .actual_tick (actual_tick)
    );

    integer errors = 0;
    integer k, n, p;

    task check_burst(input integer count, input integer with_packet);
        integer bt;
        begin
            // the cycles before the target carry zeros
            while (raw_tick != target) begin
                if (tdata !== 256'd0) begin
                    errors = errors + 1;
                    $display("non-zero beat before the target at tick %0d", raw_tick);
                end
                @(posedge clk); #1;
            end
            for (bt = 0; bt < count; bt = bt + 1) begin
                for (p = 0; p < 8; p = p + 1) begin
                    if (tdata[32*p +: 16] !== (with_packet ? full[8*bt+p] : plain[8*bt+p]) ||
                        tdata[32*p+16 +: 16] !== 16'd0) begin
                        if (errors < 10)
                            $display("beat %0d sample %0d: %h, expected %h", bt, p, tdata[32*p +: 16],
                                     with_packet ? full[8*bt+p] : plain[8*bt+p]);
                        errors = errors + 1;
                    end
                end
                @(posedge clk); #1;
            end
            for (bt = 0; bt < 20; bt = bt + 1) begin
                if (tdata !== 256'd0) begin
                    errors = errors + 1;
                    $display("non-zero beat %0d after the burst", bt);
                end
                @(posedge clk); #1;
            end
            if (actual_tick !== target) begin
                errors = errors + 1;
                $display("actual_tick %0d, target %0d", actual_tick, target);
            end
        end
    endtask

    initial begin
        $readmemh("burst_cfg.hex", cfg);
        $readmemh("burst_prn.hex", prn_words);
        $readmemh("burst_code.hex", code_words);
        $readmemh("burst_taps.hex", tap_mem);
        $readmemh("burst_packet.hex", packet_mem);
        $readmemh("burst_plain.hex", plain);
        $readmemh("burst_full.hex", full);
        for (k = 0; k < 81; k = k + 1) taps[16*k +: 16] = tap_mem[k];
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        // load the code memories
        for (k = 0; k < (cfg[0] + 31) / 32; k = k + 1) begin
            @(posedge clk);
            prn_we <= 4'hF; waddr <= k; wdata <= prn_words[k];
        end
        @(posedge clk); prn_we <= 4'h0;
        for (k = 0; k < 576; k = k + 1) begin
            @(posedge clk);
            code_we <= 4'hF; waddr <= k; wdata <= code_words[k];
        end
        @(posedge clk); code_we <= 4'h0;

        // 1: the PRN alone
        @(posedge clk);
        target <= raw_tick + 64'd100; packets <= 1'b0; beats <= cfg[2]; arm <= 1'b1;
        @(posedge clk); arm <= 1'b0; #1;
        check_burst(cfg[2], 0);
        if (busy) begin errors = errors + 1; $display("busy after burst 1"); end

        // 2: with a packet, armed 40 ticks ahead
        @(posedge clk);
        target <= raw_tick + 64'd40; packets <= 1'b1; beats <= cfg[3]; arm <= 1'b1;
        @(posedge clk); arm <= 1'b0; #1;
        check_burst(cfg[3], 1);

        // 3: tready low in a burst -> FAULT, zero output, clear_fault leaves it
        @(posedge clk);
        target <= raw_tick + 64'd50; packets <= 1'b0; beats <= cfg[2]; arm <= 1'b1;
        @(posedge clk); arm <= 1'b0;
        while (raw_tick != target + 64'd20) @(posedge clk);
        tready <= 1'b0;
        @(posedge clk); tready <= 1'b1;
        repeat (2) @(posedge clk); #1;
        if (!fault || tdata !== 256'd0) begin
            errors = errors + 1;
            $display("no FAULT on tready low (fault %b)", fault);
        end
        @(posedge clk); arm <= 1'b1; target <= raw_tick + 64'd50;
        @(posedge clk); arm <= 1'b0;
        repeat (100) @(posedge clk); #1;
        if (tdata !== 256'd0 || !fault) begin errors = errors + 1; $display("armed in FAULT"); end
        clear_fault <= 1'b1; @(posedge clk); clear_fault <= 1'b0;
        repeat (2) @(posedge clk); #1;
        if (fault || busy) begin errors = errors + 1; $display("FAULT not cleared"); end

        if (errors == 0) $display("PASS tb_p3_burst_gen");
        else             $display("FAIL tb_p3_burst_gen: %0d errors", errors);
        $finish;
    end
endmodule
