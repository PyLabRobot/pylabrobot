import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.brooks.precise_flex import PreciseFlexDriver
from pylabrobot.brooks.precise_flex.driver.master import _drain_named_result
from pylabrobot.events import EventBus, use_event_bus


def _arm() -> PreciseFlexDriver:
  """A driver whose vision server connections the test attaches as mocks."""
  return PreciseFlexDriver(
    host="127.0.0.1", gripper_length=162.0, gripper_z_offset=0.0, closed_gripper_position=500.0
  )


def _framed(camera: int, jpeg: bytes = b"\xff\xd8\xff\xe0img\xff\xd9") -> bytes:
  """A ``Primary Image [camera]`` result on :1500, framed as the arm's stream frames it."""
  name = f"Primary Image [{camera}]".encode("ascii")
  header = bytes([0x01, len(name)]) + b"\x00\x00\x00" + struct.pack("<I", len(jpeg)) + b"\x00" * 7
  return header + name + jpeg


class TestVisionServerFraming(unittest.TestCase):
  """A result is taken off the image stream by the length its header announces."""

  def test_a_complete_result_is_taken_whole(self):
    jpeg = b"\xff\xd8\xff\xe0abc\xff\xd9"
    buf = bytearray(_framed(1, jpeg))
    self.assertEqual(_drain_named_result(buf), ("Primary Image [1]", jpeg))
    self.assertEqual(buf, bytearray())

  def test_an_incomplete_result_waits(self):
    buf = bytearray(_framed(1))[:-3]
    self.assertIsNone(_drain_named_result(buf))

  def test_a_stream_out_of_step_raises(self):
    oversized = bytes([0x01, 1, 0, 0, 0]) + struct.pack("<I", 16 * 1024 * 1024 + 1) + bytes(7)
    for name, buf in (("not a result start", b"\x99" + bytes(20)), ("too long", oversized)):
      with self.subTest(name), self.assertRaises(ValueError):
        _drain_named_result(bytearray(buf))


class TestDriverVisionServer(unittest.IsolatedAsyncioTestCase):
  """The driver's two vision server connections: commands on :1450, results off :1500."""

  def setUp(self):
    self.prop = MagicMock()
    self.prop.write = AsyncMock()
    self.prop.readline = AsyncMock(return_value=b"0\r\n")
    self.img = MagicMock()
    self.driver = _arm()
    self.driver._vision_server_io = self.prop  # type: ignore[assignment]
    self.driver._vision_image_io = self.img  # type: ignore[assignment]

  async def test_send_command_writes_line_and_parses_value(self):
    """To the vision server, `send_command` ends the line CRLF and returns the success value."""
    self.prop.readline = AsyncMock(return_value=b"0 5.3.3.0\r\n")
    self.assertEqual(
      await self.driver.send_command("property get system.engineversion", use_server="vision"),
      "5.3.3.0",
    )
    self.prop.write.assert_awaited_once_with(b"property get system.engineversion\r\n")

  async def test_a_vision_server_command_emits_the_firmware_command_events(self):
    self.driver._vision_host = "192.168.0.200"
    events: list = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    with use_event_bus(event_bus):
      await self.driver.send_command("property get system.engineversion", use_server="vision")
    self.assertEqual(
      [event.name for event in events],
      ["precise_flex.firmware_command.started", "precise_flex.firmware_command.completed"],
    )
    self.assertEqual(events[0].data["device"]["host"], "192.168.0.200")
    self.assertEqual(events[0].data["device"]["port"], 1450)

  async def test_concurrent_commands_do_not_interleave(self):
    """Two concurrent commands each keep their write paired with their own reply."""
    events: list = []

    async def write(data: bytes) -> None:
      events.append("w")
      await asyncio.sleep(0)

    async def readline() -> bytes:
      events.append("r")
      await asyncio.sleep(0)
      return b"0\r\n"

    self.prop.write, self.prop.readline = write, readline
    await asyncio.gather(
      self.driver.send_command("A", use_server="vision"),
      self.driver.send_command("B", use_server="vision"),
    )
    self.assertEqual(events, ["w", "r", "w", "r"])

  async def test_concurrent_result_reads_take_results_in_call_order(self):
    """Two concurrent readers each take a whole result, the first caller the first result."""
    first, second = _framed(1), _framed(2)
    chunks = iter([first[:10], first[10:], second[:10], second[10:]])

    async def read(n: int, timeout: float) -> bytes:
      await asyncio.sleep(0)
      return next(chunks)

    self.img.read = read
    a, b = await asyncio.gather(
      self.driver.read_next_vision_server_result(), self.driver.read_next_vision_server_result()
    )
    assert a is not None and b is not None
    self.assertEqual((a[0], b[0]), ("Primary Image [1]", "Primary Image [2]"))

  async def test_one_read_can_carry_several_results(self):
    two = _framed(2, b"\xff\xd8\xff\xe0a\xff\xd9") + _framed(1, b"\xff\xd8\xff\xe0b\xff\xd9")
    self.img.read = AsyncMock(side_effect=[two, b""])
    first = await self.driver.read_next_vision_server_result()
    second = await self.driver.read_next_vision_server_result()
    assert first is not None and second is not None
    self.assertEqual((first[0], second[0]), ("Primary Image [2]", "Primary Image [1]"))
    self.img.read.assert_awaited_once()

  async def test_a_result_cut_by_a_timeout_is_finished_by_the_next_read(self):
    whole = _framed(1, b"\xff\xd8\xff\xe0data\xff\xd9")
    self.img.read = AsyncMock(side_effect=[whole[:20], TimeoutError, whole[20:]])
    with self.assertRaises(TimeoutError):
      await self.driver.read_next_vision_server_result()
    recovered = await self.driver.read_next_vision_server_result()
    assert recovered is not None
    self.assertEqual(recovered[0], "Primary Image [1]")
