`timescale 1ns / 1ps
// One copy of a code memory: CHIPS chips packed 32 to a word (chip n is bit
// n % 32 of word n / 32, 1 for +1), written from the burst memory port and
// read by one reader.  Each reader of a code has its own copy, so no reader
// waits for another.  `rd_chip` is the chip at `rd_addr` two clocks later
// (block RAM output register, then the bit select).  `we` holds byte enables.
module p_chip_mem #(
    parameter integer AW = 11   // word address bits: 2048 words = 65536 chips
) (
    input  wire          clk,
    input  wire [3:0]    we,
    input  wire [AW-1:0] waddr,
    input  wire [31:0]   wdata,
    input  wire [AW+4:0] rd_addr,  // chip index
    output reg           rd_chip,
    output wire [31:0]   rd_word   // the word of rd_addr, one clock later
);
    (* ram_style = "block" *)
    reg [31:0] mem [0:(1 << AW) - 1];
    reg [31:0] q;
    reg [4:0]  bit_d;
    integer    c;

    always @(posedge clk) begin
        for (c = 0; c < 4; c = c + 1)
            if (we[c]) mem[waddr][8*c +: 8] <= wdata[8*c +: 8];
        q       <= mem[rd_addr[AW+4:5]];
        bit_d   <= rd_addr[4:0];
        rd_chip <= q[bit_d];
    end
    assign rd_word = q;
endmodule
