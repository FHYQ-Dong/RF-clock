`timescale 1ns / 1ps
// P12: the serial line to the FS725 (docs/ABI.md 4.15), through a TTL-to-RS-232
// module on a Pmod.  8 data bits, no parity, one stop bit, least significant
// bit first, BIT_TICKS ticks a bit (10417 for 9600 baud).
//
// Two sources share the transmitter.  The steering engine (P13) hands over its
// FC command with the tick at which it is to start; it starts at that tick, or
// as soon as the line is free if it is busy then (counted as late).  The ARM
// fills a line buffer of LINE_BYTES bytes and asks for it to be sent; a line
// starts only while no command is pending and, while P13 reserves the line, only
// if it ends at least one bit before P13's next command (reserve_tick).
//
// The receiver takes the AND of two input pins (only one of them is wired to the
// module: which one is not known yet, the other is pulled high, which reads as
// an idle line) and puts each byte with a good stop bit into a FIFO of RX_DEPTH
// bytes, which the ARM pops by reading.  The transmitter drives both output pins.
module p12_serial #(
    parameter integer LINE_BYTES = 32,
    parameter integer RX_DEPTH   = 256,
    parameter integer CMD_BYTES  = 16
) (
    input  wire                     clk,
    input  wire                     rst,
    input  wire [63:0]              raw_tick,
    // configuration
    input  wire                     enable,      // 0: TX idle high, RX ignored
    input  wire [15:0]              bit_ticks,
    // the ARM's line
    input  wire                     line_push,   // append line_byte
    input  wire [7:0]               line_byte,
    input  wire                     line_send,   // send the buffered line
    input  wire                     line_clear,
    output wire                     line_queued,
    output reg  [5:0]               line_len,
    output reg  [31:0]              lines_sent,
    output reg  [63:0]              line_tick,   // when the last ARM line started
    // P13's command
    input  wire                     cmd_valid,   // pulse: bytes, length and start tick
    input  wire [63:0]              cmd_tick,
    input  wire [4:0]               cmd_len,
    input  wire [8*CMD_BYTES-1:0]   cmd_bytes,   // byte 0 in [7:0], sent first
    input  wire                     reserve_valid,
    input  wire [63:0]              reserve_tick,
    output wire                     cmd_pending,
    output reg                      cmd_started, // pulse
    output reg                      cmd_late,    // with cmd_started: later than cmd_tick
    output reg  [63:0]              cmd_start_tick,
    output reg  [31:0]              cmds_sent,
    output wire                     tx_active,
    // receiver FIFO
    input  wire                     rx_pop,
    input  wire                     rx_flush,
    output wire [7:0]               rx_byte,
    output wire                     rx_valid,
    output wire [8:0]               rx_level,
    output reg  [31:0]              rx_count,
    output reg  [15:0]              rx_framing_count,
    output reg  [15:0]              rx_overrun_count,
    // pins
    output reg                      txd,
    input  wire                     rxd_a,
    input  wire                     rxd_b,
    output wire                     rxd_a_level,
    output wire                     rxd_b_level,
    output reg  [7:0]               rxd_a_edges, // falling edges, wrap
    output reg  [7:0]               rxd_b_edges
);
    // ------------------------------------------------------------------
    // Transmitter
    // ------------------------------------------------------------------
    reg [7:0]  line_mem [0:LINE_BYTES-1];
    reg        line_q;           // a line is queued
    reg [4:0]  pend_len;
    reg [8*CMD_BYTES-1:0] pend_bytes;
    reg [63:0] pend_tick;
    reg        pend;

    assign line_queued = line_q;
    assign cmd_pending = pend;

    localparam [1:0] T_IDLE = 2'd0, T_SEND = 2'd1;
    reg [1:0]  tstate;
    reg        tsrc_cmd;         // the byte source of the frame in flight
    reg [5:0]  tbyte;            // byte index
    reg [5:0]  tcount;           // bytes of the frame
    reg [3:0]  tbit;             // 0 start, 1..8 data, 9 stop
    reg [15:0] ttimer;
    reg [9:0]  tshift;
    reg [8*CMD_BYTES-1:0] tbytes;  // the command's bytes, copied at its start

    assign tx_active = (tstate != T_IDLE);

    // the ARM line's duration in ticks: len * 10 * bit_ticks (len < 64, bit_ticks < 2^16)
    reg  [25:0] line_dur;
    always @(posedge clk) line_dur <= {20'd0, line_len} * ({10'd0, bit_ticks} * 26'd10);
    wire line_fits = !reserve_valid ||
                     (raw_tick + {38'd0, line_dur} + {48'd0, bit_ticks} <= reserve_tick);

    always @(posedge clk) begin
        cmd_started <= 1'b0;
        cmd_late    <= 1'b0;
        if (rst) begin
            tstate     <= T_IDLE;
            txd        <= 1'b1;
            line_q     <= 1'b0;
            line_len   <= 6'd0;
            lines_sent <= 32'd0;
            line_tick  <= 64'd0;
            pend       <= 1'b0;
            cmds_sent  <= 32'd0;
            cmd_start_tick <= 64'd0;
            tsrc_cmd   <= 1'b0;
            tbyte      <= 6'd0;
            tcount     <= 6'd0;
            tbit       <= 4'd0;
            ttimer     <= 16'd0;
            tshift     <= 10'h3FF;
        end else begin
            // the ARM's line buffer
            if (line_clear && !(tx_active && !tsrc_cmd)) begin
                line_len <= 6'd0;
                line_q   <= 1'b0;
            end else begin
                if (line_push && !line_q && line_len < LINE_BYTES) begin
                    line_mem[line_len[4:0]] <= line_byte;
                    line_len <= line_len + 6'd1;
                end
                if (line_send && line_len != 6'd0) line_q <= 1'b1;
            end
            // P13's command: the newest one replaces one not yet started
            if (cmd_valid) begin
                pend       <= 1'b1;
                pend_len   <= cmd_len;
                pend_bytes <= cmd_bytes;
                pend_tick  <= cmd_tick;
            end
            case (tstate)
                T_IDLE: begin
                    txd <= 1'b1;
                    if (!enable) begin
                        // nothing leaves; a pending command is dropped
                        pend <= cmd_valid;
                    end else if (pend && raw_tick >= pend_tick) begin
                        tstate   <= T_SEND;
                        tsrc_cmd <= 1'b1;
                        tbyte    <= 6'd0;
                        tcount   <= {1'b0, pend_len};
                        tbit     <= 4'd0;
                        ttimer   <= bit_ticks - 16'd1;
                        tshift   <= {1'b1, pend_bytes[7:0], 1'b0};
                        tbytes   <= pend_bytes;
                        txd      <= 1'b0;
                        pend     <= cmd_valid;  // a newer command arriving now stays pending
                        cmd_started    <= 1'b1;
                        cmd_late       <= (raw_tick != pend_tick);
                        cmd_start_tick <= raw_tick;
                        cmds_sent      <= cmds_sent + 32'd1;
                    end else if (line_q && !pend && !cmd_valid && line_fits) begin
                        tstate   <= T_SEND;
                        tsrc_cmd <= 1'b0;
                        tbyte    <= 6'd0;
                        tcount   <= line_len;
                        tbit     <= 4'd0;
                        ttimer   <= bit_ticks - 16'd1;
                        tshift   <= {1'b1, line_mem[0], 1'b0};
                        txd      <= 1'b0;
                        line_tick <= raw_tick;
                    end
                end
                T_SEND: begin
                    if (ttimer != 16'd0) begin
                        ttimer <= ttimer - 16'd1;
                    end else begin
                        ttimer <= bit_ticks - 16'd1;
                        if (tbit != 4'd9) begin
                            tbit   <= tbit + 4'd1;
                            txd    <= tshift[1];
                            tshift <= {1'b1, tshift[9:1]};
                        end else if (tbyte + 6'd1 < tcount) begin
                            // next byte: start bit now
                            tbyte  <= tbyte + 6'd1;
                            tbit   <= 4'd0;
                            txd    <= 1'b0;
                            tshift <= {1'b1, (tsrc_cmd ? tbytes[8*(tbyte[3:0]+4'd1) +: 8]
                                                       : line_mem[tbyte[4:0]+5'd1]), 1'b0};
                        end else begin
                            tstate <= T_IDLE;
                            txd    <= 1'b1;
                            if (!tsrc_cmd) begin
                                line_q     <= 1'b0;
                                line_len   <= 6'd0;
                                lines_sent <= lines_sent + 32'd1;
                            end
                        end
                    end
                end
                default: tstate <= T_IDLE;
            endcase
        end
    end

    // ------------------------------------------------------------------
    // Receiver
    // ------------------------------------------------------------------
    reg [2:0] sync_a, sync_b;
    always @(posedge clk) begin
        sync_a <= {sync_a[1:0], rxd_a};
        sync_b <= {sync_b[1:0], rxd_b};
    end
    assign rxd_a_level = sync_a[1];
    assign rxd_b_level = sync_b[1];
    wire rx = sync_a[1] & sync_b[1];

    always @(posedge clk) begin
        if (rst) begin
            rxd_a_edges <= 8'd0;
            rxd_b_edges <= 8'd0;
        end else begin
            if (sync_a[2] && !sync_a[1]) rxd_a_edges <= rxd_a_edges + 8'd1;
            if (sync_b[2] && !sync_b[1]) rxd_b_edges <= rxd_b_edges + 8'd1;
        end
    end

    localparam integer RW = $clog2(RX_DEPTH);
    reg [7:0]  rx_mem [0:RX_DEPTH-1];
    reg [RW:0] rx_head, rx_tail;
    assign rx_level = rx_head - rx_tail;
    assign rx_valid = (rx_head != rx_tail);
    assign rx_byte  = rx_mem[rx_tail[RW-1:0]];

    localparam [1:0] R_IDLE = 2'd0, R_START = 2'd1, R_DATA = 2'd2, R_STOP = 2'd3;
    reg [1:0]  rstate;
    reg [15:0] rtimer;
    reg [2:0]  rbit;
    reg [7:0]  rshift;
    reg        rx_prev;

    always @(posedge clk) begin
        if (rst) begin
            rstate  <= R_IDLE;
            rx_head <= {(RW+1){1'b0}};
            rx_tail <= {(RW+1){1'b0}};
            rx_count <= 32'd0;
            rx_framing_count <= 16'd0;
            rx_overrun_count <= 16'd0;
            rx_prev <= 1'b1;
            rtimer  <= 16'd0;
            rbit    <= 3'd0;
            rshift  <= 8'd0;
        end else begin
            rx_prev <= rx;
            if (rx_flush) rx_tail <= rx_head;
            else if (rx_pop && rx_valid) rx_tail <= rx_tail + 1'b1;
            case (rstate)
                R_IDLE: if (enable && rx_prev && !rx) begin
                    rstate <= R_START;
                    rtimer <= {1'b0, bit_ticks[15:1]};  // to the middle of the start bit
                end
                R_START: if (rtimer != 16'd0) rtimer <= rtimer - 16'd1;
                    else if (rx) rstate <= R_IDLE;      // a glitch, not a start bit
                    else begin
                        rstate <= R_DATA;
                        rtimer <= bit_ticks - 16'd1;
                        rbit   <= 3'd0;
                    end
                R_DATA: if (rtimer != 16'd0) rtimer <= rtimer - 16'd1;
                    else begin
                        rtimer <= bit_ticks - 16'd1;
                        rshift <= {rx, rshift[7:1]};
                        rbit   <= rbit + 3'd1;
                        if (rbit == 3'd7) rstate <= R_STOP;
                    end
                R_STOP: if (rtimer != 16'd0) rtimer <= rtimer - 16'd1;
                    else begin
                        rstate <= R_IDLE;
                        if (!rx) begin
                            rx_framing_count <= rx_framing_count + 16'd1;
                        end else if (rx_level == RX_DEPTH[RW:0]) begin
                            rx_overrun_count <= rx_overrun_count + 16'd1;
                        end else begin
                            rx_mem[rx_head[RW-1:0]] <= rshift;
                            rx_head  <= rx_head + 1'b1;
                            rx_count <= rx_count + 32'd1;
                        end
                    end
            endcase
        end
    end
endmodule
