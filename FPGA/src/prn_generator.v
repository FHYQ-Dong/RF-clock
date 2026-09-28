// Pseudo-Random Number Generator
// Algorithm: 16-bit Linear Feedback Shift Register (LFSR)

module prn_generator (
    input clk,
    input rst_n,
    output reg [15:0] prn_out
);
    always @(posedge clk) begin
        if (!rst_n) begin
            prn_out <= 16'h1;
        end else begin
            prn_out <= {
                prn_out[0] ^ prn_out[2] ^ prn_out[3] ^ prn_out[5], 
                prn_out[15:1]
            };
        end
    end
endmodule
