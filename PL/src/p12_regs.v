`timescale 1ns / 1ps
// P12's registers (0x400 .. 0x43F, docs/ABI.md 4.15) around the serial line
// (p12_serial).  P2 forwards the range; a read of SERIAL_RX takes the byte out
// of the FIFO in the cycle after the read, a read of SERIAL_LINE_TICK_LO
// latches its HI word.
module p12_regs (
    input  wire          clk,
    input  wire          rst,
    input  wire [63:0]   raw_tick,
    input  wire          reg_wr,
    input  wire [11:0]   reg_waddr,
    input  wire [31:0]   reg_wdata,
    input  wire [11:0]   reg_waddr_live,
    input  wire [31:0]   reg_wdata_live,
    output reg           reg_wr_ok,
    input  wire [11:0]   reg_raddr,
    output reg  [31:0]   reg_rdata,
    output reg           reg_rd_ok,
    input  wire          reg_rd,
    input  wire [11:0]   reg_rd_addr,
    // P13
    input  wire          cmd_valid,
    input  wire [63:0]   cmd_tick,
    input  wire [4:0]    cmd_len,
    input  wire [127:0]  cmd_bytes,
    input  wire          reserve_valid,
    input  wire [63:0]   reserve_tick,
    output wire          cmd_pending,
    output wire          cmd_started,
    output wire          cmd_late,
    output wire [63:0]   cmd_start_tick,
    // pins
    output wire          txd,
    input  wire          rxd_a,
    input  wire          rxd_b
);
    `include "src/p_regs.vh"

    reg  [15:0] bit_ticks;
    reg         enable;
    reg         line_push, line_send, line_clear, rx_flush, rx_pop;
    reg  [7:0]  line_byte;
    reg  [31:0] tick_snap_hi;

    wire        line_queued, tx_active, rx_valid, rxd_a_level, rxd_b_level;
    wire [5:0]  line_len;
    wire [31:0] lines_sent, cmds_sent, rx_count;
    wire [63:0] line_tick;
    wire [7:0]  rx_byte, rxd_a_edges, rxd_b_edges;
    wire [8:0]  rx_level;
    wire [15:0] rx_framing_count, rx_overrun_count;

    p12_serial u_serial (
        .clk (clk), .rst (rst), .raw_tick (raw_tick),
        .enable (enable), .bit_ticks (bit_ticks),
        .line_push (line_push), .line_byte (line_byte), .line_send (line_send), .line_clear (line_clear),
        .line_queued (line_queued), .line_len (line_len), .lines_sent (lines_sent), .line_tick (line_tick),
        .cmd_valid (cmd_valid), .cmd_tick (cmd_tick), .cmd_len (cmd_len), .cmd_bytes (cmd_bytes),
        .reserve_valid (reserve_valid), .reserve_tick (reserve_tick),
        .cmd_pending (cmd_pending), .cmd_started (cmd_started), .cmd_late (cmd_late),
        .cmd_start_tick (cmd_start_tick), .cmds_sent (cmds_sent), .tx_active (tx_active),
        .rx_pop (rx_pop), .rx_flush (rx_flush), .rx_byte (rx_byte), .rx_valid (rx_valid), .rx_level (rx_level),
        .rx_count (rx_count), .rx_framing_count (rx_framing_count), .rx_overrun_count (rx_overrun_count),
        .txd (txd), .rxd_a (rxd_a), .rxd_b (rxd_b), .rxd_a_level (rxd_a_level), .rxd_b_level (rxd_b_level),
        .rxd_a_edges (rxd_a_edges), .rxd_b_edges (rxd_b_edges)
    );

    always @* begin
        case (reg_waddr_live)
            REG_SERIAL_CONTROL: reg_wr_ok = 1'b1;
            REG_SERIAL_CONFIG:  reg_wr_ok = (reg_wdata_live[15:0] >= SERIAL_BIT_TICKS_MIN);
            // a byte only while no line waits and there is room
            REG_SERIAL_LINE:    reg_wr_ok = !line_queued && (line_len < 6'd32);
            default:            reg_wr_ok = 1'b0;
        endcase
    end

    always @(posedge clk) begin
        line_push  <= 1'b0;
        line_send  <= 1'b0;
        line_clear <= 1'b0;
        rx_flush   <= 1'b0;
        rx_pop     <= 1'b0;
        if (rst) begin
            bit_ticks <= 16'd10417;
            enable    <= 1'b0;
            line_byte <= 8'd0;
            tick_snap_hi <= 32'd0;
        end else begin
            if (reg_wr) begin
                case (reg_waddr)
                    REG_SERIAL_CONTROL: begin
                        line_send  <= reg_wdata[SERCTL_SEND];
                        line_clear <= reg_wdata[SERCTL_CLEAR];
                        rx_flush   <= reg_wdata[SERCTL_RX_FLUSH];
                    end
                    REG_SERIAL_CONFIG: begin
                        bit_ticks <= reg_wdata[15:0];
                        enable    <= reg_wdata[SERCFG_ENABLE];
                    end
                    REG_SERIAL_LINE: begin
                        line_push <= 1'b1;
                        line_byte <= reg_wdata[7:0];
                    end
                    default: ;
                endcase
            end
            if (reg_rd) begin
                if (reg_rd_addr == REG_SERIAL_RX) rx_pop <= 1'b1;
                if (reg_rd_addr == REG_SERIAL_LINE_TICK_LO) tick_snap_hi <= line_tick[63:32];
            end
        end
    end

    wire [8:0] rx_left = rx_valid ? rx_level - 9'd1 : 9'd0;

    always @* begin
        reg_rd_ok = 1'b1;
        reg_rdata = 32'd0;
        case (reg_raddr)
            REG_SERIAL_CONTROL:      reg_rdata = 32'd0;
            REG_SERIAL_CONFIG:       reg_rdata = {15'd0, enable, bit_ticks};
            REG_SERIAL_STATUS:       reg_rdata = {3'd0, rx_level, 2'd0, rxd_b_level, rxd_a_level, 2'd0, line_len,
                                                  5'd0, cmd_pending, tx_active, line_queued};
            REG_SERIAL_LINE:         reg_rdata = 32'd0;
            REG_SERIAL_RX:           reg_rdata = {7'd0, rx_left, 7'd0, rx_valid, rx_valid ? rx_byte : 8'd0};
            REG_SERIAL_LINES_CNT:    reg_rdata = lines_sent;
            REG_SERIAL_CMDS_CNT:     reg_rdata = cmds_sent;
            REG_SERIAL_RX_CNT:       reg_rdata = rx_count;
            REG_SERIAL_ERR_CNT:      reg_rdata = {rx_overrun_count, rx_framing_count};
            REG_SERIAL_RX_EDGES:     reg_rdata = {16'd0, rxd_b_edges, rxd_a_edges};
            REG_SERIAL_LINE_TICK_LO: reg_rdata = line_tick[31:0];
            REG_SERIAL_LINE_TICK_HI: reg_rdata = tick_snap_hi;
            default:                 reg_rd_ok = 1'b0;
        endcase
    end
endmodule
