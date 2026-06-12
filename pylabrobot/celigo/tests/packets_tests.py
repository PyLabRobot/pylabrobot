"""Tests for the Celigo USB-IO packet framing."""

import struct
import unittest

from pylabrobot.celigo.packets import (
  ACK_STATE,
  IO_CTLR_CMDS,
  RX_HEADER_SIZE,
  Sequencer,
  USBIOError,
  build_tx_packet,
  fletcher16,
  transact,
)


class FakeTransport:
  """A loopback transport that delivers one queued response per request written.

  Modeling responses as a reaction to ``write`` (rather than pre-filling the read
  buffer) matches the real device and the driver's purge-and-retry flow: a ``purge``
  only discards bytes not yet read, never a reply that a later re-send will produce.
  """

  def __init__(self):
    self.written = b""
    self._responses: "list[bytes]" = []
    self._to_read = b""
    self.purged = 0

  def queue_response(self, data: bytes):
    self._responses.append(data)

  def write(self, data: bytes) -> int:
    self.written += data
    if self._responses:
      self._to_read += self._responses.pop(0)
    return len(data)

  def read(self, n: int) -> bytes:
    chunk, self._to_read = self._to_read[:n], self._to_read[n:]
    return chunk

  def purge(self) -> None:
    self.purged += 1
    self._to_read = b""


def make_response(cmd, sequence, payload=b"", ack=ACK_STATE.ACK_OK):
  header = bytearray(RX_HEADER_SIZE)
  header[0] = int(ack)
  header[1] = int(cmd)
  struct.pack_into(">i", header, 2, sequence)
  struct.pack_into(">i", header, 6, len(payload))
  a, b = fletcher16(header, 10)
  header[10] = a
  header[11] = b
  return bytes(header) + payload


class TestFletcher16(unittest.TestCase):
  def test_known_seed_empty(self):
    # zero-length -> just the folded seeds
    self.assertEqual(fletcher16(b"", 0), (0xFF, 0xFF))

  def test_deterministic(self):
    data = bytes(range(40))
    self.assertEqual(fletcher16(data, len(data)), fletcher16(data, len(data)))

  def test_block_boundary(self):
    # 21-byte block boundary is exercised; result stays in byte range
    a, b = fletcher16(bytes(range(50)), 50)
    self.assertTrue(0 <= a <= 0xFF and 0 <= b <= 0xFF)


class TestTxPacket(unittest.TestCase):
  def test_header_layout(self):
    pkt = build_tx_packet(IO_CTLR_CMDS.CONTROLLER_STATUS, 7, b"\x01\x02")
    self.assertEqual(pkt[0], int(IO_CTLR_CMDS.CONTROLLER_STATUS))
    self.assertEqual(struct.unpack_from(">i", pkt, 1)[0], 7)  # sequence
    self.assertEqual(struct.unpack_from(">i", pkt, 5)[0], len(pkt))  # total length
    a, b = fletcher16(pkt[:9], 9)
    self.assertEqual((pkt[9], pkt[10]), (a, b))
    self.assertEqual(pkt[11:], b"\x01\x02")  # payload after 11-byte header

  def test_length_includes_header(self):
    pkt = build_tx_packet(IO_CTLR_CMDS.MOTOR_CMD, 1, b"")
    self.assertEqual(struct.unpack_from(">i", pkt, 5)[0], 11)


class TestTransact(unittest.TestCase):
  def test_roundtrip_payload(self):
    t = FakeTransport()
    t.queue_response(make_response(IO_CTLR_CMDS.READ_AD_CHANNEL, 2, b"\xde\xad\xbe\xef"))
    out = transact(t, IO_CTLR_CMDS.READ_AD_CHANNEL, 2, b"\x00")
    self.assertEqual(out, b"\xde\xad\xbe\xef")
    # request was framed correctly
    self.assertEqual(t.written[0], int(IO_CTLR_CMDS.READ_AD_CHANNEL))

  def test_empty_payload(self):
    t = FakeTransport()
    t.queue_response(make_response(IO_CTLR_CMDS.RESET_CONTROLLER, 5))
    self.assertEqual(transact(t, IO_CTLR_CMDS.RESET_CONTROLLER, 5), b"")

  def test_nack_raises(self):
    t = FakeTransport()
    t.queue_response(make_response(IO_CTLR_CMDS.MOTOR_CMD, 3, ack=ACK_STATE.NACK_CMD_REJECT))
    with self.assertRaises(USBIOError) as ctx:
      transact(t, IO_CTLR_CMDS.MOTOR_CMD, 3)
    self.assertEqual(ctx.exception.ack, ACK_STATE.NACK_CMD_REJECT)

  def test_retry_on_checksum_then_success(self):
    t = FakeTransport()
    bad = make_response(IO_CTLR_CMDS.CONTROLLER_STATUS, 4, ack=ACK_STATE.NACK_INVALID_CHKSUM)
    good = make_response(IO_CTLR_CMDS.CONTROLLER_STATUS, 4, b"\x00")
    t.queue_response(bad)
    t.queue_response(good)
    out = transact(t, IO_CTLR_CMDS.CONTROLLER_STATUS, 4)
    self.assertEqual(out, b"\x00")
    self.assertEqual(t.purged, 1)

  def test_sequence_mismatch_raises(self):
    t = FakeTransport()
    t.queue_response(make_response(IO_CTLR_CMDS.CONTROLLER_STATUS, 999))
    with self.assertRaises(USBIOError):
      transact(t, IO_CTLR_CMDS.CONTROLLER_STATUS, 4)


class TestSequencer(unittest.TestCase):
  def test_monotonic(self):
    s = Sequencer(start=1)
    self.assertEqual([s.next(), s.next(), s.next()], [2, 3, 4])


if __name__ == "__main__":
  unittest.main()
