// H02: AXI4-Lite control and status registers
//
// This module is intentionally a small, single-outstanding-transaction
// AXI4-Lite slave.  The AXI clock and the raw_tick/status inputs are assumed
// to be in the same clock domain for this first version.  If the PS AXI clock
// is different from the timebase clock, add an explicit CDC bridge around
// this module instead of sampling raw_tick directly here.

/* Register map (32-bit data, 4-byte aligned)
 *
 * 0x00  DESIGN_ID       read-only, 32'h5246434c ("RFCL")
 * 0x04  ABI_VERSION     read-only
 * 0x08  FEATURES        read-only
 * 0x0c  BUILD_ID        read-only
 * 0x10  STATUS0         read-only: clock/reference/time validity
 * 0x14  STATUS1         read-only: PPS lock and raw-tick exhaustion
 * 0x18  CMD_FIELDS      read-only: written-field bitmap
 * 0x1c  SNAPSHOT_REQ    write bit 0 = capture raw_tick and status atomically
 * 0x20  SNAP_TICK_LO    read-only
 * 0x24  SNAP_TICK_HI    read-only
 * 0x28  SNAP_STATUS0   read-only
 * 0x2c  SNAP_STATUS1   read-only, bit 31 = snapshot_valid
 *
 * 0x40  CMD_ID          shadow command ID, full-word writes only
 * 0x44  TARGET_TICK_LO  shadow target tick low word, full-word writes only
 * 0x48  TARGET_TICK_HI  shadow target tick high word, full-word writes only
 * 0x4c  CONTROL_VALUE   shadow opaque control value, full-word writes only
 * 0x50  CMD_COMMIT      write bit 0 = atomically commit the four shadow fields
 * 0x54  CMD_STATUS      read-only: bit 0 = fields complete, bit 1 = pending,
 *                       bit 2 = H05 ready
 * 0x58  FAULT_CLEAR     write bit 0 = one-clock H05 fault-clear pulse
 *
 * A command commit is accepted only after all four shadow fields have been
 * written with WSTRB=4'b1111.  The committed fields are copied into a
 * pending-command register.  cmd_valid remains asserted until H05 raises
 * cmd_ready; cmd_commit_pulse is the one-clock valid && ready handshake event.
 * This prevents a command from being lost while H05 is busy and keeps the
 * command fields stable even if software rewrites the shadow registers.
 */

