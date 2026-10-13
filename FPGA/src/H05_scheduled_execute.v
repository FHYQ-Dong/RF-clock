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
  * `apply_now` is the target-edge enable.  `cmd_accepted` is a one-clock
  * event generated only when a semantically valid command is latched for
  * execution.  A command-level rejection is reported by `cmd_return_code`
  * while the scheduler remains idle; it is not a sticky scheduler fault.
*/

/* State machine:
  - `STATE_IDLE`: No command is waiting.  H05 may assert `cmd_ready` when the
    scheduling policy and raw timebase are valid.
  - `STATE_WAIT`: A command was accepted and its fields were latched.  H05
    waits for `raw_tick == target_tick`; `cmd_ready` remains low so a second
    command cannot overwrite the pending one.
  - `STATE_EXECUTING`: The target edge was reached and the TX path was
    admitted. H05 waits for `tx_done` or `tx_fault`; a new command is not
    accepted while the frame is active.
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

## Input Signals
  - `clk`: H05 working clock. It must be the same timebase clock used to sample `raw_tick` and the downstream command consumer.
  - `rst`: Synchronous active-high reset. Reset returns H05 to `STATE_IDLE` and clears the latched command fields.
  - `raw_tick`: Current 64-bit coarse time from H03. It is the common time coordinate used for acceptance and execution.
  - `cmd_valid`: Asserted by H02 while a committed command is available. H02 must keep this signal and all command fields stable until `cmd_ready` is also high.
  - `cmd_id`: Non-zero monotonically increasing command identifier.  Duplicate or older identifiers are rejected.
  - `cmd_target_tick`: Future raw-tick value at which the command is applied.
  - `cmd_control_value`: Opaque 192-bit value latched and presented to the
    downstream consumer when the command is executed.  H05 does not decode
    this payload; H08/H09/RFDC-facing bridges decode the agreed command format.
  - `schedule_enable`: Higher-level permission to accept and execute scheduled commands. Use `local_time_valid` for local-only timing; use `system_time_valid` only for commands whose correctness depends on cross-node time convergence.
  - `raw_tick_exhausted`: Asserted when H03 can no longer advance its timebase. It blocks acceptance and causes a waiting command to enter `STATE_FAULT`.
  - `tx_admission_ready`: The complete H08/H09/RFDC TX chain is idle, healthy,
    and ready to start a full frame without active-frame backpressure. It is
    checked both when H02 submits a command and again at the target tick.
  - `tx_done`: One-clock event from the TX aggregator after the final RRC tail
    beat has been accepted by the RFDC path.
  - `tx_fault`: Downstream fault indication. `tx_return_code` identifies the
    originating module and local cause.
  - `tx_return_code`: Eight-bit source/cause code from the TX status
    aggregator. H05 latches it when `tx_fault` is observed.
  - `clear_fault`: One-clock request to leave `STATE_FAULT`.  Recovery is allowed only when `schedule_enable` is high and `raw_tick_exhausted` is low.

## Output Signals:
  - `cmd_ready`: High only in healthy `STATE_IDLE` while the timebase and the
    complete TX chain are ready. A transport transfer occurs on
    `cmd_valid && cmd_ready` at a clock edge.
  - `cmd_idle`: Level signal indicating that no command is waiting for execution (`STATE_IDLE`). It does not by itself guarantee that a new command can be accepted; use `cmd_ready` for that.
  - `cmd_accepted`: One-clock pulse only when the transferred command passes H05 validation and is latched for execution. It is not asserted for a command-level rejection.
  - `cmd_wait`: Level signal indicating `STATE_WAIT` or `STATE_EXECUTING`.
  - `cmd_fault`: Level signal indicating sticky `STATE_FAULT`; while high, `cmd_ready` remains low.
  - `cmd_return_code`: Eight-bit latched status/result for the whole H05
    command path. The upper nibble identifies the fault source and the lower
    nibble identifies that source's cause. `PENDING` remains latched while a
    command is waiting or executing; `SUCCESS` is latched only after `tx_done`.
  - `cmd_accepted` and `cmd_wait` correspond to the `PENDING` code. A target
    miss, unavailable TX admission, timebase failure, or downstream fault
    leaves the scheduler in `STATE_FAULT` with the corresponding code.
  - `apply_now`: Combinational target-edge start event. It is high only when
    the sampled `raw_tick` equals the latched target and `tx_admission_ready`
    is still valid. Downstream synchronous logic should sample this signal
    together with `active_control_value`.
  - `active_control_value`: Latched 192-bit control value for the
    pending/most recently executed command. It does not change when H02
    rewrites its shadow fields.

## Integration assumptions and limitations:
  - H02 and H05 command-channel signals are assumed to be in the same clock domain. A CDC bridge is required if the AXI clock and H05 timebase clock differ.
  - There is only one in-flight command; H05 is not a FIFO.
  - `apply_now` is the one-cycle target-edge start event for downstream command
    consumers. `cmd_return_code == SUCCESS` is a latched result only after the
    complete downstream transaction reports `tx_done`.
*/

/* Delays:
  - 0-cycle delay from the cmd_target_tick to the apply_now signal.
*/

