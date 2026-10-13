// H10: TX status aggregator
//
// H10 is the boundary between the continuously streaming TX datapath and the
// scheduled command path in H05.  It combines the H03 timebase, H08 frame
// source, H09 pulse shaper, and the RFDC/DAC adapter into one admission and
// result interface.  It does not configure RFDC registers and it does not
// buffer samples.
//
// The TX contract is deliberately strict:
//   * tx_admission_ready must be true before H05 starts a frame.
//   * During an active frame, a lost downstream ready is a fault, not a
//     request to pause the stream.
//   * tx_done is a one-clock pulse after the complete chain returns idle.
//   * Individual return codes are latched for H02 diagnostics.  H05 receives
//     one primary eight-bit source/cause code.

`include "src/H05_protocol_defs.vh"
`include "src/H08_protocol_defs.vh"
`include "src/H09_protocol_defs.vh"

module H10_tx_status_aggregator (
    input wire        clk,
    input wire        rst,
    input wire        clear_fault,

    // H03 timebase status.  H03 has no local return-code output, so H10
    // derives a fixed H03 source/cause code when raw_tick_exhausted is high.
    input wire        h03_raw_tick_exhausted,

    // H08 frame source status.
    input wire        h08_frame_ready,
    input wire        h08_frame_idle,
    input wire        h08_frame_generating,
    input wire        h08_frame_fault,
    input wire [3:0]  h08_return_code,

    // H09 pulse shaper status.  chip_ready is the admission/stream boundary
    // presented to H08; pulse_busy covers both OUTPUT and TAIL.
    input wire        h09_chip_ready,
    input wire        h09_pulse_busy,
    input wire        h09_pulse_fault,
    input wire [3:0]  h09_return_code,

    // RFDC/DAC adapter status.  These signals must already be synchronized to
    // clk (or be generated in this same clock domain).
    input wire        rfdc_tx_ready,
    input wire        rfdc_tx_active,
    input wire        rfdc_tx_done,
    input wire        rfdc_tx_fault,
    input wire [3:0]  rfdc_return_code,

    // Unified interface to H05.
    output wire        tx_admission_ready,
    output wire        tx_active,
    output reg         tx_done,
    output wire        tx_fault,
    output wire [`H05_RETURN_CODE_WIDTH-1:0] tx_return_code,

    // Live status for H02/ARM readback.
    output wire        h03_exhausted_status,
    output wire        h08_ready_status,
    output wire        h08_idle_status,
    output wire        h08_active_status,
    output wire        h08_fault_status,
    output wire [3:0]  h08_return_code_status,
    output wire        h09_ready_status,
    output wire        h09_active_status,
    output wire        h09_fault_status,
    output wire [3:0]  h09_return_code_status,
    output wire        rfdc_ready_status,
    output wire        rfdc_active_status,
    output wire        rfdc_fault_status,
    output wire [3:0]  rfdc_return_code_status,

    // Sticky diagnostic state for H02. clear_fault clears all snapshots.
    output reg         h03_exhausted_latched,
    output reg         h08_fault_latched,
    output reg [3:0]   h08_return_code_latched,
    output reg         h09_fault_latched,
    output reg [3:0]   h09_return_code_latched,
    output reg         rfdc_fault_latched,
    output reg [3:0]   rfdc_return_code_latched,
    output reg         tx_fault_latched
);

    localparam [3:0] H10_CAUSE_NONE = 4'h0;

    // H08 is allowed to keep its final chip presented while H09 drains that
    // chip and emits the RRC tail.  Therefore h08_frame_generating &&
    // !h09_chip_ready is not, by itself, a fault: H09 deliberately withholds
    // chip_ready while it is processing the current/last chip.
    // A frame is not allowed to wait indefinitely before its first chip is
    // accepted.  Once H08 has entered GENERATING, an idle H09 with no
    // chip-ready indication means the RFDC path cannot accept the first
    // beat.  The separate busy case covers OUTPUT/TAIL after the first chip.
    wire h09_stream_fault =
        ((h09_pulse_busy && !rfdc_tx_ready) ||
         (h08_frame_generating && !h09_pulse_busy && !h09_chip_ready)) &&
        !rfdc_tx_fault;
    wire any_fault_now =
        h03_raw_tick_exhausted ||
        h08_frame_fault ||
        h09_pulse_fault ||
        rfdc_tx_fault ||
        h09_stream_fault;

    assign h03_exhausted_status = h03_raw_tick_exhausted;
    assign h08_ready_status     = h08_frame_ready;
    assign h08_idle_status      = h08_frame_idle;
    assign h08_active_status    = h08_frame_generating;
    assign h08_fault_status     = h08_frame_fault;
    assign h08_return_code_status = h08_fault_latched ?
        h08_return_code_latched : h08_return_code;
    assign h09_ready_status     = h09_chip_ready;
    assign h09_active_status    = h09_pulse_busy;
    assign h09_fault_status     = h09_pulse_fault;
    assign h09_return_code_status = h09_fault_latched ?
        h09_return_code_latched : h09_return_code;
    assign rfdc_ready_status    = rfdc_tx_ready;
    assign rfdc_active_status   = rfdc_tx_active;
    assign rfdc_fault_status    = rfdc_tx_fault;
    assign rfdc_return_code_status = rfdc_fault_latched ?
        rfdc_return_code_latched : rfdc_return_code;

    // Readiness is a pre-frame admission condition.  Once a frame is active,
    // a falling RFDC ready signal is detected while H09 is busy and becomes a
    // sticky fault instead of creating a bubble.  H09 itself also reports the
    // corresponding downstream-ready fault.
    assign tx_admission_ready =
        !rst &&
        !tx_fault &&
        !h03_raw_tick_exhausted &&
        h08_frame_ready &&
        h09_chip_ready &&
        rfdc_tx_ready;

    assign tx_active =
        h08_frame_generating ||
        h09_pulse_busy ||
        rfdc_tx_active;

    assign tx_fault = tx_fault_latched || any_fault_now;

    // Select the first useful live source before the sticky snapshot takes
    // over.  H05 receives this as {source[3:0], cause[3:0]}.
    assign tx_return_code =
        tx_fault_latched ? tx_return_code_latched_reg :
        h03_raw_tick_exhausted ?
            {`H05_RETURN_SOURCE_H03, `H05_CAUSE_TIMEBASE_EXHAUSTED} :
        rfdc_tx_fault ?
            {`H05_RETURN_SOURCE_RFDC, rfdc_return_code} :
        h09_pulse_fault ?
            {`H05_RETURN_SOURCE_H09, h09_return_code} :
        h08_frame_fault ?
            {`H05_RETURN_SOURCE_H08, h08_return_code} :
        h09_stream_fault ?
            {`H05_RETURN_SOURCE_RFDC, `H05_CAUSE_TX_NOT_READY} :
        {`H05_RETURN_SOURCE_NONE, H10_CAUSE_NONE};

    reg [3:0] tx_fault_source_reg;
    reg [3:0] tx_fault_cause_reg;
    reg       tx_active_d;
    wire [`H05_RETURN_CODE_WIDTH-1:0] tx_return_code_latched_reg =
        {tx_fault_source_reg, tx_fault_cause_reg};

    // Snapshot source-local diagnostics and detect the one-cycle completion
    // event.  The individual latches intentionally do not overwrite an older
    // non-zero cause until clear_fault is asserted.
    always @(posedge clk) begin
        if (rst) begin
            tx_done                    <= 1'b0;
            tx_active_d                <= 1'b0;
            tx_fault_latched           <= 1'b0;
            tx_fault_source_reg        <= `H05_RETURN_SOURCE_NONE;
            tx_fault_cause_reg         <= `H05_CAUSE_SUCCESS;
            h03_exhausted_latched      <= 1'b0;
            h08_fault_latched          <= 1'b0;
            h08_return_code_latched    <= `H08_RETURN_CODE_IDLE;
            h09_fault_latched          <= 1'b0;
            h09_return_code_latched    <= `H09_RETURN_CODE_SUCCESS;
            rfdc_fault_latched         <= 1'b0;
            rfdc_return_code_latched   <= 4'h0;
        end
        else begin
            tx_done     <= (rfdc_tx_done || (tx_active_d && !tx_active)) &&
                           !tx_fault;
            tx_active_d <= tx_active;

            if (clear_fault) begin
                tx_fault_latched         <= 1'b0;
                tx_fault_source_reg      <= `H05_RETURN_SOURCE_NONE;
                tx_fault_cause_reg       <= `H05_CAUSE_SUCCESS;
                h03_exhausted_latched    <= 1'b0;
                h08_fault_latched        <= 1'b0;
                h08_return_code_latched  <= `H08_RETURN_CODE_IDLE;
                h09_fault_latched        <= 1'b0;
                h09_return_code_latched  <= `H09_RETURN_CODE_SUCCESS;
                rfdc_fault_latched       <= 1'b0;
                rfdc_return_code_latched <= 4'h0;
            end
            else begin
                if (h03_raw_tick_exhausted)
                    h03_exhausted_latched <= 1'b1;

                if (h08_frame_fault) begin
                    h08_fault_latched <= 1'b1;
                    h08_return_code_latched <= h08_return_code;
                end

                if (h09_pulse_fault || h09_stream_fault) begin
                    h09_fault_latched <= 1'b1;
                    h09_return_code_latched <= h09_pulse_fault ?
                        h09_return_code : `H09_RETURN_CODE_DOWNSTREAM_READY_NOT_KEPT;
                end

                if (rfdc_tx_fault || h09_stream_fault) begin
                    rfdc_fault_latched <= 1'b1;
                    rfdc_return_code_latched <= rfdc_tx_fault ?
                        rfdc_return_code : `H05_CAUSE_TX_NOT_READY;
                end

                if (!tx_fault_latched && any_fault_now) begin
                    tx_fault_latched <= 1'b1;
                    if (h03_raw_tick_exhausted) begin
                        tx_fault_source_reg <= `H05_RETURN_SOURCE_H03;
                        tx_fault_cause_reg  <= `H05_CAUSE_TIMEBASE_EXHAUSTED;
                    end
                    else if (rfdc_tx_fault || h09_stream_fault) begin
                        tx_fault_source_reg <= `H05_RETURN_SOURCE_RFDC;
                        tx_fault_cause_reg  <= rfdc_tx_fault ?
                            rfdc_return_code : `H05_CAUSE_TX_NOT_READY;
                    end
                    else if (h09_pulse_fault) begin
                        tx_fault_source_reg <= `H05_RETURN_SOURCE_H09;
                        tx_fault_cause_reg  <= h09_return_code;
                    end
                    else if (h08_frame_fault) begin
                        tx_fault_source_reg <= `H05_RETURN_SOURCE_H08;
                        tx_fault_cause_reg  <= h08_return_code;
                    end
                end
            end
        end
    end
endmodule
