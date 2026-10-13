//Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
//Copyright 2022-2024 Advanced Micro Devices, Inc. All Rights Reserved.
//--------------------------------------------------------------------------------
//Tool Version: Vivado v.2024.1 (lin64) Build 5076996 Wed May 22 18:36:09 MDT 2024
//Date        : Mon Oct 12 19:44:15 2026
//Host        : RF-clock running 64-bit Ubuntu 22.04.5 LTS
//Command     : generate_target rf_clock_bd_wrapper.bd
//Design      : rf_clock_bd_wrapper
//Purpose     : IP block netlist
//--------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

module rf_clock_bd_wrapper
   (ADC2_CLK_clk_n,
    ADC2_CLK_clk_p,
    DAC2_CLK_clk_n,
    DAC2_CLK_clk_p,
    M22_AXIS_tdata,
    M22_AXIS_tready,
    M22_AXIS_tvalid,
    M23_AXIS_tdata,
    M23_AXIS_tready,
    M23_AXIS_tvalid,
    S20_AXIS_tdata,
    S20_AXIS_tready,
    S20_AXIS_tvalid,
    SYSREF_IN_diff_n,
    SYSREF_IN_diff_p,
    S_AXI_araddr,
    S_AXI_arready,
    S_AXI_arvalid,
    S_AXI_awaddr,
    S_AXI_awready,
    S_AXI_awvalid,
    S_AXI_bready,
    S_AXI_bresp,
    S_AXI_bvalid,
    S_AXI_rdata,
    S_AXI_rready,
    S_AXI_rresp,
    S_AXI_rvalid,
    S_AXI_wdata,
    S_AXI_wready,
    S_AXI_wstrb,
    S_AXI_wvalid,
    VIN2_23_v_n,
    VIN2_23_v_p,
    VOUT20_v_n,
    VOUT20_v_p,
    adc22_datapath_overflow,
    adc23_datapath_overflow,
    adc2_23_cm_over_voltage,
    adc2_23_cm_under_voltage,
    adc2_23_over_range,
    adc2_23_over_threshold1,
    adc2_23_over_threshold2,
    adc2_23_over_voltage,
    dac20_datapath_overflow,
    fabric_reset,
    irq,
    rx_clk_100,
    rx_clk_100_locked,
    s_axi_aclk,
    s_axi_aresetn,
    tx_clk_100,
    tx_clk_100_locked);
  input ADC2_CLK_clk_n;
  input ADC2_CLK_clk_p;
  input DAC2_CLK_clk_n;
  input DAC2_CLK_clk_p;
  output [127:0]M22_AXIS_tdata;
  input M22_AXIS_tready;
  output M22_AXIS_tvalid;
  output [127:0]M23_AXIS_tdata;
  input M23_AXIS_tready;
  output M23_AXIS_tvalid;
  input [255:0]S20_AXIS_tdata;
  output S20_AXIS_tready;
  input S20_AXIS_tvalid;
  input SYSREF_IN_diff_n;
  input SYSREF_IN_diff_p;
  input [17:0]S_AXI_araddr;
  output S_AXI_arready;
  input S_AXI_arvalid;
  input [17:0]S_AXI_awaddr;
  output S_AXI_awready;
  input S_AXI_awvalid;
  input S_AXI_bready;
  output [1:0]S_AXI_bresp;
  output S_AXI_bvalid;
  output [31:0]S_AXI_rdata;
  input S_AXI_rready;
  output [1:0]S_AXI_rresp;
  output S_AXI_rvalid;
  input [31:0]S_AXI_wdata;
  output S_AXI_wready;
  input [3:0]S_AXI_wstrb;
  input S_AXI_wvalid;
  input VIN2_23_v_n;
  input VIN2_23_v_p;
  output VOUT20_v_n;
  output VOUT20_v_p;
  output adc22_datapath_overflow;
  output adc23_datapath_overflow;
  output adc2_23_cm_over_voltage;
  output adc2_23_cm_under_voltage;
  output adc2_23_over_range;
  output adc2_23_over_threshold1;
  output adc2_23_over_threshold2;
  output adc2_23_over_voltage;
  output dac20_datapath_overflow;
  input fabric_reset;
  output irq;
  output rx_clk_100;
  output rx_clk_100_locked;
  input s_axi_aclk;
  input s_axi_aresetn;
  output tx_clk_100;
  output tx_clk_100_locked;

  wire ADC2_CLK_clk_n;
  wire ADC2_CLK_clk_p;
  wire DAC2_CLK_clk_n;
  wire DAC2_CLK_clk_p;
  wire [127:0]M22_AXIS_tdata;
  wire M22_AXIS_tready;
  wire M22_AXIS_tvalid;
  wire [127:0]M23_AXIS_tdata;
  wire M23_AXIS_tready;
  wire M23_AXIS_tvalid;
  wire [255:0]S20_AXIS_tdata;
  wire S20_AXIS_tready;
  wire S20_AXIS_tvalid;
  wire SYSREF_IN_diff_n;
  wire SYSREF_IN_diff_p;
  wire [17:0]S_AXI_araddr;
  wire S_AXI_arready;
  wire S_AXI_arvalid;
  wire [17:0]S_AXI_awaddr;
  wire S_AXI_awready;
  wire S_AXI_awvalid;
  wire S_AXI_bready;
  wire [1:0]S_AXI_bresp;
  wire S_AXI_bvalid;
  wire [31:0]S_AXI_rdata;
  wire S_AXI_rready;
  wire [1:0]S_AXI_rresp;
  wire S_AXI_rvalid;
  wire [31:0]S_AXI_wdata;
  wire S_AXI_wready;
  wire [3:0]S_AXI_wstrb;
  wire S_AXI_wvalid;
  wire VIN2_23_v_n;
  wire VIN2_23_v_p;
  wire VOUT20_v_n;
  wire VOUT20_v_p;
  wire adc22_datapath_overflow;
  wire adc23_datapath_overflow;
  wire adc2_23_cm_over_voltage;
  wire adc2_23_cm_under_voltage;
  wire adc2_23_over_range;
  wire adc2_23_over_threshold1;
  wire adc2_23_over_threshold2;
  wire adc2_23_over_voltage;
  wire dac20_datapath_overflow;
  wire fabric_reset;
  wire irq;
  wire rx_clk_100;
  wire rx_clk_100_locked;
  wire s_axi_aclk;
  wire s_axi_aresetn;
  wire tx_clk_100;
  wire tx_clk_100_locked;

  rf_clock_bd rf_clock_bd_i
       (.ADC2_CLK_clk_n(ADC2_CLK_clk_n),
        .ADC2_CLK_clk_p(ADC2_CLK_clk_p),
        .DAC2_CLK_clk_n(DAC2_CLK_clk_n),
        .DAC2_CLK_clk_p(DAC2_CLK_clk_p),
        .M22_AXIS_tdata(M22_AXIS_tdata),
        .M22_AXIS_tready(M22_AXIS_tready),
        .M22_AXIS_tvalid(M22_AXIS_tvalid),
        .M23_AXIS_tdata(M23_AXIS_tdata),
        .M23_AXIS_tready(M23_AXIS_tready),
        .M23_AXIS_tvalid(M23_AXIS_tvalid),
        .S20_AXIS_tdata(S20_AXIS_tdata),
        .S20_AXIS_tready(S20_AXIS_tready),
        .S20_AXIS_tvalid(S20_AXIS_tvalid),
        .SYSREF_IN_diff_n(SYSREF_IN_diff_n),
        .SYSREF_IN_diff_p(SYSREF_IN_diff_p),
        .S_AXI_araddr(S_AXI_araddr),
        .S_AXI_arready(S_AXI_arready),
        .S_AXI_arvalid(S_AXI_arvalid),
        .S_AXI_awaddr(S_AXI_awaddr),
        .S_AXI_awready(S_AXI_awready),
        .S_AXI_awvalid(S_AXI_awvalid),
        .S_AXI_bready(S_AXI_bready),
        .S_AXI_bresp(S_AXI_bresp),
        .S_AXI_bvalid(S_AXI_bvalid),
        .S_AXI_rdata(S_AXI_rdata),
        .S_AXI_rready(S_AXI_rready),
        .S_AXI_rresp(S_AXI_rresp),
        .S_AXI_rvalid(S_AXI_rvalid),
        .S_AXI_wdata(S_AXI_wdata),
        .S_AXI_wready(S_AXI_wready),
        .S_AXI_wstrb(S_AXI_wstrb),
        .S_AXI_wvalid(S_AXI_wvalid),
        .VIN2_23_v_n(VIN2_23_v_n),
        .VIN2_23_v_p(VIN2_23_v_p),
        .VOUT20_v_n(VOUT20_v_n),
        .VOUT20_v_p(VOUT20_v_p),
        .adc22_datapath_overflow(adc22_datapath_overflow),
        .adc23_datapath_overflow(adc23_datapath_overflow),
        .adc2_23_cm_over_voltage(adc2_23_cm_over_voltage),
        .adc2_23_cm_under_voltage(adc2_23_cm_under_voltage),
        .adc2_23_over_range(adc2_23_over_range),
        .adc2_23_over_threshold1(adc2_23_over_threshold1),
        .adc2_23_over_threshold2(adc2_23_over_threshold2),
        .adc2_23_over_voltage(adc2_23_over_voltage),
        .dac20_datapath_overflow(dac20_datapath_overflow),
        .fabric_reset(fabric_reset),
        .irq(irq),
        .rx_clk_100(rx_clk_100),
        .rx_clk_100_locked(rx_clk_100_locked),
        .s_axi_aclk(s_axi_aclk),
        .s_axi_aresetn(s_axi_aresetn),
        .tx_clk_100(tx_clk_100),
        .tx_clk_100_locked(tx_clk_100_locked));
endmodule
