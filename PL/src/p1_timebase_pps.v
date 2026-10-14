`timescale 1ns / 1ps
// P1: raw tick and PPS capture.
//
// raw_tick counts every fabric clock from reset, never wraps (saturates and
// reports exhausted), and is never reset at frame boundaries.
//
// pps_tick is the raw_tick of the clock cycle in which the PPS rising edge
// arrived at the synchronizer input; the fixed synchronizer latency is
// removed.  The FS725 10 MHz and PPS are coherent with the fabric clock, so
// every period should be exactly PPS_PERIOD ticks.  A period within
// PPS_TOLERANCE but not exact is counted in pps_jitter_count: it means the
// PPS edge sits next to the sampling edge and should be moved with IDELAY.
// A larger deviation or a missing pulse is a fault event and drops lock.
//
// The board offers two conditioned copies of the PPS input (IRIG_COMP_OUT
// from the comparator, IRIG_TRIG_OUT from the Schmitt trigger).  Both are
// synchronized; pps_src_trig selects one and pps_invert flips it, so the
// timed edge can be chosen by software without a rebuild.  Changing either
// restarts PPS acquisition (no fault).  The level and a wrapping rising-edge
// count of each copy are exported for bring-up checks.
module p1_timebase_pps #(
    parameter [31:0] PPS_PERIOD     = 32'd100_000_000,
    parameter [31:0] PPS_TOLERANCE  = 32'd1,
    parameter [31:0] PPS_LOCK_COUNT = 32'd4
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        pps_comp_async,
    input  wire        pps_trig_async,
    input  wire        pps_src_trig,
    input  wire        pps_invert,
    output wire        pps_comp_level,
    output wire        pps_trig_level,
    output reg  [7:0]  pps_comp_edges,
    output reg  [7:0]  pps_trig_edges,
    output reg  [63:0] raw_tick,
    output wire        raw_tick_exhausted,
    output reg         pps_event,
    output reg  [63:0] pps_tick,
    output reg  [31:0] pps_count,
    output reg  [31:0] pps_last_period,
    output reg  [31:0] pps_jitter_count,
    output wire        pps_locked,
    output reg         pps_fault_event
);
    localparam [63:0] SYNC_LATENCY = 64'd2;

    assign raw_tick_exhausted = &raw_tick;

    always @(posedge clk) begin
        if (rst)                      raw_tick <= 64'd0;
        else if (!raw_tick_exhausted) raw_tick <= raw_tick + 64'd1;
    end

    (* ASYNC_REG = "TRUE" *) reg [1:0] comp_sync;
    (* ASYNC_REG = "TRUE" *) reg [1:0] trig_sync;
    always @(posedge clk) begin
        comp_sync <= {comp_sync[0], pps_comp_async};
        trig_sync <= {trig_sync[0], pps_trig_async};
    end
    assign pps_comp_level = comp_sync[1];
    assign pps_trig_level = trig_sync[1];

    reg comp_prev, trig_prev;
    always @(posedge clk) begin
        comp_prev <= comp_sync[1];
        trig_prev <= trig_sync[1];
        if (rst) begin
            pps_comp_edges <= 8'd0;
            pps_trig_edges <= 8'd0;
        end else begin
            if (comp_sync[1] & ~comp_prev) pps_comp_edges <= pps_comp_edges + 8'd1;
            if (trig_sync[1] & ~trig_prev) pps_trig_edges <= pps_trig_edges + 8'd1;
        end
    end

    // Selected and optionally inverted PPS; same latency as either copy.
    wire pps_s = (pps_src_trig ? trig_sync[1] : comp_sync[1]) ^ pps_invert;

    reg [1:0] cfg_d;
    always @(posedge clk) cfg_d <= {pps_src_trig, pps_invert};
    wire cfg_change = !rst && (cfg_d != {pps_src_trig, pps_invert});

    reg pps_prev;
    always @(posedge clk)
        // Reset to 1 so a PPS that is already high at reset is not an edge.
        pps_prev <= rst ? 1'b1 : pps_s;
    wire pps_rise = pps_s & ~pps_prev;

    wire [63:0] edge_tick = raw_tick - SYNC_LATENCY;
    wire [63:0] delta64   = edge_tick - pps_tick;
    wire [31:0] delta     = (|delta64[63:32]) ? 32'hFFFF_FFFF : delta64[31:0];
    wire [31:0] diff_abs  = (delta >= PPS_PERIOD) ? delta - PPS_PERIOD
                                                  : PPS_PERIOD - delta;

    reg        have_prev;
    reg        missing_reported;
    reg [31:0] since_last;
    reg [31:0] good_count;

    assign pps_locked = (good_count >= PPS_LOCK_COUNT);

    always @(posedge clk) begin
        pps_event       <= 1'b0;
        pps_fault_event <= 1'b0;
        if (rst) begin
            pps_tick         <= 64'd0;
            pps_count        <= 32'd0;
            pps_last_period  <= 32'd0;
            pps_jitter_count <= 32'd0;
            have_prev        <= 1'b0;
            missing_reported <= 1'b0;
            since_last       <= 32'd0;
            good_count       <= 32'd0;
        end else if (cfg_change) begin
            // A new source/polarity: the level may jump, so neither this
            // cycle's "edge" nor the old reference edge is usable.
            have_prev        <= 1'b0;
            missing_reported <= 1'b0;
            since_last       <= 32'd0;
            good_count       <= 32'd0;
        end else if (pps_rise) begin
            pps_event        <= 1'b1;
            pps_tick         <= edge_tick;
            pps_count        <= pps_count + 32'd1;
            have_prev        <= 1'b1;
            missing_reported <= 1'b0;
            since_last       <= 32'd1;
            if (have_prev) begin
                pps_last_period <= delta;
                if (diff_abs <= PPS_TOLERANCE) begin
                    if (diff_abs != 32'd0)
                        pps_jitter_count <= pps_jitter_count + 32'd1;
                    if (!pps_locked)
                        good_count <= good_count + 32'd1;
                end else begin
                    pps_fault_event <= 1'b1;
                    good_count      <= 32'd0;
                end
            end
        end else begin
            if (since_last != 32'hFFFF_FFFF)
                since_last <= since_last + 32'd1;
            if (have_prev && !missing_reported &&
                since_last > PPS_PERIOD + PPS_TOLERANCE) begin
                pps_fault_event  <= 1'b1;
                missing_reported <= 1'b1;
                good_count       <= 32'd0;
            end
        end
    end
endmodule
