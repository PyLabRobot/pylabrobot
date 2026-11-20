"""AceBoard against a fake board: matching, two-stage replies, unsolicited messages, errors."""

import asyncio
import unittest
from typing import Callable, List, Optional

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.errors import (
  InterlockTrippedError,
  MotionError,
  NotHomedError,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.framing import frame, split
from pylabrobot.io.usb import USB

Handler = Callable[[bytes], List[bytes]]


def done(request: bytes, payload: bytes = b"") -> bytes:
  return request[:5] + b"\x02\x00" + payload


def accepted(request: bytes) -> bytes:
  return request[:5] + b"\x01\x00"


def recorder(into: List[bytes], answer: Handler = lambda m: []) -> Handler:
  """A handler that keeps every request in `into`, then answers with `answer`."""

  def handler(message: bytes) -> List[bytes]:
    into.append(message)
    return answer(message)

  return handler


def answer_done(message: bytes) -> List[bytes]:
  return [done(message)]


class FakeAceUSB(USB):
  """A board on the far side of the USB link: records transfers, answers through `handler`, and
  sends replies in 16-byte packets as a board does."""

  def __init__(self, handler: Optional[Handler] = None):
    super().__init__(0x0AB5, 0xACE0, "fake ACE board")
    self.handler: Handler = handler or answer_done
    self.transfers: List[bytes] = []
    self._packets: "asyncio.Queue[bytes]" = asyncio.Queue()

  async def setup(self, empty_buffer=True):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, timeout: Optional[float] = None):
    self.transfers.append(data)
    for message in split(data):
      for reply in self.handler(message):
        self.push(reply)

  def push(self, message: bytes) -> None:
    framed = frame(message)
    for i in range(0, len(framed), 16):
      self._packets.put_nowait(framed[i : i + 16])

  async def read(self, timeout=None, size=None, endpoint=None) -> bytes:
    try:
      return await asyncio.wait_for(self._packets.get(), timeout=0.01)
    except asyncio.TimeoutError:
      raise TimeoutError("no packet") from None


class BoardTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.usb = FakeAceUSB()
    self.board = AceBoard(io=self.usb, default_timeout=1)
    await self.board.setup()

  async def asyncTearDown(self) -> None:
    await self.board.stop()

  async def test_simple_request(self) -> None:
    self.usb.handler = lambda m: [done(m, b"\x80\x00")]
    reply = await self.board.send(p.get_module_id())
    self.assertEqual(p.parse_module_id(reply.payload).type, "chassis")
    self.assertEqual(self.usb.transfers, [frame(p.get_module_id().encode(1))])

  async def test_replies_out_of_order_match_their_requests(self) -> None:
    held: List[bytes] = []
    self.usb.handler = recorder(held)
    task = asyncio.create_task(
      self.board.send_many([p.get_current_position(p.Device.MOTOR, axis) for axis in range(1, 9)])
    )
    while len(held) < 8:
      await asyncio.sleep(0)
    for message in reversed(held):
      self.usb.push(done(message, message[1].to_bytes(4, "little")))
    replies = await task
    self.assertEqual([p.parse_s32(r.payload) for r in replies], list(range(1, 9)))
    self.assertEqual(len(self.usb.transfers), 1)  # packed together

  async def test_two_stage_reply_waits_for_done(self) -> None:
    held: List[bytes] = []
    self.usb.handler = recorder(held, lambda m: [accepted(m)])
    task = asyncio.create_task(self.board.send(p.move_all(5000)))
    await asyncio.sleep(0.05)
    self.assertFalse(task.done())
    self.usb.push(done(held[0]))
    self.assertEqual((await task).status, p.Status.DONE)

  async def test_unsolicited_messages_go_to_subscribers_between_replies(self) -> None:
    seen: List[p.Reply] = []
    self.board.subscribe(seen.append)
    host_alive = bytes.fromhex("0000000040FE00")
    emcy = bytes.fromhex("0000000031FF007E0010013704110000")
    self.usb.handler = lambda m: [host_alive, emcy, done(m)]
    await self.board.send(p.get_module_serial_number())
    await asyncio.sleep(0.05)
    self.assertEqual([(r.device, r.opcode) for r in seen], [p.HOST_ALIVE, p.EMCY])
    self.assertEqual(p.parse_emcy(seen[1].data).device_error, 0x0437)

  async def test_error_status_raises(self) -> None:
    self.usb.handler = lambda m: [m[:5] + b"\x00\x32"]
    with self.assertRaises(NotHomedError):
      await self.board.send(p.set_motor_move_parameters(1, 0, 1, 1, 1))

  async def test_failed_move_all_raises_with_its_axes(self) -> None:
    def handler(m: bytes) -> List[bytes]:
      axis = bytes([0x11, 0x0A, 0x00, m[3], 0x50, 0x02, 0x3C, 0x00, 0x01, 0x00, 0x00])
      return [accepted(m), axis, m[:5] + b"\x02\x38"]

    self.usb.handler = handler
    with self.assertRaises(MotionError) as ctx:
      await self.board.send(p.move_all(5000))
    self.assertEqual([e.device_error for e in ctx.exception.axis_errors], [0x100])
    self.assertIn("motion error window exceeded", str(ctx.exception))

  async def test_interlock_on_completion(self) -> None:
    self.usb.handler = lambda m: [accepted(m), m[:5] + b"\x02\x0f\x07\x04\x00\x00"]
    with self.assertRaises(InterlockTrippedError) as ctx:
      await self.board.send(p.move(1, 30000))
    self.assertEqual(ctx.exception.device_error, 0x407)

  async def test_timeout_releases_the_sequence(self) -> None:
    self.usb.handler = lambda m: []
    with self.assertRaises(TimeoutError):
      await self.board.send(p.get_module_id(), timeout=0.05)
    self.assertEqual(self.board._pending, {})

  async def test_sequence_wraps_past_zero_and_skips_waiting_ones(self) -> None:
    self.board._sequence = 254
    seen_requests: List[bytes] = []
    self.usb.handler = recorder(seen_requests, lambda m: [done(m)])
    for _ in range(3):
      await self.board.send(p.get_module_id())
    self.assertEqual([m[3] for m in seen_requests], [255, 1, 2])

    held: List[bytes] = []
    self.usb.handler = recorder(held)
    waiting = asyncio.create_task(self.board.send(p.get_module_id()))  # takes 3
    await asyncio.sleep(0)
    self.board._sequence = 2
    self.usb.handler = recorder(seen_requests, lambda m: [done(m)])
    await self.board.send(p.get_module_serial_number())
    self.assertEqual(seen_requests[-1][3], 4)
    self.usb.push(done(held[0]))
    await waiting

  async def test_stop_fails_waiting_requests(self) -> None:
    self.usb.handler = lambda m: []
    task = asyncio.create_task(self.board.send(p.get_module_id()))
    await asyncio.sleep(0.02)
    await self.board.stop()
    with self.assertRaises(ConnectionError):
      await task
    await self.board.setup()  # so tearDown can stop it again


if __name__ == "__main__":
  unittest.main()
