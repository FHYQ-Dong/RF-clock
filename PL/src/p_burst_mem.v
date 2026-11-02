`timescale 1ns / 1ps
// The burst memory window (docs/ABI.md 4.9), AXI BRAM Controller port A
// (32-bit words, byte address, read latency 1):
//
//   0x0000 .. 0x1FFF  PRN or acquisition code, 32 chips a word
//   0x2000 .. 0x3FFF  packet code
//   0x4000 .. 0x4140  taps: tap k a signed 16-bit value in bits 15:0 of word k
//
// Writes go to every reader's copy of a code (the write bus below) and to the
// taps; while `lock` (the slot engine enabled or running) they are refused
// and counted.  Reads come from a readback copy of each code and the taps.
module p_burst_mem #(
    parameter integer AW   = 11,
    parameter integer TAPS = 81
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               lock,
    input  wire               bram_en,
    input  wire [3:0]         bram_we,
    input  wire [15:0]        bram_addr,
    input  wire [31:0]        bram_wrdata,
    output reg  [31:0]        bram_rddata,
    output wire [3:0]         prn_we,
    output wire [3:0]         code_we,
    output wire [AW-1:0]      mem_waddr,
    output wire [31:0]        mem_wdata,
    output reg  [TAPS*16-1:0] taps,
    output reg  [31:0]        rej_count
);
    wire [2:0]    region = bram_addr[15:13];
    wire [AW-1:0] word   = bram_addr[AW+1:2];
    wire          write  = bram_en && (|bram_we);
    wire          ok     = write && !lock;

    assign prn_we    = (ok && region == 3'd0) ? bram_we : 4'd0;
    assign code_we   = (ok && region == 3'd1) ? bram_we : 4'd0;
    assign mem_waddr = word;
    assign mem_wdata = bram_wrdata;

    wire [31:0] prn_word, code_word;
    p_chip_mem #(.AW(AW)) u_prn_rb (
        .clk (clk), .we (prn_we), .waddr (word), .wdata (bram_wrdata),
        .rd_addr ({word, 5'd0}), .rd_chip (), .rd_word (prn_word)
    );
    p_chip_mem #(.AW(AW)) u_code_rb (
        .clk (clk), .we (code_we), .waddr (word), .wdata (bram_wrdata),
        .rd_addr ({word, 5'd0}), .rd_chip (), .rd_word (code_word)
    );

    reg [2:0]  rd_region;
    reg [31:0] rd_tap;
    integer    k;
    always @(posedge clk) begin
        if (bram_en) begin
            rd_region <= region;
            rd_tap    <= (bram_addr[12:2] < TAPS) ? {{16{taps[16*bram_addr[12:2] + 15]}}, taps[16*bram_addr[12:2] +: 16]} : 32'd0;
        end
        if (rst) begin
            taps      <= {(TAPS*16){1'b0}};
            rej_count <= 32'd0;
        end else begin
            if (write && lock) rej_count <= rej_count + 32'd1;
            if (ok && region == 3'd2 && bram_addr[12:2] < TAPS) begin
                for (k = 0; k < TAPS; k = k + 1)
                    if (bram_addr[12:2] == k) begin
                        if (bram_we[0]) taps[16*k +: 8]     <= bram_wrdata[7:0];
                        if (bram_we[1]) taps[16*k + 8 +: 8] <= bram_wrdata[15:8];
                    end
            end
        end
    end
    always @* begin
        case (rd_region)
            3'd0:    bram_rddata = prn_word;
            3'd1:    bram_rddata = code_word;
            3'd2:    bram_rddata = rd_tap;
            default: bram_rddata = 32'd0;
        endcase
    end
endmodule
