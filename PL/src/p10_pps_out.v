`timescale 1ns / 1ps
// P10: the output PPS (since 2.1, plan/pps_output.md).
//
// The node's time scale starts each second at the tick that latched its PPS
// (P1's pps_tick).  P10 keeps a phase counter of ticks since that start: a
// locked PPS edge (pps_event with pps_locked) sets it to raw_tick - pps_tick,
// and between edges it counts and wraps every PPS_PERIOD ticks, so it carries
// on from the last good edge when an edge is missing or the PPS unlocked (the
// flywheel).  When the phase reaches OFFSET, P10 sends a step on DAC tile 0's
// stream: in the beat presented at raw_tick = second start + OFFSET, the I
// samples from SAMPLE on are AMPLITUDE and the earlier ones IDLE, WIDTH - 1
// more beats of AMPLITUDE follow, then IDLE again; Q stays 0.  The DAC (mixer bypassed by
// software) turns it into a pulse on DAC_B whose edge lies at the second's
// start + OFFSET x 10 ns + SAMPLE x 1.25 ns + the stream-to-pin latency.  An
// IDLE of -AMPLITUDE doubles the step (since 2.2; the path is AC-coupled, so
// IDLE sets no level at the connector).  With FRAC (since 2.3) the step's first
// sample is AMPLITUDE + (IDLE - AMPLITUDE) x FRAC / 2^16 instead of AMPLITUDE, which
// moves the edge after the DAC's interpolation by up to one sample (1.25 ns),
// nearly in proportion to FRAC (calibrated on the scope): the fractional delay
// with which the ARM makes B's output follow A's time scale to the measurement
// (plan/pps_output.md section 7).  TVALID stays high out of reset, so
// the DAC reads IDLE between pulses and never repeats a pulse's last sample.
//
// OFFSET >= MIN_OFFSET (P2 refuses less), so the edge that anchors a second
// (whose event comes SYNC_LATENCY + 1 ticks after it) is always in before
// that second's pulse.  A pulse is never sent within half a second of the
// last one: when an edge re-anchors the phase later than the flywheel had it
// (the PPS moved later), that second's pulse, already sent, is not repeated.
//
// Configuration is taken at the start of each second: where the counter
// wraps, and again at the locked edge, so also in a second whose edge came
// early (the PPS moved earlier) and the counter did not wrap.  A new IDLE
// therefore steps the output once, at the start of the second it is taken.
// pps_out_invalid (the validity line on the Pmod header) is high
// unless the output is enabled and anchored, the PPS and the clocks are
// locked, the DAC takes the stream (TREADY), the ARM's "synchronized" bit is
// set, and the current second was started by a locked edge (allowing
// MIN_OFFSET ticks for its event).
module p10_pps_out #(
    parameter integer DATA_W     = 256,
    parameter [31:0]  PPS_PERIOD = 32'd100_000_000,
    parameter [31:0]  MIN_OFFSET = 32'd8
) (
    input  wire              clk,
    input  wire              rst,
    input  wire [63:0]       raw_tick,
    input  wire              pps_event,
    input  wire [63:0]       pps_tick,
    input  wire              pps_locked,
    input  wire              clocks_locked,
    // Configuration (P2), taken at the start of each second.
    input  wire              cfg_enable,
    input  wire              cfg_synced,
    input  wire [31:0]       cfg_offset,
    input  wire [2:0]        cfg_sample,
    input  wire [15:0]       cfg_width,
    input  wire [15:0]       cfg_amplitude,
    input  wire [15:0]       cfg_idle,
    input  wire              cfg_frac_en,
    input  wire [15:0]       cfg_frac,
    // DAC tile 0 stream.
    output reg  [DATA_W-1:0] m_axis_tdata,
    output reg               m_axis_tvalid,
    input  wire              m_axis_tready,
    // Status.
    output reg               have_ref,
    output reg               running,       // enabled for the current second
    output reg               last_flywheel, // the last pulse came from the flywheel
    output reg  [31:0]       pulse_count,
    output reg  [31:0]       flywheel_count,
    output reg  [63:0]       last_tick,     // tick at which the last pulse's first beat was presented
    output reg               pps_out_invalid
);
    localparam integer SAMPLES = DATA_W / 32;

    reg [31:0] phase;        // ticks since the current second's start
    reg        anchored;     // the current second was started by a locked PPS edge
    reg        fired_once;
    reg [31:0] off_l;
    reg [2:0]  sample_l;
    reg [15:0] width_l;
    reg [15:0] amp_l;
    reg [15:0] idle_l;
    reg [15:0] edge_l;       // the step's first sample
    reg [15:0] beats_left;

    wire anchor = pps_event && pps_locked;
    wire wrap   = have_ref && !anchor && (phase == PPS_PERIOD - 32'd1);
    // The beat is registered, so decide one tick early: it is presented when phase == off_l.
    wire fire = have_ref && running && !anchor && (phase == off_l - 32'd1) &&
                (!fired_once || (raw_tick - last_tick) > {32'd0, PPS_PERIOD >> 1});

    // A beat: I = idle before sample `first`, first_v at it, amp after it; Q = 0.
    function [DATA_W-1:0] step_beat(input [2:0] first, input [15:0] amp, input [15:0] idle, input [15:0] first_v);
        integer n;
        begin
            step_beat = {DATA_W{1'b0}};
            for (n = 0; n < SAMPLES; n = n + 1)
                step_beat[32*n +: 16] = (n > first) ? amp : (n == first) ? first_v : idle;
        end
    endfunction

    // The first sample with FRAC: amp + (idle - amp) x frac / 2^16, between amp and idle.
    wire signed [16:0] span     = $signed({cfg_idle[15], cfg_idle}) - $signed({cfg_amplitude[15], cfg_amplitude});
    wire signed [33:0] scaled   = span * $signed({1'b0, cfg_frac});
    wire signed [16:0] edge_cfg = $signed({cfg_amplitude[15], cfg_amplitude}) + $signed(scaled[32:16]);
    wire        [15:0] edge_new = cfg_frac_en ? edge_cfg[15:0] : cfg_amplitude;

    always @(posedge clk) begin
        if (rst) begin
            phase          <= 32'd0;
            have_ref       <= 1'b0;
            anchored       <= 1'b0;
            running        <= 1'b0;
            fired_once     <= 1'b0;
            last_flywheel  <= 1'b0;
            pulse_count    <= 32'd0;
            flywheel_count <= 32'd0;
            last_tick      <= 64'd0;
            off_l          <= MIN_OFFSET;
            sample_l       <= 3'd0;
            width_l        <= 16'd1;
            amp_l          <= 16'd0;
            idle_l         <= 16'd0;
            edge_l         <= 16'd0;
            beats_left     <= 16'd0;
            m_axis_tdata   <= {DATA_W{1'b0}};
            m_axis_tvalid  <= 1'b0;
        end else begin
            m_axis_tvalid <= 1'b1;
            // Phase: anchored by a locked edge, else counting and wrapping (flywheel).
            if (anchor) begin
                // Also a second's start (the configuration again, its pulse still ahead:
                // the phase is 4 < MIN_OFFSET - 1), including one that came early.
                phase    <= raw_tick + 64'd1 - pps_tick;  // its value in the next cycle
                anchored <= 1'b1;
                have_ref <= 1'b1;
                running  <= cfg_enable;
                off_l    <= cfg_offset;
                sample_l <= cfg_sample;
                width_l  <= cfg_width;
                amp_l    <= cfg_amplitude;
                idle_l   <= cfg_idle;
                edge_l   <= edge_new;
            end else if (have_ref) begin
                phase <= wrap ? 32'd0 : phase + 32'd1;
                if (wrap) begin  // a new second: take the configuration, not yet anchored
                    anchored <= 1'b0;
                    running  <= cfg_enable;
                    off_l    <= cfg_offset;
                    sample_l <= cfg_sample;
                    width_l  <= cfg_width;
                    amp_l    <= cfg_amplitude;
                    idle_l   <= cfg_idle;
                    edge_l   <= edge_new;
                end
            end

            // The pulse.
            m_axis_tdata <= step_beat(3'd0, idle_l, idle_l, idle_l);
            if (fire) begin
                m_axis_tdata   <= step_beat(sample_l, amp_l, idle_l, edge_l);
                beats_left     <= width_l - 16'd1;
                fired_once     <= 1'b1;
                last_tick      <= raw_tick + 64'd1;
                pulse_count    <= pulse_count + 32'd1;
                last_flywheel  <= !anchored;
                if (!anchored) flywheel_count <= flywheel_count + 32'd1;
            end else if (beats_left != 16'd0) begin
                m_axis_tdata <= step_beat(3'd0, amp_l, amp_l, amp_l);
                beats_left   <= beats_left - 16'd1;
            end
        end
    end

    // A second counts as anchored from its start until MIN_OFFSET: its edge's event
    // comes a few ticks after the edge, so the line does not blink at every second.
    wire second_ok = anchored || (phase < MIN_OFFSET);
    always @(posedge clk) begin
        if (rst) pps_out_invalid <= 1'b1;
        else     pps_out_invalid <= !(running && have_ref && second_ok && pps_locked && clocks_locked &&
                                          m_axis_tready && cfg_synced);
    end
endmodule
