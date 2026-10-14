`timescale 1ns / 1ps
// p_top integration: AXI-Lite register access, PPS capture, a TX burst
// looped back digitally into the RX window (DAC -> D-cycle delay -> ADC),
// and the TX fault / CLEAR_FAULT path through P6.
module tb_p_top;
    `include "src/p_defs.vh"
    `include "src/p_regs.vh"

    localparam integer DATA_W    = 256;
    localparam integer TX_ADDR_W = 5;    // 32-beat window
    localparam integer TX_DEPTH  = 24;   // not a power of two, like the real 69632
    localparam integer LOOP_D    = 3;    // loopback delay in ticks
    localparam [31:0]  PPS_P     = 32'd200;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg dma_clk = 1'b0;
    always #2 dma_clk = ~dma_clk;

    reg arst_n = 1'b0;
    reg pps = 1'b0;

    reg  [7:0]  awaddr = 0, araddr = 0;
    reg         awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
    reg  [31:0] wdata = 0;
    reg  [3:0]  wstrb = 0;
    wire        awready, wready, bvalid, arready, rvalid;
    wire [1:0]  bresp, rresp;
    wire [31:0] rdata;

    reg                   bram_en = 0;
    reg  [DATA_W/8-1:0]   bram_we = 0;
    reg  [TX_ADDR_W+4:0]  bram_addr = 0;
    reg  [DATA_W-1:0]     bram_wrdata = 0;
    wire [DATA_W-1:0]     bram_rddata;

    wire [DATA_W-1:0] dac_tdata;
    wire              dac_tvalid;
    reg               dac_tready = 1'b1;

    // Digital loopback DAC -> ADC.
    reg [DATA_W-1:0] loop [0:LOOP_D-1];
    integer li;
    always @(posedge clk) begin
        loop[0] <= dac_tdata;
        for (li = 1; li < LOOP_D; li = li + 1) loop[li] <= loop[li-1];
    end
    wire [DATA_W-1:0] adc_tdata = loop[LOOP_D-1];

    wire [DATA_W-1:0]   s2mm_tdata;
    wire [DATA_W/8-1:0] s2mm_tkeep;
    wire                s2mm_tvalid, s2mm_tlast;
    reg                 s2mm_tready = 1'b1;

    p_top #(
        .TX_ADDR_W      (TX_ADDR_W),
        .TX_DEPTH       (TX_DEPTH),
        .RX_MAX_BEATS   (32'd1024),
        .RX_FIFO_ADDR_W (4),
        .MIN_LEAD_TICKS (32'd64),
        .STABLE_CYCLES  (8),
        .PPS_PERIOD     (PPS_P),
        .PPS_TOLERANCE  (32'd1),
        .PPS_LOCK_COUNT (32'd2),
        .BUILD_VERSION  (32'h0102_0304)
    ) dut (
        .clk(clk), .arst_n(arst_n), .clocks_locked_async(1'b1), .pps_in(pps), .pps_trig_in(1'b0),
        .dma_clk(dma_clk), .dma_arst_n(arst_n),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .bram_en(bram_en), .bram_we(bram_we), .bram_addr(bram_addr),
        .bram_wrdata(bram_wrdata), .bram_rddata(bram_rddata),
        .m_axis_dac_tdata(dac_tdata), .m_axis_dac_tvalid(dac_tvalid), .m_axis_dac_tready(dac_tready),
        .s_axis_adc_tdata(adc_tdata), .s_axis_adc_tvalid(1'b1), .adc_overrange(1'b0),
        .m_axis_s2mm_tdata(s2mm_tdata), .m_axis_s2mm_tkeep(s2mm_tkeep),
        .m_axis_s2mm_tvalid(s2mm_tvalid), .m_axis_s2mm_tready(s2mm_tready),
        .m_axis_s2mm_tlast(s2mm_tlast)
    );

    // DMA sink.
    reg [DATA_W-1:0] rx_beat [0:255];
    reg              rx_last [0:255];
    integer          rx_n = 0;
    always @(posedge dma_clk) begin
        if (s2mm_tvalid && s2mm_tready) begin
            rx_beat[rx_n] <= s2mm_tdata;
            rx_last[rx_n] <= s2mm_tlast;
            rx_n <= rx_n + 1;
        end
    end

    // PPS source: one edge every PPS_P ticks; remember the edge tick.
    reg [63:0] last_pps_tick = 0;
    initial begin
        @(posedge arst_n);
        repeat (30) @(negedge clk);
        forever begin
            last_pps_tick = dut.raw_tick;
            pps = 1'b1;
            repeat (10) @(negedge clk);
            pps = 1'b0;
            repeat (PPS_P - 10) @(negedge clk);
        end
    end

    // Watchdog: a stuck handshake or wait loop fails instead of hanging.
    initial begin
        #200_000;
        $display("FAIL tb_p_top (timeout)");
        $finish;
    end

    integer errors = 0;
    task check(input cond, input [8*64-1:0] msg);
        if (!cond) begin
            errors = errors + 1;
            $display("FAIL: %0s (t=%0t)", msg, $time);
        end
    endtask

    task axi_write(input [7:0] addr, input [31:0] data, input [3:0] strb, output [1:0] resp);
        begin
            @(negedge clk);
            awaddr = addr; awvalid = 1; wdata = data; wstrb = strb; wvalid = 1; bready = 1;
            while (!awready) @(negedge clk);
            @(negedge clk);
            awvalid = 0; wvalid = 0;
            while (!bvalid) @(negedge clk);
            resp = bresp;
            @(negedge clk);
            bready = 0;
        end
    endtask

    task wr(input [7:0] addr, input [31:0] data);
        reg [1:0] r;
        begin
            axi_write(addr, data, 4'hF, r);
            check(r == 2'b00, "write OKAY");
        end
    endtask

    task axi_read(input [7:0] addr, output [31:0] data, output [1:0] resp);
        begin
            @(negedge clk);
            araddr = addr; arvalid = 1; rready = 1;
            while (!arready) @(negedge clk);
            @(negedge clk);
            arvalid = 0;
            while (!rvalid) @(negedge clk);
            data = rdata; resp = rresp;
            @(negedge clk);
            rready = 0;
        end
    endtask

    task rd(input [7:0] addr, output [31:0] data);
        reg [1:0] r;
        begin
            axi_read(addr, data, r);
            check(r == 2'b00, "read OKAY");
        end
    endtask

    task rd64(input [7:0] lo_addr, output [63:0] v);
        reg [31:0] lo, hi;
        begin
            rd(lo_addr, lo);
            rd(lo_addr + 8'd4, hi);
            v = {hi, lo};
        end
    endtask

    function [DATA_W-1:0] beat(input integer i);
        beat = {8{32'hC000_0000 + i}};
    endfunction

    task bram_write(input integer i, input [DATA_W-1:0] d);
        begin
            @(negedge clk);
            bram_en = 1; bram_we = {(DATA_W/8){1'b1}}; bram_addr = i << 5; bram_wrdata = d;
            @(negedge clk);
            bram_en = 0; bram_we = 0;
        end
    endtask

    reg [31:0] v;
    reg [1:0]  resp;
    reg [63:0] t_now, t_now2, tgt, v64;
    integer    i, n, start_n;
    reg [DATA_W-1:0] exp_beat;

    initial begin
        repeat (5) @(posedge clk);
        arst_n = 1'b1;

        // ID/VERSION are checked against literal values: they are the ABI.
        rd(REG_ID, v);       check(v == 32'h5243_4C4B, "ID");
        rd(REG_VERSION, v);  check(v == 32'h0001_0003, "VERSION");
        rd(REG_BUILD_VERSION, v); check(v == 32'h0102_0304, "BUILD_VERSION");
        axi_write(REG_BUILD_VERSION, 32'h1, 4'hF, resp); check(resp == 2'b10, "BUILD_VERSION is read-only");
        rd(REG_TX_DEPTH, v); check(v == TX_DEPTH, "TX_DEPTH");
        rd(REG_TX_REPEAT, v); check(v == 1, "TX_REPEAT reset value");
        wr(REG_TX_REPEAT, 7); rd(REG_TX_REPEAT, v); check(v == 7, "TX_REPEAT readback");
        wr(REG_TX_REPEAT, 1);

        n = 0;
        v = 0;
        while (!v[STAT_TIME_VALID] && n < 100) begin rd(REG_STATUS, v); n = n + 1; end
        check(v[STAT_TIME_VALID], "time_valid");
        check(v[STAT_DMA_READY], "dma_ready");
        rd(REG_TIME_EPOCH, v); check(v == 1, "epoch 1");

        // Tick snapshot is monotonic and consistent.
        rd64(REG_TICK_LO, t_now);
        rd64(REG_TICK_LO, t_now2);
        check(t_now2 > t_now && t_now2 - t_now < 20, "tick snapshot monotonic");

        // Load the TX waveform.
        for (i = 0; i < 6; i = i + 1) bram_write(i, beat(i));

        // Schedule TX and RX at the same target; one CONTROL write rings both.
        rd64(REG_TICK_LO, t_now);
        tgt = t_now + 200;
        wr(REG_TX_TARGET_LO, tgt[31:0]); wr(REG_TX_TARGET_HI, tgt[63:32]); wr(REG_TX_LENGTH, 6);
        wr(REG_RX_TARGET_LO, tgt[31:0]); wr(REG_RX_TARGET_HI, tgt[63:32]); wr(REG_RX_LENGTH, 12);
        start_n = rx_n;
        wr(REG_CONTROL, (32'd1 << CTRL_TX_DOORBELL) | (32'd1 << CTRL_RX_DOORBELL));
        rd(REG_STATUS, v);
        check(v[STAT_TX_STATE_LSB +: 3] == ST_ARMED && v[STAT_RX_STATE_LSB +: 3] == ST_ARMED,
              "both armed");

        n = 0;
        while (!(v[STAT_TX_STATE_LSB +: 3] == ST_DONE && v[STAT_RX_STATE_LSB +: 3] == ST_DONE)
               && n < 200) begin
            rd(REG_STATUS, v); n = n + 1;
        end
        check(v[STAT_TX_STATE_LSB +: 3] == ST_DONE && v[STAT_RX_STATE_LSB +: 3] == ST_DONE,
              "both done");
        rd(REG_TX_RESULT, v);  check(v[7:0] == RC_DONE, "TX_RESULT done");
        rd(REG_TX_SEQ, v);     check(v == 1, "TX_SEQ");
        rd64(REG_TX_ACTUAL_LO, v64); check(v64 == tgt, "TX_ACTUAL = target");
        rd(REG_RX_RESULT, v);
        check(v[7:0] == RC_DONE && v[RX_RESULT_FLAGS_LSB +: 4] == 0, "RX_RESULT done, no flags");
        rd(REG_RX_SEQ, v);     check(v == 1, "RX_SEQ");
        rd(REG_RX_BEATS, v);   check(v == 12, "RX_BEATS");
        rd64(REG_RX_ACTUAL_LO, v64); check(v64 == tgt, "RX_ACTUAL = target");

        // RX beat k is the DAC beat from LOOP_D ticks earlier.
        check(rx_n - start_n == 12, "12 beats to DMA");
        for (i = 0; i < 12; i = i + 1) begin
            exp_beat = (i >= LOOP_D && i < LOOP_D + 6) ? beat(i - LOOP_D) : {DATA_W{1'b0}};
            check(rx_beat[start_n + i] == exp_beat, "loopback beat content and alignment");
            check(rx_last[start_n + i] == (i == 11), "TLAST only on last beat");
        end

        // AXI error responses.
        axi_write(REG_TX_TARGET_LO, 32'h1234, 4'h3, resp); check(resp == 2'b10, "partial write SLVERR");
        rd(REG_TX_TARGET_LO, v); check(v == tgt[31:0], "partial write ignored");
        axi_write(REG_ID, 32'h1, 4'hF, resp); check(resp == 2'b10, "RO write SLVERR");
        axi_read(8'hFC, v, resp); check(resp == 2'b10, "unmapped read SLVERR");

        // PPS: locked, exact period, timestamp matches the driven edge.
        n = 0;
        v = 0;
        while (!v[STAT_PPS_LOCKED] && n < 400) begin rd(REG_STATUS, v); n = n + 1; end
        check(v[STAT_PPS_LOCKED], "pps_locked");
        rd(REG_PPS_LAST_PERIOD, v); check(v == PPS_P, "PPS_LAST_PERIOD");
        // Read well after an edge so the snapshot is not racing the next one.
        while (dut.raw_tick - last_pps_tick < 40) @(negedge clk);
        rd64(REG_PPS_TICK_LO, v64);
        check(v64 == last_pps_tick, "PPS_TICK equals edge tick");

        // PPS probes and configuration: the comparator copy has pulsed, the
        // unconnected Schmitt copy has not; PPS_CONFIG reads back 2 bits.
        rd(REG_PPS_INPUTS, v);
        check(v[PPSIN_COMP_EDGES_LSB +: 8] != 0 && v[PPSIN_TRIG_EDGES_LSB +: 8] == 0,
              "PPS_INPUTS edge counts");
        rd(REG_PPS_CONFIG, v); check(v == 0, "PPS_CONFIG reset value");
        wr(REG_PPS_CONFIG, 32'hFFFF_FFFF);
        rd(REG_PPS_CONFIG, v); check(v == 32'h3, "PPS_CONFIG keeps 2 bits");
        wr(REG_PPS_CONFIG, 32'h0);

        // TX fault through P6, then CLEAR_FAULT.
        rd64(REG_TICK_LO, t_now);
        tgt = t_now + 150;
        wr(REG_TX_TARGET_LO, tgt[31:0]); wr(REG_TX_TARGET_HI, tgt[63:32]); wr(REG_TX_LENGTH, 6);
        wr(REG_CONTROL, 32'd1 << CTRL_TX_DOORBELL);
        while (dut.raw_tick != tgt + 2) @(negedge clk);
        dac_tready = 0;
        @(negedge clk);
        dac_tready = 1;
        rd(REG_STATUS, v);
        check(v[STAT_TX_STATE_LSB +: 3] == ST_FAULT && v[STAT_ANY_FAULT], "TX FAULT and any_fault");
        rd(REG_FAULT_BITS, v); check(v[FB_TX], "FAULT_BITS TX");
        rd(REG_FAULT_INFO, v); check(v[3:0] == FB_TX + 1, "first_source TX");
        rd(REG_TX_RESULT, v);  check(v[7:0] == RC_FAULT_TREADY, "TX_RESULT tready fault");
        wr(REG_CONTROL, 32'd1 << CTRL_CLEAR_FAULT);
        rd(REG_STATUS, v);
        check(v[STAT_TX_STATE_LSB +: 3] == ST_IDLE && !v[STAT_ANY_FAULT], "CLEAR_FAULT clears");

        if (errors == 0) $display("PASS tb_p_top");
        else             $display("FAIL tb_p_top (%0d errors)", errors);
        $finish;
    end
endmodule
