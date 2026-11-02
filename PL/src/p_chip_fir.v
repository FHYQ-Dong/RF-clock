`timescale 1ns / 1ps
// The RRC FIR on a chip stream, one beat (8 samples) per chip
// (rf_clock.plmodel.fir.shape): with c[j] the chip j chips before the newest,
//
//   sample p of the beat = sum over j = 0 .. 10 of c[j] * tap[p + 8 j]   (p + 8 j <= 80)
//
// Chips are +1, -1 or 0 (`nz` = 0), so every term is +tap, -tap or 0 and the
// sum is exact in 16-bit two's complement whenever the true sum fits 16 bits
// (the ARM chooses such taps).  `en` shifts a chip in; `beat` is the beat of
// the chips shifted in up to LATENCY clocks earlier (5 register stages after
// the chip register).  With `en` low the chips hold and the beat repeats.
module p_chip_fir #(
    parameter integer TAPS = 81,
    parameter integer SPS  = 8
) (
    input  wire               clk,
    input  wire               rst,
    input  wire               en,
    input  wire               chip_nz,
    input  wire               chip_neg,
    input  wire [TAPS*16-1:0] taps,      // tap k in bits [16k +: 16]
    output wire [SPS*16-1:0]  beat       // sample p in bits [16p +: 16]
);
    localparam integer NC = (TAPS + SPS - 1) / SPS;  // chips in the filter: 11
    localparam integer NT = 12;                      // terms per sample, padded

    reg [NC-1:0] c_nz, c_neg;   // [0] the newest chip
    always @(posedge clk) begin
        if (rst) begin
            c_nz  <= {NC{1'b0}};
            c_neg <= {NC{1'b0}};
        end else if (en) begin
            c_nz  <= {c_nz[NC-2:0], chip_nz};
            c_neg <= {c_neg[NC-2:0], chip_neg};
        end
    end

    genvar p;
    generate
        for (p = 0; p < SPS; p = p + 1) begin : g_phase
            reg [15:0] term [0:NT-1];
            reg [15:0] s1 [0:5];
            reg [15:0] s2 [0:2];
            reg [15:0] s3a, s3b;
            reg [15:0] s4;
            integer j;
            always @(posedge clk) begin
                for (j = 0; j < NT; j = j + 1) begin
                    if (j < NC && p + SPS * j < TAPS && c_nz[j])
                        term[j] <= c_neg[j] ? -taps[16*(p + SPS*j) +: 16] : taps[16*(p + SPS*j) +: 16];
                    else
                        term[j] <= 16'd0;
                end
                for (j = 0; j < 6; j = j + 1) s1[j] <= term[2*j] + term[2*j+1];
                for (j = 0; j < 3; j = j + 1) s2[j] <= s1[2*j] + s1[2*j+1];
                s3a <= s2[0] + s2[1];
                s3b <= s2[2];
                s4  <= s3a + s3b;
            end
            assign beat[16*p +: 16] = s4;
        end
    endgenerate
endmodule
