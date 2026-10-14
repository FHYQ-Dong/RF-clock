`timescale 1ns / 1ps
// P4: window timing, DMA stream content/TLAST, invalid-window handling
// (FIFO overflow, ADC gap), DMA-not-ready reject, cancel.
//
// Each ADC beat carries the raw_tick of the cycle it is on the bus, so a
// correct capture contains target, target+1, ... in order.
module tb_p4_rx_capture;
    `include "src/p_defs.vh"

    localparam integer DATA_W = 256;

    reg clk = 1'b0;
    always #5 clk = ~clk;          // 100 MHz fabric
    reg dma_clk = 1'b0;
    always #2 dma_clk = ~dma_clk;  // 250 MHz DMA

    reg         rst = 1'b1;
    reg         dma_rst = 1'b1;
    reg  [63:0] raw_tick = 64'd0;
    reg         time_valid = 1'b1;
    always @(posedge clk) raw_tick <= rst ? 64'd0 : raw_tick + 64'd1;

    reg              adc_gap = 1'b0;
    wire [DATA_W-1:0] adc_tdata  = {8{raw_tick[31:0]}};
    wire              adc_tvalid = ~adc_gap;

    reg         doorbell = 0, cancel = 0;
    reg  [63:0] cmd_target = 0;
    reg  [31:0] cmd_length = 0;
    wire [DATA_W-1:0] m_tdata;
    wire        m_tvalid, m_tlast;
    reg         m_tready = 1'b0;
    wire [2:0]  state;
    wire [7:0]  result;
    wire [3:0]  flags;
    wire [31:0] seq, beats, invalid_count;
    wire [63:0] actual_tick;
    wire        dma_ready;

    p4_rx_capture #(
        .DATA_W(DATA_W), .MAX_BEATS(32'd1000), .FIFO_ADDR_W(3), .MIN_LEAD_TICKS(64'd16)
    ) dut (
        .clk(clk), .rst(rst), .raw_tick(raw_tick), .time_valid(time_valid),
        .doorbell(doorbell), .cancel(cancel),
        .cmd_target(cmd_target), .cmd_length(cmd_length),
        .s_axis_adc_tdata(adc_tdata), .s_axis_adc_tvalid(adc_tvalid),
        .adc_overrange(1'b0),
        .dma_clk(dma_clk), .dma_rst(dma_rst),
        .m_axis_tdata(m_tdata), .m_axis_tvalid(m_tvalid),
        .m_axis_tready(m_tready), .m_axis_tlast(m_tlast),
        .state(state), .result(result), .flags(flags), .seq(seq),
        .beats(beats), .actual_tick(actual_tick),
        .invalid_count(invalid_count), .dma_ready(dma_ready)
    );

    // DMA sink: record every accepted beat.
    reg [31:0] rx_word [0:1023];
    reg        rx_last [0:1023];
    integer    rx_n = 0;
    always @(posedge dma_clk) begin
        if (m_tvalid && m_tready) begin
            rx_word[rx_n] <= m_tdata[31:0];
            rx_last[rx_n] <= m_tlast;
            rx_n <= rx_n + 1;
        end
    end

    initial begin
        #100_000;
        $display("FAIL tb_p4_rx_capture (timeout)");
        $finish;
    end

    integer errors = 0;
    task check(input cond, input [8*64-1:0] msg);
        if (!cond) begin
            errors = errors + 1;
            $display("FAIL: %0s (t=%0t)", msg, $time);
        end
    endtask

    task submit(input [63:0] target, input [31:0] len);
        begin
            @(negedge clk);
            cmd_target = target; cmd_length = len; doorbell = 1;
            @(negedge clk);
            doorbell = 0;
        end
    endtask

    task wait_done;
        integer n;
        begin
            n = 0;
            while (state != ST_DONE && n < 2000) begin @(negedge clk); n = n + 1; end
            check(state == ST_DONE, "window reaches DONE");
        end
    endtask

    integer i, start_n, lasts;
    reg [63:0] tgt;
    initial begin
        repeat (4) @(posedge clk);
        rst = 0; dma_rst = 0;
        repeat (4) @(negedge clk);

        // DMA not ready -> reject.
        submit(raw_tick + 50, 10);
        check(result == RC_REJ_DMA_NOT_READY && state == ST_IDLE, "dma not ready rejected");

        m_tready = 1;
        repeat (4) @(negedge clk);
        check(dma_ready, "dma_ready synchronized");

        // Normal window: 20 beats, contents = target .. target+19.
        start_n = rx_n;
        tgt = raw_tick + 40;
        submit(tgt, 20);
        check(state == ST_ARMED, "armed");
        wait_done;
        check(result == RC_DONE && flags == 0, "valid window");
        check(actual_tick == tgt && beats == 20 && seq == 1, "timestamp and count");
        check(rx_n - start_n == 20, "20 beats reached the DMA");
        lasts = 0;
        for (i = 0; i < 20; i = i + 1) begin
            check(rx_word[start_n + i] == tgt[31:0] + i, "beat i carries tick target+i");
            if (rx_last[start_n + i]) lasts = lasts + 1;
        end
        check(lasts == 1 && rx_last[start_n + 19], "single TLAST on last beat");

        // Stale and length rejects.
        submit(raw_tick + 3, 4);
        check(result == RC_REJ_STALE, "stale rejected");
        submit(raw_tick + 100, 1001);
        check(result == RC_REJ_LENGTH, "over-max rejected");

        // Cancel from ARMED.
        submit(raw_tick + 100, 4);
        @(negedge clk); cancel = 1; @(negedge clk); cancel = 0;
        check(state == ST_IDLE && result == RC_CANCELLED, "cancel");

        // Overflow: DMA stalls inside a 30-beat window with an 8-entry FIFO.
        start_n = rx_n;
        tgt = raw_tick + 40;
        submit(tgt, 30);
        while (raw_tick != tgt + 2) @(negedge clk);
        m_tready = 0;
        while (raw_tick != tgt + 40) @(negedge clk);
        m_tready = 1;
        wait_done;
        check(result == RC_RX_INVALID && flags[RXF_OVERFLOW], "overflow invalidates window");
        check(invalid_count == 1 && seq == 2, "invalid counted, seq advances");
        lasts = 0;
        for (i = start_n; i < rx_n; i = i + 1) if (rx_last[i]) lasts = lasts + 1;
        check(lasts == 1 && rx_last[rx_n - 1], "overflowed window still ends with one TLAST");
        check(rx_n - start_n == beats, "beats register matches DMA beats");

        // Next window after an invalid one is clean again.
        start_n = rx_n;
        tgt = raw_tick + 40;
        submit(tgt, 6);
        wait_done;
        check(result == RC_DONE && flags == 0 && rx_n - start_n == 6, "recovers after overflow");
        check(rx_word[start_n] == tgt[31:0], "first beat aligned after overflow");

        // ADC tvalid gap inside the window, on the last beat.
        tgt = raw_tick + 40;
        submit(tgt, 6);
        while (raw_tick != tgt + 5) @(negedge clk);
        adc_gap = 1;
        @(negedge clk);
        adc_gap = 0;
        wait_done;
        check(result == RC_RX_INVALID && flags[RXF_GAP], "gap invalidates window");
        check(rx_last[rx_n - 1] && rx_word[rx_n - 1] == 0, "tail beat carries TLAST");
        check(invalid_count == 2, "second invalid window counted");

        if (errors == 0) $display("PASS tb_p4_rx_capture");
        else             $display("FAIL tb_p4_rx_capture (%0d errors)", errors);
        $finish;
    end
endmodule
