"""Native request fixtures and simulated INCO replies; never connects to hardware."""

import asyncio
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pylabrobot.io import Socket

from ._inco import Inco, IncoError, IncoProtocolError, _decode_number, _decode_string


def numeric(value: float, order: str = ">", fixed: bool = False) -> bytes:
  """Build a synthetic typed numeric response, independently of the encoder."""
  if fixed:
    integer = int(value // 1)
    return struct.pack(order + "H2xiI", 0x10D, integer, int((value - integer) * 2**32))
  return struct.pack(order + "H2xd", 0x109, value)


def reply(
  data: bytes,
  *,
  kind: int = 2,
  subtype: int = 1,
  order: str = ">",
  sequence: int = 1,
  error: int = 0,
) -> tuple[bytes, bytes]:
  """Build a synthetic complete, unfragmented response for transport tests."""
  header = bytearray(52)
  header[:20] = bytes.fromhex("100100820101ff01007a1003fc02030203060201")
  header[18:20] = bytes((kind, subtype))
  struct.pack_into(order + "IHHI", header, 20, sequence, 1, 0, error)
  header[32] = int(order == ">")
  struct.pack_into(order + "HHH", header, 46, 0, 8, len(data))
  return bytes(header), b"\0" * 8 + data


class IncoTests(unittest.IsolatedAsyncioTestCase):
  """Check packets, stream failures and serialization without a real socket."""

  async def asyncSetUp(self) -> None:
    self.inco = Inco("example.invalid", "CERTUS", timeout=0.1)
    self.io = MagicMock(spec=Socket)
    self.inco.io = self.io
    await self.inco.setup()

  async def test_native_request_fixtures(self) -> None:
    fixtures = json.loads((Path(__file__).parent / "fixtures/native_requests.json").read_text())
    for fixture in fixtures:
      with self.subTest(path=fixture["path"], arguments=fixture["arguments"]):
        self.inco._sequence = 0
        expected = bytes.fromhex(fixture["frame"])
        op, path, args = fixture["operation"], fixture["path"], fixture["arguments"]
        value = bytes.fromhex("0202000000040000") + b"FW1\0" if op == "gets" else numeric(4.0)
        self.io.read_exact.side_effect = reply(value, kind=expected[18], subtype=expected[19])
        if op == "get":
          self.assertEqual(await self.inco.get_number(path), 4.0)
        elif op == "gets":
          self.assertEqual(await self.inco.get_string(path), "FW1")
        elif op == "put":
          await self.inco.put_number(path, args[0])
        else:
          await self.inco.call(path, *args)
        self.io.write.assert_awaited_with(expected)

  async def test_sequence_increments_and_little_endian(self) -> None:
    self.io.read_exact.side_effect = (
      *reply(numeric(-12.25, "<"), order="<"),
      *reply(numeric(3.0), sequence=2),
    )
    self.assertEqual(await self.inco.get_number("Position"), -12.25)
    self.assertEqual(await self.inco.get_number("State"), 3.0)
    self.assertEqual(struct.unpack_from(">I", self.io.write.call_args.args[0], 20)[0], 2)

  async def test_controller_error_preserves_code_and_connection(self) -> None:
    self.io.read_exact.side_effect = (*reply(b"", error=0x50201), *reply(numeric(4.0), sequence=2))
    with self.assertRaises(IncoError) as caught:
      await self.inco.get_number("Missing")
    self.assertEqual(caught.exception.code, 0x50201)
    self.assertEqual(caught.exception.path, "Missing")
    self.assertEqual(await self.inco.get_number("State"), 4.0)
    self.io.stop.assert_not_awaited()

  async def test_malformed_headers_close_stream(self) -> None:
    good, _ = reply(numeric(4.0))
    mutations = {
      0: b"X",
      13: b"\x03",
      17: b"\x01",
      20: b"\xff",
      24: b"\x00\x02",
      26: b"\x00\x01",
      48: b"\xff\xff",
    }
    for offset, replacement in mutations.items():
      with self.subTest(offset=offset):
        await self.inco.setup()
        self.inco._sequence = 0
        header = bytearray(good)
        header[offset : offset + len(replacement)] = replacement
        self.io.read_exact.side_effect = [bytes(header)]
        with self.assertRaises(IncoProtocolError):
          await self.inco.get_number("State")
        with self.assertRaises(RuntimeError):
          await self.inco.get_number("State")
    self.assertEqual(self.io.stop.await_count, len(mutations))

  async def test_wrong_response_type_closes_stream(self) -> None:
    self.io.read_exact.side_effect = reply(numeric(4.0), subtype=5)
    with self.assertRaises(IncoProtocolError):
      await self.inco.get_number("State")
    self.io.stop.assert_awaited_once()

  async def test_disconnect_is_not_retried(self) -> None:
    self.io.read_exact.side_effect = ConnectionError("EOF")
    with self.assertRaises(ConnectionError):
      await self.inco.call("Valve.Cmd.Dispens", 1000.0)
    self.io.write.assert_awaited_once()
    self.io.stop.assert_awaited_once()

  async def test_socket_reads_a_reply_split_across_tcp_chunks(self) -> None:
    reader = asyncio.StreamReader()
    writer = MagicMock(spec=asyncio.StreamWriter)
    transport = Socket("test INCO", "example.invalid", 7979)
    transport._reader = reader
    transport._writer = writer
    self.inco.io = transport
    header, body = reply(numeric(4.0))
    wire = header + body

    async def feed() -> None:
      for start in range(0, len(wire), 7):
        reader.feed_data(wire[start : start + 7])
        await asyncio.sleep(0)

    feeder = asyncio.create_task(feed())
    try:
      self.assertEqual(await self.inco.get_number("State"), 4.0)
      writer.write.assert_called_once()
    finally:
      await feeder
      await self.inco.stop()

  async def test_timeout_and_cancel_close_stream(self) -> None:
    waiting = asyncio.Event()

    async def slow_read(size: int) -> bytes:
      waiting.set()
      await asyncio.Event().wait()
      return b""

    self.io.read_exact.side_effect = slow_read
    with self.assertRaises(asyncio.TimeoutError):
      await self.inco.get_number("State")
    self.io.stop.assert_awaited_once()
    await self.inco.setup()
    waiting.clear()
    task = asyncio.create_task(self.inco.call("Valve.Cmd.Dispens", 1000.0))
    await waiting.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(self.io.stop.await_count, 2)
    self.assertEqual(self.io.write.await_count, 2)

  async def test_concurrent_requests_do_not_interleave(self) -> None:
    waiting, release = asyncio.Event(), asyncio.Event()
    parts = iter((*reply(numeric(1.0)), *reply(numeric(2.0), sequence=2)))

    async def read(size: int) -> bytes:
      waiting.set()
      await release.wait()
      return next(parts)

    self.io.read_exact.side_effect = read
    first = asyncio.create_task(self.inco.get_number("One"))
    await waiting.wait()
    second = asyncio.create_task(self.inco.get_number("Two"))
    await asyncio.sleep(0)
    self.io.write.assert_awaited_once()
    release.set()
    self.assertEqual(await asyncio.gather(first, second), [1.0, 2.0])

  async def test_bad_arguments_do_not_write(self) -> None:
    for value in (float("nan"), float("inf"), 2**31, True):
      with self.subTest(value=value), self.assertRaises(ValueError):
        await self.inco.put_number("Pressure", value)
    with self.assertRaises(ValueError):
      await self.inco.call("Procedure", "bad\0string")
    with self.assertRaises(ValueError):
      await self.inco.get_number("x" * 1024)
    self.io.write.assert_not_awaited()


class ValueTests(unittest.TestCase):
  """Decode known layouts including negative fixed point and length prefixes."""

  def test_numeric_types(self) -> None:
    for order in (">", "<"):
      for fixed in (False, True):
        self.assertEqual(_decode_number(numeric(-1.25, order, fixed), order), -1.25)
    for bad in (b"", b"\0" * 12, numeric(float("nan"))):
      with self.assertRaises(IncoProtocolError):
        _decode_number(bad, ">")

  def test_string_lengths(self) -> None:
    for order in (">", "<"):
      self.assertEqual(
        _decode_string(struct.pack(order + "H2xHH", 0x202, 4, 0) + b"FW1\0", order), "FW1"
      )
    for bad in (
      b"",
      bytes.fromhex("0202000000080000") + b"FW1\0",
      bytes.fromhex("0202000000040000") + b"FW1X",
    ):
      with self.assertRaises(IncoProtocolError):
        _decode_string(bad, ">")

  def test_target_validation(self) -> None:
    for target in ("", "x" * 32, "server/device", "...", "bad\0name"):
      with self.assertRaises(ValueError):
        Inco("example.invalid", target)
