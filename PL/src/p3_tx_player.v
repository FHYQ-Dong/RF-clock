`timescale 1ns / 1ps
// P3: timed TX playback from an on-chip waveform buffer.
//
// The ARM writes the complete burst into the buffer through an AXI BRAM
// Controller (port A, byte address, read latency 1) before arming.  After
// the doorbell, beat 0 is presented on m_axis_tdata in the cycle where
// raw_tick == target, and one beat follows every clock without a gap until
// the length is reached.  Outside a burst the output is zero.
//
// There is no backpressure during a burst: a low DAC tready, a buffer write
// while ARMED/RUNNING, or loss of time_valid enters FAULT, which only
// CLEAR_FAULT leaves.  The waveform is never paused and resumed.
//
// A command may repeat the burst: cmd_repeat passes over beats
// 0..length-1, where beat 0 of each pass follows the last beat of the
// previous one in the next clock.  actual_tick is the start of the first
// pass; DONE and seq follow the last.
//
// MIN_LEAD_TICKS must exceed TX_PIPE + 1 so an accepted target can be met.
//
// DEPTH need not be a power of two: port A addresses 2**ADDR_W beats, and
// writes at or above DEPTH are dropped (reads there are undefined).
module p3_tx_player #(
    parameter integer DATA_W         = 256,
    parameter integer ADDR_W         = 13,  // buffer address bits
    parameter integer DEPTH          = 1 << ADDR_W,  // beats, at most 2**ADDR_W
    parameter integer BANK_ADDR_W    = 12,  // 4096-beat banks: 4 UltraRAMs each
    parameter integer BRAM_ADDR_W    = ADDR_W + 5,  // byte address: ADDR_W + log2(DATA_W/8)
    parameter [63:0]  MIN_LEAD_TICKS = 64'd64
) (
    input  wire                   clk,
    input  wire                   rst,
    input  wire [63:0]            raw_tick,
    input  wire                   time_valid,
    // Commands from P2 (one-cycle pulses; fields sampled on doorbell).
    input  wire                   doorbell,
    input  wire                   cancel,
    input  wire                   clear_fault,
    input  wire [63:0]            cmd_target,
    input  wire [31:0]            cmd_length,
    input  wire [31:0]            cmd_repeat,
    // Waveform buffer port A (AXI BRAM Controller).
    input  wire                   bram_en,
    input  wire [DATA_W/8-1:0]    bram_we,
    input  wire [BRAM_ADDR_W-1:0] bram_addr,
    input  wire [DATA_W-1:0]      bram_wrdata,
    output wire [DATA_W-1:0]      bram_rddata,
    // RFDC DAC AXI4-Stream.
    output wire [DATA_W-1:0]      m_axis_tdata,
    output reg                    m_axis_tvalid,
    input  wire                   m_axis_tready,
    // Status.
    output reg  [2:0]             state,
    output reg  [7:0]             result,
    output reg  [31:0]            seq,
    output reg  [63:0]            actual_tick,
    output wire                   fault
);
    `include "src/p_defs.vh"
    `include "src/p_regs.vh"

    localparam integer NBYTES     = DATA_W / 8;
    localparam integer BYTE_SHIFT = $clog2(NBYTES);
    // raw_tick at read issue + TX_PIPE = raw_tick when the beat is on tdata:
    // issue -> bank output register -> ram_q2 -> o_data.
    localparam [63:0]  TX_PIPE    = 64'd4;

    assign fault = (state == ST_FAULT);

    // ---------------------------------------------------------------
    // Waveform buffer in UltraRAM, as banks of 2**BANK_W beats.  UltraRAM
    // inference needs each memory a power of two deep (DEPTH need not be)
    // and port A coded no-change (no read in a write cycle); the AXI BRAM
    // Controller ignores read data during writes.  An address at or above
    // DEPTH selects no bank.  The bank multiplexer sits after each bank's
    // output register, so both ports keep their read latency.
    // ---------------------------------------------------------------
    localparam integer BANK_W = (ADDR_W < BANK_ADDR_W) ? ADDR_W : BANK_ADDR_W;
    localparam integer NBANKS = (DEPTH + (1 << BANK_W) - 1) / (1 << BANK_W);
    localparam integer SEL_W  = (ADDR_W > BANK_W) ? ADDR_W - BANK_W : 1;

    wire [ADDR_W-1:0] a_addr = bram_addr[BYTE_SHIFT +: ADDR_W];
    wire [SEL_W-1:0]  a_sel  = a_addr >> BANK_W;
    reg  [ADDR_W-1:0] rd_addr;
    wire [SEL_W-1:0]  b_sel  = rd_addr >> BANK_W;
    wire [DATA_W-1:0] a_q [0:NBANKS-1];
    wire [DATA_W-1:0] b_q [0:NBANKS-1];

    genvar k;
    generate
        for (k = 0; k < NBANKS; k = k + 1) begin : g_bank
            (* ram_style = "ultra" *)
            reg [DATA_W-1:0] mem [0:(1 << BANK_W) - 1];
            reg [DATA_W-1:0] qa, qb;
            integer c;
            always @(posedge clk) begin
                if (bram_en && a_sel == k) begin
                    for (c = 0; c < NBYTES; c = c + 1)
                        if (bram_we[c]) mem[a_addr[BANK_W-1:0]][8*c +: 8] <= bram_wrdata[8*c +: 8];
                    if (!(|bram_we)) qa <= mem[a_addr[BANK_W-1:0]];
                end
            end
            always @(posedge clk) qb <= mem[rd_addr[BANK_W-1:0]];
            assign a_q[k] = qa;
            assign b_q[k] = qb;
        end
    endgenerate

    reg [SEL_W-1:0]  a_sel_q, b_sel_q;  // bank of the word in qa / qb
    reg [DATA_W-1:0] ram_q2;
    always @(posedge clk) begin
        if (bram_en && !(|bram_we)) a_sel_q <= a_sel;
        b_sel_q <= b_sel;
        ram_q2  <= b_q[b_sel_q];
    end
    assign bram_rddata = a_q[a_sel_q];

    // ---------------------------------------------------------------
    // Playback pipeline and scheduler.
    // ---------------------------------------------------------------
    reg [63:0]       start_tick;
    reg [ADDR_W:0]   length;
    reg              rd_active;
    reg              rd_first;
    reg [ADDR_W:0]   rd_left;
    reg [15:0]       passes_left;  // including the pass being read
    reg              s1_v, s1_f, s1_l;
    reg              s2_v, s2_f, s2_l;
    reg              o_v,  o_f,  o_l;
    reg [DATA_W-1:0] o_data;

    assign m_axis_tdata = o_data;

    wire buf_write = bram_en && (|bram_we);

    task enter_fault(input [7:0] code);
        begin
            state     <= ST_FAULT;
            result    <= code;
            rd_active <= 1'b0;
            s1_v      <= 1'b0;
            s2_v      <= 1'b0;
            o_v       <= 1'b0;
            o_data    <= {DATA_W{1'b0}};
        end
    endtask

    always @(posedge clk) begin
        if (rst) begin
            state         <= ST_IDLE;
            result        <= RC_NONE;
            seq           <= 32'd0;
            actual_tick   <= 64'd0;
            start_tick    <= 64'd0;
            length        <= {(ADDR_W+1){1'b0}};
            rd_active     <= 1'b0;
            rd_first      <= 1'b0;
            rd_left       <= {(ADDR_W+1){1'b0}};
            passes_left   <= 16'd0;
            rd_addr       <= {ADDR_W{1'b0}};
            {s1_v, s1_f, s1_l} <= 3'b000;
            {s2_v, s2_f, s2_l} <= 3'b000;
            {o_v,  o_f,  o_l}  <= 3'b000;
            o_data        <= {DATA_W{1'b0}};
            m_axis_tvalid <= 1'b0;
        end else begin
            m_axis_tvalid <= 1'b1;

            // Pipeline advances every clock; fault/start below override.
            s1_v <= rd_active;
            s1_f <= rd_active && rd_first;
            s1_l <= rd_active && (rd_left == 1) && (passes_left == 1);
            {s2_v, s2_f, s2_l} <= {s1_v, s1_f, s1_l};
            {o_v,  o_f,  o_l}  <= {s2_v, s2_f, s2_l};
            o_data <= s2_v ? ram_q2 : {DATA_W{1'b0}};
            if (rd_active) begin
                rd_first <= 1'b0;
                if (rd_left != 1) begin
                    rd_addr <= rd_addr + 1'b1;
                    rd_left <= rd_left - 1'b1;
                end else if (passes_left != 1) begin
                    // Next pass: beat 0 is read right after the last beat.
                    rd_addr     <= {ADDR_W{1'b0}};
                    rd_left     <= length;
                    passes_left <= passes_left - 1'b1;
                end else begin
                    rd_active <= 1'b0;
                end
            end

            case (state)
                ST_IDLE, ST_DONE: begin
                    if (doorbell) begin
                        if (!time_valid)
                            result <= RC_REJ_TIME_INVALID;
                        else if (cmd_length == 32'd0 || cmd_length > DEPTH ||
                                 cmd_repeat == 32'd0 || cmd_repeat > TX_REPEAT_MAX)
                            result <= RC_REJ_LENGTH;
                        else if (cmd_target < raw_tick + MIN_LEAD_TICKS)
                            result <= RC_REJ_STALE;
                        else begin
                            state      <= ST_ARMED;
                            result     <= RC_ARMED;
                            start_tick  <= cmd_target - TX_PIPE;
                            length      <= cmd_length[ADDR_W:0];
                            passes_left <= cmd_repeat[15:0];
                        end
                    end
                end

                ST_ARMED: begin
                    if (doorbell) result <= RC_REJ_BUSY;
                    if (buf_write)
                        enter_fault(RC_FAULT_BUF_WRITE);
                    else if (!time_valid)
                        enter_fault(RC_FAULT_TIME);
                    else if (cancel) begin
                        state  <= ST_IDLE;
                        result <= RC_CANCELLED;
                    end else if (raw_tick == start_tick) begin
                        state     <= ST_RUNNING;
                        result    <= RC_RUNNING;
                        rd_active <= 1'b1;
                        rd_first  <= 1'b1;
                        rd_addr   <= {ADDR_W{1'b0}};
                        rd_left   <= length;
                    end
                end

                ST_RUNNING: begin
                    if (doorbell) result <= RC_REJ_BUSY;
                    if (buf_write)
                        enter_fault(RC_FAULT_BUF_WRITE);
                    else if (!time_valid)
                        enter_fault(RC_FAULT_TIME);
                    else if (o_v && !m_axis_tready)
                        enter_fault(RC_FAULT_TREADY);
                    else if (o_v) begin
                        if (o_f) actual_tick <= raw_tick;
                        if (o_l) begin
                            state  <= ST_DONE;
                            result <= RC_DONE;
                            seq    <= seq + 32'd1;
                        end
                    end
                end

                ST_FAULT: begin
                    if (clear_fault) begin
                        state  <= ST_IDLE;
                        result <= RC_NONE;
                    end
                end

                default: state <= ST_IDLE;
            endcase
        end
    end
endmodule
