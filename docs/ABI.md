# RF-clock 控制与数据 ABI

本文定义 ARM/PS 软件、H02/H05 控制链、H08/H09 波形链和 RFDC AXI4-Stream
之间的当前接口约定。它描述的是本项目当前 RTL 的 ABI，不是 Xilinx RFDC
驱动的完整替代品。

## 1. 两条完全不同的通路

系统有两条通路，不能混为一谈：

```text
控制面：ARM/Linux/Python
       └─ AXI4-Lite ─ H02 ─ H05 ─ H08/H09

数据面：H09
       └─ AXI4-Stream ─ H09_pulse_rfdc_bridge ─ RFDC DAC
```

控制面传输的是寄存器和命令，数据面传输的是连续 DAC 样本。当前 H09
活动 frame 不允许靠 `TREADY` 暂停；RFDC 接收端在活动期间失去 ready 会进入
FAULT，而不是插入一个气泡。

## 2. RFDC 采样率、数据率和 AXIS 时钟

### 2.1 三个“频率”不是一回事

| 名称 | 当前候选值 | 含义 |
|---|---:|---|
| DAC converter sampling rate | 9.6 GS/s | RF-DAC 模拟转换器每秒真正转换的样本数 |
| RFDC fabric input rate | 800 MS/s | 插值前，PL 送入 RFDC 的复样本率 |
| AXI4-Stream clock | 100 MHz | `TDATA/TVALID/TREADY` 握手所在的数字时钟 |

当前 profile 的总插值倍率为 12，因此：

```text
9.6 GS/s / 12 = 800 MS/s
```

RFDC 的配置字段中，`DAC_Interpolation_Mode20 = 6` 是 DUC 插值倍率；当前
启用的 IMR 路径在 mixer 后还增加一个 2 倍插值，因此有效总倍率是
`6 × 2 = 12`。这不是“一个固定的 2x stage 再接一个固定的 6x stage”这么
简单：RF-DAC 内部有多级 FIR 插值链，6x 由其中的级联组合实现，IMR 的 2x
是额外的后级。AMD PG269 对 Gen3/DFE 的说明是：FIR1 可选 2x/3x/5x，后续
FIR2/FIR3/FIR4 为 2x 级，并且 IMR mode 会在 mixer 后再增加 2x。
最终仍应以目标 Vivado 版本生成的 RFDC IP readback 为准。

