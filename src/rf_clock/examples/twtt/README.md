# Two-way time transfer examples

Annotated examples of `rf_clock.twtt` (docs/ABI.md §11.12) on a development machine, with no hardware:

```text
python -m rf_clock.examples.twtt.01_fake_two_way
python -m rf_clock.examples.twtt.02_fake_steer
```

`01_fake_two_way` forms theta and the one-way delay from one node's measurement queue: each window's own measurement and the peer's packet in that burst make one pair (`rf_clock.twtt.pairs`). `02_fake_steer` steers a simulated FS725 with the predictive control law, its frequency lock loop taken from a board config's `fs725` entry as `rf_clock.checks.fs725_loop` writes it. On the board, `../pl/04_link_node.py` gets the queue from the PL, and `rf_clock.checks.m4c_link` runs the whole node, steering included.

Chinese translation: [README_zh.md](README_zh.md).
