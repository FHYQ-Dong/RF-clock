* The tuning voltage path, shared by efc_tran.cir, efc_ac.cir and efc_noise.cir.
* The DAC section (as the CN0531, schematic 02_058962 Rev B): LTC6655-5 reference, AD5791 in unity gain
* (RBUF = 1), AD8675 output buffer. Then the tuning filter: R8 100 ohm, C21 1 uF + C22 100 nF (C0G),
* BAV199 clamp to GND and to the OCXO's 4.985 V, and the OCXO's Vcon input (at least 50 k to 2.5 V inside).
* CTRACK stands for DAC_OUT's track from the AD8675 to R8 and TP13, 48 mm at about 1 pF per cm over
* the ground plane (inferred).
.lib ADI.lib
.lib LTC6655.lib
VP vp 0 7.5
VN vn 0 -7.5
XREF vp vp 0 ref ref LTC6655-5
CREF ref 0 10u
* The AD5791's output is Vref * code / 2^20 behind its 3.4 k ladder resistance, whose thermal noise
* (7.5 nV/rtHz) is the DAC's own noise. The reference buffers (AD8676) are left out.
ECODE dacv 0 ref 0 {code/1048576}
* VSTEP (dacs to dacv) is in each deck: the step, the AC source or nothing.
RDAC dacs inp 3.4k
XA inp inv vp vn out AD8675
RFB out inv 3.4k
C31 out inv 10p
CTRACK out 0 5p
R8 out vcon 100
C21 vcon 0 1u
C22 vcon 0 100n
RIN vcon vc0 50k
VC0 vc0 0 2.5
D4A 0 vcon BAV199
D4B vcon v5 BAV199
V5 v5 0 4.985
.model BAV199 D(Is=2p N=1.9 Rs=2 Cjo=1.5p)
.param code=524288
