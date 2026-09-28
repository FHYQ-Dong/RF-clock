// H04: PPS Capture Module
/** This module captures PPS signals from FS725 and validates their timing.
  * It works under a 100 MHz clock, so each PPS should arrive around every 100,000,000 cycles, with a tolerance of 5 cycles.
  * Only after receiving a sufficient number of valid PPS signals will the module assert the locked signal.
  * If a PPS signal is not received within a certain timeout period, the module will reset its stability counter.
  * THIS MODULE WORKS UNDER A 100 MHz CLOCK, SO THE PPS SAMPLING ACCURACY IS LIMITED TO 10 NS.
*/

/* Interface

## Parameter:
  - `PPS_PERIOD`: The expected period of the PPS signal (in clock cycles)
  - `PPS_TOLERANCE`: The allowable tolerance for the PPS signal period (in clock cycles)
  - `PPS_TIMEOUT`: The timeout period for receiving a valid PPS signal (in clock cycles)
  - `STABLE_CYCLES`: The number of consecutive valid PPS signals required to assert the locked signal

## Input Signals:
  - `clk`: Working clock (100 MHz)
  - `rst`: Synchronous reset (active high)
  - `pps_in_async`: Asynchronous PPS signal
  - `raw_tick`: Raw time counter value from H03

## Output Signals:
  - `pps_event`: PPS event signal, asserted on each PPS edge
  - `pps_locked`: Locked signal indicating valid PPS reception
*/

module H04_pps_capture #(
    parameter [63:0] PPS_PERIOD = 64'd100_000_000, // 100 MHz
    parameter [63:0] PPS_TOLERANCE = 64'd5, // 50 ns <=> 5 cycles
    parameter [63:0] PPS_TIMEOUT = 64'd100_000_100, // 1s + 1us
    parameter integer STABLE_CYCLES = 4 // After 4 PPS cycles
)(
    input wire clk,
    input wire rst,
    input wire pps_in_async,
    input wire [63:0] raw_tick,

    output wire pps_event,
    output wire pps_locked
);
    // Asynchronous register for PPS input [1:0]
    // pps_in_buf[2] is used to determine the rising edge
    (* ASYNC_REG = "TRUE" *) reg [2:0] pps_in_buf;
    reg [63:0] prev_pps_tick;
    reg [STABLE_CYCLES-1:0] pps_stable;
    wire [63:0] pps_interval = raw_tick - prev_pps_tick;
    wire [63:0] pps_period_min = PPS_PERIOD - PPS_TOLERANCE;
    wire [63:0] pps_period_max = PPS_PERIOD + PPS_TOLERANCE;
    wire pps_sample_valid = (pps_interval > pps_period_min) && (pps_interval < pps_period_max);
    wire pps_rise = ~pps_in_buf[2] && pps_in_buf[1];

    always @(posedge clk) begin
        if (rst) begin
            pps_in_buf <= 3'b000;
            prev_pps_tick <= 64'b0;
            pps_stable <= {STABLE_CYCLES{1'b0}};
        end else begin
            pps_in_buf <= {pps_in_buf[1:0], pps_in_async};
            if (pps_rise) begin
                pps_stable <= {pps_stable[STABLE_CYCLES-2:0], pps_sample_valid};
                prev_pps_tick <= raw_tick;
            end
            if (pps_interval > PPS_TIMEOUT) begin
                pps_stable <= {STABLE_CYCLES{1'b0}};
            end
        end
    end

    assign pps_event = pps_rise;
    assign pps_locked = &pps_stable;
endmodule
