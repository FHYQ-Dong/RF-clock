`timescale 1ns/1ps

module tb_H02_AXI_control_regs;
    reg clk;
    reg aresetn;

    reg [7:0] awaddr;
    reg       awvalid;
    wire      awready;
    reg [31:0] wdata;
    reg [3:0]  wstrb;
    reg        wvalid;
    wire       wready;
    wire [1:0] bresp;
    wire       bvalid;
    reg        bready;

    reg [7:0] araddr;
    reg       arvalid;
    wire      arready;
    wire [31:0] rdata;
    wire [1:0]  rresp;
    wire        rvalid;
    reg         rready;

    reg clock_ok;
    reg clock_reference_ok;
    reg local_time_valid;
    reg system_time_valid;
    reg pps_locked;
    reg raw_tick_exhausted;
    reg [63:0] raw_tick;
    reg cmd_ready;

    wire [31:0] cmd_id_shadow;
    wire [63:0] target_tick_shadow;
    wire [31:0] control_value_shadow;
    wire        cmd_valid;
    wire [31:0] cmd_id;
    wire [63:0] cmd_target_tick;
    wire [31:0] cmd_control_value;
    wire        cmd_commit_pulse;
    wire        fault_clear_pulse;
    wire        cmd_fields_complete;
    wire [3:0]  cmd_written_mask;

    integer failures;
    integer commit_count;
    integer fault_clear_count;

    H02_AXI_control_regs #(
        .AXI_ADDR_WIDTH(8),
        .ABI_VERSION(32'h0002_0001),
        .FEATURES(32'h0000_00a5),
        .BUILD_ID(32'h1234_5678)
    ) dut (
        .s_axi_aclk(clk),
        .s_axi_aresetn(aresetn),
        .s_axi_awaddr(awaddr),
        .s_axi_awvalid(awvalid),
        .s_axi_awready(awready),
        .s_axi_wdata(wdata),
        .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid),
        .s_axi_wready(wready),
        .s_axi_bresp(bresp),
        .s_axi_bvalid(bvalid),
        .s_axi_bready(bready),
        .s_axi_araddr(araddr),
        .s_axi_arvalid(arvalid),
        .s_axi_arready(arready),
        .s_axi_rdata(rdata),
        .s_axi_rresp(rresp),
        .s_axi_rvalid(rvalid),
        .s_axi_rready(rready),
        .clock_ok(clock_ok),
        .clock_reference_ok(clock_reference_ok),
        .local_time_valid(local_time_valid),
        .system_time_valid(system_time_valid),
        .pps_locked(pps_locked),
        .raw_tick_exhausted(raw_tick_exhausted),
        .raw_tick(raw_tick),
        .cmd_ready(cmd_ready),
        .cmd_id_shadow(cmd_id_shadow),
        .target_tick_shadow(target_tick_shadow),
        .control_value_shadow(control_value_shadow),
        .cmd_valid(cmd_valid),
        .cmd_id(cmd_id),
        .cmd_target_tick(cmd_target_tick),
        .cmd_control_value(cmd_control_value),
        .cmd_commit_pulse(cmd_commit_pulse),
        .fault_clear_pulse(fault_clear_pulse),
        .cmd_fields_complete(cmd_fields_complete),
        .cmd_written_mask(cmd_written_mask)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (!aresetn)
            raw_tick <= 64'd0;
        else
            raw_tick <= raw_tick + 64'd1;
        if (cmd_commit_pulse)
            commit_count <= commit_count + 1;
        if (fault_clear_pulse)
            fault_clear_count <= fault_clear_count + 1;
    end

    task check;
        input condition;
        input [8*120-1:0] message;
        begin
            if (!condition) begin
                $display("FAIL @%0t: %0s", $time, message);
                failures = failures + 1;
            end
        end
    endtask

    task axi_write;
        input [7:0]  address;
        input [31:0] data;
        input [3:0]  strobes;
        output [1:0] response;
        begin
            // Send AW first, then W two cycles later to verify independent
            // address/data channel handling.
            @(negedge clk);
            awaddr = address;
            awvalid = 1'b1;
            while (!awready)
                @(posedge clk);
            @(posedge clk);
            @(negedge clk);
            awvalid = 1'b0;

            repeat (2) @(negedge clk);
            wdata = data;
            wstrb = strobes;
            wvalid = 1'b1;
            while (!wready)
                @(posedge clk);
            @(posedge clk);
            @(negedge clk);
            wvalid = 1'b0;

            while (!bvalid)
                @(posedge clk);
            response = bresp;
            @(negedge clk);
            bready = 1'b1;
            @(posedge clk);
            @(negedge clk);
            bready = 1'b0;
        end
    endtask

    task axi_read;
        input [7:0] address;
        output [31:0] data;
        output [1:0] response;
        begin
            @(negedge clk);
            araddr = address;
            arvalid = 1'b1;
            while (!arready)
                @(posedge clk);
            @(posedge clk);
            @(negedge clk);
            arvalid = 1'b0;

            while (!rvalid)
                @(posedge clk);
            data = rdata;
            response = rresp;
            @(negedge clk);
            rready = 1'b1;
            @(posedge clk);
            @(negedge clk);
            rready = 1'b0;
        end
    endtask

    reg [1:0] response;
    reg [31:0] read_data;

    initial begin
        clk = 1'b0;
        aresetn = 1'b0;
        awaddr = 8'b0;
        awvalid = 1'b0;
        wdata = 32'b0;
        wstrb = 4'b0;
        wvalid = 1'b0;
        bready = 1'b0;
        araddr = 8'b0;
        arvalid = 1'b0;
        rready = 1'b0;
        clock_ok = 1'b1;
        clock_reference_ok = 1'b1;
        local_time_valid = 1'b1;
        system_time_valid = 1'b0;
        pps_locked = 1'b1;
        raw_tick_exhausted = 1'b0;
        raw_tick = 64'b0;
        cmd_ready = 1'b1;
        failures = 0;
        commit_count = 0;
        fault_clear_count = 0;

        repeat (3) @(posedge clk);
        aresetn = 1'b1;
        repeat (2) @(posedge clk);

        axi_read(8'h00, read_data, response);
        check(response == 2'b00 && read_data == 32'h5246_434c,
              "design ID readback");

        axi_read(8'h10, read_data, response);
        check(response == 2'b00 && read_data[3:0] == 4'b0111,
              "status0 bit mapping");

        axi_write(8'h40, 32'h0000_0021, 4'b1111, response);
        check(response == 2'b00 && cmd_written_mask[0],
              "CMD_ID shadow write");
        axi_write(8'h44, 32'h0000_1000, 4'b1111, response);
        axi_write(8'h48, 32'h0000_0001, 4'b1111, response);
        axi_write(8'h4c, 32'hdead_beef, 4'b1111, response);
        check(target_tick_shadow == 64'h0000_0001_0000_1000 &&
              control_value_shadow == 32'hdead_beef &&
              cmd_fields_complete,
              "all command shadow fields assembled");

        // A commit must remain valid while H05 is busy/not ready.
        cmd_ready = 1'b0;
        axi_write(8'h50, 32'h0000_0001, 4'b1111, response);
        repeat (2) @(posedge clk);
        check(response == 2'b00 && commit_count == 0 && cmd_valid &&
              cmd_id == 32'h0000_0021 &&
              cmd_target_tick == 64'h0000_0001_0000_1000 &&
              cmd_control_value == 32'hdead_beef &&
              !cmd_fields_complete,
              "command valid must hold stable while H05 is not ready");

        @(negedge clk);
        cmd_ready = 1'b1;
        repeat (2) @(posedge clk);
        check(commit_count == 1 && !cmd_valid,
              "valid/ready command handshake");

        axi_write(8'h50, 32'h0000_0001, 4'b1111, response);
        check(response == 2'b10 && commit_count == 1,
              "incomplete command must return SLVERR");

        axi_write(8'h1c, 32'h0000_0001, 4'b0001, response);
        check(response == 2'b00, "snapshot request write");
        axi_read(8'h20, read_data, response);
        check(response == 2'b00, "snapshot tick low read");

        axi_write(8'h58, 32'h0000_0001, 4'b1111, response);
        repeat (2) @(posedge clk);
        check(response == 2'b00 && fault_clear_count == 1,
              "H05 fault-clear pulse");

        axi_write(8'h00, 32'h0, 4'b1111, response);
        check(response == 2'b10, "write to read-only register must fail");

        if (failures == 0)
            $display("PASS: tb_H02_control_regs");
        else
            $display("FAIL: tb_H02_control_regs failures=%0d", failures);
        $finish;
    end
endmodule
