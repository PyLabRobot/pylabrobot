import asyncio
import math
import struct
import threading
import time
import unittest
from typing import List, Optional
from unittest import mock

from pylabrobot.hamilton.transport import rs232_ppp as ppp

# Frames an MPE2 sends on its USB port.
MPE2_DISCOVER = bytes.fromhex("aa1400000000007569 00ffda42 0010050000 00ffff cc".replace(" ", ""))
MPE2_ACK = bytes.fromhex("00070000000000000da5a97a")


class FramingTests(unittest.TestCase):
  def test_crc_matches_the_instrument(self):
    frame = ppp.decode_frame(MPE2_DISCOVER[1:-1])
    assert frame is not None
    self.assertFalse(frame["data"])
    self.assertEqual(frame["protocol"], ppp.LINK_PROTOCOL)
    self.assertEqual(frame["payload"], bytes.fromhex("0010050000 00ffff".replace(" ", "")))

  def test_encode_reproduces_the_instrument(self):
    payload = bytes.fromhex("001005000000ffff")
    self.assertEqual(ppp.encode_frame(payload, False, 0, 0, ppp.LINK_PROTOCOL), MPE2_DISCOVER)

  def test_escapes_round_trip(self):
    payload = bytes([0xAA, 0xBB, 0xCC, 0x00, 0xFF])
    frame = ppp.encode_frame(payload, True, ppp.VERSION, 0x9A, ppp.DATA_PROTOCOL)
    body = frame[1:-1]
    self.assertNotIn(0xAA, body)
    self.assertNotIn(0xCC, body)
    decoded = ppp.decode_frame(body)
    assert decoded is not None
    self.assertEqual(decoded["payload"], payload)
    self.assertEqual(decoded["session"], 0x9A)

  def test_corrupt_frame_refused(self):
    body = bytearray(MPE2_DISCOVER[1:-1])
    body[-1] ^= 1
    self.assertIsNone(ppp.decode_frame(bytes(body)))

  def test_ack_layout(self):
    protocol, flags, _version, code, length, sequence = ppp.SP0_HEADER.unpack(MPE2_ACK)
    self.assertEqual(flags, ppp.SP0_SYN | ppp.SP0_FIN | ppp.SP0_ACK)
    self.assertEqual((protocol, code, length), (0, 0, 0))
    self.assertEqual(sequence, struct.unpack("<I", bytes.fromhex("0da5a97a"))[0])


class FakeSerial:
  """An in-memory serial port: the test feeds what the instrument sends, and reads what the
  link wrote."""

  instances: List["FakeSerial"] = []

  def __init__(self):
    self.port: Optional[str] = None
    self.baudrate = 9600
    self.rts = True
    self.dtr = True
    self.timeout: Optional[float] = None
    self.write_timeout: Optional[float] = None
    self.is_open = False
    self.fail: Optional[BaseException] = None
    self.stuck = threading.Event()  # while set, read() ignores its timeout
    self.release = threading.Event()
    self._incoming = bytearray()
    self._cancelled = False
    self.written = bytearray()
    self._cond = threading.Condition()
    FakeSerial.instances.append(self)

  def open(self):
    self.is_open = True

  def close(self):
    with self._cond:
      self.is_open = False
      self._cond.notify_all()

  @property
  def in_waiting(self) -> int:
    with self._cond:
      return len(self._incoming)

  def cancel_read(self) -> None:
    # As pyserial's: a read waiting for bytes returns at once.
    self.release.set()
    with self._cond:
      self._cancelled = True
      self._cond.notify_all()

  def read(self, size: int) -> bytes:
    if self.stuck.is_set():
      self.release.wait()
    with self._cond:
      self._cancelled = False
      self._cond.wait_for(
        lambda: self._incoming or self.fail is not None or not self.is_open or self._cancelled,
        self.timeout,
      )
      if self.fail is not None:
        raise self.fail
      out = bytes(self._incoming[:size])
      del self._incoming[:size]
      return out

  def write(self, data: bytes) -> int:
    with self._cond:
      self.written += data
      self._cond.notify_all()
    return len(data)

  def feed(self, data: bytes) -> None:
    with self._cond:
      self._incoming += data
      self._cond.notify_all()

  def break_port(self, exc: BaseException) -> None:
    with self._cond:
      self.fail = exc
      self._cond.notify_all()

  def frames(self) -> List[dict]:
    """Every frame the link wrote, decoded."""
    with self._cond:
      data = bytes(self.written)
    out = []
    start = data.find(ppp.START)
    while start >= 0:
      end = data.find(ppp.END, start + 1)
      if end < 0:
        break
      frame = ppp.decode_frame(data[start + 1 : end])
      if frame is not None:
        out.append(frame)
      start = data.find(ppp.START, end + 1)
    return out

  def link_frames(self, kind: int) -> List[dict]:
    return [f for f in self.frames() if not f["data"] and f["payload"][0] == kind]

  def data_frames(self) -> List[dict]:
    return [f for f in self.frames() if f["data"]]


