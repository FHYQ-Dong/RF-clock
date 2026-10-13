// Shared H09 command/status protocol definitions.
//
// This is a Verilog header, not a synthesizable module. Include it in every
// RTL or testbench file that exchanges H09 return codes. The include guard
// prevents duplicate macro definitions when a file is included indirectly.

`ifndef H09_PROTOCOL_DEFS_VH
`define H09_PROTOCOL_DEFS_VH
// Width of the H09 command status/result field.
`define H09_RETURN_CODE_WIDTH 4

// H09 cmd_return_code values. These values are part of the command protocol
// ABI and must not be changed casually; PS software and other RTL may decode
// the same values.
`define H09_RETURN_CODE_SUCCESS                   4'b0000
`define H09_RETURN_CODE_UPSTREAM_VALID_NOT_KEPT   4'b0001
`define H09_RETURN_CODE_DOWNSTREAM_READY_NOT_KEPT 4'b0010
`define H09_RETURN_CODE_ENABLE_DROPPED            4'b0011

`define H09_RETURN_CODE_UNEXPECTED_ERROR          4'b1111

`endif
