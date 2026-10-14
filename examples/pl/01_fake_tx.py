"""在开发机上演示一次 TX 波形写入和定时提交。

这个例子不需要 PYNQ。FakePL 只模拟 ABI 中与软件顺序有关的寄存器变化；真正的
板上运行应把 MemoryMMIO 换成 PynqMMIO，并把波形写入 AXI BRAM buffer。
"""

from __future__ import annotations

from rf_clock.pl import abi
from rf_clock.pl.client import PLClient
from rf_clock.pl.mmio import MemoryMMIO
from rf_clock.pl.txbuf import TXBuffer


def status_word(tx: abi.TxState = abi.TxState.IDLE) -> int:
    """构造一个 time_valid/pps_locked 的假 STATUS。"""
    return (
        (1 << abi.ABI.STAT.TIME_VALID)
        | (1 << abi.ABI.STAT.CLOCKS_LOCKED)
        | (1 << abi.ABI.STAT.PPS_LOCKED)
        | (int(tx) << abi.ABI.STAT.TX_STATE_LSB)
    )


class FakePL:
    """只模拟 TX doorbell 和完成状态；不模拟 RFDC 数据流。"""

    def __init__(self) -> None:
        self.clock = MemoryMMIO(initial={0: 1})  # GPIO bit0: fabric MMCM locked
        self.regs = MemoryMMIO(
            initial={
                abi.ABI.REG.ID: abi.ABI.DESIGN_ID,
                abi.ABI.REG.VERSION: abi.ABI.VERSION,
                abi.ABI.REG.STATUS: status_word(),
                abi.ABI.REG.TIME_EPOCH: 1,
                abi.ABI.REG.TX_DEPTH: 8192,
                abi.ABI.REG.RX_MAX_BEATS: 2097151,
                abi.ABI.REG.MIN_LEAD_TICKS: 64,
            },
            on_write=self._write,
        )
        self.tx_target = 0

    def _write(self, offset: int, value: int) -> None:
        if offset == abi.ABI.REG.TX_TARGET_LO:
            self.tx_target = (self.tx_target & ~0xFFFF_FFFF) | value
        elif offset == abi.ABI.REG.TX_TARGET_HI:
            self.tx_target = (self.tx_target & 0xFFFF_FFFF) | (value << 32)
        elif offset == abi.ABI.REG.CONTROL and value == 1 << abi.ABI.CTRL.TX_DOORBELL:
            # 硬件在 doorbell 后立即返回 ARMED；不能直接返回 DONE。
            self.regs.values[abi.ABI.REG.TX_RESULT] = int(abi.ResultCode.ARMED)
            self.regs.values[abi.ABI.REG.STATUS] = status_word(abi.TxState.ARMED)

    def complete_tx(self) -> None:
        """模拟 DAC 接收了第 0 拍后的 DONE 状态。"""
        self.regs.values[abi.ABI.REG.TX_RESULT] = int(abi.ResultCode.DONE)
        self.regs.values[abi.ABI.REG.TX_ACTUAL_LO] = self.tx_target & 0xFFFF_FFFF
        self.regs.values[abi.ABI.REG.TX_ACTUAL_HI] = self.tx_target >> 32
        self.regs.values[abi.ABI.REG.STATUS] = status_word(abi.TxState.DONE)


def main() -> None:
    pl = FakePL()
    client = PLClient(pl.regs, pl.clock)
    info = client.validate()  # 先检查 ID/VERSION，再使用其余寄存器

    # TX buffer 只在 IDLE/DONE 写入；每个 beat 固定 32 字节、按小端 32-bit 写。
    tx_buffer = TXBuffer(MemoryMMIO(), client)
    tx_buffer.write_beat(0, bytes(range(32)))

    # target 必须是未来 tick；PL 还会再次按 MIN_LEAD_TICKS 检查。
    target = 100_000_100
    client.arm_tx(target, beats=1)  # shadow → doorbell → RESULT == ARMED
    pl.complete_tx()
    client.wait_tx(timeout=0.0, poll_interval=0.0)
    if client.tx_actual() != target:
        raise RuntimeError("TX_ACTUAL does not match the requested target")
    print(f"ABI 0x{info.version:08x}: TX completed at tick {client.tx_actual()}")


if __name__ == "__main__":
    main()
