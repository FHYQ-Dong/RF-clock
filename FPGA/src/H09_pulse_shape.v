// Root Raised Cosine Pulse Shaper
// The coefficients should be generated using python scripts and put in data/H09_rrc_coeffs.vh. This Verilog module will automatically load the coefficients when the design is synthesized.

/* Interface:

## Input Signals
  - clk: 100 MHz clock signal

## Output Signals
  - sample_I: I component of the output sample
    * sample_I[15:0] = lane 0
    * sample_I[31:16] = lane 1
    * ...
    * sample_I[127:112] = lane 7
  - sample_Q: Q component of the output sample 
    * sample_Q[15:0] = lane 0
    * sample_Q[31:16] = lane 1
    * ...
    * sample_Q[127:112] = lane 7

*/

/* Timing
  There's a one-cycle delay from prn_chip to cur_prn_chip_reg, i.e. from the input prn chip to the output rrc pulse.

  -   N: ...
  - N+1: ...
  - N+2: ...
  - N+...
*/

`include "src/H09_protocol_defs.vh"

module H09_pulse_shape #(
    // DAC amplitude is 2**DAC_AMPLITUDE_BASE_2.  Keeping the amplitude in
    // power-of-two form lets the scaler use only a shift instead of a
    // general multiplier.
    parameter integer DAC_AMPLITUDE_BASE_2 = 9
)(
    input clk,
    input rst,
    input wire pulse_enable,
    input wire clear_fault,

    input  wire chip_valid,
    output wire chip_ready,
    input  wire prn_chip,
    input  wire chip_last,

    output wire         sample_valid,
    input  wire         sample_ready,
    output wire [127:0] sample_I,
    output wire [127:0] sample_Q,
    output wire [7:0]   sample_mask,
    output wire         sample_first,
    output wire         sample_last,
    output wire [31:0]  sample_index,

    output wire       pulse_busy,
    output wire       pulse_fault,
    output wire [3:0] pulse_return_code
);

    `include "data/H09_rrc_coeffs.vh"
    task clear_chip_history;
        integer j;
        begin
            for (j = 0; j < H09_RRC_SPAN_CHIPS; j = j + 1) begin
                chip_history_reg[j] <= 2'sd0;
            end
        end
    endtask
    task append_chip_history;
        integer j;
        begin
            for (j = H09_RRC_SPAN_CHIPS-1; j > 0; j = j - 1) begin
                chip_history_reg[j] <= chip_history_reg[j-1];
            end
            chip_history_reg[0] <= cur_prn_chip_reg;
        end
    endtask

    localparam [3:0] STATE_IDLE   = 4'b0000;
    localparam [3:0] STATE_OUTPUT = 4'b0001;
    localparam [3:0] STATE_TAIL   = 4'b0010;
    localparam [3:0] STATE_FAULT  = 4'b0011;    
    
    // All the registers represent the state of the CURRENT CYCLE, and output samples are generated using combinational logic
    reg [3:0] state;
    assign pulse_busy = (state == STATE_OUTPUT) || (state == STATE_TAIL);
    
    assign pulse_fault = (state == STATE_FAULT);

    reg signed [1:0] cur_prn_chip_reg;
    reg signed [1:0] chip_history_reg [0:H09_RRC_SPAN_CHIPS-1];
    reg chip_last_reg;
    assign chip_ready =
        !rst && !pulse_fault && pulse_enable &&
        (
            (state == STATE_IDLE && sample_ready) ||
            ((state == STATE_OUTPUT) && !chip_last_reg && sample_ready)
        );

    reg sample_first_reg;
    assign sample_first = sample_first_reg;
    reg [31:0] sample_index_reg;
    assign sample_index = sample_index_reg;
    assign sample_valid = 
        !rst && !pulse_fault && pulse_enable &&
        (
            (state == STATE_OUTPUT) || 
            ((state == STATE_TAIL) && tail_samples_remaining_reg != 32'd0)
        );

    reg [3:0] pulse_return_code_reg;
    assign pulse_return_code = pulse_return_code_reg;

    reg [31:0] tail_samples_remaining_reg;

    // State machine
    always @(posedge clk) begin
        if (rst) begin
            state <= STATE_IDLE;
            cur_prn_chip_reg <= 2'sd0;
            clear_chip_history;
            chip_last_reg <= 0;
            sample_first_reg <= 0;
            sample_index_reg <= 0;
            pulse_return_code_reg <= 0;
            tail_samples_remaining_reg <= 0;
        end
        else begin
            case (state)
                STATE_IDLE: begin
                    cur_prn_chip_reg <= 2'sd0;
                    clear_chip_history;
                    chip_last_reg <= 0;
                    sample_first_reg <= 0;
                    sample_index_reg <= 0;
                    tail_samples_remaining_reg <= 0;
                    if (chip_ready && chip_valid) begin
                        state <= STATE_OUTPUT;
                        cur_prn_chip_reg <= (prn_chip == 1'b1 ? 2'sd1 : -2'sd1);
                        chip_last_reg <= chip_last;
                        sample_first_reg <= 1;
                        sample_index_reg <= 0;
                        tail_samples_remaining_reg <= 0;
                    end
                end
                STATE_OUTPUT: begin
                    if (!pulse_enable) begin
                        state <= STATE_FAULT;
                        pulse_return_code_reg <= `H09_RETURN_CODE_ENABLE_DROPPED;
                    end
                    else if (sample_valid && sample_ready) begin
                        sample_first_reg <= 0;
                        if (chip_last_reg) begin
                            if (H09_RRC_TAIL_SAMPLES == 32'd0) begin
                                state <= STATE_IDLE;
                                tail_samples_remaining_reg <= 0;
                                cur_prn_chip_reg <= 2'sd0;
                                clear_chip_history;
                                chip_last_reg <= 0;
                                sample_first_reg <= 0;
                                sample_index_reg <= 0;
                                pulse_return_code_reg <= `H09_RETURN_CODE_SUCCESS;
                            end
                            else begin
                                state <= STATE_TAIL;
                                tail_samples_remaining_reg <= H09_RRC_TAIL_SAMPLES;
                                cur_prn_chip_reg <= 2'sd0;
                                append_chip_history;
                                sample_index_reg <= sample_index_reg + 32'd8;
                            end
                        end
                        else if (chip_valid && chip_ready) begin
                            cur_prn_chip_reg <= (prn_chip == 1'b1 ? 2'sd1 : -2'sd1);
                            chip_last_reg <= chip_last;
                            append_chip_history;
                            sample_index_reg <= sample_index_reg + 32'd8;
                        end
                        else begin
                            state <= STATE_FAULT;
                            pulse_return_code_reg <= `H09_RETURN_CODE_UPSTREAM_VALID_NOT_KEPT;
                        end
                    end
                    else begin
                        state <= STATE_FAULT;
                        pulse_return_code_reg <= `H09_RETURN_CODE_DOWNSTREAM_READY_NOT_KEPT;
                    end
                end
                STATE_TAIL: begin
                    if (!pulse_enable) begin
                        state <= STATE_FAULT;
                        pulse_return_code_reg <= `H09_RETURN_CODE_ENABLE_DROPPED;
                    end
                    else if (sample_valid && sample_ready) begin
                        if (tail_samples_remaining_reg > 32'd8)  begin
                            tail_samples_remaining_reg <= tail_samples_remaining_reg - 32'd8;
                            sample_index_reg <= sample_index_reg + 32'd8;
                            cur_prn_chip_reg <= 2'b00;
                            append_chip_history;
                        end
                        else begin
                            state <= STATE_IDLE;
                            tail_samples_remaining_reg <= 0;
                            sample_index_reg <= 0;
                            cur_prn_chip_reg <= 2'sd0;
                            clear_chip_history;
                            chip_last_reg <= 0;
                            pulse_return_code_reg <= `H09_RETURN_CODE_SUCCESS;
                        end
                    end
                    else begin
                        state <= STATE_FAULT;
                        pulse_return_code_reg <= `H09_RETURN_CODE_DOWNSTREAM_READY_NOT_KEPT;
                    end
                end
                STATE_FAULT: begin
                    if (clear_fault && pulse_enable) begin
                        state <= STATE_IDLE;
                        pulse_return_code_reg <= `H09_RETURN_CODE_SUCCESS;
                        cur_prn_chip_reg <= 2'sd0;
                        clear_chip_history;
                        chip_last_reg <= 0;
                        sample_first_reg <= 0;
                        sample_index_reg <= 0;
                        tail_samples_remaining_reg <= 0;
                    end
                end
                default: begin
                    state <= STATE_FAULT;
                    pulse_return_code_reg <= `H09_RETURN_CODE_UNEXPECTED_ERROR;
                end
            endcase
        end
    end

    // Combinational logic for RRC
    localparam integer H09_ACC_WIDTH = 32;
    localparam integer H09_MAX_CHIP_TERMS =
        (H09_RRC_TAP_COUNT + H09_RRC_SAMPLES_PER_SYMBOL - 1) / 
        H09_RRC_SAMPLES_PER_SYMBOL;

    // Convert the signed RRC accumulator to a signed 16-bit DAC sample.
    // The coefficients are fractional, so scale by 2**DAC_AMPLITUDE_BASE_2
    // with a shift, round symmetrically to nearest, remove the coefficient
    // fractional bits, and saturate instead of allowing signed wraparound.
    function signed [15:0] scale_and_saturate;
        input signed [H09_ACC_WIDTH-1:0] accumulator_value;
        reg signed [63:0] accumulator_ext;
        reg signed [63:0] product;
        reg signed [63:0] rounded_value;
        reg signed [63:0] rounding_offset;
        begin
            accumulator_ext = accumulator_value;
            // DAC_AMPLITUDE = 2**DAC_AMPLITUDE_BASE_2; this is a shift,
            // not a variable multiplier.
            product = accumulator_ext <<< DAC_AMPLITUDE_BASE_2;
            rounding_offset = 64'sd1 << (H09_RRC_FRAC_BITS - 1);
            // Match the software model's symmetric signed rounding.
            if (product >= 0)
                rounded_value = (product + rounding_offset) >>> H09_RRC_FRAC_BITS;
            else
                rounded_value = -(((-product) + rounding_offset) >>> H09_RRC_FRAC_BITS);
            // Saturate the result
            if (rounded_value > 64'sd32767)
                scale_and_saturate = 16'sh7fff;
            else if (rounded_value < -64'sd32768)
                scale_and_saturate = 16'sh8000;
            else
                scale_and_saturate = rounded_value[15:0];
        end
    endfunction

    wire signed [H09_ACC_WIDTH-1:0] coefficient_ext [0:H09_RRC_TAP_COUNT-1];
    wire signed [H09_ACC_WIDTH-1:0] tap_term [0:H09_RRC_TAP_COUNT-1];
    wire signed [H09_ACC_WIDTH-1:0] phase_sum [0:H09_RRC_SAMPLES_PER_SYMBOL-1][0:H09_MAX_CHIP_TERMS];
    wire signed [H09_ACC_WIDTH-1:0] accumulator [0:H09_RRC_SAMPLES_PER_SYMBOL-1];
    
    // Extend coefficient values to H09_ACC_WIDTH
    genvar t;
    generate
        for (t = 0; t < H09_RRC_TAP_COUNT; t = t + 1) begin : gen_coeff
            assign coefficient_ext[t] =
                {{(H09_ACC_WIDTH-H09_RRC_COEFF_BITS)
                {h09_rrc_rom[t][H09_RRC_COEFF_BITS-1]}},
                h09_rrc_rom[t]};
        end
    endgenerate

    // Current tap: current chip or history chip
    generate
        for (t = 0; t < H09_RRC_TAP_COUNT; t = t + 1) begin : gen_tap
            localparam integer DELAY = t / H09_RRC_SAMPLES_PER_SYMBOL;
            if (DELAY == 0) begin : gen_current_chip
                assign tap_term[t] =
                    (cur_prn_chip_reg == 2'b01) ?
                        coefficient_ext[t] :
                    (cur_prn_chip_reg == 2'b11) ?
                        -coefficient_ext[t] :
                        {H09_ACC_WIDTH{1'b0}};
            end
            else begin : gen_history_chip
                assign tap_term[t] =
                    (chip_history_reg[DELAY-1] == 2'b01) ?
                        coefficient_ext[t] :
                    (chip_history_reg[DELAY-1] == 2'b11) ?
                        -coefficient_ext[t] :
                        {H09_ACC_WIDTH{1'b0}};
            end
        end
    endgenerate

    genvar phase;
    genvar delay;
    generate
        for (phase = 0; phase < H09_RRC_SAMPLES_PER_SYMBOL; phase = phase + 1) begin : gen_phase
            assign phase_sum[phase][0] = {H09_ACC_WIDTH{1'b0}};
            for (delay = 0; delay < H09_MAX_CHIP_TERMS; delay = delay + 1) begin : gen_sum
                if ((phase + delay * H09_RRC_SAMPLES_PER_SYMBOL) < H09_RRC_TAP_COUNT) begin : gen_valid_tap
                    assign phase_sum[phase][delay+1] =
                        phase_sum[phase][delay] + tap_term[phase + delay * H09_RRC_SAMPLES_PER_SYMBOL];
                end
                else begin : gen_invalid_tap
                    assign phase_sum[phase][delay+1] = phase_sum[phase][delay];
                end
            end
            assign accumulator[phase] = phase_sum[phase][H09_MAX_CHIP_TERMS];
        end
    endgenerate

    assign sample_Q = 128'b0;
    genvar lane;
    generate
        for (lane = 0; lane < H09_RRC_SAMPLES_PER_SYMBOL; lane = lane + 1) begin : gen_output_lane
            assign sample_mask[lane] = sample_valid &&
                (
                    (state == STATE_OUTPUT) ||
                    (
                        (state == STATE_TAIL) &&
                        (tail_samples_remaining_reg > lane)
                    )
                );
            assign sample_I[lane*16 +: 16] =
                sample_mask[lane] ?
                    scale_and_saturate(accumulator[lane]) :
                    16'sd0;
        end
    endgenerate

    assign sample_last =
    sample_valid && ((
        state == STATE_TAIL && 
        tail_samples_remaining_reg <= 32'd8
    ) || (
        state == STATE_OUTPUT && chip_last_reg &&
        H09_RRC_TAIL_SAMPLES == 32'd0
    ));
    
endmodule
