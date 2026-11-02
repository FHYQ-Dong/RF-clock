"""Bit-exact models of the PL modules of the measurement chain (plan section 6).

Each function computes, in integers, what the RTL is to compute, so that RTL
testbenches compare against it sample by sample and the ARM can reproduce the PL's
results:

- ``fir``: P3's chip stream through the RRC FIR into 16-bit samples;
- ``correlator``: P8's integer correlation, its estimator (peak, sub-sample
  position, carrier phase) and the tracker and whole-window search built on it;
- ``packet``: P9's measurement packet: fields, CRC-16, spreading, coherent despreading.

The models stand beside ``rf_clock.pl.waveform`` and ``rf_clock.correlator``, which
serve the first version's bitstream (1.3) and do not change.
"""
