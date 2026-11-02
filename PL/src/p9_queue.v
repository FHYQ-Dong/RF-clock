`timescale 1ns / 1ps
// The measurement queue (docs/ABI.md 4.12): a ring of ENTRIES entries of 16
// words in block RAM, read by the ARM through an AXI BRAM Controller (byte
// address, read latency 1; writes from that side are ignored).
//
// Two producers (P8's OWN entries, P9's PEER entries) each hold an entry and
// a request until acknowledged; the queue takes one at a time, OWN first.  It
// puts the entry sequence (mod 2^16, counting dropped entries too) into word
// 0 [31:16], writes the 16 words at `head`, then advances `head`.  An entry
// for a full ring (head + 1 == tail) is dropped and counted.
module p9_queue #(
    parameter integer ENTRIES = 1024
) (
    input  wire          clk,
    input  wire          rst,
    input  wire          own_req,
    input  wire [511:0]  own_entry,
    output reg           own_ack,
    input  wire          peer_req,
    input  wire [511:0]  peer_entry,
    output reg           peer_ack,
    input  wire [31:0]   tail,
    output reg  [31:0]   head,
    output reg  [31:0]   dropped_count,
    // AXI BRAM Controller port (read only).
    input  wire          bram_en,
    input  wire [15:0]   bram_addr,
    output reg  [31:0]   bram_rddata
);
    localparam integer EW = $clog2(ENTRIES);

    (* ram_style = "block" *)
    reg [31:0] mem [0:16*ENTRIES-1];

    reg         busy;
    reg [511:0] entry;
    reg [3:0]   w;
    reg [15:0]  seq;

    wire [31:0] next_head = (head == ENTRIES - 1) ? 32'd0 : head + 32'd1;
    wire        full      = (next_head == tail);

    always @(posedge clk) begin
        if (busy) mem[{head[EW-1:0], w}] <= entry[32*w +: 32];
        if (bram_en) bram_rddata <= mem[bram_addr[EW+5:2]];
    end

    always @(posedge clk) begin
        own_ack  <= 1'b0;
        peer_ack <= 1'b0;
        if (rst) begin
            busy          <= 1'b0;
            head          <= 32'd0;
            dropped_count <= 32'd0;
            seq           <= 16'd0;
            w             <= 4'd0;
        end else if (busy) begin
            w <= w + 4'd1;
            if (w == 4'd15) begin
                busy <= 1'b0;
                head <= next_head;
            end
        end else if ((own_req && !own_ack) || (peer_req && !peer_ack)) begin
            // take one; the producer drops its request in the cycle after the ack
            if (own_req && !own_ack) begin
                own_ack <= 1'b1;
                entry   <= {own_entry[511:32], seq, own_entry[15:0]};
            end else begin
                peer_ack <= 1'b1;
                entry    <= {peer_entry[511:32], seq, peer_entry[15:0]};
            end
            seq <= seq + 16'd1;
            if (full) dropped_count <= dropped_count + 32'd1;
            else begin
                busy <= 1'b1;
                w    <= 4'd0;
            end
        end
    end
endmodule
