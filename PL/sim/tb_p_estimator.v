`timescale 1ns / 1ps
// p_estimator against rf_clock.plmodel.correlator (vectors from gen_vectors.py):
// magnitude, fraction, phase and power of every case to the bit.
module tb_p_estimator;
    localparam integer W = 47;
    localparam integer F = 11;   // lines per vector
    localparam integer MAXV = 112;  // the vectors gen_vectors.py writes

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;

    reg [127:0] vec [0:MAXV*F-1];
    reg [31:0]  count_mem [0:0];

    reg                 start = 1'b0;
    reg                 edge_in = 1'b0;
    reg signed [W-1:0]  l_i, l_q, c_i, c_q, r_i, r_q;
    wire                busy, done;
    wire [W-1:0]        magnitude;
    wire signed [31:0]  fraction, phase;
    wire [2*W-1:0]      power;

    p_estimator #(.W(W)) dut (
        .clk (clk), .rst (rst), .start (start), .edge_in (edge_in),
        .l_i (l_i), .l_q (l_q), .c_i (c_i), .c_q (c_q), .r_i (r_i), .r_q (r_q),
        .busy (busy), .done (done), .magnitude (magnitude), .fraction (fraction),
        .phase (phase), .power (power)
    );

    integer n, b, errors, cycles, max_cycles;
    initial begin
        $readmemh("est_vectors.hex", vec);
        $readmemh("est_count.hex", count_mem);
        errors = 0;
        max_cycles = 0;
        if (count_mem[0] != MAXV) begin
            $display("FAIL tb_p_estimator: %0d vectors, expected %0d", count_mem[0], MAXV);
            $finish;
        end
        repeat (4) @(posedge clk);
        rst <= 1'b0;
        @(posedge clk);
        for (n = 0; n < count_mem[0]; n = n + 1) begin
            b = n * F;
            l_i <= vec[b+0][W-1:0]; l_q <= vec[b+1][W-1:0];
            c_i <= vec[b+2][W-1:0]; c_q <= vec[b+3][W-1:0];
            r_i <= vec[b+4][W-1:0]; r_q <= vec[b+5][W-1:0];
            edge_in <= vec[b+6][0];
            start <= 1'b1;
            @(posedge clk);
            start <= 1'b0;
            cycles = 0;
            while (!done) begin
                @(posedge clk);
                cycles = cycles + 1;
                if (cycles > 1000) begin
                    $display("FAIL tb_p_estimator: vector %0d timed out", n);
                    $finish;
                end
            end
            if (cycles > max_cycles) max_cycles = cycles;
            if (magnitude !== vec[b+7][W-1:0] || fraction !== vec[b+8][31:0] ||
                phase !== vec[b+9][31:0] || power !== vec[b+10][2*W-1:0]) begin
                errors = errors + 1;
                $display("vector %0d: mag %h/%h frac %h/%h phase %h/%h power %h/%h", n,
                         magnitude, vec[b+7][W-1:0], fraction, vec[b+8][31:0],
                         phase, vec[b+9][31:0], power, vec[b+10][2*W-1:0]);
            end
            @(posedge clk);
        end
        if (errors == 0 && count_mem[0] > 0)
            $display("PASS tb_p_estimator (%0d vectors, %0d clocks each at most)", count_mem[0], max_cycles);
        else
            $display("FAIL tb_p_estimator: %0d of %0d vectors differ", errors, count_mem[0]);
        $finish;
    end
endmodule
