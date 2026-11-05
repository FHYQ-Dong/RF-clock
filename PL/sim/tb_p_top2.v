`timescale 1ns / 1ps
// Two p_top nodes on one timebase, each DAC looped into the other's ADC
// (A -> B as it is, B -> A rotated by +90 degrees), LOOP_D ticks apart, with
// short slots, a small window and 16 search lanes.  Through AXI as the ARM
// would: load the burst memories, configure the slot engines (A even slots,
// B odd, with packets) and enable them; after the first search on each node
// accept the strongest peak; then run.  Checks: the search report, the OWN
// entries (lock, lag, phase, T_rx), the PEER entries (the other node's fields,
// T_tx, and its T_rx equal to that node's own measurement), the counters.
module tb_p_top2;
    `include "src/p_defs.vh"
    `include "src/p_regs.vh"

    localparam integer DATA_W    = 256;
    localparam [31:0]  SLOT_T    = 32'd20000;   // the burst (19752 beats) and the span fit
    localparam [31:0]  NSLOT     = 32'd4;
    localparam [31:0]  PPS_P     = SLOT_T * NSLOT;
    localparam integer WB        = 400;
    localparam integer LOOP_D    = 50;
    localparam integer TX_OFF    = 1000;
    localparam integer RX_OFF    = TX_OFF + LOOP_D - 38;   // the burst arrives at window beat 38
    localparam integer LAG       = 8 * 38;
    localparam integer QN        = 64;                     // queue entries

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg dma_clk = 1'b0;
    always #2 dma_clk = ~dma_clk;
    reg arst_n = 1'b0;

    // PPS: one shared edge every PPS_P ticks
    reg [31:0] pps_cnt = 32'd0;
    reg        pps = 1'b0;
    always @(posedge clk) begin
        pps_cnt <= (pps_cnt == PPS_P - 1) ? 32'd0 : pps_cnt + 32'd1;
        pps     <= (pps_cnt < 32'd20);
    end

    // ---- per-node bus signals ----
    reg  [11:0] awaddr [0:1], araddr [0:1];
    reg         awvalid [0:1], wvalid [0:1], bready [0:1], arvalid [0:1], rready [0:1];
    reg  [31:0] wdata [0:1];
    wire        awready [0:1], wready [0:1], bvalid [0:1], arready [0:1], rvalid [0:1];
    wire [1:0]  bresp [0:1], rresp [0:1];
    wire [31:0] rdata [0:1];
    reg         ben [0:1];
    reg  [3:0]  bwe [0:1];
    reg  [15:0] baddr [0:1];
    reg  [31:0] bwdata [0:1];
    wire [31:0] brdata [0:1];
    reg         qen [0:1];
    reg  [15:0] qaddr [0:1];
    wire [31:0] qrdata [0:1];
    wire [DATA_W-1:0] dac [0:1];
    wire              dac_v [0:1];
    reg  [DATA_W-1:0] loop0 [0:LOOP_D-1];
    reg  [DATA_W-1:0] loop1 [0:LOOP_D-1];

    function [DATA_W-1:0] dac2adc(input [DATA_W-1:0] d, input rot);
        integer n;
        reg [15:0] i, q;
        begin
            dac2adc = {DATA_W{1'b0}};
            for (n = 0; n < 8; n = n + 1) begin
                i = d[32*n +: 16];
                q = d[32*n + 16 +: 16];
                dac2adc[16*n +: 16]       = rot ? -q : i;
                dac2adc[128 + 16*n +: 16] = rot ? i : q;
            end
        end
    endfunction

    integer li;
    always @(posedge clk) begin
        loop0[0] <= dac2adc(dac[0], 1'b0);   // A -> B
        loop1[0] <= dac2adc(dac[1], 1'b1);   // B -> A, +90 degrees
        for (li = 1; li < LOOP_D; li = li + 1) begin
            loop0[li] <= loop0[li-1];
            loop1[li] <= loop1[li-1];
        end
    end
    wire [DATA_W-1:0] adc [0:1];
    assign adc[1] = loop0[LOOP_D-1];
    assign adc[0] = loop1[LOOP_D-1];

    genvar g;
    generate
        for (g = 0; g < 2; g = g + 1) begin : node
            wire [DATA_W-1:0] s2mm_tdata;
            wire              s2mm_tvalid, s2mm_tlast;
            p_top #(
                .TX_ADDR_W (5), .TX_DEPTH (24), .RX_MAX_BEATS (32'd65536), .RX_FIFO_ADDR_W (4),
                .MIN_LEAD_TICKS (32'd64), .STABLE_CYCLES (8), .PPS_PERIOD (PPS_P),
                .PPS_TOLERANCE (32'd1), .PPS_LOCK_COUNT (32'd2), .BUILD_VERSION (32'h0200_0000),
                .SLOT_TICKS (SLOT_T), .SLOTS (NSLOT), .WINDOW_BEATS (WB), .SEARCH_LANES (16),
                .QUEUE_ENTRIES (QN)
            ) dut (
                .clk (clk), .arst_n (arst_n), .clocks_locked_async (1'b1), .pps_in (pps), .pps_trig_in (1'b0),
                .dma_clk (dma_clk), .dma_arst_n (arst_n),
                .s_axi_awaddr (awaddr[g]), .s_axi_awvalid (awvalid[g]), .s_axi_awready (awready[g]),
                .s_axi_wdata (wdata[g]), .s_axi_wstrb (4'hF), .s_axi_wvalid (wvalid[g]), .s_axi_wready (wready[g]),
                .s_axi_bresp (bresp[g]), .s_axi_bvalid (bvalid[g]), .s_axi_bready (bready[g]),
                .s_axi_araddr (araddr[g]), .s_axi_arvalid (arvalid[g]), .s_axi_arready (arready[g]),
                .s_axi_rdata (rdata[g]), .s_axi_rresp (rresp[g]), .s_axi_rvalid (rvalid[g]), .s_axi_rready (rready[g]),
                .bram_en (1'b0), .bram_we (32'd0), .bram_addr (10'd0), .bram_wrdata (256'd0), .bram_rddata (),
                .burst_en (ben[g]), .burst_we (bwe[g]), .burst_addr (baddr[g]), .burst_wrdata (bwdata[g]),
                .burst_rddata (brdata[g]),
                .queue_en (qen[g]), .queue_we (4'd0), .queue_addr (qaddr[g]), .queue_wrdata (32'd0),
                .queue_rddata (qrdata[g]),
                .m_axis_dac_tdata (dac[g]), .m_axis_dac_tvalid (dac_v[g]), .m_axis_dac_tready (1'b1),
                .s_axis_adc_tdata (adc[g]), .s_axis_adc_tvalid (1'b1), .adc_overrange (1'b0),
                .m_axis_s2mm_tdata (s2mm_tdata), .m_axis_s2mm_tkeep (), .m_axis_s2mm_tvalid (s2mm_tvalid),
                .m_axis_s2mm_tready (1'b1), .m_axis_s2mm_tlast (s2mm_tlast),
                .m_axis_dac0_tdata (), .m_axis_dac0_tvalid (), .m_axis_dac0_tready (1'b1),
                .pps_out_invalid (), .pps_out_led ()
            );
        end
    endgenerate

    // Node 0 receives node 1 rotated by +90 degrees (2^30 in 2^-32 turn), node 1
    // receives node 0 as it is; CORDIC lands within a few units of either.
    function phase_near(input [31:0] phase, input integer node);
        reg signed [31:0] d;
        begin
            d = phase - (node == 0 ? 32'h4000_0000 : 32'h0);
            phase_near = (d < 32'sd4096) && (d > -32'sd4096);
        end
    endfunction

    // ---- AXI and memory tasks ----
    integer errors = 0;
    task check(input cond, input [8*72-1:0] msg);
        begin
            if (!cond) begin
                errors = errors + 1;
                $display("FAIL: %0s (t=%0t)", msg, $time);
            end
        end
    endtask

    task wr(input integer n, input [11:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            awaddr[n] = addr; awvalid[n] = 1; wdata[n] = data; wvalid[n] = 1; bready[n] = 1;
            @(posedge clk);
            while (!(awready[n] && wready[n])) @(posedge clk);
            @(negedge clk);
            awvalid[n] = 0; wvalid[n] = 0;
            while (!bvalid[n]) @(negedge clk);
            if (bresp[n] != 2'b00) begin errors = errors + 1; $display("FAIL: write %h to node %0d: %b", addr, n, bresp[n]); end
            @(negedge clk);
            bready[n] = 0;
        end
    endtask

    task rd(input integer n, input [11:0] addr, output [31:0] data);
        begin
            @(negedge clk);
            araddr[n] = addr; arvalid[n] = 1; rready[n] = 1;
            @(posedge clk);
            while (!arready[n]) @(posedge clk);
            @(negedge clk);
            arvalid[n] = 0;
            while (!rvalid[n]) @(negedge clk);
            data = rdata[n];
            @(negedge clk);
            rready[n] = 0;
        end
    endtask

    task bmem(input integer n, input [15:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            ben[n] = 1; bwe[n] = 4'hF; baddr[n] = addr; bwdata[n] = data;
            @(negedge clk);
            ben[n] = 0; bwe[n] = 4'h0;
        end
    endtask

    task qword(input integer n, input integer entry, input integer w, output [31:0] data);
        begin
            @(negedge clk);
            qen[n] = 1; qaddr[n] = 64 * entry + 4 * w;
            @(negedge clk);
            qen[n] = 0;
            data = qrdata[n];
        end
    endtask

    // ---- vectors: the tracker's PRN and taps, P9's packet code ----
    reg [31:0] prn_words [0:9];
    reg [15:0] tap_mem [0:80];
    reg [31:0] code_words [0:575];

    integer n, k, e, w;
    reg [31:0] v, v2;
    reg [31:0] ent [0:15];
    integer own_locked [0:1], peers [0:1];
    // each node's locked OWN entries, for the cross-check
    reg [63:0] own_trx [0:1][0:31];
    reg [31:0] own_count [0:1][0:31];
    reg [31:0] own_slot [0:1][0:31];
    reg [7:0]  own_q [0:1][0:31];
    integer    own_n [0:1];
    reg [31:0] want_count, want_slot;
    integer    found;
    reg [63:0] t_rx, t_tx, want;

    initial begin
        for (n = 0; n < 2; n = n + 1) begin
            awaddr[n] = 0; araddr[n] = 0; awvalid[n] = 0; wvalid[n] = 0; bready[n] = 0;
            arvalid[n] = 0; rready[n] = 0; wdata[n] = 0;
            ben[n] = 0; bwe[n] = 0; baddr[n] = 0; bwdata[n] = 0; qen[n] = 0; qaddr[n] = 0;
            own_locked[n] = 0; peers[n] = 0; own_n[n] = 0;
        end
        $readmemh("trk_prn.hex", prn_words);
        $readmemh("trk_taps.hex", tap_mem);
        $readmemh("p9_code.hex", code_words);
        repeat (5) @(posedge clk);
        arst_n = 1'b1;
        repeat (40) @(posedge clk);

        for (n = 0; n < 2; n = n + 1) begin
            rd(n, REG_VERSION, v); check(v == P_VERSION, "VERSION 2.0");
            for (k = 0; k < 10; k = k + 1)  bmem(n, BMEM_PRN_OFFSET + 4 * k, prn_words[k]);
            for (k = 0; k < 576; k = k + 1) bmem(n, BMEM_PACKET_OFFSET + 4 * k, code_words[k]);
            for (k = 0; k < 81; k = k + 1)  bmem(n, BMEM_TAPS_OFFSET + 4 * k, {{16{tap_mem[k][15]}}, tap_mem[k]});
            wr(n, REG_BURST_PRN_CHIPS, 32'd300);
            wr(n, REG_SLOT_TX_OFFSET, TX_OFF);
            wr(n, REG_SLOT_RX_OFFSET, RX_OFF);
            wr(n, REG_PACKET_CONFIG_ID, 32'hC0DE_0000 + n);
            wr(n, REG_SLOT_CONTROL, (1 << SLOTCTL_ENABLE) | (1 << SLOTCTL_PACKETS) | (n << SLOTCTL_ROLE_B));
            rd(n, REG_BURST_BEATS, v);   check(v == 300 + 10 + 10 + 288 * 64, "BURST_BEATS");
            rd(n, REG_RX_SPAN_BEATS, v); check(v == WB + 10 + 288 * 64 + 1, "RX_SPAN_BEATS");
            // burst memory writes are refused now
            bmem(n, BMEM_TAPS_OFFSET, 32'h1234);
            rd(n, REG_BURST_MEM_REJ_CNT, v); check(v == 1, "burst memory write refused while enabled");
            @(negedge clk); ben[n] = 1; baddr[n] = BMEM_TAPS_OFFSET; @(negedge clk); ben[n] = 0;
            check(brdata[n] == {{16{tap_mem[0][15]}}, tap_mem[0]}, "tap 0 read back unchanged");
            rd(n, REG_STATUS, v);
        end

        // ---- wait for each node's first search, then accept its strongest peak ----
        for (n = 0; n < 2; n = n + 1) begin
            k = 0;
            v = 0;
            while (!v[P8ST_WAITING] && k < 3000) begin
                repeat (500) @(posedge clk);
                rd(n, REG_P8_STATUS, v);
                k = k + 1;
            end
            check(v[P8ST_WAITING], "a search reported");
            rd(n, REG_SR_STRONG_LAG, v);  check(v == LAG, "strongest lag");
            rd(n, REG_SR_EARLY_LAG, v2);  check(v2 == LAG, "earliest lag");
            rd(n, REG_SR_STRONG_PHASE, v); check(phase_near(v, n), "strongest phase");
            rd(n, REG_SR_SLOT, v);        check(v[0] == !n, "searched a peer slot");
            wr(n, REG_P8_CONTROL, 1 << P8CTL_ACCEPT_STRONGEST);
            rd(n, REG_P8_STATUS, v);      check(v[P8ST_LOCKED] && !v[P8ST_WAITING], "locked after ACCEPT_STRONGEST");
            rd(n, REG_P8_LOCK_LAG, v);    check(v == LAG, "lock lag");
        end

        // ---- run two seconds ----
        repeat (2 * PPS_P) @(posedge clk);

        for (n = 0; n < 2; n = n + 1) begin
            rd(n, REG_P8_STATUS, v);         check(v[P8ST_LOCKED], "still locked");
            rd(n, REG_PACKET_CRC_CNT, v);    check(v == 0, "no CRC errors");
            rd(n, REG_PACKET_RX_CNT, v);     check(v >= 3, "packets decoded");
            rd(n, REG_QUEUE_DROPPED_CNT, v); check(v == 0, "no entries dropped");
            rd(n, REG_SLOT_SKIPPED_CNT, v);  check(v == 0, "no slots skipped");
            rd(n, REG_P8_LOST_CNT, v);       check(v == 0, "no lock lost");
            rd(n, REG_BURST_CNT, v2);        check(v2 >= 6, "bursts sent");
            rd(n, REG_STATUS, v);            check(v[STAT_SLOTS_RUNNING] && v[STAT_LOCKED], "STATUS bits");
            $display("node %0d: %0d bursts", n, v2);
        end

        // ---- the queues ----
        for (n = 0; n < 2; n = n + 1) begin
            rd(n, REG_QUEUE_HEAD, v);
            $display("node %0d: %0d queue entries", n, v);
            for (e = 0; e < v; e = e + 1) begin
                for (w = 0; w < 16; w = w + 1) qword(n, e, w, ent[w]);
                check(ent[0][31:16] == e, "entry sequence");
                if (ent[0][7:0] == QE_TYPE_OWN) begin
                    check(ent[QE_SLOT][0] == !n, "OWN entry in a peer slot");
                    if (ent[0][8 + QEF_LOCKED]) begin
                        own_locked[n] = own_locked[n] + 1;
                        check(ent[QE_LAG] == LAG, "OWN lag");
                        check(phase_near(ent[QE_PHASE], n), "OWN phase");
                        check(ent[0][8 + QEF_WINDOW_BAD] == 1'b0, "OWN window good");
                        t_rx = {ent[QE_TRX_HI], ent[QE_TRX_LO]};
                        want = (ent[QE_SLOT] * SLOT_T + RX_OFF) * 64'd10000000 + LAG * 64'd1250000 +
                               (($signed(ent[QE_FRACTION]) * 64'sd1250000 + 64'sd2147483648) >>> 32);
                        check(t_rx == want, "OWN T_rx");
                        own_trx[n][own_n[n]]   = t_rx;
                        own_count[n][own_n[n]] = ent[QE_PPS_COUNT];
                        own_slot[n][own_n[n]]  = ent[QE_SLOT];
                        own_q[n][own_n[n]]     = ent[QE_QUALITY][7:0];
                        own_n[n] = own_n[n] + 1;
                    end
                end else if (ent[0][7:0] == QE_TYPE_PEER) begin
                    peers[n] = peers[n] + 1;
                    check(ent[QE_P_CONFIG_ID] == 32'hC0DE_0000 + (1 - n), "PEER config_id");
                    check(ent[QE_P_SLOT] == ent[QE_SLOT] && ent[QE_P_PPS_COUNT] == ent[QE_PPS_COUNT],
                          "PEER slot and second are the window's");
                    t_tx = {ent[QE_P_TTX_HI], ent[QE_P_TTX_LO]};
                    check(t_tx == (ent[QE_P_SLOT] * SLOT_T + TX_OFF) * 64'd10000000, "PEER T_tx");
                    check(ent[QE_P_HEAD][31:24] == PACKET_FORMAT_VERSION, "PEER version");
                    if (ent[QE_P_HEAD][PKST_MEASUREMENT]) begin
                        // the sender measured this node's burst in the slot before
                        check(ent[QE_P_HEAD][PKST_LOCKED], "PEER locked");
                    end
                end else begin
                    check(0, "entry type");
                end
            end
        end
        // a PEER packet that carries a measurement carries the sender's OWN entry of
        // the slot before (slot 0: the previous second's last slot)
        for (n = 0; n < 2; n = n + 1) begin
            rd(n, REG_QUEUE_HEAD, v);
            for (e = 0; e < v; e = e + 1) begin
                for (w = 0; w < 16; w = w + 1) qword(n, e, w, ent[w]);
                if (ent[0][7:0] == QE_TYPE_PEER && ent[QE_P_HEAD][PKST_MEASUREMENT]) begin
                    want_count = (ent[QE_P_SLOT] == 0) ? ent[QE_P_PPS_COUNT] - 1 : ent[QE_P_PPS_COUNT];
                    want_slot  = (ent[QE_P_SLOT] == 0) ? NSLOT - 1 : ent[QE_P_SLOT] - 1;
                    found = 0;
                    for (k = 0; k < own_n[1-n]; k = k + 1)
                        if (own_count[1-n][k] == want_count && own_slot[1-n][k] == want_slot) begin
                            found = 1;
                            check({ent[QE_P_TRX_HI], ent[QE_P_TRX_LO]} == own_trx[1-n][k],
                                  "PEER T_rx = the sender's OWN T_rx");
                            check(ent[QE_P_HEAD][15:8] == own_q[1-n][k], "PEER quality = the sender's");
                        end
                    check(found, "the sender's OWN entry of a PEER measurement");
                    peers[n] = peers[n] + 100;
                end
            end
            check(own_locked[n] >= 3, "locked OWN entries");
            check(peers[n] >= 100 + 3, "PEER entries, one cross-checked");
            $display("node %0d: %0d locked OWN entries, PEER entries/cross-checks %0d", n, own_locked[n], peers[n]);
        end

        if (errors == 0) $display("PASS tb_p_top2");
        else             $display("FAIL tb_p_top2 (%0d errors)", errors);
        $finish;
    end

    always @(posedge clk) if (node[0].dut.pps_event)
        $display("PPS %0d at %0t: running %b locked %b%b search %b%b", node[0].dut.pps_count, $time,
                 node[0].dut.slots_running, node[0].dut.p8_locked, node[1].dut.p8_locked,
                 node[0].dut.p8_search_busy, node[1].dut.p8_search_busy);
    always @(posedge clk) if (node[0].dut.pps_event) $fflush;

    initial begin
        #(64'd100_000_000);
        $display("FAIL tb_p_top2 (timeout)");
        $finish;
    end
endmodule
