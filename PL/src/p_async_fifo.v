`timescale 1ns / 1ps
// Dual-clock FIFO with Gray-coded pointers and an AXI4-Stream read side.
//
// The write side never sees backpressure: the caller checks wr_full and
// decides what a lost word means.  The read side is first-word-fall-through
// through one output register, so the memory infers a dual-clock block RAM
// with a registered read.
module p_async_fifo #(
    parameter integer DATA_W = 257,
    parameter integer ADDR_W = 9
) (
    input  wire              wr_clk,
    input  wire              wr_rst,
    input  wire              wr_en,
    input  wire [DATA_W-1:0] wr_data,
    output wire              wr_full,

    input  wire              rd_clk,
    input  wire              rd_rst,
    output reg  [DATA_W-1:0] m_tdata,
    output reg               m_tvalid,
    input  wire              m_tready
);
    reg [DATA_W-1:0] mem [0:(1 << ADDR_W) - 1];

    function [ADDR_W:0] bin2gray(input [ADDR_W:0] v);
        bin2gray = (v >> 1) ^ v;
    endfunction

    // ---------------- write domain ----------------
    reg [ADDR_W:0] wbin, wgray;
    (* ASYNC_REG = "TRUE" *) reg [ADDR_W:0] rgray_w1, rgray_w2;

    assign wr_full = (wgray == {~rgray_w2[ADDR_W:ADDR_W-1], rgray_w2[ADDR_W-2:0]});
    wire do_wr = wr_en && !wr_full;

    always @(posedge wr_clk) begin
        if (do_wr) mem[wbin[ADDR_W-1:0]] <= wr_data;
    end

    always @(posedge wr_clk) begin
        if (wr_rst) begin
            wbin     <= {(ADDR_W+1){1'b0}};
            wgray    <= {(ADDR_W+1){1'b0}};
            rgray_w1 <= {(ADDR_W+1){1'b0}};
            rgray_w2 <= {(ADDR_W+1){1'b0}};
        end else begin
            if (do_wr) begin
                wbin  <= wbin + 1'b1;
                wgray <= bin2gray(wbin + 1'b1);
            end
            rgray_w1 <= rgray;
            rgray_w2 <= rgray_w1;
        end
    end

    // ---------------- read domain ----------------
    reg [ADDR_W:0] rbin, rgray;
    (* ASYNC_REG = "TRUE" *) reg [ADDR_W:0] wgray_r1, wgray_r2;

    wire rd_empty = (rgray == wgray_r2);
    wire do_rd    = !rd_empty && (!m_tvalid || m_tready);

    always @(posedge rd_clk) begin
        if (do_rd) m_tdata <= mem[rbin[ADDR_W-1:0]];
    end

    always @(posedge rd_clk) begin
        if (rd_rst) begin
            rbin     <= {(ADDR_W+1){1'b0}};
            rgray    <= {(ADDR_W+1){1'b0}};
            wgray_r1 <= {(ADDR_W+1){1'b0}};
            wgray_r2 <= {(ADDR_W+1){1'b0}};
            m_tvalid <= 1'b0;
        end else begin
            wgray_r1 <= wgray;
            wgray_r2 <= wgray_r1;
            if (do_rd) begin
                rbin     <= rbin + 1'b1;
                rgray    <= bin2gray(rbin + 1'b1);
                m_tvalid <= 1'b1;
            end else if (m_tready) begin
                m_tvalid <= 1'b0;
            end
        end
    end
endmodule
