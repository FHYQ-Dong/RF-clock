// Minimal /dev/mem access to the RF-clock PL from C++ (docs/ABI.md appendix A.3).
//
//   g++ -O2 -o mmio_demo mmio_demo.cpp && sudo ./mmio_demo
//
// (the source is in the installed package, rf_clock/examples/native/)
//
// Needs the rf_clock overlay loaded; checks the clock-status GPIO before
// touching the fabric-clock registers.  Reads ID, VERSION, TICK and times a
// register read (about 0.54 us on the board, 2026-10-14).
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

static volatile uint32_t *map(int fd, uint64_t phys, size_t len) {
    void *p = mmap(nullptr, len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, phys);
    if (p == MAP_FAILED) { perror("mmap"); _exit(1); }
    return static_cast<volatile uint32_t *>(p);
}

int main() {
    int fd = open("/dev/mem", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open /dev/mem"); return 1; }
    volatile uint32_t *gpio = map(fd, 0xA0020000, 0x1000);  // clock status, pl_clk0
    volatile uint32_t *p2 = map(fd, 0xA0000000, 0x1000);    // P2 registers, fabric clock

    if (!(gpio[0] & 1)) { std::puts("fabric clock not running: not touching P2"); return 1; }
    std::printf("ID 0x%08x VERSION 0x%08x\n", p2[0x00 / 4], p2[0x04 / 4]);

    uint32_t lo = p2[0x20 / 4];  // TICK_LO first: latches TICK_HI
    uint32_t hi = p2[0x24 / 4];
    std::printf("TICK %llu\n", (unsigned long long)hi << 32 | lo);

    const int n = 100000;
    auto t0 = std::chrono::steady_clock::now();
    uint32_t sink = 0;
    for (int i = 0; i < n; ++i) sink ^= p2[0x0C / 4];  // STATUS
    double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count();
    std::printf("P2 read: %.3f us each (status 0x%x)\n", us / n, sink & 0);
    return 0;
}