SESSION = 0x42


def link_frame(kind: int, session: int, version: int = 0) -> bytes:
  payload = bytes([kind, ppp.VERSION, session, 0]) + struct.pack("<HH", 1000, 1000)
  return ppp.encode_frame(payload, False, version, session if version else 0, ppp.LINK_PROTOCOL)


def sp0_frame(protocol: int, flags: int, sequence: int, body: bytes, code: int = 0) -> bytes:
  sp0 = ppp.SP0_HEADER.pack(protocol, flags, 0, code, len(body), sequence) + body
  return ppp.encode_frame(sp0, True, ppp.VERSION, SESSION, ppp.DATA_PROTOCOL)


class LinkTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    FakeSerial.instances = []
    patcher = mock.patch("serial.Serial", FakeSerial)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.link = ppp.HamiltonSerialLink("COM-FAKE", link_timeout=2.0)

  async def asyncTearDown(self):
    for fake in FakeSerial.instances:
      fake.release.set()
    await asyncio.wait_for(self.link.stop(), 5)

  async def wait_until(self, predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
      if time.monotonic() > deadline:
        self.fail("condition not met in time")
      await asyncio.sleep(0.01)

  async def bring_up(self) -> FakeSerial:
    """The instrument announces itself, the host accepts, the instrument confirms."""
    setup = asyncio.create_task(self.link.setup())
    await self.wait_until(lambda: bool(FakeSerial.instances))
    fake = FakeSerial.instances[-1]
    fake.feed(link_frame(ppp.DISCOVER, SESSION))
    await self.wait_until(lambda: bool(fake.link_frames(ppp.ACCEPT)))
    fake.feed(link_frame(ppp.CONFIRM, SESSION))
    await asyncio.wait_for(setup, 1.0)
    return fake

  async def test_handshake(self):
    fake = await self.bring_up()
    self.assertTrue(self.link.connected)
    self.assertEqual(
      (fake.port, fake.baudrate, fake.rts, fake.dtr), ("COM-FAKE", 921600, False, False)
    )
    accept = fake.link_frames(ppp.ACCEPT)[0]
    self.assertEqual(accept["payload"][2], SESSION)
    self.assertEqual(accept["version"], 0)

  async def test_no_link_refused(self):
    self.link.link_timeout = 0.3
    t0 = time.monotonic()
    with self.assertRaises(ConnectionError):
      await self.link.setup()
    self.assertLess(time.monotonic() - t0, 1.0)
    self.assertFalse(FakeSerial.instances[-1].is_open)

  async def test_data_frame_becomes_ip_packet(self):
    fake = await self.bring_up()
    fake.feed(sp0_frame(6, ppp.SP0_SYN | ppp.SP0_FIN, 0x1234, b"hello"))
    size = await self.link.read_exact(2, timeout=1.0)
    rest = await self.link.read_exact(struct.unpack("<H", size)[0], timeout=1.0)
    self.assertEqual(size + rest, struct.pack("<HBBH", 9, 6, ppp.IP_VERSION_BYTE, 0) + b"hello")
    await self.wait_until(lambda: bool(fake.data_frames()))
    ack = ppp.SP0_HEADER.unpack(fake.data_frames()[0]["payload"])
    self.assertTrue(ack[1] & ppp.SP0_ACK)
    self.assertEqual(ack[5], 0x1234 + 5)

  async def test_duplicate_dropped(self):
    fake = await self.bring_up()
    frame = sp0_frame(6, ppp.SP0_SYN | ppp.SP0_FIN, 7, b"ab")
    fake.feed(frame + frame + sp0_frame(6, ppp.SP0_SYN | ppp.SP0_FIN, 9, b"cd"))
    first = await self.link.read_exact(8, timeout=1.0)
    second = await self.link.read_exact(8, timeout=1.0)
    self.assertEqual((first[-2:], second[-2:]), (b"ab", b"cd"))

  async def test_write_waits_for_ack(self):
    fake = await self.bring_up()
    packet = struct.pack("<HBBH", 7, 6, ppp.IP_VERSION_BYTE, 0) + b"xyz"
    write = asyncio.create_task(self.link.write(packet))
    await self.wait_until(lambda: bool(fake.data_frames()))
    _, flags, _, _, length, sequence = ppp.SP0_HEADER.unpack_from(fake.data_frames()[0]["payload"])
    self.assertEqual((flags, length), (ppp.SP0_SYN | ppp.SP0_FIN, 3))
    fake.feed(sp0_frame(0, ppp.SP0_SYN | ppp.SP0_FIN | ppp.SP0_ACK, sequence + 3, b""))
    await asyncio.wait_for(write, 1.0)

  async def test_silent_read_times_out_without_a_new_thread(self):
    await self.bring_up()
    threads = threading.active_count()
    t0 = time.monotonic()
    with self.assertRaises(asyncio.TimeoutError):
      await asyncio.wait_for(self.link.read_exact(2), 0.5)
    with self.assertRaises(TimeoutError):
      await self.link.read_exact(2, timeout=0.2)
    self.assertLess(time.monotonic() - t0, 1.2)
    self.assertEqual(threading.active_count(), threads)

  async def test_stop_releases_a_pending_read(self):
    await self.bring_up()
    read = asyncio.create_task(self.link.read_exact(2, timeout=math.inf))
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    await asyncio.wait_for(self.link.stop(), 1.0)
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(read, 1.0)
    self.assertLess(time.monotonic() - t0, 0.5)
    fake = FakeSerial.instances[-1]
    self.assertFalse(fake.is_open)
    self.assertTrue(fake.link_frames(ppp.CLOSE))
    self.assertFalse(self.link.connected)

  async def test_stop_releases_a_pending_write(self):
    await self.bring_up()
    packet = struct.pack("<HBBH", 5, 6, ppp.IP_VERSION_BYTE, 0) + b"x"
    write = asyncio.create_task(self.link.write(packet))
    await asyncio.sleep(0.05)
    await asyncio.wait_for(self.link.stop(), 1.0)
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(write, 1.0)

  async def test_stop_twice_and_before_setup(self):
    await self.link.stop()
    await self.bring_up()
    await self.link.stop()
    await self.link.stop()

  async def test_stop_during_setup(self):
    setup = asyncio.create_task(self.link.setup())
    await self.wait_until(lambda: bool(FakeSerial.instances))
    await asyncio.wait_for(self.link.stop(), 1.0)
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(setup, 1.0)

  async def test_stop_returns_when_a_read_is_stuck(self):
    fake = await self.bring_up()
    self.link._STOP_TIMEOUT = 0.2
    fake.stuck.set()
    await asyncio.sleep(0.1)  # the port's read now ignores its timeout
    t0 = time.monotonic()
    await asyncio.wait_for(self.link.stop(), 2.0)  # io's stop cancels the read, then closes
    self.assertLess(time.monotonic() - t0, 1.5)
    self.assertFalse(fake.is_open)

  async def test_link_error_wakes_a_pending_read(self):
    fake = await self.bring_up()
    read = asyncio.create_task(self.link.read_exact(2, timeout=math.inf))
    await asyncio.sleep(0.05)
    fake.break_port(OSError("the port went away"))
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(read, 1.0)
    self.assertFalse(self.link.connected)
    with self.assertRaises(ConnectionError):
      await self.link.read_exact(2, timeout=1.0)

  async def test_instrument_close_fails_a_pending_read_and_stays_down(self):
    # a session the instrument dropped isn't brought back behind PLR's back.
    fake = await self.bring_up()
    read = asyncio.create_task(self.link.read_exact(2, timeout=math.inf))
    await asyncio.sleep(0.05)
    discovers = len(fake.link_frames(ppp.DISCOVER))
    fake.feed(link_frame(ppp.CLOSE, SESSION))
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(read, 1.0)
    self.assertFalse(self.link.connected)
    await asyncio.sleep(1.3)  # longer than the rediscovery interval
    self.assertEqual(len(fake.link_frames(ppp.DISCOVER)), discovers)
    await self.link.stop()
    FakeSerial.instances = []
    await self.bring_up()  # setup() brings it up again
    self.assertTrue(self.link.connected)

  async def test_message_over_several_frames(self):
    fake = await self.bring_up()
    fake.feed(sp0_frame(6, ppp.SP0_SYN, 0x10, b"he"))
    fake.feed(sp0_frame(6, 0, 0x11, b"llo "))
    fake.feed(sp0_frame(6, ppp.SP0_FIN, 0x12, b"world"))
    size = await self.link.read_exact(2, timeout=1.0)
    rest = await self.link.read_exact(struct.unpack("<H", size)[0], timeout=1.0)
    self.assertTrue(rest.endswith(b"hello world"))

  async def test_handshake_from_the_host(self):
    # An instrument still holding an old session doesn't announce itself; it answers the host's.
    setup = asyncio.create_task(self.link.setup())
    await self.wait_until(lambda: bool(FakeSerial.instances))
    fake = FakeSerial.instances[-1]
    await self.wait_until(lambda: bool(fake.link_frames(ppp.DISCOVER)), timeout=2.0)
    own = fake.link_frames(ppp.DISCOVER)[-1]["payload"][2]
    fake.feed(link_frame(ppp.ACCEPT, own))
    await asyncio.wait_for(setup, 1.0)
    self.assertTrue(self.link.connected)
    self.assertEqual(self.link._session, own)
    self.assertTrue(fake.link_frames(ppp.CONFIRM))

  async def test_crossed_discovers_take_one_way(self):
    # The instrument's discover while the host's waits for its answer is let be.
    setup = asyncio.create_task(self.link.setup())
    await self.wait_until(lambda: bool(FakeSerial.instances))
    fake = FakeSerial.instances[-1]
    await self.wait_until(lambda: bool(fake.link_frames(ppp.DISCOVER)), timeout=2.0)
    own = fake.link_frames(ppp.DISCOVER)[-1]["payload"][2]
    fake.feed(link_frame(ppp.DISCOVER, SESSION if SESSION != own else SESSION + 1))
    await asyncio.sleep(0.2)
    self.assertFalse(fake.link_frames(ppp.ACCEPT))  # not answered
    fake.feed(link_frame(ppp.ACCEPT, own))
    await asyncio.wait_for(setup, 1.0)
    self.assertEqual(self.link._session, own)

  async def test_reader_ending_fails_a_pending_read(self):
    await self.bring_up()
    read = asyncio.create_task(self.link.read_exact(2, timeout=math.inf))
    await asyncio.sleep(0.05)
    assert self.link._reader is not None
    self.link._reader.cancel()
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(read, 1.0)

  async def test_unanswered_keep_alives_fail_the_link(self):
    self.link.keep_alive_ms = 100
    self.link._SILENT_KEEP_ALIVES = 3
    await self.bring_up()  # the fake never acknowledges a keep-alive
    read = asyncio.create_task(self.link.read_exact(2, timeout=math.inf))
    with self.assertRaises(ConnectionError):
      await asyncio.wait_for(read, 2.0)
    self.assertFalse(self.link.connected)

  async def test_answered_keep_alives_keep_the_link(self):
    self.link.keep_alive_ms = 100
    self.link._SILENT_KEEP_ALIVES = 3
    fake = await self.bring_up()
    for _ in range(10):
      fake.feed(link_frame(ppp.KEEP_ALIVE_ACK, SESSION, version=ppp.VERSION))
      await asyncio.sleep(0.05)
    self.assertTrue(self.link.connected)

  async def test_two_messages_at_once_are_sent_one_after_the_other(self):
    fake = await self.bring_up()
    packet = struct.pack("<HBBH", 5, 6, ppp.IP_VERSION_BYTE, 0) + b"x"
    first = asyncio.create_task(self.link.write(packet))
    second = asyncio.create_task(self.link.write(packet))
    for n in (1, 2):
      await self.wait_until(lambda: len(fake.data_frames()) >= n)
      await asyncio.sleep(0.05)
      self.assertEqual(len(fake.data_frames()), n)  # the second waits for the first's ack
      sequence = ppp.SP0_HEADER.unpack_from(fake.data_frames()[-1]["payload"])[5]
      fake.feed(sp0_frame(0, ppp.SP0_SYN | ppp.SP0_FIN | ppp.SP0_ACK, sequence + 1, b""))
    await asyncio.wait_for(asyncio.gather(first, second), 1.0)

  async def test_a_failed_link_is_not_brought_up_again_behind_plrs_back(self):
    fake = await self.bring_up()
    fake.feed(link_frame(ppp.CLOSE, SESSION))
    await self.wait_until(lambda: not self.link.connected)
    fake.feed(link_frame(ppp.DISCOVER, SESSION + 1))  # the instrument announces itself again
    await asyncio.sleep(0.2)
    fake.feed(link_frame(ppp.CONFIRM, SESSION + 1))
    await asyncio.sleep(0.2)
    self.assertFalse(self.link.connected)
    self.assertEqual(len(fake.link_frames(ppp.ACCEPT)), 1)  # only setup's


if __name__ == "__main__":
  unittest.main()