`include "src/H05_protocol_defs.vh"
`include "src/H05_command_defs.vh"

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
    input wire [`H05_CMD_CONTROL_WIDTH-1:0] cmd_control_value,

    // Scheduling policy and recovery controls.
    // For local-only commands, schedule_enable can be local_time_valid.
    // For cross-node commands, a higher-level policy may use system_time_valid.
    input wire         schedule_enable,
    input wire         raw_tick_exhausted,
    input wire         clear_fault,

    // The complete downstream TX path must be admitted before a frame starts.
    // During an active frame, loss of downstream readiness is reported as a
    // fault rather than handled as normal backpressure.
    input wire         tx_admission_ready,
    input wire         tx_done,
    input wire         tx_fault,
    input wire [`H05_RETURN_CODE_WIDTH-1:0] tx_return_code,

    // Command status and execution outputs.
    output reg         cmd_accepted,
    output wire        cmd_idle,
    output wire        cmd_wait,
    output wire        cmd_fault,
    output reg [`H05_RETURN_CODE_WIDTH-1:0] cmd_return_code,
    output wire        apply_now,
    output wire [`H05_CMD_CONTROL_WIDTH-1:0] active_control_value
);

    localparam [1:0] STATE_IDLE       = 2'b00;
    localparam [1:0] STATE_WAIT       = 2'b01;
    localparam [1:0] STATE_EXECUTING = 2'b10;
    localparam [1:0] STATE_FAULT      = 2'b11;

    reg [1:0]  state;
    reg [31:0] last_cmd_id;
    reg [63:0] target_tick;
    reg [`H05_CMD_CONTROL_WIDTH-1:0] control_value;

    assign cmd_idle = (state == STATE_IDLE);
    assign cmd_wait = (state == STATE_WAIT) || (state == STATE_EXECUTING);
    assign cmd_fault = (state == STATE_FAULT);

    // A command is accepted only when the scheduler and the complete TX path
    // are idle/healthy.  H02 holds cmd_valid and the command fields until this
    // condition becomes true.
    assign cmd_ready =
        !rst &&
        (state == STATE_IDLE) &&
        schedule_enable &&
        !raw_tick_exhausted &&
        tx_admission_ready;

    // apply_now is intentionally combinational. It is high during the cycle
    // whose sampled raw_tick equals target_tick, so a downstream synchronous
    // consumer can use it as the target-edge enable.
    assign apply_now =
        !rst &&
        (state == STATE_WAIT) &&
        (raw_tick == target_tick) &&
        schedule_enable &&
        !raw_tick_exhausted &&
        tx_admission_ready &&
        !tx_fault;

    assign active_control_value = control_value;

    always @(posedge clk) begin
        if (rst) begin
            state           <= STATE_IDLE;
            last_cmd_id     <= 32'b0;
            target_tick     <= 64'b0;
            control_value   <= {`H05_CMD_CONTROL_WIDTH{1'b0}};
            cmd_accepted    <= 1'b0;
            cmd_return_code <= `H05_RETURN_CODE_IDLE;
        end else begin
            // cmd_accepted is a one-clock semantic-acceptance pulse.
            // cmd_return_code is a latched status/result: PENDING after
            // acceptance, SUCCESS after the target edge, and an error code
            // for a rejection or abandoned execution.
            cmd_accepted <= 1'b0;

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
                        // The TX path must still be admitted at the target
                        // edge.  A missed admission is a fault, not a legal
                        // active-frame stall.
                        if (!tx_admission_ready) begin
                            state           <= STATE_FAULT;
                            cmd_return_code <= `H05_RETURN_CODE_TX_NOT_READY;
                        end else if (tx_fault) begin
                            state <= STATE_FAULT;
                            if (tx_return_code != {`H05_RETURN_CODE_WIDTH{1'b0}})
                                cmd_return_code <= tx_return_code;
                            else
                                cmd_return_code <= `H05_RETURN_CODE_TX_FAULT;
                        end else begin
                            state           <= STATE_EXECUTING;
                            cmd_return_code <= `H05_RETURN_CODE_PENDING;
                        end
                    end else if (raw_tick > target_tick) begin
                        // Defensive handling in case the target was skipped.
                        state           <= STATE_FAULT;
                        cmd_return_code <= `H05_RETURN_CODE_CMD_MISSED;
                    end
                end

                STATE_EXECUTING: begin
                    // Active-frame backpressure is not legal.  The TX
                    // aggregator reports a loss of readiness/lock/stream
                    // health through tx_fault; H05 waits only for completion
                    // or that explicit fault.
                    if (raw_tick_exhausted || !schedule_enable) begin
                        state           <= STATE_FAULT;
                        cmd_return_code <= `H05_RETURN_CODE_TIME_INVALID;
                    end else if (tx_fault) begin
                        state <= STATE_FAULT;
                        if (tx_return_code != {`H05_RETURN_CODE_WIDTH{1'b0}})
                            cmd_return_code <= tx_return_code;
                        else
                            cmd_return_code <= `H05_RETURN_CODE_TX_FAULT;
                    end else if (tx_done) begin
                        state           <= STATE_IDLE;
                        cmd_return_code <= `H05_RETURN_CODE_SUCCESS;
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
