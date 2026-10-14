`timescale 1ns / 1ps
// P4: timed RX capture window into DDR through AXI DMA S2MM.
//
//   ADC AXIS -> window gate + timestamp -> async FIFO -> m_axis (S2MM)
//   (clk, fabric domain)                   (dma_clk domain)
//
// The window opens on the ADC beat that was on the bus in the cycle where
// raw_tick == target, and covers exactly `length` ticks.  The timestamp is
// taken here, before the FIFO, so DDR latency does not affect it.
//
// Windows need not be contiguous with each other, but no beat may be lost
// inside a window.  A lost beat (FIFO full or ADC tvalid low), ADC
// over-range, or time_valid loss sets a flag and the window completes as
// RC_RX_INVALID: one measurement is discarded, the system does not FAULT.
// Every window ends with exactly one TLAST beat; if the real last beat was
// lost, a zero tail beat carries TLAST so the DMA transfer still finishes.
// The window is DONE when the DMA has accepted that TLAST beat.
//
// dma_ready is the synchronized S2MM tready level: software programs the
// S2MM transfer before ringing the RX doorbell.
module p4_rx_capture #(
    parameter integer DATA_W         = 256,
    // AXI DMA simple-mode length is at most 26 bits (2**26 - 1 bytes).
    parameter [31:0]  MAX_BEATS      = 32'd2097151,
    parameter integer FIFO_ADDR_W    = 9,
    parameter [63:0]  MIN_LEAD_TICKS = 64'd64
) (
    input  wire              clk,
    input  wire              rst,
    input  wire [63:0]       raw_tick,
    input  wire              time_valid,
    // Commands from P2.
    input  wire              doorbell,
    input  wire              cancel,
    input  wire [63:0]       cmd_target,
    input  wire [31:0]       cmd_length,
    // RFDC ADC AXI4-Stream (no backpressure).
    input  wire [DATA_W-1:0] s_axis_adc_tdata,
    input  wire              s_axis_adc_tvalid,
    input  wire              adc_overrange,
    // AXI DMA S2MM stream.
    input  wire              dma_clk,
    input  wire              dma_rst,
    output wire [DATA_W-1:0] m_axis_tdata,
    output wire              m_axis_tvalid,
    input  wire              m_axis_tready,
    output wire              m_axis_tlast,
    // Status.
    output reg  [2:0]        state,
    output reg  [7:0]        result,
    output reg  [3:0]        flags,
    output reg  [31:0]       seq,
    output reg  [31:0]       beats,
    output reg  [63:0]       actual_tick,
    output reg  [31:0]       invalid_count,
    output wire              dma_ready
);
    `include "src/p_defs.vh"

    // ---------------------------------------------------------------
    // Input register: tick_d is the raw_tick of the cycle adc_d was on
    // the ADC bus.
    // ---------------------------------------------------------------
    reg [DATA_W-1:0] adc_d;
    reg              adc_v;
    reg              adc_or;
    reg [63:0]       tick_d;
    always @(posedge clk) begin
        adc_d  <= s_axis_adc_tdata;
        adc_v  <= rst ? 1'b0 : s_axis_adc_tvalid;
        adc_or <= rst ? 1'b0 : adc_overrange;
        tick_d <= raw_tick;
    end

    // ---------------------------------------------------------------
    // DMA-domain feedback.
    // ---------------------------------------------------------------
    (* ASYNC_REG = "TRUE" *) reg [1:0] ready_sync;
    always @(posedge clk) ready_sync <= {ready_sync[0], m_axis_tready};
    assign dma_ready = ready_sync[1];

    reg tlast_toggle;  // dma_clk domain: flips on each accepted TLAST beat
    always @(posedge dma_clk) begin
        if (dma_rst)
            tlast_toggle <= 1'b0;
        else if (m_axis_tvalid && m_axis_tready && m_axis_tlast)
            tlast_toggle <= ~tlast_toggle;
    end

    (* ASYNC_REG = "TRUE" *) reg [1:0] tlast_sync;
    reg tlast_sync_d;
    always @(posedge clk) begin
        tlast_sync   <= {tlast_sync[0], tlast_toggle};
        tlast_sync_d <= tlast_sync[1];
    end
    wire tlast_ack = tlast_sync[1] ^ tlast_sync_d;

    // ---------------------------------------------------------------
    // Window control.
    // ---------------------------------------------------------------
    reg [63:0] target;
    reg [63:0] end_tick;
    reg        tail_pending;
    reg        ack_seen;

    wire fifo_full;
    wire at_start  = (state == ST_ARMED) && (tick_d == target);
    wire in_window = at_start || (state == ST_RUNNING);
    wire is_last   = in_window && (tick_d == end_tick);
    wire push_beat = in_window && adc_v && !fifo_full;
    wire push_tail = (state == ST_DRAIN) && tail_pending && !fifo_full;

    wire              fifo_wr_en   = push_beat || push_tail;
    wire [DATA_W:0]   fifo_wr_data = push_tail ? {1'b1, {DATA_W{1'b0}}}
                                               : {is_last, adc_d};
    wire [DATA_W:0]   fifo_rd_data;

    p_async_fifo #(
        .DATA_W (DATA_W + 1),
        .ADDR_W (FIFO_ADDR_W)
    ) u_fifo (
        .wr_clk   (clk),
        .wr_rst   (rst),
        .wr_en    (fifo_wr_en),
        .wr_data  (fifo_wr_data),
        .wr_full  (fifo_full),
        .rd_clk   (dma_clk),
        .rd_rst   (dma_rst),
        .m_tdata  (fifo_rd_data),
        .m_tvalid (m_axis_tvalid),
        .m_tready (m_axis_tready)
    );
    assign m_axis_tdata = fifo_rd_data[DATA_W-1:0];
    assign m_axis_tlast = fifo_rd_data[DATA_W];

    always @(posedge clk) begin
        if (rst) begin
            state         <= ST_IDLE;
            result        <= RC_NONE;
            flags         <= 4'd0;
            seq           <= 32'd0;
            beats         <= 32'd0;
            actual_tick   <= 64'd0;
            invalid_count <= 32'd0;
            target        <= 64'd0;
            end_tick      <= 64'd0;
            tail_pending  <= 1'b0;
            ack_seen      <= 1'b0;
        end else begin
            if (tlast_ack) ack_seen <= 1'b1;

            // Flags are final when the window closes; DRAIN only waits for DMA.
            if ((state == ST_ARMED || state == ST_RUNNING) && !time_valid)
                flags[RXF_TIME] <= 1'b1;

            if (in_window) begin
                if (!adc_v)                flags[RXF_GAP]       <= 1'b1;
                if (adc_v && fifo_full)    flags[RXF_OVERFLOW]  <= 1'b1;
                if (adc_or)                flags[RXF_OVERRANGE] <= 1'b1;
                if (push_beat)             beats <= beats + 32'd1;
                if (at_start)              actual_tick <= tick_d;
                if (is_last) begin
                    state <= ST_DRAIN;
                    if (!push_beat) tail_pending <= 1'b1;
                end else if (at_start) begin
                    state  <= ST_RUNNING;
                    result <= RC_RUNNING;
                end
            end

            case (state)
                ST_IDLE, ST_DONE: begin
                    if (doorbell) begin
                        if (!time_valid)
                            result <= RC_REJ_TIME_INVALID;
                        else if (cmd_length == 32'd0 || cmd_length > MAX_BEATS)
                            result <= RC_REJ_LENGTH;
                        else if (cmd_target < raw_tick + MIN_LEAD_TICKS)
                            result <= RC_REJ_STALE;
                        else if (!dma_ready)
                            result <= RC_REJ_DMA_NOT_READY;
                        else begin
                            state        <= ST_ARMED;
                            result       <= RC_ARMED;
                            flags        <= 4'd0;
                            beats        <= 32'd0;
                            target       <= cmd_target;
                            end_tick     <= cmd_target + {32'd0, cmd_length} - 64'd1;
                            tail_pending <= 1'b0;
                            ack_seen     <= 1'b0;
                        end
                    end
                end

                ST_ARMED, ST_RUNNING: begin
                    if (doorbell) result <= RC_REJ_BUSY;
                    if (state == ST_ARMED && cancel && !at_start) begin
                        state  <= ST_IDLE;
                        result <= RC_CANCELLED;
                    end
                end

                ST_DRAIN: begin
                    if (doorbell) result <= RC_REJ_BUSY;
                    if (push_tail) begin
                        tail_pending <= 1'b0;
                        beats        <= beats + 32'd1;
                    end
                    if (!tail_pending && (ack_seen || tlast_ack)) begin
                        state <= ST_DONE;
                        seq   <= seq + 32'd1;
                        if (flags == 4'd0) begin
                            result <= RC_DONE;
                        end else begin
                            result        <= RC_RX_INVALID;
                            invalid_count <= invalid_count + 32'd1;
                        end
                    end
                end

                default: state <= ST_IDLE;
            endcase
        end
    end
endmodule
