// Shared H05 command/status protocol definitions.
//
// This is a Verilog header, not a synthesizable module. Include it in every
// RTL or testbench file that exchanges H05 return codes. The include guard
// prevents duplicate macro definitions when a file is included indirectly.

`ifndef H05_PROTOCOL_DEFS_VH
`define H05_PROTOCOL_DEFS_VH

// Width of the H05 command status/result field.
//
// The upper nibble identifies the fault owner/source and the lower nibble is
// the source-local cause.  Normal H05 states use source 0.  H08/H09/RFDC
// faults are passed to H05 already encoded by the TX status aggregator.
`define H05_RETURN_CODE_WIDTH          8

`define H05_RETURN_SOURCE_NONE         4'h0
`define H05_RETURN_SOURCE_H05          4'h1
`define H05_RETURN_SOURCE_H08          4'h2
`define H05_RETURN_SOURCE_H09          4'h3
`define H05_RETURN_SOURCE_RFDC         4'h4
`define H05_RETURN_SOURCE_H03          4'h5

`define H05_CAUSE_SUCCESS              4'h0
`define H05_CAUSE_ID_INVALID           4'h1
`define H05_CAUSE_CMD_MISSED           4'h2
`define H05_CAUSE_TIME_INVALID         4'h3
`define H05_CAUSE_PENDING              4'h4
`define H05_CAUSE_IDLE                 4'h5
`define H05_CAUSE_TX_NOT_READY         4'h6
`define H05_CAUSE_TX_FAULT             4'h7
`define H05_CAUSE_TIMEBASE_EXHAUSTED    4'h8

// H05 cmd_return_code values. These values are part of the command protocol
// ABI and must not be changed casually; PS software and other RTL may decode
// the same values.
`define H05_RETURN_CODE_SUCCESS        8'h00
`define H05_RETURN_CODE_ID_INVALID     8'h11
`define H05_RETURN_CODE_CMD_MISSED     8'h12
`define H05_RETURN_CODE_TIME_INVALID   8'h13
`define H05_RETURN_CODE_PENDING        8'h04
`define H05_RETURN_CODE_IDLE           8'h05
`define H05_RETURN_CODE_TX_NOT_READY   8'h16
`define H05_RETURN_CODE_TX_FAULT       8'h17

`endif
