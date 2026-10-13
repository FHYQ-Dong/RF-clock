// H02: AXI4-Lite control and status registers
//
// This module is a small, single-outstanding-transaction AXI4-Lite slave.
// The AXI clock, H05 clock, H10 status signals, and raw_tick are assumed to
// be in one clock domain in this prototype. If that assumption is not true
// in the final design, put an explicit CDC/mailbox bridge around H02.
//
// The command payload is opaque to H02. Its current 192-bit ABI is:
//   [191:184] command_type, [183:176] payload_version,
//   [175:144] frame_id, [143:128] frame_seed,
//   [127:096] frame_len, [095:064] config_id,
//   [063:000] reserved.
// H02 only assembles and transports these six words; H08/H09-facing logic
// validates and interprets the fields.

/* Register map (32-bit data, 4-byte aligned)
 *
 * 0x00 DESIGN_ID       read-only, 32'h5246434c ("RFCL")
 * 0x04 ABI_VERSION     read-only
 * 0x08 FEATURES        read-only
 * 0x0c BUILD_ID        read-only
 * 0x10 STATUS0         clock/reference/time validity
 * 0x14 STATUS1         PPS lock and raw-tick exhaustion
 * 0x18 CMD_FIELDS      written-field bitmap, bits [8:0]
 * 0x1c SNAPSHOT_REQ    write bit 0 = capture raw_tick and status
 * 0x20 SNAP_TICK_LO    read-only
 * 0x24 SNAP_TICK_HI    read-only
 * 0x28 SNAP_STATUS0    read-only
 * 0x2c SNAP_STATUS1    read-only, bit 31 = snapshot_valid
 *
 * 0x30 TX_STATUS0      TX done/ready/active/fault and primary return code
 *                    [31:12]=0, [11]=fault, [10]=active, [9]=admission,
 *                    [8]=done, [7:0]=return code
 * 0x34 H03_STATUS      H03 live/sticky exhausted status
 *                    [31:2]=0, [1]=sticky exhausted, [0]=live exhausted
 *                    H03's source/cause encoding is reported in TX_FAULT.
 * 0x38 H08_STATUS      H08 live status and return code
 *                    [31:12]=0, [11]=fault, [10]=active, [9]=idle,
 *                    [8]=ready, [7:4]=0, [3:0]=return code
 * 0x3c H09_STATUS      H09 live status and return code
 *                    [31:7]=0, [6]=fault, [5]=active, [4]=ready,
 *                    [3:0]=return code
 *
 * 0x40 CMD_ID          shadow command ID
 * 0x44 TARGET_TICK_LO  shadow target tick low word
 * 0x48 TARGET_TICK_HI  shadow target tick high word
 * 0x4c CONTROL_WORD0   cmd_control_value[31:0]
 * 0x50 CONTROL_WORD1   cmd_control_value[63:32]
 * 0x54 CONTROL_WORD2   cmd_control_value[95:64]
 * 0x58 CONTROL_WORD3   cmd_control_value[127:96]
 * 0x5c CONTROL_WORD4   cmd_control_value[159:128]
 * 0x60 CONTROL_WORD5   cmd_control_value[191:160]
 * 0x64 CMD_COMMIT      write bit 0 = atomically commit all fields
 * 0x68 CMD_STATUS      [6]=TX fault, [5]=TX active, [4]=TX done,
 *                    [3]=TX admission, [2]=cmd_ready, [1]=pending,
 *                    [0]=fields complete
 * 0x6c FAULT_CLEAR     write bit 0 = one-clock H05/H10 clear pulse
 * 0x70 H05_STATUS      [6]=fault, [5]=wait, [4]=idle, [3]=pending,
 *                    [2]=fields complete, [1]=cmd_ready
 * 0x74 H05_RETURN     H05's 8-bit latched return code in [7:0]
 *
 * 0x80 H08_FAULT       sticky H08 status and return code
 * 0x84 H09_FAULT       sticky H09 status and return code
 * 0x88 RFDC_FAULT      sticky RFDC status and return code
 * 0x8c TX_FAULT        [8]=sticky aggregate fault, [7:0]=source/cause code
 * 0x90 RFDC_STATUS     RFDC live ready/active/fault and return code
 *
 * All command words require WSTRB=4'b1111. CMD_COMMIT is accepted only
 * after all nine field bits are set and no previous command is pending.
 * H02's transport event is cmd_valid && cmd_ready. H05 may still reject the
 * command contents (for example an old ID or a missed target) while remaining
 * in IDLE; software observes that validation result through H05_RETURN and
 * the H05 return-code register rather than through a second AXI handshake.
 */