module H02_AXI_control_regs #(
    parameter integer AXI_ADDR_WIDTH = 8,
    parameter [31:0] ABI_VERSION = 32'h0001_0000,
    parameter [31:0] FEATURES = 32'h0000_000f,
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

    // H05 command-channel handshake.  These signals are in the AXI clock
    // domain in this prototype; use a CDC bridge if H05 uses another clock.
    input wire                         cmd_ready,

    output reg [31:0]                  cmd_id_shadow,
    output reg [63:0]                  target_tick_shadow,
    output reg [31:0]                  control_value_shadow,
    output wire                        cmd_valid,
    output wire [31:0]                 cmd_id,
    output wire [63:0]                 cmd_target_tick,
    output wire [31:0]                 cmd_control_value,
    output wire                        cmd_commit_pulse,
    output reg                         fault_clear_pulse,
    output wire                        cmd_fields_complete,
    output reg [3:0]                   cmd_written_mask
);

    localparam [1:0] AXI_OKAY   = 2'b00;
    localparam [1:0] AXI_SLVERR = 2'b10;

    // One outstanding write is enough for this control plane.  AW and W are
    // captured independently because AXI-Lite does not require them together.
    reg                            aw_hold;
    reg [AXI_ADDR_WIDTH-1:0]      awaddr_hold;
    reg                            w_hold;
    reg [31:0]                     wdata_hold;
    reg [3:0]                      wstrb_hold;

    // Atomic status snapshot registers.
    reg [63:0] snapshot_tick;
    reg [31:0] snapshot_status0;
    reg [31:0] snapshot_status1;
    reg        snapshot_valid;

    // Stable command snapshot held until the H05 handshake completes.
    reg        cmd_pending;
    reg [31:0] cmd_id_pending;
    reg [63:0] cmd_target_tick_pending;
    reg [31:0] cmd_control_value_pending;

    wire [31:0] status0 = {
        28'b0,
        system_time_valid,
        local_time_valid,
        clock_reference_ok,
        clock_ok
    };

    wire [31:0] status1 = {
        30'b0,
        raw_tick_exhausted,
        pps_locked
    };

    assign s_axi_awready = s_axi_aresetn && !aw_hold && !s_axi_bvalid;
    assign s_axi_wready  = s_axi_aresetn && !w_hold  && !s_axi_bvalid;
    assign s_axi_arready = s_axi_aresetn && !s_axi_rvalid;
    assign cmd_fields_complete = &cmd_written_mask;
    assign cmd_valid = cmd_pending;
    assign cmd_id = cmd_id_pending;
    assign cmd_target_tick = cmd_target_tick_pending;
    assign cmd_control_value = cmd_control_value_pending;
    assign cmd_commit_pulse = cmd_pending && cmd_ready;

    // Byte-strobe merge for a normal 32-bit register.  Command registers are
    // deliberately stricter and are handled below with WSTRB == 4'b1111.
    task automatic merge_word;
        inout [31:0] old_value;
        input [31:0] new_value;
        input [3:0]  byte_enable;
        begin
            if (byte_enable[0]) old_value[7:0]   = new_value[7:0];
            if (byte_enable[1]) old_value[15:8]  = new_value[15:8];
            if (byte_enable[2]) old_value[23:16] = new_value[23:16];
            if (byte_enable[3]) old_value[31:24] = new_value[31:24];
        end
    endtask

    // Write channel and command/shadow register state.
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            aw_hold              <= 1'b0;
            awaddr_hold          <= {AXI_ADDR_WIDTH{1'b0}};
            w_hold               <= 1'b0;
            wdata_hold           <= 32'b0;
            wstrb_hold           <= 4'b0;
            s_axi_bresp          <= AXI_OKAY;
            s_axi_bvalid         <= 1'b0;
            cmd_id_shadow        <= 32'b0;
            target_tick_shadow   <= 64'b0;
            control_value_shadow <= 32'b0;
            cmd_written_mask     <= 4'b0;
            snapshot_tick        <= 64'b0;
            snapshot_status0     <= 32'b0;
            snapshot_status1     <= 32'b0;
            snapshot_valid       <= 1'b0;
            cmd_pending          <= 1'b0;
            cmd_id_pending       <= 32'b0;
            cmd_target_tick_pending <= 64'b0;
            cmd_control_value_pending <= 32'b0;
            fault_clear_pulse    <= 1'b0;
        end else begin
            fault_clear_pulse <= 1'b0;

            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;

            if (cmd_pending && cmd_ready)
                cmd_pending <= 1'b0;

            if (s_axi_awvalid && s_axi_awready) begin
                aw_hold     <= 1'b1;
                awaddr_hold <= s_axi_awaddr;
            end

            if (s_axi_wvalid && s_axi_wready) begin
                w_hold     <= 1'b1;
                wdata_hold <= s_axi_wdata;
                wstrb_hold <= s_axi_wstrb;
            end

            // Execute only after both independent write channels are held.
            if (aw_hold && w_hold && !s_axi_bvalid) begin
                aw_hold      <= 1'b0;
                w_hold       <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= AXI_SLVERR;

                case (awaddr_hold[AXI_ADDR_WIDTH-1:2])
                    6'h07: begin // 0x1c SNAPSHOT_REQ
                        if (wstrb_hold[0]) begin
                            if (wdata_hold[0]) begin
                                snapshot_tick    <= raw_tick;
                                snapshot_status0 <= status0;
                                snapshot_status1 <= status1;
                                snapshot_valid   <= 1'b1;
                            end
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end

                    6'h10: begin // 0x40 CMD_ID
                        if (wstrb_hold == 4'b1111) begin
                            cmd_id_shadow    <= wdata_hold;
                            cmd_written_mask[0] <= 1'b1;
                            s_axi_bresp      <= AXI_OKAY;
                        end
                    end

                    6'h11: begin // 0x44 TARGET_TICK_LO
                        if (wstrb_hold == 4'b1111) begin
                            target_tick_shadow[31:0] <= wdata_hold;
                            cmd_written_mask[1]      <= 1'b1;
                            s_axi_bresp              <= AXI_OKAY;
                        end
                    end

                    6'h12: begin // 0x48 TARGET_TICK_HI
                        if (wstrb_hold == 4'b1111) begin
                            target_tick_shadow[63:32] <= wdata_hold;
                            cmd_written_mask[2]       <= 1'b1;
                            s_axi_bresp               <= AXI_OKAY;
                        end
                    end

                    6'h13: begin // 0x4c CONTROL_VALUE
                        if (wstrb_hold == 4'b1111) begin
                            control_value_shadow <= wdata_hold;
                            cmd_written_mask[3]  <= 1'b1;
                            s_axi_bresp           <= AXI_OKAY;
                        end
                    end

                    6'h14: begin // 0x50 CMD_COMMIT
                        if (wstrb_hold == 4'b1111) begin
                            if (!wdata_hold[0]) begin
                                s_axi_bresp <= AXI_OKAY;
                            end else if (&cmd_written_mask && !cmd_pending) begin
                                cmd_id_pending <= cmd_id_shadow;
                                cmd_target_tick_pending <= target_tick_shadow;
                                cmd_control_value_pending <= control_value_shadow;
                                cmd_pending <= 1'b1;
                                cmd_written_mask <= 4'b0;
                                s_axi_bresp       <= AXI_OKAY;
                            end
                        end
                    end

                    6'h16: begin // 0x58 FAULT_CLEAR
                        if (wstrb_hold == 4'b1111) begin
                            if (wdata_hold[0])
                                fault_clear_pulse <= 1'b1;
                            s_axi_bresp <= AXI_OKAY;
                        end
                    end

                    default: begin
                        // Read-only, unimplemented, and misaligned addresses
                        // all return SLVERR on the write response.
                        s_axi_bresp <= AXI_SLVERR;
                    end
                endcase
            end
        end
    end

    // Read channel.  The response is registered, so RDATA/RRESP remain
    // stable until the master asserts RREADY.
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            s_axi_rdata  <= 32'b0;
            s_axi_rresp  <= AXI_OKAY;
            s_axi_rvalid <= 1'b0;
        end else begin
            if (s_axi_rvalid && s_axi_rready)
                s_axi_rvalid <= 1'b0;

            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rdata  <= 32'b0;
                s_axi_rresp  <= AXI_OKAY;
                s_axi_rvalid <= 1'b1;

                case (s_axi_araddr[AXI_ADDR_WIDTH-1:2])
                    6'h00: s_axi_rdata <= 32'h5246_434c;
                    6'h01: s_axi_rdata <= ABI_VERSION;
                    6'h02: s_axi_rdata <= FEATURES;
                    6'h03: s_axi_rdata <= BUILD_ID;
                    6'h04: s_axi_rdata <= status0;
                    6'h05: s_axi_rdata <= status1;
                    6'h06: s_axi_rdata <= {28'b0, cmd_written_mask};
                    6'h08: s_axi_rdata <= snapshot_tick[31:0];
                    6'h09: s_axi_rdata <= snapshot_tick[63:32];
                    6'h0a: s_axi_rdata <= snapshot_status0;
                    6'h0b: s_axi_rdata <= {snapshot_valid, 31'b0} | snapshot_status1;
                    6'h10: s_axi_rdata <= cmd_id_shadow;
                    6'h11: s_axi_rdata <= target_tick_shadow[31:0];
                    6'h12: s_axi_rdata <= target_tick_shadow[63:32];
                    6'h13: s_axi_rdata <= control_value_shadow;
                    6'h15: s_axi_rdata <= {
                        29'b0,
                        cmd_ready,
                        cmd_pending,
                        cmd_fields_complete
                    };
                    default: begin
                        s_axi_rdata <= 32'b0;
                        s_axi_rresp <= AXI_SLVERR;
                    end
                endcase
            end
        end
    end

endmodule
