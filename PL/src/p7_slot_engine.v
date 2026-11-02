`timescale 1ns / 1ps
// P7: the slot engine (docs/ABI.md 4.8).  From each PPS edge it runs the
// 2 ms ping-pong without a command per slot:
//
//   slot k = 0 .. SLOTS-1 starts at PPS_TICK + k * SLOT_TICKS
//   own slot  (k even for node A, odd for node B): a burst at start + tx_offset
//   peer slot:                                      a window at start + rx_offset
//
// The configuration is taken at each PPS edge.  Each event is armed LEAD
// ticks before its tick (the burst generator, the window capture and P4 then
// wait for the tick themselves).  A slot is skipped and counted when its tick
// is too close or past when the engine reaches it, when its burst would run
// into the next slot (tx_offset + burst beats > SLOT_TICKS) or its window
// would still be open at this node's next burst (rx_offset + span >
// SLOT_TICKS + tx_offset), or when the unit it needs is busy.  Without
// time_valid or pps_locked, or after a burst FAULT, the engine idles and
// starts again at the next PPS edge.
module p7_slot_engine #(
    parameter [31:0] SLOT_TICKS = 32'd200000,
    parameter [31:0] SLOTS      = 32'd500,
    parameter [31:0] LEAD       = 32'd128,
    parameter integer AW        = 11
) (
    input  wire          clk,
    input  wire          rst,
    input  wire [63:0]   raw_tick,
    input  wire          time_valid,
    input  wire          pps_locked,
    input  wire          pps_event,
    input  wire [63:0]   pps_tick,
    input  wire [31:0]   pps_count,
    // Configuration registers (P2), taken at each PPS edge.
    input  wire [2:0]    cfg_control,     // SLOTCTL_* bits
    input  wire [31:0]   cfg_tx_offset,
    input  wire [31:0]   cfg_rx_offset,
    input  wire [AW+5:0] cfg_prn_chips,
    input  wire [15:0]   cfg_guard_chips,
    input  wire [31:0]   window_beats,    // RX_WINDOW_BEATS
    input  wire [31:0]   packet_chips,    // PACKET_BITS * PACKET_SPREAD
    input  wire          copy_request,    // SLOT_COPY written with 1
    // Units.
    input  wire          tx_busy,
    input  wire          tx_fault,
    input  wire          rx_busy,
    // Active configuration (this second).
    output reg           running,
    output reg  [8:0]    slot,
    output reg  [31:0]   skipped_count,
    output reg           copy_pending,
    output reg           role_b,
    output reg           packets,
    output reg  [AW+5:0] prn_chips,
    output reg  [15:0]   guard_chips,
    output wire [31:0]   burst_beats,     // of the active configuration
    output wire [31:0]   span_beats,
    // Arm pulses.  The second's PPS_COUNT and PPS_TICK and the slot go along.
    output reg           tx_arm,
    output reg           rx_arm,
    output reg           rx_copy,         // with rx_arm: P4 copies this window to DDR
    output reg  [63:0]   ev_tick,
    output reg  [8:0]    ev_slot,
    output reg  [31:0]   sec_count,
    output reg  [63:0]   sec_tick
);
    `include "src/p_regs.vh"

    reg [31:0] tx_offset, rx_offset;

    assign burst_beats = {{(26-AW){1'b0}}, prn_chips} + 32'd10 +
                         (packets ? {16'd0, guard_chips} + packet_chips : 32'd0);
    assign span_beats  = window_beats + {16'd0, guard_chips} + packet_chips + 32'd1;

    localparam [1:0] S_IDLE = 2'd0, S_SCHED = 2'd1, S_WAIT = 2'd2, S_DONE = 2'd3;
    reg [1:0]  state;
    reg [63:0] slot_start;

    wire        own      = (slot[0] == role_b);
    wire [63:0] ev       = slot_start + {32'd0, own ? tx_offset : rx_offset};
    wire        bad_tx   = ({1'b0, tx_offset} + {1'b0, burst_beats}) > {1'b0, SLOT_TICKS};
    wire        bad_rx   = ({1'b0, rx_offset} + {1'b0, span_beats}) > ({1'b0, SLOT_TICKS} + {1'b0, tx_offset});
    wire        too_late = ev < raw_tick + {32'd0, LEAD} + 64'd2;
    wire        unit_busy = own ? (tx_busy || tx_fault) : rx_busy;
    wire        last_slot = ({23'd0, slot} == SLOTS - 32'd1);

    always @(posedge clk) begin
        tx_arm  <= 1'b0;
        rx_arm  <= 1'b0;
        rx_copy <= 1'b0;
        if (copy_request) copy_pending <= 1'b1;
        if (rst) begin
            state         <= S_IDLE;
            running       <= 1'b0;
            slot          <= 9'd0;
            skipped_count <= 32'd0;
            copy_pending  <= 1'b0;
            role_b        <= 1'b0;
            packets       <= 1'b0;
            tx_offset     <= 32'd0;
            rx_offset     <= 32'd0;
            prn_chips     <= {(AW+6){1'b0}};
            guard_chips   <= 16'd0;
            slot_start    <= 64'd0;
            ev_tick       <= 64'd0;
            ev_slot       <= 9'd0;
            sec_count     <= 32'd0;
            sec_tick      <= 64'd0;
        end else if (pps_event) begin
            if (state == S_WAIT) skipped_count <= skipped_count + 32'd1;
            role_b      <= cfg_control[SLOTCTL_ROLE_B];
            packets     <= cfg_control[SLOTCTL_PACKETS];
            tx_offset   <= cfg_tx_offset;
            rx_offset   <= cfg_rx_offset;
            prn_chips   <= cfg_prn_chips;
            guard_chips <= cfg_guard_chips;
            slot        <= 9'd0;
            slot_start  <= pps_tick;
            sec_count   <= pps_count;
            sec_tick    <= pps_tick;
            if (cfg_control[SLOTCTL_ENABLE] && time_valid && pps_locked && !tx_fault) begin
                running <= 1'b1;
                state   <= S_SCHED;
            end else begin
                running <= 1'b0;
                state   <= S_IDLE;
            end
        end else if (running && (!time_valid || !pps_locked || tx_fault)) begin
            running <= 1'b0;
            state   <= S_IDLE;
        end else begin
            case (state)
                S_SCHED: begin
                    if ((own ? bad_tx : bad_rx) || too_late || unit_busy) begin
                        skipped_count <= skipped_count + 32'd1;
                        if (last_slot) state <= S_DONE;
                        else begin
                            slot       <= slot + 9'd1;
                            slot_start <= slot_start + {32'd0, SLOT_TICKS};
                        end
                    end else begin
                        state <= S_WAIT;
                    end
                end
                S_WAIT: if (raw_tick + {32'd0, LEAD} >= ev) begin
                    ev_tick <= ev;
                    ev_slot <= slot;
                    if (own) begin
                        tx_arm <= 1'b1;
                    end else begin
                        rx_arm <= 1'b1;
                        if (copy_pending || copy_request) begin
                            rx_copy      <= 1'b1;
                            copy_pending <= 1'b0;
                        end
                    end
                    if (last_slot) state <= S_DONE;
                    else begin
                        slot       <= slot + 9'd1;
                        slot_start <= slot_start + {32'd0, SLOT_TICKS};
                        state      <= S_SCHED;
                    end
                end
                default: ;
            endcase
        end
    end
endmodule
