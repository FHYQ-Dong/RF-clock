// Shared 192-bit H05 command payload layout.
//
// H02 treats this value as opaque and assembles it from six AXI-Lite words.
// H05 schedules/latches it without interpreting the fields.  A downstream
// bridge such as H08_H05_command_bridge decodes the fields at the execution
// boundary.

`ifndef H05_COMMAND_DEFS_VH
`define H05_COMMAND_DEFS_VH

`define H05_CMD_CONTROL_WIDTH 192

// Command types currently understood by the H08 PRN path.  A future H08/H09
// or RFDC command dispatcher can add new values without changing the AXI
// register layout.
`define H05_CMD_TYPE_PRN_FRAME       8'h01
`define H05_CMD_PAYLOAD_VERSION_1    8'h01

// Big-endian bit layout inside the opaque control value.
`define H05_CMD_TYPE_MSB       191
`define H05_CMD_TYPE_LSB       184
`define H05_CMD_VERSION_MSB    183
`define H05_CMD_VERSION_LSB    176
`define H05_CMD_FRAME_ID_MSB   175
`define H05_CMD_FRAME_ID_LSB   144
`define H05_CMD_FRAME_SEED_MSB 143
`define H05_CMD_FRAME_SEED_LSB 128
`define H05_CMD_FRAME_LEN_MSB  127
`define H05_CMD_FRAME_LEN_LSB   96
`define H05_CMD_CONFIG_ID_MSB   95
`define H05_CMD_CONFIG_ID_LSB   64
`define H05_CMD_RESERVED_MSB    63
`define H05_CMD_RESERVED_LSB     0

// Current command contract used by H08:
//   command_type = H05_CMD_TYPE_PRN_FRAME
//   payload_version = H05_CMD_PAYLOAD_VERSION_1
//   frame_seed != 0 and frame_len != 0
//   reserved[63:0] = 0 for this version
// H05 transports these fields; the H08-facing adapter/dispatcher owns the
// semantic validation before a future command type is enabled.

// AXI-Lite word order: CONTROL_WORD0 is the least-significant word.
`define H05_CMD_WORD_COUNT 6
`define H05_CMD_FIELD_COUNT 9

`endif
