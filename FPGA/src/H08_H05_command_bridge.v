`include "src/H05_command_defs.vh"

module H08_H05_command_bridge (
    input wire [`H05_CMD_CONTROL_WIDTH-1:0] cmd_control_value,
    output wire [7:0]  command_type,
    output wire [7:0]  payload_version,
    output wire [31:0] frame_id,
    output wire [15:0] frame_seed,
    output wire [31:0] frame_len,
    output wire [31:0] config_id,
    output wire [63:0] reserved
);

    // Fixed 192-bit payload layout.  This module is intentionally pure
    // combinational logic: H05 owns the timing/latching, while this bridge
    // only exposes the fields consumed by H08 or by a later command adapter.
    //
    //   [191:184] command_type
    //   [183:176] payload_version
    //   [175:144] frame_id
    //   [143:128] frame_seed
    //   [127:096] frame_len
    //   [095:064] config_id
    //   [063:000] reserved
    assign command_type   = cmd_control_value[`H05_CMD_TYPE_MSB:`H05_CMD_TYPE_LSB];
    assign payload_version = cmd_control_value[`H05_CMD_VERSION_MSB:`H05_CMD_VERSION_LSB];
    assign frame_id       = cmd_control_value[`H05_CMD_FRAME_ID_MSB:`H05_CMD_FRAME_ID_LSB];
    assign frame_seed     = cmd_control_value[`H05_CMD_FRAME_SEED_MSB:`H05_CMD_FRAME_SEED_LSB];
    assign frame_len      = cmd_control_value[`H05_CMD_FRAME_LEN_MSB:`H05_CMD_FRAME_LEN_LSB];
    assign config_id      = cmd_control_value[`H05_CMD_CONFIG_ID_MSB:`H05_CMD_CONFIG_ID_LSB];
    assign reserved       = cmd_control_value[`H05_CMD_RESERVED_MSB:`H05_CMD_RESERVED_LSB];

endmodule
