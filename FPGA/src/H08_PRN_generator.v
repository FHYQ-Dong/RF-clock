// H08: Pseudo-Random Number Generator
// Algorithm: 16-bit Linear Feedback Shift Register (LFSR) 

/* Delays:
  - 1-cycle delay from the frame_seed being set to the output being generated.
*/

// TODO: 重写regs，表示当前cycle状态

`include "src/H05_protocol_defs.vh"
`include "src/H08_protocol_defs.vh"


module H08_PRN_generator (
    input wire clk,
    input wire rst,
    input wire prn_enable,
    input wire clear_fault,

    input wire        frame_valid,
    output wire       frame_ready,
    output reg        frame_accepted,
    input wire [31:0] frame_id,
    input wire [15:0] frame_seed,
    input wire [31:0] frame_len,

    output wire        chip_valid,
    input wire         chip_ready,
    output wire        prn_chip,
    output wire [31:0] chip_index,
    output wire        chip_last,
    
    output wire        frame_idle,
    output wire        frame_generating,
    output wire        frame_fault,
    output reg [3:0]   frame_return_code,
    output wire [31:0] active_frame_id
);
    localparam [1:0] STATE_IDLE = 2'b00;
    localparam [1:0] STATE_GENERATING = 2'b01;
    localparam [1:0] STATE_FAULT = 2'b10;

    reg [1:0] state;
    reg [31:0] frame_id_reg;
    reg [31:0] frame_len_reg;
    reg [31:0] chip_count_reg;
    reg [15:0] psnr_state;

    assign prn_chip = psnr_state[0];
    assign chip_index = chip_count_reg;
    assign chip_last = chip_valid && (chip_count_reg == frame_len_reg - 1);
    assign active_frame_id = frame_id_reg;

    assign frame_idle = (state == STATE_IDLE);
    assign frame_generating = (state == STATE_GENERATING);
    assign frame_fault = (state == STATE_FAULT);
    assign frame_ready = !rst && (state == STATE_IDLE) && prn_enable;
    assign chip_valid = !rst && (state == STATE_GENERATING) && prn_enable;

    always @(posedge clk) begin
        if (rst) begin
            state <= STATE_IDLE;
            frame_id_reg <= 32'b0;
            frame_len_reg <= 32'b0;
            chip_count_reg <= 32'b0;
            psnr_state <= 16'b0;
            frame_accepted <= 0;
            frame_return_code <= `H08_RETURN_CODE_IDLE;
        end
        else begin
            frame_accepted <= 0;
            
            case (state)
                STATE_IDLE: begin
                    frame_id_reg <= 32'b0;
                    frame_len_reg <= 32'b0;
                    psnr_state <= 16'b0;
                    chip_count_reg <= 32'b0;
                    if (frame_valid && frame_ready) begin
                        if (frame_len == 32'd0) begin
                            frame_accepted <= 0;
                            frame_return_code <= `H08_RETURN_CODE_FRAME_LENGTH_INVALID;
                        end
                        else if (frame_seed == 16'd0) begin
                            frame_accepted <= 0;
                            frame_return_code <= `H08_RETURN_CODE_FRAME_SEED_INVALID;
                        end
                        else begin
                            frame_accepted <= 1;
                            frame_return_code <= `H08_RETURN_CODE_PENDING;
                            state <= STATE_GENERATING;
                            frame_id_reg <= frame_id;
                            frame_len_reg <= frame_len;
                            psnr_state <= frame_seed;
                            chip_count_reg <= 32'd0;
                        end
                    end
                end
                STATE_GENERATING: begin
                    // chip_valid/chip_index/prn_chip remain stable while the
                    // downstream applies backpressure.  Only a real
                    // handshake advances the LFSR and chip counter.
                    if (!prn_enable) begin
                        state <= STATE_FAULT;
                        frame_return_code <= `H08_RETURN_CODE_DOWNSTREAM_READY_NOT_KEPT;
                    end
                    else if (chip_ready) begin
                        if (chip_count_reg == frame_len_reg - 1) begin
                            state <= STATE_IDLE;
                            frame_return_code <= `H08_RETURN_CODE_SUCCESS;
                            chip_count_reg <= 32'b0;
                            psnr_state <= 16'b0;
                            frame_id_reg <= 32'b0;
                            frame_len_reg <= 32'b0;
                        end
                        else begin
                            psnr_state <= {
                                psnr_state[0] ^ psnr_state[2] ^ psnr_state[3] ^ psnr_state[5],
                                psnr_state[15:1]
                            };
                            chip_count_reg <= chip_count_reg + 1;
                        end
                    end
                    // Otherwise chip_ready is low: hold all generating
                    // registers and keep the current chip available.
                end
                STATE_FAULT: begin
                    psnr_state <= 16'b0;
                    if (clear_fault && prn_enable) begin
                        state <= STATE_IDLE;
                        frame_return_code <= `H08_RETURN_CODE_IDLE;
                    end
                end
                default: begin
                    psnr_state <= 16'b0;
                    state <= STATE_FAULT;
                end
            endcase
        end
    end
endmodule
