// Shared H08 command/status protocol definitions.
//
// This is a Verilog header, not a synthesizable module. Include it in every
// RTL or testbench file that exchanges H08 return codes. The include guard
// prevents duplicate macro definitions when a file is included indirectly.

`ifndef H08_PROTOCOL_DEFS_VH
`define H08_PROTOCOL_DEFS_VH

// Width of the H08 command status/result field.
`define H08_RETURN_CODE_WIDTH 4

// H08 cmd_return_code values. These values are part of the command protocol
// ABI and must not be changed casually; PS software and other RTL may decode
// the same values.
`define H08_RETURN_CODE_SUCCESS                   4'b0000
`define H08_RETURN_CODE_FRAME_LENGTH_INVALID      4'b0001
`define H08_RETURN_CODE_FRAME_SEED_INVALID        4'b0010
`define H08_RETURN_CODE_IDLE                      4'b0100
`define H08_RETURN_CODE_PENDING                   4'b0101
`define H08_RETURN_CODE_UPSTREAM_VALID_NOT_KEPT   4'b0110
`define H08_RETURN_CODE_DOWNSTREAM_READY_NOT_KEPT 4'b0111

`endif
