`timescale 1ns / 1ps
// P6: fault aggregation.
//
// Each fault_in bit may be a pulse or a level; its rising edge is one fault
// event.  fault_bits are sticky, first_source is the first source since the
// last clear (bit index + 1, 0 = none), and fault_count is a sequence number
// that is never cleared.  Faults clear only on an explicit clear_fault or the
// start of a new time epoch; an event in the same cycle as a clear survives.
module p6_status #(
    parameter integer N = 4
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         clear_fault,
    input  wire         epoch_start,
    input  wire [N-1:0] fault_in,
    output reg  [N-1:0] fault_bits,
    output reg  [3:0]   first_source,
    output reg  [15:0]  fault_count,
    output wire         any_fault
);
    reg [N-1:0] fault_in_d;
    wire [N-1:0] rise = fault_in & ~fault_in_d;

    // Lowest-index rising source, encoded as index + 1.
    integer i;
    reg [3:0] src;
    always @* begin
        src = 4'd0;
        for (i = N - 1; i >= 0; i = i - 1)
            if (rise[i]) src = i + 1;
    end

    assign any_fault = |fault_bits;

    always @(posedge clk) begin
        if (rst) begin
            fault_in_d   <= {N{1'b0}};
            fault_bits   <= {N{1'b0}};
            first_source <= 4'd0;
            fault_count  <= 16'd0;
        end else begin
            fault_in_d <= fault_in;
            if (clear_fault || epoch_start) begin
                fault_bits   <= rise;
                first_source <= src;
            end else begin
                fault_bits <= fault_bits | rise;
                if (first_source == 4'd0)
                    first_source <= src;
            end
            if (|rise)
                fault_count <= fault_count + 16'd1;
        end
    end
endmodule
