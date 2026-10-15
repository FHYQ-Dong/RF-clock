"""Packing and unpacking of the 256-bit TX/RX sample beats.

``BeatLayout`` describes bit offsets instead of baking one interpretation
into the client, because the two directions differ (docs/ABI.md 4.5.8, 4.6.9;
measured with the M3 lane check on the board, 2026-10-14):

- ``TX_LAYOUT`` (DAC): I/Q interleaved per 32-bit word, lane n =
  ``{Q[32n+31:32n+16], I[32n+15:32n]}``;
- ``RX_LAYOUT`` (ADC, after the block design's I/Q combiner): planar,
  ``{Q[255:128], I[127:0]}`` with lane zero in the low 16 bits of each plane.
  This is also the ``BeatLayout()`` default (``DEFAULT_LAYOUT``).

The lane check used I-only impulses, so it fixes the lane order but not which
16-bit field of a pair is I and which is Q.  A beat is always represented as a
256-bit integer internally and as 32 little-endian bytes at a DMA/TX-buffer
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import operator
from typing import Any, Iterable, Sequence


BEAT_BITS = 256
BEAT_BYTES = BEAT_BITS // 8
DEFAULT_LANE_COUNT = 8
DEFAULT_SAMPLE_BITS = 16
DEFAULT_I_BASE = 0
DEFAULT_Q_BASE = DEFAULT_LANE_COUNT * DEFAULT_SAMPLE_BITS
DEFAULT_LANE_ORDER = tuple(range(DEFAULT_LANE_COUNT))


def _as_int(value: Any, *, name: str) -> int:
    """Convert an integer-like value without silently truncating floats."""

    try:
        return int(operator.index(value))
    except (TypeError, ValueError):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise TypeError(f"{name} must be an integer") from None


def _sample_component(value: object, *, name: str, sample_bits: int = DEFAULT_SAMPLE_BITS) -> int:
    number = _as_int(value, name=name)
    minimum = -(1 << (sample_bits - 1))
    maximum = (1 << (sample_bits - 1)) - 1
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} is outside signed {sample_bits}-bit range: {number}")
    return number


def _signed_to_bits(value: int, width: int) -> int:
    return int(value) & ((1 << width) - 1)


def _bits_to_signed(value: int, width: int) -> int:
    sign = 1 << (width - 1)
    value &= (1 << width) - 1
    return value - (1 << width) if value & sign else value


def _coerce_sample(
    sample: Any, index: int = 0, *, sample_bits: int = DEFAULT_SAMPLE_BITS
) -> tuple[int, int]:
    """Convert a scalar, complex value, or two-element I/Q row."""

    if isinstance(sample, complex):
        if not sample.real.is_integer() or not sample.imag.is_integer():
            raise TypeError(f"sample {index} has non-integral complex components")
        i_value, q_value = int(sample.real), int(sample.imag)
    elif isinstance(sample, (str, bytes, bytearray)):
        raise TypeError(f"sample {index} must be a scalar or I/Q pair")
    else:
        try:
            parts = list(sample)  # numpy rows and ordinary I/Q tuples
        except TypeError:
            parts = None
        if parts is None:
            i_value, q_value = sample, 0
        else:
            if len(parts) != 2:
                raise ValueError(f"sample {index} must contain exactly I and Q")
            i_value, q_value = parts

    return (
        _sample_component(i_value, name=f"sample {index} I", sample_bits=sample_bits),
        _sample_component(q_value, name=f"sample {index} Q", sample_bits=sample_bits),
    )


@dataclass(frozen=True)
class BeatLayout:
    """Bit layout for one beat of ``lane_count`` complex samples.

    ``lane_order`` maps a logical input lane to a physical lane slot.  For
    example, ``(7, 6, ..., 0)`` puts input lane zero in the highest lane field.
    ``i_offsets`` and ``q_offsets`` are the least-significant bit of each
    physical lane slot; when omitted they are contiguous I and Q planes.
    """

    lane_count: int = DEFAULT_LANE_COUNT
    sample_bits: int = DEFAULT_SAMPLE_BITS
    width_bits: int = BEAT_BITS
    i_base: int = DEFAULT_I_BASE
    q_base: int = DEFAULT_Q_BASE
    lane_order: Sequence[int] = field(default_factory=lambda: DEFAULT_LANE_ORDER)
    i_offsets: Sequence[int] | None = None
    q_offsets: Sequence[int] | None = None
    # i_offsets / q_offsets after defaults are filled in (one entry per lane).
    _i: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _q: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        lane_count = _as_int(self.lane_count, name="lane_count")
        sample_bits = _as_int(self.sample_bits, name="sample_bits")
        width_bits = _as_int(self.width_bits, name="width_bits")
        if lane_count <= 0 or sample_bits <= 0 or width_bits <= 0:
            raise ValueError("lane_count, sample_bits, and width_bits must be positive")
        if width_bits != lane_count * sample_bits * 2:
            raise ValueError("width_bits must hold two planes of all lanes")

        order = tuple(_as_int(value, name="lane_order entry") for value in self.lane_order)
        if order == DEFAULT_LANE_ORDER and lane_count != DEFAULT_LANE_COUNT:
            # The default identity order scales naturally for custom lane counts.
            order = tuple(range(lane_count))
        if len(order) != lane_count or set(order) != set(range(lane_count)):
            raise ValueError("lane_order must be a permutation of all lane indices")

        if self.i_offsets is None:
            i_offsets = tuple(
                _as_int(self.i_base, name="i_base") + lane * sample_bits
                for lane in range(lane_count)
            )
        else:
            i_offsets = tuple(
                _as_int(value, name="i_offsets entry") for value in self.i_offsets
            )
        if self.q_offsets is None:
            q_offsets = tuple(
                _as_int(self.q_base, name="q_base") + lane * sample_bits
                for lane in range(lane_count)
            )
        else:
            q_offsets = tuple(
                _as_int(value, name="q_offsets entry") for value in self.q_offsets
            )
        if len(i_offsets) != lane_count or len(q_offsets) != lane_count:
            raise ValueError("i_offsets and q_offsets must have one entry per lane")

        occupied: list[tuple[int, int]] = []
        for offset in (*i_offsets, *q_offsets):
            if offset < 0 or offset + sample_bits > width_bits:
                raise ValueError("sample offset is outside the beat width")
            occupied.append((offset, offset + sample_bits))
        for index, (start, end) in enumerate(occupied):
            if any(
                start < other_end and other_start < end
                for other_start, other_end in occupied[:index]
            ):
                raise ValueError("sample fields overlap")

        object.__setattr__(self, "lane_count", lane_count)
        object.__setattr__(self, "sample_bits", sample_bits)
        object.__setattr__(self, "width_bits", width_bits)
        object.__setattr__(self, "lane_order", order)
        object.__setattr__(self, "i_offsets", i_offsets)
        object.__setattr__(self, "q_offsets", q_offsets)
        object.__setattr__(self, "_i", i_offsets)
        object.__setattr__(self, "_q", q_offsets)

    @property
    def beat_bytes(self) -> int:
        return self.width_bits // 8

    def _physical_lane(self, logical_lane: int) -> int:
        return self.lane_order[logical_lane]

    def pack_beat(self, samples: Iterable[object], *, pad: bool = True) -> int:
        """Pack up to one beat and return its 256-bit integer value.

        Missing lanes in the last TX beat are zero padded by default.  Set
        ``pad=False`` to require exactly ``lane_count`` samples.
        """

        values = list(samples)
        if len(values) > self.lane_count:
            raise ValueError(f"a beat contains at most {self.lane_count} samples")
        if not pad and len(values) != self.lane_count:
            raise ValueError(f"a beat requires exactly {self.lane_count} samples")

        beat = 0
        for logical_lane, sample in enumerate(values):
            i_value, q_value = _coerce_sample(
                sample, logical_lane, sample_bits=self.sample_bits
            )
            physical_lane = self._physical_lane(logical_lane)
            beat |= _signed_to_bits(i_value, self.sample_bits) << self._i[physical_lane]
            beat |= _signed_to_bits(q_value, self.sample_bits) << self._q[physical_lane]
        return beat

    def pack_samples(self, samples: Iterable[object]) -> list[int]:
        """Pack a flat sample sequence into beats, zero-padding the tail beat.

        An integer NumPy array of I samples ``(N,)`` or I/Q rows ``(N, 2)`` is
        packed with array operations when the layout has 16-bit samples on
        16-bit boundaries (every RFDC layout); the beats are the same.
        """

        if getattr(getattr(samples, "dtype", None), "kind", None) in ("i", "u") and (
            self.sample_bits == 16 and not any(o % 16 for o in (*self._i, *self._q))
        ):
            return self._pack_array(samples)
        values = list(samples)
        if not values:
            return []
        return [
            self.pack_beat(
                values[start : start + self.lane_count],
                pad=True,
            )
            for start in range(0, len(values), self.lane_count)
        ]

    def _pack_array(self, samples) -> list[int]:
        """:meth:`pack_samples` for an integer array: fill the 16-bit words of
        all beats at once, then cut the bytes into 256-bit integers."""

        import numpy as np

        values = np.asarray(samples)
        if values.ndim == 1:
            values = np.stack([values, np.zeros_like(values)], axis=1)
        if values.ndim != 2 or values.shape[1] != 2:
            raise ValueError("a sample array must have shape (N,) or (N, 2)")
        if values.size and (values.min() < -32768 or values.max() > 32767):
            raise ValueError("samples are outside the signed 16-bit range")

        count = -(-len(values) // self.lane_count)
        lanes = np.zeros((count * self.lane_count, 2), dtype=np.int16)
        lanes[: len(values)] = values
        lanes = lanes.reshape(count, self.lane_count, 2)
        physical = [self._physical_lane(lane) for lane in range(self.lane_count)]
        words = np.zeros((count, self.width_bits // 16), dtype="<i2")
        words[:, [self._i[p] // 16 for p in physical]] = lanes[:, :, 0]
        words[:, [self._q[p] // 16 for p in physical]] = lanes[:, :, 1]
        data = words.tobytes()
        size = self.beat_bytes
        return [int.from_bytes(data[k : k + size], "little") for k in range(0, len(data), size)]

    def pack_bytes(self, samples: Iterable[object]) -> bytes:
        """Pack samples as the little-endian bytes used by MMIO/DMA buffers."""

        return b"".join(
            beat.to_bytes(self.beat_bytes, "little")
            for beat in self.pack_samples(samples)
        )

    def unpack_beat(self, beat: int | bytes | bytearray | memoryview) -> list[tuple[int, int]]:
        """Decode one beat into logical-lane ``(I, Q)`` pairs."""

        if isinstance(beat, (bytes, bytearray, memoryview)):
            raw = bytes(beat)
            if len(raw) != self.beat_bytes:
                raise ValueError(f"a beat must contain exactly {self.beat_bytes} bytes")
            value = int.from_bytes(raw, "little")
        else:
            value = _as_int(beat, name="beat")
        if value < 0 or value >= (1 << self.width_bits):
            raise ValueError("beat does not fit the configured width")

        result: list[tuple[int, int]] = []
        for logical_lane in range(self.lane_count):
            physical_lane = self._physical_lane(logical_lane)
            i_value = _bits_to_signed(
                value >> self._i[physical_lane], self.sample_bits
            )
            q_value = _bits_to_signed(
                value >> self._q[physical_lane], self.sample_bits
            )
            result.append((i_value, q_value))
        return result

    def unpack_beats(
        self,
        beats: Iterable[int | bytes | bytearray | memoryview],
        *,
        sample_count: int | None = None,
    ) -> list[tuple[int, int]]:
        """Decode beats and optionally remove zero-padded tail lanes."""

        result: list[tuple[int, int]] = []
        for beat in beats:
            result.extend(self.unpack_beat(beat))
        if sample_count is not None:
            sample_count = _as_int(sample_count, name="sample_count")
            if sample_count < 0 or sample_count > len(result):
                raise ValueError("sample_count is outside the decoded data")
            return result[:sample_count]
        return result

    def unpack_bytes(self, data: bytes | bytearray | memoryview, *, sample_count: int | None = None) -> list[tuple[int, int]]:
        """Decode a contiguous little-endian DMA buffer."""

        raw = bytes(data)
        if len(raw) % self.beat_bytes:
            raise ValueError("DMA data length is not a whole number of beats")
        beats = (
            raw[offset : offset + self.beat_bytes]
            for offset in range(0, len(raw), self.beat_bytes)
        )
        return self.unpack_beats(beats, sample_count=sample_count)

    def unpack_complex(self, data, *, sample_count: int | None = None):
        """Vectorised decode of a DMA buffer to complex64 ``I + jQ`` samples.

        ``data`` is anything exposing the buffer protocol (bytes, a PYNQ
        buffer, a uint8 array).  Needs 16-bit samples on 16-bit boundaries,
        which every RFDC layout has; use :meth:`unpack_numpy` otherwise.
        """

        import numpy as np

        if self.sample_bits != 16 or any(o % 16 for o in (*self._i, *self._q)):
            raise ValueError("unpack_complex needs 16-bit samples on 16-bit boundaries")
        raw = np.frombuffer(memoryview(data).cast("B"), dtype=np.uint8)
        if raw.size % self.beat_bytes:
            raise ValueError("DMA data length is not a whole number of beats")
        words = raw.view("<i2").reshape(-1, self.width_bits // 16)
        lanes = [self._physical_lane(lane) for lane in range(self.lane_count)]
        i = words[:, [self._i[p] // 16 for p in lanes]].reshape(-1)
        q = words[:, [self._q[p] // 16 for p in lanes]].reshape(-1)
        result = i.astype(np.float32) + 1j * q.astype(np.float32)
        if sample_count is not None:
            if not 0 <= sample_count <= result.size:
                raise ValueError("sample_count is outside the decoded data")
            result = result[:sample_count]
        return result.astype(np.complex64)

    def unpack_numpy(self, data, *, sample_count: int | None = None):
        """Return decoded samples as an ``(N, 2)`` int16 NumPy array."""

        import numpy as np

        return np.asarray(
            self.unpack_bytes(data, sample_count=sample_count)
            if isinstance(data, (bytes, bytearray, memoryview))
            else self.unpack_beats(data, sample_count=sample_count),
            dtype=np.int16,
        )


class BeatPacker:
    """Convenience object shared by TX and RX software paths."""

    def __init__(self, layout: BeatLayout | None = None):
        self.layout = DEFAULT_LAYOUT if layout is None else layout
        if not isinstance(self.layout, BeatLayout):
            raise TypeError("layout must be a BeatLayout")

    def pack_beat(self, samples: Iterable[object], *, pad: bool = True) -> int:
        return self.layout.pack_beat(samples, pad=pad)

    def pack_samples(self, samples: Iterable[object]) -> list[int]:
        return self.layout.pack_samples(samples)

    def pack_bytes(self, samples: Iterable[object]) -> bytes:
        return self.layout.pack_bytes(samples)

    def unpack_beat(self, beat):
        return self.layout.unpack_beat(beat)

    def unpack_beats(self, beats, *, sample_count: int | None = None):
        return self.layout.unpack_beats(beats, sample_count=sample_count)

    def unpack_bytes(self, data, *, sample_count: int | None = None):
        return self.layout.unpack_bytes(data, sample_count=sample_count)

    def unpack_numpy(self, data, *, sample_count: int | None = None):
        return self.layout.unpack_numpy(data, sample_count=sample_count)

    def unpack_complex(self, data, *, sample_count: int | None = None):
        return self.layout.unpack_complex(data, sample_count=sample_count)


DEFAULT_LAYOUT = BeatLayout()
DEFAULT_BEAT_LAYOUT = DEFAULT_LAYOUT
RX_LAYOUT = DEFAULT_LAYOUT
TX_LAYOUT = BeatLayout(
    i_offsets=tuple(32 * lane for lane in range(DEFAULT_LANE_COUNT)),
    q_offsets=tuple(32 * lane + 16 for lane in range(DEFAULT_LANE_COUNT)),
)


def pack_tx_beats(samples: Iterable[object], layout: BeatLayout | None = None) -> list[int]:
    """Pack signed I samples or I/Q pairs for the TX buffer (TX_LAYOUT)."""

    return (TX_LAYOUT if layout is None else layout).pack_samples(samples)


def unpack_rx_beats(
    beats: Iterable[int | bytes | bytearray | memoryview],
    layout: BeatLayout | None = None,
    *,
    sample_count: int | None = None,
) -> list[tuple[int, int]]:
    """Unpack RX beats into logical-lane I/Q pairs (RX_LAYOUT)."""

    return (RX_LAYOUT if layout is None else layout).unpack_beats(
        beats, sample_count=sample_count
    )


__all__ = [
    "BEAT_BITS",
    "BEAT_BYTES",
    "BeatLayout",
    "BeatPacker",
    "DEFAULT_BEAT_LAYOUT",
    "DEFAULT_LAYOUT",
    "RX_LAYOUT",
    "TX_LAYOUT",
    "pack_tx_beats",
    "unpack_rx_beats",
]
