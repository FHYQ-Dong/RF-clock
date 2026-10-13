// H03: Raw Tick Generator
/** This module requires a 100 MHz clock signal and a reset signal.
  * It generates a 64-bit timestamp which increments on each clock cycle, serving as a time reference for PRN sequences (H08) and RRC samples (H09).
  * If the timestamp reaches its maximum value, the exhausted signal is set.
  * This module should ONLY BE RESET WHEN H01's reset signal is asserted. Other signals like H01's oks, valids SHOULD NOT TRIGGER A RESET.
*/

/* Interface

## Input Signals
  - clk: 100 MHz clock signal
  - rst: Synchronous reset signal (active high)

## Output Signals
  - raw_tick_out: 64-bit timestamp
  - exhausted: Signal indicating timestamp exhaustion
*/

module H03_raw_tick(
    input clk,
    input rst,
    output reg [63:0] raw_tick_out,
    output reg exhausted
);
    always @(posedge clk) begin
        if (rst) begin
            raw_tick_out <= 64'b0;
            exhausted <= 1'b0;
        end 
        else begin
            if (raw_tick_out == 64'hFFFFFFFFFFFFFFFF) begin
                exhausted <= 1'b1;
            end 
            else begin
                raw_tick_out <= raw_tick_out + 1;
                exhausted <= 1'b0;
            end
        end
    end
endmodule
