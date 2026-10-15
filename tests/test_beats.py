import numpy as np
import pytest

from rf_clock.pl.beats import RX_LAYOUT, TX_LAYOUT, BeatLayout


def test_default_layout_round_trip_and_little_endian_bytes():
    layout = BeatLayout()
    samples = [(0x1234, -2), (-32768, 32767)] + [(lane, -lane) for lane in range(2, 8)]
    beat = layout.pack_beat(samples, pad=False)
    assert layout.unpack_beat(beat) == samples
    assert int.from_bytes(layout.pack_bytes(samples), "little") == beat


def test_unpack_complex_matches_scalar_decode():
    layout = BeatLayout(lane_order=(3, 1, 0, 2, 7, 6, 4, 5))
    samples = [(i * 37 - 500, 1000 - i * 11) for i in range(29)]
    data = layout.pack_bytes(samples)
    expected = [complex(i, q) for i, q in layout.unpack_bytes(data, sample_count=29)]
    assert list(layout.unpack_complex(data, sample_count=29)) == expected
    assert list(layout.unpack_complex(bytearray(data))[:29]) == expected


def test_custom_lane_width_uses_identity_lane_order():
    layout = BeatLayout(lane_count=2, sample_bits=8, width_bits=32, i_base=0, q_base=16)
    samples = [(127, -128), (-1, 2)]
    assert layout.unpack_beat(layout.pack_beat(samples, pad=False)) == samples


def test_tx_layout_interleaves_iq_per_word():
    beat = TX_LAYOUT.pack_beat([(1, 2), (3, 4)])
    words = [(beat >> (32 * n)) & 0xFFFF_FFFF for n in range(8)]
    assert words[:3] == [0x0002_0001, 0x0004_0003, 0]


def test_sample_arrays_pack_like_scalar_samples():
    iq = np.random.default_rng(1).integers(-32768, 32768, size=(29, 2))  # partial last beat
    iq[:2] = [[-32768, 32767], [32767, -32768]]
    rows = [(int(i), int(q)) for i, q in iq]
    for layout in (TX_LAYOUT, RX_LAYOUT, BeatLayout(lane_order=(3, 1, 0, 2, 7, 6, 4, 5))):
        assert layout.pack_samples(iq) == layout.pack_samples(rows)
        assert layout.pack_samples(iq.astype(np.int16)) == layout.pack_samples(rows)
        assert layout.pack_samples(iq[:, 0]) == layout.pack_samples([i for i, _ in rows])
    assert TX_LAYOUT.pack_samples(np.zeros((0, 2), dtype=np.int16)) == []
    small = BeatLayout(lane_count=2, sample_bits=8, width_bits=32, i_base=0, q_base=16)
    assert small.pack_samples(np.array([[127, -128]])) == [small.pack_beat([(127, -128)])]


def test_sample_arrays_are_checked():
    with pytest.raises(ValueError):
        TX_LAYOUT.pack_samples(np.array([0, 32768]))
    with pytest.raises(ValueError):
        TX_LAYOUT.pack_samples(np.zeros((4, 3), dtype=np.int16))