`include "src/H05_command_defs.vh"

module H02_AXI_control_regs #(
    parameter integer AXI_ADDR_WIDTH = 8,
    parameter [31:0] ABI_VERSION = 32'h0001_0001,
    parameter [31:0] FEATURES = 32'h0000_001f,
    parameter [31:0] BUILD_ID = 32'h0000_0001
)(
    input wire                         s_axi_aclk,
    input wire                         s_axi_aresetn,
    input wire [AXI_ADDR_WIDTH-1:0]    s_axi_awaddr,
    input wire                         s_axi_awvalid,
    output wire                        s_axi_awready,
    input wire [31:0]                  s_axi_wdata,
    input wire [3:0]                   s_axi_wstrb,
    input wire                         s_axi_wvalid,
    output wire                        s_axi_wready,
    output reg [1:0]                   s_axi_bresp,
    output reg                         s_axi_bvalid,
    input wire                         s_axi_bready,
    input wire [AXI_ADDR_WIDTH-1:0]    s_axi_araddr,
    input wire                         s_axi_arvalid,
    output wire                        s_axi_arready,
    output reg [31:0]                  s_axi_rdata,
    output reg [1:0]                   s_axi_rresp,
    output reg                         s_axi_rvalid,
    input wire                         s_axi_rready,

    input wire                         clock_ok,
    input wire                         clock_reference_ok,
    input wire                         local_time_valid,
    input wire                         system_time_valid,
    input wire                         pps_locked,
    input wire                         raw_tick_exhausted,
    input wire [63:0]                  raw_tick,

    // H05 command/status interface (same clock domain in this prototype).
    input wire                         cmd_ready,
    input wire                         h05_cmd_idle,
    input wire                         h05_cmd_wait,
    input wire                         h05_cmd_fault,
    input wire [7:0]                   h05_cmd_return_code,

    // H10 TX live/sticky status, synchronized to this clock.
    input wire                         tx_admission_ready,
    input wire                         tx_active,
    input wire                         tx_done,
    input wire                         tx_fault,
    input wire [7:0]                   tx_return_code,
    input wire                         h03_exhausted_status,
    input wire                         h08_ready_status,
    input wire                         h08_idle_status,
    input wire                         h08_active_status,
    input wire                         h08_fault_status,
    input wire [3:0]                   h08_return_code_status,
    input wire                         h09_ready_status,
    input wire                         h09_active_status,
    input wire                         h09_fault_status,
    input wire [3:0]                   h09_return_code_status,
    input wire                         rfdc_ready_status,
    input wire                         rfdc_active_status,
    input wire                         rfdc_fault_status,
    input wire [3:0]                   rfdc_return_code_status,
    input wire                         h10_tx_fault_latched,
    input wire                         h03_exhausted_latched,
    input wire                         h08_fault_latched,
    input wire [3:0]                   h08_return_code_latched,
    input wire                         h09_fault_latched,
    input wire [3:0]                   h09_return_code_latched,
    input wire                         rfdc_fault_latched,
    input wire [3:0]                   rfdc_return_code_latched,

    output reg [31:0]                  cmd_id_shadow,
    output reg [63:0]                  target_tick_shadow,
    output reg [`H05_CMD_CONTROL_WIDTH-1:0] control_value_shadow,
    output wire                        cmd_valid,
    output wire [31:0]                 cmd_id,
    output wire [63:0]                 cmd_target_tick,
    output wire [`H05_CMD_CONTROL_WIDTH-1:0] cmd_control_value,
    output wire                        cmd_commit_pulse,
    output reg                         fault_clear_pulse,
    output wire                        cmd_fields_complete,
    output reg [8:0]                   cmd_written_mask
);

    localparam [1:0] AXI_OKAY = 2'b00;
    localparam [1:0] AXI_SLVERR = 2'b10;
    reg aw_hold;
    reg [AXI_ADDR_WIDTH-1:0] awaddr_hold;
    reg w_hold;
    reg [31:0] wdata_hold;
    reg [3:0] wstrb_hold;
    reg [63:0] snapshot_tick;
    reg [31:0] snapshot_status0;
    reg [31:0] snapshot_status1;
    reg snapshot_valid;
    reg cmd_pending;
    reg [31:0] cmd_id_pending;
    reg [63:0] cmd_target_tick_pending;
    reg [`H05_CMD_CONTROL_WIDTH-1:0] cmd_control_value_pending;

    wire [31:0] status0 = {28'b0, system_time_valid, local_time_valid,
                           clock_reference_ok, clock_ok};
    wire [31:0] status1 = {30'b0, raw_tick_exhausted, pps_locked};
    wire [31:0] tx_status0 = {20'b0, tx_fault, tx_active,
                              tx_admission_ready, tx_done, tx_return_code};
    wire [31:0] h03_status = {30'b0, h03_exhausted_latched,
                              h03_exhausted_status};
    wire [31:0] h08_status = {20'b0, h08_fault_status, h08_active_status,
                              h08_idle_status, h08_ready_status, 4'b0,
                              h08_return_code_status};
    wire [31:0] h09_status = {25'b0, h09_fault_status, h09_active_status,
                              h09_ready_status, h09_return_code_status};
    wire [31:0] rfdc_status = {25'b0, rfdc_fault_status, rfdc_active_status,
                               rfdc_ready_status, rfdc_return_code_status};
    wire [31:0] h05_status = {25'b0, h05_cmd_fault, h05_cmd_wait,
                              h05_cmd_idle, cmd_pending,
                              cmd_fields_complete, cmd_ready, 1'b0};
    wire [31:0] cmd_status = {25'b0, tx_fault, tx_active, tx_done,
                              tx_admission_ready, cmd_ready,
                              cmd_pending, cmd_fields_complete};

    assign s_axi_awready = s_axi_aresetn && !aw_hold && !s_axi_bvalid;
    assign s_axi_wready = s_axi_aresetn && !w_hold && !s_axi_bvalid;
    assign s_axi_arready = s_axi_aresetn && !s_axi_rvalid;
    assign cmd_fields_complete = &cmd_written_mask;
    assign cmd_valid = cmd_pending;
    assign cmd_id = cmd_id_pending;
    assign cmd_target_tick = cmd_target_tick_pending;
    assign cmd_control_value = cmd_control_value_pending;
    assign cmd_commit_pulse = cmd_pending && cmd_ready;

    // AXI write channel, command shadow words, and one-entry H05 mailbox.
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            aw_hold <= 1'b0;
            awaddr_hold <= {AXI_ADDR_WIDTH{1'b0}};
            w_hold <= 1'b0;
            wdata_hold <= 32'b0;
            wstrb_hold <= 4'b0;
            s_axi_bresp <= AXI_OKAY;
            s_axi_bvalid <= 1'b0;
            cmd_id_shadow <= 32'b0;
            target_tick_shadow <= 64'b0;
            control_value_shadow <= {`H05_CMD_CONTROL_WIDTH{1'b0}};
            cmd_written_mask <= 9'b0;
            snapshot_tick <= 64'b0;
            snapshot_status0 <= 32'b0;
            snapshot_status1 <= 32'b0;
            snapshot_valid <= 1'b0;
            cmd_pending <= 1'b0;
            cmd_id_pending <= 32'b0;
            cmd_target_tick_pending <= 64'b0;
            cmd_control_value_pending <= {`H05_CMD_CONTROL_WIDTH{1'b0}};
            fault_clear_pulse <= 1'b0;
        end else begin
            fault_clear_pulse <= 1'b0;
            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;
            if (cmd_pending && cmd_ready)
                cmd_pending <= 1'b0;
            if (s_axi_awvalid && s_axi_awready) begin
                aw_hold <= 1'b1;
                awaddr_hold <= s_axi_awaddr;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_hold <= 1'b1;
                wdata_hold <= s_axi_wdata;
                wstrb_hold <= s_axi_wstrb;
            end
            if (aw_hold && w_hold && !s_axi_bvalid) begin
                aw_hold <= 1'b0;
                w_hold <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp <= AXI_SLVERR;
                case (awaddr_hold[AXI_ADDR_WIDTH-1:2])
                    6'h07: begin // 0x1c SNAPSHOT_REQ
                        if (wstrb_hold[0]) begin
                            if (wdata_hold[0]) begin
                                snapshot_tick <= raw_tick;
                                snapshot_status0 <= status0;
                                snapshot_status1 <= status1;
                                snapshot_valid <= 1'b1;
                            end
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h10: begin // 0x40 CMD_ID
                        if (wstrb_hold == 4'b1111) begin
                            cmd_id_shadow <= wdata_hold;
                            cmd_written_mask[0] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h11: begin // 0x44 TARGET_TICK_LO
                        if (wstrb_hold == 4'b1111) begin
                            target_tick_shadow[31:0] <= wdata_hold;
                            cmd_written_mask[1] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h12: begin // 0x48 TARGET_TICK_HI
                        if (wstrb_hold == 4'b1111) begin
                            target_tick_shadow[63:32] <= wdata_hold;
                            cmd_written_mask[2] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h13: begin // 0x4c CONTROL_WORD0
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow[31:0] <= wdata_hold;
                            cmd_written_mask[3] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h14: begin // 0x50 CONTROL_WORD1
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow[63:32] <= wdata_hold;
                            cmd_written_mask[4] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h15: begin // 0x54 CONTROL_WORD2
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow[95:64] <= wdata_hold;
                            cmd_written_mask[5] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h16: begin // 0x58 CONTROL_WORD3
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow[127:96] <= wdata_hold;
                            cmd_written_mask[6] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h17: begin // 0x5c CONTROL_WORD4
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow[159:128] <= wdata_hold;
                            cmd_written_mask[7] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h18: begin // 0x60 CONTROL_WORD5
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow[191:160] <= wdata_hold;
                            cmd_written_mask[8] <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    6'h19: begin // 0x64 CMD_COMMIT
                        if (wstrb_hold == 4'b1111) begin
                            if (!wdata_hold[0]) begin
                                s_axi_bresp <= AXI_OKAY;
                            end else if (&cmd_written_mask && !cmd_pending) begin
                                cmd_id_pending <= cmd_id_shadow;
                                cmd_target_tick_pending <= target_tick_shadow;
                                cmd_control_value_pending <= control_value_shadow;
                                cmd_pending <= 1'b1;
                                cmd_written_mask <= 9'b0;
                                s_axi_bresp <= AXI_OKAY;
                            end
                        end
                    end
                    6'h1b: begin // 0x6c FAULT_CLEAR
                        if (wstrb_hold == 4'b1111) begin
                            if (wdata_hold[0])
                                fault_clear_pulse <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end
                    default: begin
                        s_axi_bresp <= AXI_SLVERR;
                    end
                endcase
            end
        end
    end

    // AXI read channel. RDATA/RRESP remain stable until RREADY.
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            s_axi_rdata <= 32'b0;
            s_axi_rresp <= AXI_OKAY;
            s_axi_rvalid <= 1'b0;
        end else begin
            if (s_axi_rvalid && s_axi_rready)
                s_axi_rvalid <= 1'b0;
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rdata <= 32'b0;
                s_axi_rresp <= AXI_OKAY;
                s_axi_rvalid <= 1'b1;
                case (s_axi_araddr[AXI_ADDR_WIDTH-1:2])
                    6'h00: s_axi_rdata <= 32'h5246_434c;
                    6'h01: s_axi_rdata <= ABI_VERSION;
                    6'h02: s_axi_rdata <= FEATURES;
                    6'h03: s_axi_rdata <= BUILD_ID;
                    6'h04: s_axi_rdata <= status0;
                    6'h05: s_axi_rdata <= status1;
                    6'h06: s_axi_rdata <= {23'b0, cmd_written_mask};
                    6'h08: s_axi_rdata <= snapshot_tick[31:0];
                    6'h09: s_axi_rdata <= snapshot_tick[63:32];
                    6'h0a: s_axi_rdata <= snapshot_status0;
                    6'h0b: s_axi_rdata <= {snapshot_valid, 31'b0} | snapshot_status1;
                    6'h0c: s_axi_rdata <= tx_status0;
                    6'h0d: s_axi_rdata <= h03_status;
                    6'h0e: s_axi_rdata <= h08_status;
                    6'h0f: s_axi_rdata <= h09_status;
                    6'h10: s_axi_rdata <= cmd_id_shadow;
                    6'h11: s_axi_rdata <= target_tick_shadow[31:0];
                    6'h12: s_axi_rdata <= target_tick_shadow[63:32];
                    6'h13: s_axi_rdata <= control_value_shadow[31:0];
                    6'h14: s_axi_rdata <= control_value_shadow[63:32];
                    6'h15: s_axi_rdata <= control_value_shadow[95:64];
                    6'h16: s_axi_rdata <= control_value_shadow[127:96];
                    6'h17: s_axi_rdata <= control_value_shadow[159:128];
                    6'h18: s_axi_rdata <= control_value_shadow[191:160];
                    6'h1a: s_axi_rdata <= cmd_status;
                    6'h1c: s_axi_rdata <= h05_status;
                    6'h1d: s_axi_rdata <= {24'b0, h05_cmd_return_code};
                    6'h20: s_axi_rdata <= {24'b0, h08_fault_latched,
                                             h08_return_code_latched};
                    6'h21: s_axi_rdata <= {24'b0, h09_fault_latched,
                                             h09_return_code_latched};
                    6'h22: s_axi_rdata <= {24'b0, rfdc_fault_latched,
                                             rfdc_return_code_latched};
                    6'h23: s_axi_rdata <= {23'b0, h10_tx_fault_latched,
                                             tx_return_code};
                    6'h24: s_axi_rdata <= rfdc_status;
                    default: begin
                        s_axi_rdata <= 32'b0;
                        s_axi_rresp <= AXI_SLVERR;
                    end
                endcase
            end
        end
    end
endmodule
