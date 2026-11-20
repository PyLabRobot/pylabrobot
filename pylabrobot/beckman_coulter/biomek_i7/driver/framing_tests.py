"""ACE framing: length-prefixed messages, packed into OUT transfers and read back from 16-byte IN
packets."""

import random
import unittest
from typing import List

from pylabrobot.beckman_coulter.biomek_i7.driver.errors import FrameError
from pylabrobot.beckman_coulter.biomek_i7.driver.framing import (
  IN_PACKET_SIZE,
  MAX_OUT_TRANSFER,
  FrameReader,
  frame,
  pack,
  split,
)

# Transfers from a captured cold start, each as it went over the wire: three Set Move Parameters
# packed into one OUT transfer, and three answers, each its own IN transfer, the last longer than a
# packet (an analog input's name, "Temperature").
CAPTURED_OUT = (
  "1b1101001840150000001f0000003201000000000000038c00000000"
  "1b1105001940030f0100298301009b1f0f000000000013f900000000"
  "1b1102001a40b86f0200077b030044ce2200000000008a9100000000"
)
CAPTURED_IN = [
  "09000000010002008000",
  "0902010007030200e500",
  "122209000b01020054656d7065726174757265",
]


def messages(rng: random.Random, count: int) -> List[bytes]:
  return [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 256))) for _ in range(count)]


class CapturedTransferTests(unittest.TestCase):
  def test_a_packed_out_transfer_splits_into_its_requests(self) -> None:
    data = bytes.fromhex(CAPTURED_OUT)
    requests = split(data)
    self.assertEqual([(m[0], m[1], m[3], m[4]) for m in requests],
                     [(0x11, 1, 0x18, 0x40), (0x11, 5, 0x19, 0x40), (0x11, 2, 0x1A, 0x40)])  # fmt: skip
    self.assertEqual(pack(requests), [data])

  def test_in_transfers_hold_one_frame_each(self) -> None:
    for hex_data in CAPTURED_IN:
      data = bytes.fromhex(hex_data)
      (message,) = split(data)
      self.assertEqual(frame(message), data)


class InverseTests(unittest.TestCase):
  """Framing and splitting undo each other, for any messages."""

  def setUp(self) -> None:
    self.rng = random.Random(11)

  def test_frame_then_split(self) -> None:
    for message in messages(self.rng, 200) + [b"\x00", b"\xff" * 255]:
      self.assertEqual(split(frame(message)), [message])

  def test_pack_then_split_keeps_order_and_fits(self) -> None:
    for _ in range(50):
      batch = [m[: MAX_OUT_TRANSFER - 1] for m in messages(self.rng, self.rng.randrange(1, 12))]
      transfers = pack(batch)
      self.assertTrue(all(len(t) <= MAX_OUT_TRANSFER for t in transfers))
      self.assertEqual([m for t in transfers for m in split(t)], batch)

  def test_frames_reassemble_from_packets(self) -> None:
    """Read packet by packet, as the board's reader does, the frames come out unchanged."""
    reader = FrameReader()
    for message in messages(self.rng, 200):
      data = frame(message)
      out: List[bytes] = []
      for i in range(0, len(data), IN_PACKET_SIZE):
        out += reader.feed(data[i : i + IN_PACKET_SIZE])
      self.assertEqual(out, [message])
      self.assertEqual(reader.pending, 0)

  def test_frames_reassemble_from_any_cut(self) -> None:
    stream = b"".join(frame(m) for m in messages(self.rng, 20))
    for _ in range(20):
      reader = FrameReader()
      cuts = sorted(self.rng.sample(range(1, len(stream)), 15))
      out: List[bytes] = []
      for a, b in zip([0] + cuts, cuts + [len(stream)]):
        out += reader.feed(stream[a:b])
      self.assertEqual(out, split(stream))


class PackTests(unittest.TestCase):
  def test_packs_in_order_until_the_transfer_is_full(self) -> None:
    messages = [bytes([i]) * 100 for i in range(3)]
    transfers = pack(messages)
    self.assertEqual(transfers, [frame(messages[0]) + frame(messages[1]), frame(messages[2])])
    self.assertTrue(all(len(t) <= MAX_OUT_TRANSFER for t in transfers))

  def test_rejects_empty_and_oversized_messages(self) -> None:
    with self.assertRaises(ValueError):
      frame(b"")
    with self.assertRaises(ValueError):
      frame(b"x" * 256)
    with self.assertRaises(ValueError):
      pack([b"x" * 255])  # a frame larger than a transfer

  def test_split_rejects_a_truncated_transfer(self) -> None:
    with self.assertRaises(FrameError):
      split(bytes([5, 1, 2]))

  def test_zero_length_frame_is_an_error(self) -> None:
    reader = FrameReader()
    with self.assertRaises(FrameError):
      reader.feed(b"\x00\x01")
    self.assertEqual(reader.pending, 0)


if __name__ == "__main__":
  unittest.main()
