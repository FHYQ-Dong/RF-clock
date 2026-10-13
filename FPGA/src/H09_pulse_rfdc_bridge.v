// H09 pulse-shaper to RFDC DAC AXI4-Stream bridge.
//
// The RFDC profile used by this project has one 256-bit DAC stream beat:
//
//   bits  15:0   I0     bits  31:16  Q0
//   bits  47:32   I1     bits  63:48  Q1
//   ...
//   bits 239:224  I7     bits 255:240  Q7
//
// More precisely, each lane occupies 32 bits and the lower 16 bits are I.
// The RFDC DAC AXIS interface has TVALID/TREADY/TDATA only; it does not have
// TKEEP or TLAST in the selected profile.  H09's sample_mask/sample_last are
// therefore exposed as sideband diagnostics, while invalid tail lanes are
// transmitted as zero-valued I/Q words in the final fixed-width beat.
//
// This bridge intentionally has no register or FIFO.  It adds no cycle of
// latency and preserves the project's fixed-rate active-frame contract:
// sample_ready is the RFDC TREADY signal, so a TREADY drop while H09 is
// emitting a frame is reported as a fault rather than buffered as a bubble.

module H09_pulse_rfdc_bridge (
    input wire         clk,
    input wire         rst,

    input wire         sample_valid,
    output wire        sample_ready,
    input wire [127:0] sample_I,
    input wire [127:0] sample_Q,
    input wire [7:0]   sample_mask,
    input wire         sample_first,
    input wire         sample_last,
    input wire [31:0]  sample_index,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_DAC_AXIS TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_DAC_AXIS, TDATA_NUM_BYTES 32, HAS_TREADY 1, HAS_TKEEP 0, HAS_TLAST 0" *)
    output wire [255:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_DAC_AXIS TVALID" *)
    output wire         m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_DAC_AXIS TREADY" *)
    input wire          m_axis_tready,

    // These outputs are not connected to RFDC.  They are intended for H10
    // and H02 diagnostics, where the beat boundary and the partial final
    // beat still matter even though RFDC has no TLAST/TKEEP pins.
    output wire [7:0]   sample_keep,
    output wire         sample_first_out,
    output wire         sample_last_out,
    output wire [31:0]  sample_index_out,
    output wire         beat_accepted,
    output wire         frame_done,
    output wire         bridge_active,
    output wire         bridge_fault
);

    // H09 sees the RFDC ready state directly.  During reset neither side of
    // the stream is advertised as valid/ready.
    assign sample_ready = !rst && m_axis_tready;
    assign m_axis_tvalid = !rst && sample_valid;

    assign sample_keep      = m_axis_tvalid ? sample_mask  : 8'b0;
    assign sample_first_out = m_axis_tvalid && sample_first;
    assign sample_last_out  = m_axis_tvalid && sample_last;
    assign sample_index_out = m_axis_tvalid ? sample_index : 32'b0;

    assign beat_accepted = m_axis_tvalid && m_axis_tready;
    assign frame_done    = beat_accepted && sample_last;
    assign bridge_active = m_axis_tvalid;

    // A ready drop while a valid beat is being emitted is not a legal pause
    // in the synchronization waveform.  H09 will enter its own FAULT state
    // on this condition; this wire gives H10 the same-cycle observation.
    assign bridge_fault = m_axis_tvalid && !m_axis_tready;

    genvar lane;
    generate
        for (lane = 0; lane < 8; lane = lane + 1) begin : gen_rfdc_lane
            // Invalid lanes in the final H09 beat are explicitly zeroed.
            // RFDC still receives one complete 256-bit beat because its
            // selected AXIS profile has no TKEEP signal.
            assign m_axis_tdata[lane*32 +: 16] =
                (m_axis_tvalid && sample_mask[lane]) ?
                    sample_I[lane*16 +: 16] : 16'b0;
            assign m_axis_tdata[lane*32 + 16 +: 16] =
                (m_axis_tvalid && sample_mask[lane]) ?
                    sample_Q[lane*16 +: 16] : 16'b0;
        end
    endgenerate

endmodule
