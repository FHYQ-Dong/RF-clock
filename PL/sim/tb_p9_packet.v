`timescale 1ns / 1ps
// p9_packet against rf_clock.plmodel.packet (vectors from gen_vectors.py):
// the assembler's packet equals pack() of the same fields; the despreader's
// 288 bits equal despread() for a clean span (good CRC: a PEER entry with the
// packet's fields) and a noisy one (whatever the bits, the same as the model's;
// a bad CRC is counted).
module tb_p9_packet;
    localparam integer AW = 11;
    localparam integer SPAN = 400 + 10 + 288 * 64 + 1;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;

    reg [31:0]  cfg [0:2];
    reg [31:0]  code_words [0:575];
    reg [15:0]  tap_mem [0:80];
    reg [287:0] asm_v [0:9];
    reg [287:0] dsp_v [0:9];
    reg [255:0] spans [0:2*SPAN-1];

    reg  [81*16-1:0] taps;
    reg  [3:0]       code_we = 4'd0;
    reg  [AW-1:0]    waddr = {AW{1'b0}};
    reg  [31:0]      wdata = 32'd0;

    // assembler inputs
    reg         tx_arm = 1'b0;
    reg  [63:0] ev_tick = 64'd0, sec_tick = 64'd0;
    reg  [8:0]  ev_slot = 9'd0;
    reg  [31:0] sec_count = 32'd0, config_id = 32'd0;
    reg  [7:0]  time_epoch = 8'd0;
    reg  [63:0] meas_trx = 64'd0;
    reg  [31:0] meas_phase = 32'd0;
    reg  [7:0]  meas_quality = 8'd0;
    wire [287:0] packet;
    wire         packet_ready;

    // span stream
    reg          span_v = 1'b0;
    reg  [31:0]  span_m = 32'd0;
    reg  [255:0] span_d = 256'd0;
    reg          span_dsp = 1'b0;
    reg  [15:0]  span_lag = 16'd0;
    reg  [93:0]  span_corr = 94'd0;
    wire [31:0]  rx_count, crc_count;
    wire         peer_req;
    wire [511:0] peer_entry;
    reg          peer_ack = 1'b0;

    p9_packet #(.AW(AW)) dut (
        .clk (clk), .rst (rst),
        .tx_arm (tx_arm), .ev_tick (ev_tick), .ev_slot (ev_slot), .sec_count (sec_count), .sec_tick (sec_tick),
        .time_epoch (time_epoch), .config_id (config_id), .locked (1'b1), .pps_locked (1'b1),
        .clocks_locked (1'b1), .meas_valid (1'b1), .meas_trx (meas_trx), .meas_phase (meas_phase),
        .meas_quality (meas_quality), .meas_flags (4'b1000),
        .packet (packet), .packet_ready (packet_ready),
        .span_v (span_v), .span_m (span_m), .span_d (span_d), .span_dsp (span_dsp), .span_lag (span_lag),
        .span_corr (span_corr), .span_pps_count (32'd99), .span_slot (9'd3), .span_tick (64'h0000_0001_2345_6789),
        .prn_chips (cfg[0][AW+5:0]), .guard_chips (cfg[1][15:0]),
        .code_we (code_we), .mem_waddr (waddr), .mem_wdata (wdata), .taps (taps),
        .rx_count (rx_count), .crc_count (crc_count),
        .peer_req (peer_req), .peer_entry (peer_entry), .peer_ack (peer_ack)
    );

    integer errors = 0;
    integer k, n, b, cycles;
    reg [287:0] want;
    initial begin
        $readmemh("p9_cfg.hex", cfg);
        $readmemh("p9_code.hex", code_words);
        $readmemh("p9_taps.hex", tap_mem);
        $readmemh("p9_asm.hex", asm_v);
        $readmemh("p9_dsp.hex", dsp_v);
        $readmemh("p9_spans.hex", spans);
        for (k = 0; k < 81; k = k + 1) taps[16*k +: 16] = tap_mem[k];
        if (cfg[2] != SPAN) begin $display("FAIL tb_p9_packet: span %0d", cfg[2]); $finish; end
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        for (k = 0; k < 576; k = k + 1) begin
            @(posedge clk);
            code_we <= 4'hF; waddr <= k; wdata <= code_words[k];
        end
        @(posedge clk); code_we <= 4'h0;

        // ---- assembler ----
        config_id <= asm_v[0][31:0]; sec_count <= asm_v[1][31:0]; ev_slot <= asm_v[2][8:0];
        time_epoch <= asm_v[3][7:0]; ev_tick <= asm_v[4][63:0]; sec_tick <= asm_v[5][63:0];
        meas_trx <= asm_v[6][63:0]; meas_phase <= asm_v[7][31:0]; meas_quality <= asm_v[8][7:0];
        @(posedge clk); tx_arm <= 1'b1;
        @(posedge clk); tx_arm <= 1'b0;
        @(posedge clk);
        while (!packet_ready) @(posedge clk);
        if (packet !== asm_v[9]) begin
            errors = errors + 1;
            $display("packet %h\n  model %h", packet, asm_v[9]);
        end

        // ---- despreader: two spans ----
        for (n = 0; n < 2; n = n + 1) begin
            b = 5 * n;
            span_lag  <= dsp_v[b][15:0];
            span_corr <= {dsp_v[b+1][46:0], dsp_v[b+2][46:0]};
            span_dsp  <= 1'b1;
            repeat (20) @(posedge clk);
            for (k = 0; k < SPAN; k = k + 1) begin
                span_v <= 1'b1; span_m <= k; span_d <= spans[n * SPAN + k];
                @(posedge clk);
            end
            span_v <= 1'b0;
            cycles = 0;
            while (dut.r_state == 2'd0 && dut.rx_bits != 288 && cycles < 200) begin @(posedge clk); cycles = cycles + 1; end
            repeat (60) @(posedge clk);
            if (dut.rx_pkt !== dsp_v[b+3]) begin
                errors = errors + 1;
                $display("span %0d (%0d bits): bits %h\n  model %h", n, dut.rx_bits, dut.rx_pkt, dsp_v[b+3]);
            end
            if (dsp_v[b+4][0]) begin
                if (!peer_req || peer_entry[7:0] !== 8'd2 || peer_entry[32 +: 32] !== 32'd99 ||
                    peer_entry[64 +: 32] !== 32'd3 || peer_entry[96 +: 64] !== 64'h0000_0001_2345_6789 ||
                    peer_entry[192 +: 32] !== dsp_v[b+3][279:248] || peer_entry[224 +: 32] !== dsp_v[b+3][247:216] ||
                    peer_entry[288 +: 64] !== dsp_v[b+3][191:128] || peer_entry[352 +: 64] !== dsp_v[b+3][127:64] ||
                    peer_entry[416 +: 32] !== dsp_v[b+3][63:32]) begin
                    errors = errors + 1;
                    $display("span %0d: no or wrong PEER entry (req %b) %h", n, peer_req, peer_entry);
                end
                peer_ack <= 1'b1; @(posedge clk); peer_ack <= 1'b0;
                repeat (2) @(posedge clk);
            end
            $display("span %0d: CRC %s, rx %0d crc errors %0d", n, dsp_v[b+4][0] ? "good" : "bad", rx_count, crc_count);
        end
        if (rx_count != 1 || crc_count != 1) begin
            errors = errors + 1;
            $display("rx %0d crc %0d, expected 1 and 1", rx_count, crc_count);
        end
        if (errors == 0) $display("PASS tb_p9_packet");
        else             $display("FAIL tb_p9_packet: %0d errors", errors);
        $finish;
    end
endmodule