参考：[AMD PG269 RF-DAC Digital Datapath](https://docs.amd.com/r/en-US/pg269-rf-data-converter/RF-DAC-Digital-Datapath)、
[RF-DAC Interpolation Filters](https://docs.amd.com/r/en-US/pg269-rf-data-converter/RF-DAC-Interpolation-Filters-Gen-3/DFE)。

### 2.2 为什么 800 MS/s 对应 100 MHz AXIS

当前 RFDC AXIS beat 是 256 bit：

```text
8 lanes × (16-bit I + 16-bit Q) = 8 个复样本/beat
```

因此：

```text
100 MHz × 8 samples/beat = 800 MS/s
```

所以，**AXIS 时钟和 800 MS/s 有直接关系，但 AXIS 时钟不是 9.6 GHz 的采样
时钟**。AXIS 时钟只决定每秒可以传输多少个 beat；每个 beat 携带多少样本由
RFDC 的数据宽度决定。

当前候选 profile 还记录了约 300 MHz 的 RFDC converter/output clock。它是
RFDC 内部/转换器侧时钟，不应直接当成 H09 的 AXIS 时钟。H09 bridge 应使用
最终生成 wrapper 中明确标出的 `s2_axis_aclk`（候选为 100 MHz）。

如果改成其他 AXIS 时钟，必须重新满足：

```text
AXIS clock × valid beats/second × samples/beat = RFDC fabric sample rate
```

例如 125 MHz、每 beat 8 个复样本时，输入率会变成 1 GS/s，而不是当前的
800 MS/s。

## 3. 192-bit 命令 ABI

一条 H05 命令由一个 envelope 和一个 payload 组成：

```text
cmd_id          32 bit
target_tick     64 bit
control_value  192 bit
```

H02 将 `control_value` 当作不透明的 192-bit 值保存；H05 负责调度和锁存；
H08-facing bridge 在执行边界解释字段。

```text
bit [191:184] command_type       8 bit
bit [183:176] payload_version    8 bit
bit [175:144] frame_id          32 bit
bit [143:128] frame_seed        16 bit
bit [127:096] frame_len         32 bit
bit [095:064] config_id         32 bit
bit [063:000] reserved          64 bit
```

当前 PRN frame 命令约定：

```text
command_type       = 8'h01
payload_version    = 8'h01
frame_seed         != 0
frame_len          != 0
reserved           = 0
```

`config_id` 用于标识 RRC/RFDC 等配置 profile；它当前由 H02/H05 透传，
不在 H05 内部解释。

定义文件为 [H05_command_defs.vh](../FPGA/src/H05_command_defs.vh)。

## 4. H02 AXI4-Lite 寄存器

H02 是 32-bit AXI4-Lite slave。所有命令字要求 `WSTRB = 4'b1111`。

### 4.1 命令写入寄存器

| 地址 | 名称 | 内容 |
|---:|---|---|
| `0x40` | `CMD_ID` | 32-bit command ID |
| `0x44` | `TARGET_TICK_LO` | target tick `[31:0]` |
| `0x48` | `TARGET_TICK_HI` | target tick `[63:32]` |
| `0x4c` | `CONTROL_WORD0` | payload `[31:0]` |
| `0x50` | `CONTROL_WORD1` | payload `[63:32]` |
| `0x54` | `CONTROL_WORD2` | payload `[95:64]` |
| `0x58` | `CONTROL_WORD3` | payload `[127:96]` |
| `0x5c` | `CONTROL_WORD4` | payload `[159:128]` |
| `0x60` | `CONTROL_WORD5` | payload `[191:160]` |
| `0x64` | `CMD_COMMIT` | 写 bit 0 = 1 提交 |

H02 使用 9-bit written mask：ID、target low/high 和 6 个 payload word
全部写入后，`CMD_COMMIT` 才会被接受。H02 内部只有一个 pending command，
不是命令 FIFO。

### 4.2 提交语义

```text
1. 软件写 CMD_ID
2. 软件写 TARGET_TICK_LO/HI
3. 软件写 CONTROL_WORD0..5
4. 软件写 CMD_COMMIT = 1
5. H02 产生 cmd_valid，并保持所有字段稳定
6. H05 置 cmd_ready 后，cmd_valid && cmd_ready 完成传输
```

如果 H05 尚未 ready，H02 不丢命令，`cmd_valid` 保持有效。提交完成后，
影子字段可以准备下一条命令，但在 pending command 被 H05 接收前不能再次
commit。

`cmd_valid && cmd_ready` 是控制面传输握手；它不等同于 H05 已经成功执行。
H05 可能在保持 IDLE 的情况下拒绝旧 ID、过晚 target tick 等命令，软件应
读取 H05 return code 判断结果。

`cmd_accepted` 是 H05 内部的一周期事件，目前没有单独的 AXI 读回位。软件
应使用唯一递增的 `cmd_id`，等待 `CMD_STATUS.pending` 清零，再读取 `H05_RETURN`。
执行中的状态依次表现为 `PENDING`，完整 TX frame 结束后为 `SUCCESS`；故障码
保持锁存，直到写 `FAULT_CLEAR`。

当前 ABI 还没有 `last_cmd_id/result_valid/result_seq` 结果寄存器。因此软件不应
快速连续提交多条命令，也不能把可能只持续一拍的 `tx_done` 当作唯一完成通知。
当前原型使用“单条命令 + 唯一递增 ID + H05_RETURN 锁存”的策略；正式多命令
队列可再增加 sticky 的结果记录寄存器。

## 5. 状态和故障回读

### 5.1 常用状态地址

| 地址 | 内容 |
|---:|---|
| `0x10` | clock/reference/local/system validity |
| `0x14` | PPS lock、raw-tick exhausted |
| `0x18` | 命令 written mask `[8:0]` |
| `0x30` | `[11]` fault, `[10]` active, `[9]` admission, `[8]` done, `[7:0]` aggregate return code |
| `0x34` | H03 live/sticky exhausted |
| `0x38` | H08 live status + local code |
| `0x3c` | H09 live status + local code |
| `0x68` | `[6]` TX fault, `[5]` TX active, `[4]` TX done, `[3]` admission, `[2]` H05 ready, `[1]` pending, `[0]` fields complete |
| `0x70` | `[6]` fault, `[5]` wait, `[4]` idle, `[3]` pending, `[2]` fields complete, `[1]` H05 ready |
| `0x74` | H05 8-bit return code in `[7:0]` |
| `0x90` | RFDC live ready/active/fault + local code |

### 5.2 Sticky 故障地址

```text
0x80 H08_FAULT   sticky fault + H08 local cause[3:0]
0x84 H09_FAULT   sticky fault + H09 local cause[3:0]
0x88 RFDC_FAULT  sticky fault + RFDC local cause[3:0]
0x8c TX_FAULT    bit[8] sticky aggregate fault, bit[7:0] source/cause
```

`TX_FAULT[7:4]` 是故障来源，`TX_FAULT[3:0]` 是来源模块的原因：

```text
0x1 H05
0x2 H08
0x3 H09
0x4 RFDC
0x5 H03
```

H03 当前没有独立的 local return-code port。raw tick exhausted 时，H10
生成 `source=H03, cause=TIMEBASE_EXHAUSTED`，并放入 aggregate TX fault。

`0x6c` 的 bit 0 是 fault-clear 脉冲，同时清除 H05 和 H10 的 sticky fault。

## 6. C/C++ 侧如何发送命令

### 6.1 裸机/Vitis

ARM 通过 PS 的 AXI master 访问 H02 的 AXI4-Lite 地址映射，不需要 XDMA。
地址由 Vivado address editor 和最终 XSA/HWH 决定，下面的
`H02_BASEADDR` 只是占位符。

```c
#include "xil_io.h"
#include <stdint.h>

#define H02_BASEADDR       0xDEADBEEFu /* replace with Vivado address map */
#define H02_CMD_ID         0x40u
#define H02_TARGET_LO      0x44u
#define H02_TARGET_HI      0x48u
#define H02_CONTROL0       0x4cu
#define H02_CMD_COMMIT     0x64u
#define H02_CMD_STATUS     0x68u
#define H02_H05_RETURN     0x74u

static void h02_write(uint32_t offset, uint32_t value)
{
    Xil_Out32(H02_BASEADDR + offset, value);
}

static void h02_send_command(uint32_t id, uint64_t target_tick,
                             const uint32_t control[6])
{
    h02_write(H02_CMD_ID, id);
    h02_write(H02_TARGET_LO, (uint32_t)target_tick);
    h02_write(H02_TARGET_HI, (uint32_t)(target_tick >> 32));

    for (unsigned i = 0; i < 6; ++i)
        h02_write(H02_CONTROL0 + 4u * i, control[i]);

    h02_write(H02_CMD_COMMIT, 1u);
}
```

裸机程序应在发送前轮询 `CMD_STATUS` 的 `cmd_ready`，并在发送后轮询
H05 return/status。实际工程还应加入超时、fault-clear 和读回校验。

### 6.2 C++

C++ 不需要另一套硬件协议，通常只是封装同一组 MMIO 写入：

```cpp
class H02CommandPort {
public:
    explicit H02CommandPort(std::uintptr_t base) : base_(base) {}

    void write_command(std::uint32_t id, std::uint64_t target,
                       const std::array<std::uint32_t, 6>& words);
    std::uint32_t read(std::uint32_t offset) const;

private:
    std::uintptr_t base_;
};
```

Linux 用户态可以通过 UIO/VFIO 或项目自己的内核驱动映射 H02；不要把
`/dev/mem` 当成最终产品接口。内核态实现应使用 `ioread32/iowrite32`，并在
提交后用合适的 I/O barrier；用户态 MMIO 则至少应使用 volatile 访问并通过
一次状态读取确认写入已到达外设。

## 7. Python/PYNQ 侧如何发送命令

PYNQ 通常通过 HWH/IP dictionary 获取 H02 的实际物理地址，然后用 `MMIO`
访问同一组 AXI4-Lite 寄存器：

```python
from pynq import Overlay
from pynq import MMIO

overlay = Overlay("rf_clock.bit", download=False)

# The key is design-dependent; inspect overlay.ip_dict for the actual name.
info = overlay.ip_dict["h02_axi_control_regs"]
h02 = MMIO(info["phys_addr"], 0x100)

def send_command(command_id: int, target_tick: int, payload: int) -> None:
    if payload.bit_length() > 192:
        raise ValueError("payload exceeds 192 bits")

    h02.write(0x40, command_id & 0xffffffff)
    h02.write(0x44, target_tick & 0xffffffff)
    h02.write(0x48, (target_tick >> 32) & 0xffffffff)

    for i in range(6):
        h02.write(0x4c + 4 * i, (payload >> (32 * i)) & 0xffffffff)

    h02.write(0x64, 1)
```

例如构造一个 PRN frame payload：

```python
payload = (
    (0x01 << 184) |       # command_type = PRN frame
    (0x01 << 176) |       # payload_version = 1
    (frame_id & 0xffffffff) << 144 |
    (frame_seed & 0xffff) << 128 |
    (frame_len & 0xffffffff) << 96 |
    (config_id & 0xffffffff) << 64
)
send_command(command_id, target_tick, payload)
```

实际程序应根据 `overlay.ip_dict` 或 HWH 检查 IP 名称和地址，不能假定所有
overlay 都叫 `h02_axi_control_regs`。

## 8. 需要 XDMA 吗？

### 当前 ARM→RFDC 结构：不需要

当前设计中：

```text
ARM → PS AXI master → H02 AXI4-Lite
H09 → AXI4-Stream → RFDC DAC
```

命令只有少量寄存器写入，数据样本由 PL 中 H08/H09 实时产生。因此：

- ARM 发送 H05/H08 命令不需要 XDMA；
- ARM 不需要把每个 DAC sample 写入内存再搬运到 RFDC；
- RFDC 的配置寄存器由 XRFdc 驱动或 `xrfdc` 访问，和 H02 命令通路分开。

### 什么时候才需要 XDMA

只有在外部主机通过 PCIe 向 FPGA 大量搬运数据，或者需要把大块采样数据
放入 FPGA DDR/BRAM 时，才考虑 XDMA/QDMA。那是另一条 PCIe DMA 数据路径，
不应为了当前少量控制命令而引入。

如果未来改成“PC→PCIe→FPGA→RFDC”的波形回放架构，需要重新定义 DMA buffer、
descriptor、帧边界和时钟域；不能把 XDMA 直接替换当前 H02 AXI-Lite。

## 9. 当前时钟域和 CDC 约束

当前 H02/H05/H10 原型假设 AXI-Lite 时钟、H03 raw tick、H08、H09 和状态
聚合器处在同一时钟域。若 PS AXI 时钟和 100 MHz AXIS 时钟不同，需要增加：

- AXI Clock Converter 或 AXI-Lite mailbox；
- 命令字段的原子跨域传输；
- 状态/故障码的同步或快照机制。

不能把不同频率的 `cmd_valid`、192-bit payload 或 raw tick 直接连线。

## 10. 当前 ABI 的边界

本 ABI 已经覆盖：

- ARM 到 H02 的控制命令；
- H02 到 H05 的 valid/ready 传输；
- H05 到 H08 的 192-bit payload；
- H09 到 RFDC 的 256-bit AXI-Stream beat；
- H03/H08/H09/RFDC 到 H10/H02 的状态和故障回读。

当前尚未冻结：

- RFDC 具体 AXI-Lite base address；
- 最终 Vivado wrapper 的端口命名；
- 多种 command type 的 dispatcher；
- 不同 AXI 时钟域之间的 CDC 实现。
- 带 `last_cmd_id/result_seq` 的多命令完成队列。

这些内容必须从最终 Vivado BD、HWH/XSA 和目标板运行时读回结果中确定。
