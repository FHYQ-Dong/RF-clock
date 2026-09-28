// H05: deterministic execution of one future-tick command
/**
  * H05 separates the time at which software submits a command from the time
  * at which the hardware applies it.  A PS/ARM thread may be delayed by Linux
  * scheduling, but a command accepted by H05 is executed at the requested
  * `raw_tick` edge instead of at the time the software happens to run.
  *
  * This prototype holds at most one command.  It receives a command from H02
  * through a standard `cmd_valid`/`cmd_ready` handshake, latches the command
  * fields, waits for the target tick, and exposes the latched control value to
  * the downstream PRN/RRC/AWG command consumer.
  *
  * `apply_now` is the target-edge enable.  `cmd_executed` is a one-clock
  * completion event generated when H05 reaches that edge.  The completion
  * event means that H05 applied the command; it is not an acknowledgement
  * from a downstream peripheral.
*/

/* State machine:
  - `STATE_IDLE`: No command is waiting.  H05 may assert `cmd_ready` when the
    scheduling policy and raw timebase are valid.
  - `STATE_WAIT`: A command was accepted and its fields were latched.  H05
    waits for `raw_tick == target_tick`; `cmd_ready` remains low so a second
    command cannot overwrite the pending one.
  - `STATE_FAULT`: The pending command could not be safely executed, or the
    timebase/scheduling authority became invalid.  The state is sticky and
    blocks new commands until `clear_fault` is asserted while the timebase is
    valid again.
*/

/* Interface

## Parameter:
  - `ARRIVE_TICKS_BEFORE_EXECUTE`: Minimum number of raw-tick counts that must
    remain between command acceptance and `cmd_target_tick`.  A command is
    accepted only when `cmd_target_tick > raw_tick` and the difference is at
    least this parameter.

## Input Signals:
  - `clk`: H05 working clock. It must be the same timebase clock used to sample `raw_tick` and the downstream command consumer.
  - `rst`: Synchronous active-high reset. Reset returns H05 to `STATE_IDLE` and clears the latched command fields.
  - `raw_tick`: Current 64-bit coarse time from H03. It is the common time coordinate used for acceptance and execution.
  - `cmd_valid`: Asserted by H02 while a committed command is available. H02 must keep this signal and all command fields stable until `cmd_ready` is also high.
  - `cmd_id`: Non-zero monotonically increasing command identifier.  Duplicate or older identifiers are rejected.
  - `cmd_target_tick`: Future raw-tick value at which the command is applied.
  - `cmd_control_value`: Opaque 32-bit value latched and presented to the downstream consumer when the command is executed.
  - `schedule_enable`: Higher-level permission to accept and execute scheduled commands. Use `local_time_valid` for local-only timing; use `system_time_valid` only for commands whose correctness depends on cross-node time convergence.
  - `raw_tick_exhausted`: Asserted when H03 can no longer advance its timebase. It blocks acceptance and causes a waiting command to enter `STATE_FAULT`.
  - `clear_fault`: One-clock request to leave `STATE_FAULT`.  Recovery is allowed only when `schedule_enable` is high and `raw_tick_exhausted` is low.

## Output Signals:
  - `cmd_ready`: Valid/ready backpressure signal. High only in healthy `STATE_IDLE`; a transfer occurs on `cmd_valid && cmd_ready` at a clock edge.
  - `cmd_accepted`: One-clock pulse on a successful command transfer. It means the command was accepted and is now waiting for execution, not that it has already executed.
  - `cmd_waiting_for_execute`: Level signal indicating `STATE_WAIT`.
  - `cmd_executed`: One-clock pulse on the target edge after H05 reaches `raw_tick == cmd_target_tick`. `cmd_return_code` changes to `SUCCESS` at the same state transition.
  - `cmd_fault`: Level signal indicating sticky `STATE_FAULT`; while high, `cmd_ready` remains low.
  - `cmd_return_code`: Latched status/result for the whole H05 command path:
    - `4'b0101` `IDLE`: reset/recovered idle baseline;
    - `4'b0100` `PENDING`: command accepted and waiting for its target edge;
    - `4'b0000` `SUCCESS`: target edge was reached and applied;
    - `4'b0001` `ID_INVALID`: command ID was zero, duplicated, or old;
    - `4'b0010` `CMD_MISSED`: target was already too late or was skipped;
    - `4'b0011` `TIME_INVALID`: timebase or scheduling permission failed.
  - `cmd_accepted` and `cmd_waiting_for_execute` therefore correspond to the `PENDING` code, while `cmd_executed` corresponds to `SUCCESS`. For a rejected command or a fault, the corresponding error code remains latched even if the state machine is no longer in `STATE_WAIT`.
  - `apply_now`: Combinational target-edge enable. It is high while the sampled `raw_tick` equals the latched target and the scheduling conditions remain valid. Downstream synchronous logic should sample this signal together with `active_control_value`.
  - `active_control_value`: Latched control value for the pending/most recently executed command. It does not change when H02 rewrites its shadow fields.

## Integration assumptions and limitations:
  - H02 and H05 command-channel signals are assumed to be in the same clock domain. A CDC bridge is required if the AXI clock and H05 timebase clock differ.
  - There is only one in-flight command; H05 is not a FIFO.
  - A successful `cmd_executed` reports the H05 timing event only.  If a downstream block must acknowledge that it consumed the control value, add a separate completion/ready handshake rather than redefining `cmd_executed`.
*/

