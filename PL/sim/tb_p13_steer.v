`timescale 1ns / 1ps
// P13 against rf_clock.plmodel.steer (PL/sim/gen_vectors.py writes st_*.hex): every
// step's queue entry and FC command, bit for bit, through a run of the algorithm
// that ends when the wanted FC stays outside its limits, and a run of FC steps
// after ENABLE 0 -> 1.  A short second (20 slots of 2500 ticks) and small tables.
module tb_p13_steer;
    localparam integer SLOTS_I      = 20;
    localparam integer SLOT_TICKS_I = 2500;
    localparam integer SECOND       = SLOTS_I * SLOT_TICKS_I;
    localparam integer MAXV         = 4096;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;
    reg [63:0] raw_tick = 64'd0;
    always @(posedge clk) raw_tick <= raw_tick + 64'd1;

    reg         pps_event = 1'b0;
    reg  [63:0] pps_tick = 64'd0;
    reg  [31:0] pps_count = 32'd0;
    reg         own_take = 1'b0, peer_take = 1'b0;
    reg [511:0] own_entry = 512'd0, peer_entry = 512'd0;
    wire        steer_req;
    wire [511:0] steer_entry;
    reg         steer_ack = 1'b0;
    wire        cmd_valid, reserve_valid;
    wire [63:0] cmd_tick, reserve_tick;
    wire [4:0]  cmd_len;
    wire [127:0] cmd_bytes;
    reg         reg_wr = 1'b0;
    reg  [11:0] reg_waddr = 12'd0, reg_waddr_live = 12'd0, reg_raddr = 12'd0;
    reg  [31:0] reg_wdata = 32'd0, reg_wdata_live = 32'd0;
    wire        reg_wr_ok, reg_rd_ok;
    wire [31:0] reg_rdata;

    p13_steer #(.SLOT_TICKS (SLOT_TICKS_I), .SLOTS (SLOTS_I), .LAW (7), .NAW (7), .PAW (3)) dut (
        .clk (clk), .rst (rst), .raw_tick (raw_tick),
        .pps_event (pps_event), .pps_tick (pps_tick), .pps_count (pps_count), .pps_locked (1'b1),
        .tx_offset (32'd300),
        .own_take (own_take), .own_entry (own_entry), .peer_take (peer_take), .peer_entry (peer_entry),
        .steer_req (steer_req), .steer_entry (steer_entry), .steer_ack (steer_ack),
        .cmd_valid (cmd_valid), .cmd_tick (cmd_tick), .cmd_len (cmd_len), .cmd_bytes (cmd_bytes),
        .reserve_valid (reserve_valid), .reserve_tick (reserve_tick),
        .cmd_started (1'b0), .cmd_late (1'b0), .cmd_start_tick (64'd0),
        .reg_wr (reg_wr), .reg_waddr (reg_waddr), .reg_wdata (reg_wdata),
        .reg_waddr_live (reg_waddr_live), .reg_wdata_live (reg_wdata_live), .reg_wr_ok (reg_wr_ok),
        .reg_raddr (reg_raddr), .reg_rdata (reg_rdata), .reg_rd_ok (reg_rd_ok),
        .reg_rd (1'b0), .reg_rd_addr (12'd0)
    );

    reg [31:0]  cfg      [0:15];
    reg [43:0]  regs     [0:63];
    reg [67:0]  coefs    [0:MAXV-1];
    reg [255:0] pairs    [0:MAXV-1];
    reg [391:0] expv   [0:MAXV-1];
    reg [199:0] cmds     [0:MAXV-1];
    integer n_regs, n_coefs, n_pairs, n_expect, n_cmds, last_sec, sec2;
    integer pi, ei, ci, errors, i;

    task reg_write(input [11:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            reg_waddr_live = addr;
            reg_wdata_live = data;
            #1;
            if (!reg_wr_ok) begin
                $display("FAIL: write of %h to %h refused", data, addr);
                errors = errors + 1;
            end
            @(negedge clk);
            reg_wr    = 1'b1;
            reg_waddr = addr;
            reg_wdata = data;
            @(negedge clk);
            reg_wr = 1'b0;
        end
    endtask

    // PPS: second s starts at tick s * SECOND, its event three ticks later
    always @(posedge clk) begin
        pps_event <= 1'b0;
        if (!rst && raw_tick >= 64'd3 && (raw_tick - 64'd3) % SECOND == 0) begin
            pps_event <= 1'b1;
            pps_tick  <= raw_tick - 64'd3;
            pps_count <= (raw_tick - 64'd3) / SECOND;
        end
    end

    // pairs: the OWN entry 400 ticks into the slot, its PEER entry 5 ticks later
    reg [63:0] pair_at;
    always @(posedge clk) begin
        own_take  <= 1'b0;
        peer_take <= 1'b0;
        if (!rst && pi < n_pairs) begin
            pair_at = pairs[pi][247:216] * SECOND + pairs[pi][215:200] * SLOT_TICKS_I + 400;
            if (raw_tick == pair_at) begin
                own_take  <= 1'b1;
                own_entry <= 512'd0;
                own_entry[8]          <= pairs[pi][196];             // QEF_LOCKED
                own_entry[63:32]      <= pairs[pi][247:216];         // PPS_COUNT
                own_entry[95:64]      <= {16'd0, pairs[pi][215:200]}; // slot
                own_entry[32*10 +: 64] <= pairs[pi][191:128];         // T_rx
            end
            if (raw_tick == pair_at + 5) begin
                peer_take  <= 1'b1;
                peer_entry <= 512'd0;
                peer_entry[32*5 + 1]   <= pairs[pi][192];              // PKST_MEASUREMENT
                peer_entry[63:32]      <= pairs[pi][247:216];
                peer_entry[95:64]      <= {16'd0, pairs[pi][215:200]};
                peer_entry[32*9 +: 64] <= pairs[pi][127:64];           // T_tx
                peer_entry[32*11 +: 64] <= pairs[pi][63:0];            // peer T_rx
                pi <= pi + 1;
            end
        end
    end

    // the queue: take each STEER entry two cycles after its request, compare it
    reg [3:0] ack_wait = 4'd0;
    reg [391:0] e;
    always @(posedge clk) begin
        steer_ack <= 1'b0;
        if (steer_req && !steer_ack) begin
            if (ack_wait == 4'd2) begin
                steer_ack <= 1'b1;
                ack_wait  <= 4'd0;
                if (ei >= n_expect) begin
                    $display("FAIL: an entry more than the %0d expected (index %0d)", n_expect, steer_entry[32*5 +: 32]);
                    errors = errors + 1;
                end else begin
                    e = expv[ei];
                    if (steer_entry[7:0] != 8'd3 || steer_entry[15:8] != e[391:384] || steer_entry[63:32] != e[383:352]
                        || steer_entry[95:64] != e[351:320] || steer_entry[191:160] != e[319:288]
                        || steer_entry[32*6 +: 64] != e[287:224] || steer_entry[32*8 +: 64] != e[223:160]
                        || steer_entry[32*10 +: 64] != e[159:96] || steer_entry[32*12 +: 32] != e[95:64]
                        || steer_entry[32*13 +: 32] != e[63:32] || steer_entry[32*14 +: 32] != e[31:0]) begin
                        if (errors < 20)
                            $display("FAIL: entry %0d: got flags %h count %0d step %h index %0d theta %h dF %h S %h fc %h rem %h s0 %h; want %h %0d %h %0d %h %h %h %h %h %h",
                                     ei, steer_entry[15:8], steer_entry[63:32], steer_entry[95:64], steer_entry[191:160],
                                     steer_entry[32*6 +: 64], steer_entry[32*8 +: 64], steer_entry[32*10 +: 64],
                                     steer_entry[32*12 +: 32], steer_entry[32*13 +: 32], steer_entry[32*14 +: 32],
                                     e[391:384], e[383:352], e[351:320], e[319:288], e[287:224], e[223:160], e[159:96],
                                     e[95:64], e[63:32], e[31:0]);
                        errors = errors + 1;
                    end
                end
                ei = ei + 1;
            end else ack_wait <= ack_wait + 4'd1;
        end
    end

    // the commands
    always @(posedge clk) begin
        if (cmd_valid) begin
            if (ci >= n_cmds) begin
                $display("FAIL: a command more than the %0d expected", n_cmds);
                errors = errors + 1;
            end else if (cmd_tick != cmds[ci][199:136] || cmd_len != cmds[ci][132:128] || cmd_bytes != cmds[ci][127:0]) begin
                if (errors < 20)
                    $display("FAIL: command %0d: tick %0d len %0d bytes %h, want %0d %0d %h", ci, cmd_tick, cmd_len, cmd_bytes,
                             cmds[ci][199:136], cmds[ci][132:128], cmds[ci][127:0]);
                errors = errors + 1;
            end
            ci = ci + 1;
            if (cmd_tick < raw_tick) begin
                $display("FAIL: command %0d computed after its tick", ci - 1);
                errors = errors + 1;
            end
        end
    end

    initial begin
        errors = 0; pi = 0; ei = 0; ci = 0;
        $readmemh("st_cfg.hex", cfg);
        n_regs = cfg[0]; n_coefs = cfg[1]; n_pairs = cfg[2]; n_expect = cfg[3]; n_cmds = cfg[4];
        last_sec = cfg[5]; sec2 = cfg[6];
        $readmemh("st_regs.hex", regs);
        $readmemh("st_coefs.hex", coefs);
        $readmemh("st_pairs.hex", pairs);
        $readmemh("st_expect.hex", expv);
        $readmemh("st_cmds.hex", cmds);
        repeat (5) @(posedge clk);
        rst = 1'b0;
        for (i = 0; i < n_regs; i = i + 1) reg_write(regs[i][43:32], regs[i][31:0]);
        for (i = 0; i < n_coefs; i = i + 1) begin
            reg_write(12'h480, {16'd0, coefs[i][66:64], coefs[i][60:48]});
            reg_write(12'h484, {16'd0, coefs[i][47:32]});
            reg_write(12'h488, coefs[i][31:0]);
        end
        reg_write(12'h440, 32'b1011);  // ENABLE, ROLE_B, LOG
        // run 2: FC steps, enabled afresh during the second before sec2
        wait (raw_tick == (sec2 - 1) * SECOND + 1000);
        reg_write(12'h440, 32'b1010);
        reg_write(12'h440, 32'b1111);  // ENABLE, ROLE_B, STEPS, LOG
        wait (raw_tick == last_sec * SECOND + 2000);
        if (ei != n_expect) begin
            $display("FAIL: %0d entries, want %0d", ei, n_expect);
            errors = errors + 1;
        end
        if (ci != n_cmds) begin
            $display("FAIL: %0d commands, want %0d", ci, n_cmds);
            errors = errors + 1;
        end
        reg_raddr = 12'h4B0;
        #1;
        $display("pairs formed: %0d, entries %0d, commands %0d", reg_rdata, ei, ci);
        if (errors == 0) $display("PASS tb_p13_steer");
        else $display("FAIL: %0d errors", errors);
        $finish;
    end
endmodule
