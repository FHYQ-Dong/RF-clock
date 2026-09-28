// H01: Clock and Reset Guard
/** There are multiple clock domains in the design, which may provide ASYNC clock and reset signals.
  * This module collects all these signals, checks whether the PL should be reset and whether the clock reference is locked.
  * This module takes in multiple ASYNC signals and generates synchronous signals for the rest of the design.
*/

/* Interface

## Parameter:
  - `STABLE_CYCLES`: Number of stable cycles required for the clock to be considered locked

## Input Signals:
  - `clk`: working clock (100 MHz)
  - `arst_n`: Asynchronous reset (active low), comes from the external domain (e.g. power-on reset)

## Input Signals:
  - `clock_pll_locked_async`: PLL clock locked signal (async)
  - `rfdc_locked_async`: RFDC clock locked signal (async)
  - `external_ref_locked_async`: External reference clock locked signal (async)
  - `wr_locked_async`: White Rabbit locked signal (async)
  - `boot_valid_async`: Boot/configuration sequence completed (async)
  - `raw_tick_exhausted_async`: Raw time counter exhausted (async, active high)
  - `sync_locked_async`: Cross-node clock synchronization locked (async)
  - `servo_converged_async`: Clock servo has converged (async)

## Output Signals:
  - `rst`: Synchronous reset signal for the rest of the design
  - `clock_ok`: The clock tree is setup and different clock domains are locked
  - `clock_reference_ok`: Besides clock_ok, local clock is also locked to the external reference (FS725)
  - `local_time_valid`: Besides clock_reference_ok, time-controlling logic is functioning correctly (e.g. H03_raw_tick is not exhausted)
  - `system_time_valid`: Besides local_time_valid, cross-nodes time synchronization is functioning correctly, ready for AWG, TimeTagger, etc.
*/

module H01_clock_reset_guard #(
    parameter integer STABLE_CYCLES = 4
)(
    input wire clk,
    input wire arst_n,

    // clock locked signal
    input wire clock_pll_locked_async,
    input wire rfdc_locked_async,
    input wire external_ref_locked_async,
    input wire wr_locked_async,
    input wire boot_valid_async,
    input wire raw_tick_exhausted_async,
    input wire sync_locked_async,
    input wire servo_converged_async,

    // output
    output wire rst,
    output wire clock_ok,
    output wire clock_reference_ok,
    output wire local_time_valid,
    output wire system_time_valid
);
    // use 2-stage shift register to avoid metastability
    // Because the input signals may come from different clock domains, which may result in setup time and hold time violations
    (* ASYNC_REG = "TRUE" *) reg [1:0] arst_n_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] clock_pll_locked_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] rfdc_locked_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] external_ref_locked_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] wr_locked_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] boot_valid_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] raw_tick_exhausted_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] sync_locked_async_buf;
    (* ASYNC_REG = "TRUE" *) reg [1:0] servo_converged_async_buf;

    // These are qualification shift registers, not CDC synchronizers.
    // STABLE_CYCLES must be at least 2 for the concatenation below.
    reg [STABLE_CYCLES-1:0] clock_ok_stable;
    reg [STABLE_CYCLES-1:0] clock_reference_ok_stable;
    reg [STABLE_CYCLES-1:0] local_time_valid_stable;
    reg [STABLE_CYCLES-1:0] system_time_valid_stable;

    // Qualification samples are taken only from the synchronized lock signals.
    wire clock_ok_sample = clock_pll_locked_async_buf[1] &
                           rfdc_locked_async_buf[1] &
                           wr_locked_async_buf[1];
    wire clock_reference_ok_sample = 
        clock_ok_sample & external_ref_locked_async_buf[1];
    wire local_time_valid_sample =
        clock_reference_ok_sample & boot_valid_async_buf[1] &
        ~raw_tick_exhausted_async_buf[1];
    wire system_time_valid_sample =
        local_time_valid_sample & sync_locked_async_buf[1] &
        servo_converged_async_buf[1];

    always @(posedge clk or negedge arst_n) begin
        if (!arst_n) begin
            arst_n_buf <= 2'b11;
            clock_pll_locked_async_buf <= 2'b00;
            rfdc_locked_async_buf <= 2'b00;
            external_ref_locked_async_buf <= 2'b00;
            wr_locked_async_buf <= 2'b00;
            boot_valid_async_buf <= 2'b00;
            raw_tick_exhausted_async_buf <= 2'b00;
            sync_locked_async_buf <= 2'b00;
            servo_converged_async_buf <= 2'b00;
            clock_ok_stable <= {STABLE_CYCLES{1'b0}};
            clock_reference_ok_stable <= {STABLE_CYCLES{1'b0}};
            local_time_valid_stable <= {STABLE_CYCLES{1'b0}};
            system_time_valid_stable <= {STABLE_CYCLES{1'b0}};
        end else begin
            arst_n_buf <= {arst_n_buf[0], 1'b0};
            // Synchronize each asynchronous lock indication.
            clock_pll_locked_async_buf <= {
                clock_pll_locked_async_buf[0], 
                clock_pll_locked_async
            };
            rfdc_locked_async_buf <= {
                rfdc_locked_async_buf[0], 
                rfdc_locked_async
            };
            external_ref_locked_async_buf <= {
                external_ref_locked_async_buf[0], 
                external_ref_locked_async
            };
            wr_locked_async_buf <= {
                wr_locked_async_buf[0], 
                wr_locked_async
            };
            boot_valid_async_buf <= {
                boot_valid_async_buf[0], 
                boot_valid_async
            };
            raw_tick_exhausted_async_buf <= {
                raw_tick_exhausted_async_buf[0],
                raw_tick_exhausted_async
            };
            sync_locked_async_buf <= {
                sync_locked_async_buf[0], 
                sync_locked_async
            };
            servo_converged_async_buf <= {
                servo_converged_async_buf[0], 
                servo_converged_async
            };
            // outputs assert only after STABLE_CYCLES good samples.
            clock_ok_stable <= {
              clock_ok_stable[STABLE_CYCLES-2:0], 
              clock_ok_sample
            };
            clock_reference_ok_stable <= {
              clock_reference_ok_stable[STABLE_CYCLES-2:0], 
              clock_reference_ok_sample
            };
            local_time_valid_stable <= {
              local_time_valid_stable[STABLE_CYCLES-2:0],
              local_time_valid_sample
            };
            system_time_valid_stable <= {
              system_time_valid_stable[STABLE_CYCLES-2:0],
              system_time_valid_sample
            };
        end
    end

    assign rst = arst_n_buf[1];
    assign clock_ok = &clock_ok_stable;
    assign clock_reference_ok = &clock_reference_ok_stable;
    assign local_time_valid = &local_time_valid_stable;
    assign system_time_valid = &system_time_valid_stable;
endmodule