`include "H05_protocol_defs.vh"

module H05_scheduled_execute #(
    parameter [63:0] ARRIVE_TICKS_BEFORE_EXECUTE = 64'd10
)(
    input wire         clk,
    input wire         rst,
    input wire [63:0]  raw_tick,

    // Command valid/ready channel from H02.
    input wire         cmd_valid,
    output wire        cmd_ready,
    input wire [31:0]  cmd_id,
    input wire [63:0]  cmd_target_tick,
    input wire [31:0]  cmd_control_value,

    // Scheduling policy and recovery controls.
    // For local-only commands, schedule_enable can be local_time_valid.
    // For cross-node commands, a higher-level policy may use system_time_valid.
    input wire         schedule_enable,
    input wire         raw_tick_exhausted,
    input wire         clear_fault,

    // Command status and execution outputs.
    output reg         cmd_accepted,
    output wire        cmd_waiting_for_execute,
    output reg         cmd_executed,
    output wire        cmd_fault,
    output reg [`H05_RETURN_CODE_WIDTH-1:0] cmd_return_code,
    output wire        apply_now,
    output wire [31:0] active_control_value
);

    localparam [1:0] STATE_IDLE  = 2'b00;
    localparam [1:0] STATE_WAIT  = 2'b01;
    localparam [1:0] STATE_FAULT = 2'b10;

    reg [1:0]  state;
    reg [31:0] last_cmd_id;
    reg [63:0] target_tick;
    reg [31:0] control_value;

    assign cmd_waiting_for_execute = (state == STATE_WAIT);
    assign cmd_fault = (state == STATE_FAULT);

    // A command is accepted only when the scheduler is idle, healthy, and
    // its timebase is currently usable.
    assign cmd_ready =
        !rst &&
        (state == STATE_IDLE) &&
        schedule_enable &&
        !raw_tick_exhausted;

    // apply_now is intentionally combinational. It is high during the cycle
    // whose sampled raw_tick equals target_tick, so a downstream synchronous
    // consumer can use it as the target-edge enable.
    assign apply_now =
        !rst &&
        (state == STATE_WAIT) &&
        (raw_tick == target_tick) &&
        schedule_enable &&
        !raw_tick_exhausted;

    assign active_control_value = control_value;

    always @(posedge clk) begin
        if (rst) begin
            state           <= STATE_IDLE;
            last_cmd_id     <= 32'b0;
            target_tick     <= 64'b0;
            control_value   <= 32'b0;
            cmd_accepted    <= 1'b0;
            cmd_executed    <= 1'b0;
            cmd_return_code <= `H05_RETURN_CODE_IDLE;
        end else begin
            // These are one-clock event pulses. cmd_return_code is a latched
            // status: PENDING after acceptance, SUCCESS only after the target
            // edge, and an error code if execution is abandoned.
            cmd_accepted <= 1'b0;
            cmd_executed <= 1'b0;

            case (state)
                STATE_IDLE: begin
                    if (raw_tick_exhausted) begin
                        state           <= STATE_FAULT;
                        cmd_return_code <= `H05_RETURN_CODE_TIME_INVALID;
                    end else if (cmd_valid && cmd_ready) begin
                        if ((cmd_id == 32'b0) || (cmd_id <= last_cmd_id)) begin
                            // A malformed command is rejected, but does not
                            // poison a healthy scheduler.
                            cmd_return_code <= `H05_RETURN_CODE_ID_INVALID;
                        end else if (
                            (cmd_target_tick <= raw_tick) ||
                            ((cmd_target_tick - raw_tick) < ARRIVE_TICKS_BEFORE_EXECUTE)
                        ) begin
                            // This command is too late; a later valid command
                            // may still be accepted immediately.
                            cmd_return_code <= `H05_RETURN_CODE_CMD_MISSED;
                        end else begin
                            last_cmd_id     <= cmd_id;
                            target_tick     <= cmd_target_tick;
                            control_value   <= cmd_control_value;
                            state           <= STATE_WAIT;
                            cmd_accepted    <= 1'b1;
                            cmd_return_code <= `H05_RETURN_CODE_PENDING;
                        end
                    end
                end

                STATE_WAIT: begin
                    if (raw_tick_exhausted || !schedule_enable) begin
                        // The timebase or scheduling authority became invalid
                        // before execution. Do not execute the command and do
                        // not accept another one until the fault is cleared.
                        state           <= STATE_FAULT;
                        cmd_return_code <= `H05_RETURN_CODE_TIME_INVALID;
                    end else if (raw_tick == target_tick) begin
                        // apply_now was high for this target edge. The state
                        // transition and cmd_executed report completion.
                        state           <= STATE_IDLE;
                        cmd_executed    <= 1'b1;
                        cmd_return_code <= `H05_RETURN_CODE_SUCCESS;
                    end else if (raw_tick > target_tick) begin
                        // Defensive handling in case the target was skipped.
                        state           <= STATE_FAULT;
                        cmd_return_code <= `H05_RETURN_CODE_CMD_MISSED;
                    end
                end

                STATE_FAULT: begin
                    // A fault is sticky. Recovery must be explicit and the
                    // scheduling/timebase conditions must be valid again.
                    if (clear_fault && schedule_enable && !raw_tick_exhausted) begin
                        state           <= STATE_IDLE;
                        cmd_return_code <= `H05_RETURN_CODE_IDLE;
                    end
                end

                default: begin
                    state           <= STATE_FAULT;
                    cmd_return_code <= `H05_RETURN_CODE_TIME_INVALID;
                end
            endcase
        end
    end

endmodule
