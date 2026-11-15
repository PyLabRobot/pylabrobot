"""Recorded classic INCO reads/park commands and simulated failure tests."""

import asyncio
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pylabrobot.io import UDP

from ._inco import IncoError, IncoProtocolError
from ._inco_udp import IncoUDP, _decode_numeric, _request, _response


def reply(sequence: int, data: bytes, error: int = 0) -> bytes:
  """Build a simulated ACK; recorded fixtures independently verify real framing."""
  header = bytes((6, sequence, error >> 8, error & 255, 1, 0))
  header += bytes(((255 - sum(header)) & 255,))
  return (
    b"\x10\x06"
    + header[1:].replace(b"\x10", b"\x10\x10")
    + data.replace(b"\x10", b"\x10\x10")
    + b"\x10\x03"
    + bytes(((252 - sum(data)) & 255,)).replace(b"\x10", b"\x10\x10")
  )


class ClassicFrameTests(unittest.TestCase):
  """Check escaping, exact packet lengths, checksums, and legacy numbers."""

  def test_captured_firmware_request_and_response(self) -> None:
    """Match the first successful hardware exchange byte for byte."""
    expected = bytes.fromhex(
      "100101020501ff0100f5536f6674776172652e5265766973696f6e2e44697370656e73657200100359"
    )
    self.assertEqual(_request(1, 2, 5, b"Software.Revision.Dispenser\0"), expected)
    self.assertEqual(
      _response(bytes.fromhex("10060100000100f7312e302e372e3337382d7472756e6b001003d7")),
      (1, 0, b"1.0.7.378-trunk\0"),
    )

  def test_escaped_data_and_checksum(self) -> None:
    """Literal control pairs and a DLE-valued checksum survive escaping."""
    data = b"\x10\x03\x10\x06\x10"
    self.assertEqual(_response(reply(16, data)), (16, 0, data))
    self.assertTrue(reply(1, b"\xec").endswith(b"\x10\x10"))
    self.assertEqual(_response(reply(1, b"\xec"))[2], b"\xec")

  def test_malformed_packets(self) -> None:
    """Reject truncated, corrupt, wrongly routed, and concatenated datagrams."""
    good = reply(1, b"FW\0")
    invalid = [
      b"",
      good[:1],
      good[:-1],
      good + b"extra",
      good + good,
      b"\x10\x15" + good[2:],
      good[:7] + bytes((good[7] ^ 1,)) + good[8:],
      good[:-1] + bytes((good[-1] ^ 1,)),
      good[:6] + b"\x01" + good[7:],
      good[:8] + b"\x10\x01" + good[8:],
      b"x" * 1025,
    ]
    for packet in invalid:
      with self.subTest(packet=packet), self.assertRaises(IncoProtocolError):
        _response(packet)

  def test_legacy_signed_fixed_and_double(self) -> None:
    """Mask the legacy sign flag rather than introducing a fractional bit."""
    self.assertEqual(_decode_numeric(bytes.fromhex("ffffffc1ffc000010d")), -62.0009765625)
    self.assertEqual(_decode_numeric(bytes.fromhex("ffffffff000000010d")), -1)
    self.assertEqual(_decode_numeric(bytes.fromhex("ffffffff00000000")), 4294967295)
    self.assertEqual(_decode_numeric(bytes.fromhex("ffffffff00000001")), -1)
    self.assertEqual(_decode_numeric(bytes.fromhex("ffff8001")), -0.5)
    self.assertEqual(_decode_numeric(struct.pack(">dB", -12.25, 9)), -12.25)
    for data in (b"", b"123", b"\0" * 8 + b"\x0f", struct.pack(">dB", float("nan"), 9)):
      with self.assertRaises(IncoProtocolError):
        _decode_numeric(data)


class IncoUDPTests(unittest.IsolatedAsyncioTestCase):
  """Exercise the driver through a mock transport; never connect to hardware."""

  async def asyncSetUp(self) -> None:
    """Use an asynchronous mock transport for each test."""
    self.inco = IncoUDP("example.invalid", timeout=0.03)
    self.io = MagicMock(spec=UDP)
    self.inco.io = self.io
    await self.inco.setup()

  async def test_recorded_hardware_reads(self) -> None:
    """Replay real serial, firmware, state, head, negative position and pressure."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_reads.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.io.read.return_value = bytes.fromhex(fixture["response"])
      if fixture["operation"] == "get_string":
        value: str | float = await self.inco.get_string(fixture["path"])
      else:
        value = await self.inco.get_number(fixture["path"])
      self.assertEqual(value, fixture["value"])
      self.io.write.assert_awaited_with(bytes.fromhex(fixture["request"]))

  async def test_recorded_initialize_and_park_commands(self) -> None:
    """Replay accepted start and park requests captured from the dry protocol."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_procedures.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.return_value = bytes.fromhex(fixture["response"])
      await self.inco.call(fixture["path"], *fixture["arguments"])
      self.io.write.assert_awaited_with(bytes.fromhex(fixture["request"]))

  async def test_recorded_plate_moves(self) -> None:
    """Replay numeric motion arguments from verified A1, A2, and A3 dry moves."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_plate_moves.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.return_value = bytes.fromhex(fixture["response"])
      await self.inco.call(fixture["path"], *fixture["arguments"])
      self.io.write.assert_awaited_with(bytes.fromhex(fixture["request"]))

  async def test_recorded_water_dispensing(self) -> None:
    """Replay verified pressure writes, timed valve calls, barriers, and discharge."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_water.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.return_value = bytes.fromhex(fixture["response"])
      if fixture["operation"] == "put_number":
        await self.inco.put_number(fixture["path"], fixture["arguments"][0])
      else:
        await self.inco.call(fixture["path"], *fixture["arguments"])
      self.io.write.assert_awaited_with(bytes.fromhex(fixture["request"]))

  async def test_recorded_water_clean_cycle(self) -> None:
    """Replay prime limits, waste positioning, purge/prime calls, barriers, and park."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_maintenance.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.return_value = bytes.fromhex(fixture["response"])
      if fixture["operation"] == "get_number":
        value = await self.inco.get_number(fixture["path"])
        self.assertEqual(value, fixture["value"])
      else:
        await self.inco.call(fixture["path"], *fixture["arguments"])
      self.io.write.assert_awaited_with(bytes.fromhex(fixture["request"]))

  async def test_recorded_plate_groups_run_controls_and_maintenance(self) -> None:
    """Replay accepted geometry reads, grouped doses, closure, flush, and idle purge."""
    fixture = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_features.json").read_text()
    )
    for exchange in fixture["exchanges"]:
      self.inco._sequence = exchange["sequence"] - 1
      self.io.read.return_value = bytes.fromhex(exchange["response"])
      if exchange["operation"] == "get_number":
        self.assertEqual(await self.inco.get_number(exchange["path"]), exchange["value"])
      else:
        await self.inco.call(exchange["path"], *exchange["arguments"])
      self.io.write.assert_awaited_with(bytes.fromhex(exchange["request"]))

  async def test_fixed_point_put_and_typed_procedures(self) -> None:
    """Verify payload layout recovered from the vendor's classic conversion routines."""
    self.io.read.return_value = reply(1, b"")
    await self.inco.put_number("Pressure", 0.3)
    self.io.write.assert_awaited_with(
      _request(1, 2, 0, bytes.fromhex("000000004ccccccc") + b"Pressure\0\x0d")
    )
    self.io.read.return_value = reply(2, b"\0" * 8 + b"\x0d")
    await self.inco.call("MoveXYZ", -12.25, 0.3, 0, "Slow")
    self.io.write.assert_awaited_with(
      _request(
        2,
        4,
        1,
        bytes.fromhex("00000004000f0007")
        + b"MoveXYZ\0"
        + bytes.fromhex("fffffff3c0000000000000004ccccccc0000000000000000")
        + b"Slow\0\0\0\0",
      )
    )
    self.io.read.return_value = reply(3, b"\0" * 4)
    await self.inco.call("SyncValves")
    self.io.write.assert_awaited_with(_request(3, 4, 0, b"\0" * 4 + b"SyncValves\0\0"))
    self.io.read.return_value = reply(4, b"\0" * 8 + b"\x0d")
    await self.inco.call("Init", "")
    self.io.write.assert_awaited_with(
      _request(4, 4, 1, bytes.fromhex("0000000100010000") + b"Init\0\0\0\0" + b"\0" * 4)
    )

  async def test_stale_packets_are_ignored_without_resending(self) -> None:
    """A duplicate older reply cannot satisfy the next request."""
    self.inco._sequence = 7
    self.io.read.side_effect = [reply(7, b"old\0"), reply(8, b"current\0")]
    self.assertEqual(await self.inco.get_string("Firmware"), "current")
    self.io.write.assert_awaited_once()

  async def test_sequence_rollover_opens_new_socket(self) -> None:
    """Use a fresh source port before reusing the controller's eight-bit sequence."""
    self.inco._sequence = 255
    self.io.read.return_value = reply(1, b"FW\0")
    self.assertEqual(await self.inco.get_string("Firmware"), "FW")
    self.io.stop.assert_awaited_once()
    self.assertEqual(self.io.setup.await_count, 2)
    self.assertEqual(self.inco._sequence, 1)

  async def test_controller_error_keeps_connection(self) -> None:
    """Preserve a complete error code and allow a subsequent explicit read."""
    self.io.read.side_effect = [reply(1, b"", 0x201), reply(2, b"FW\0")]
    with self.assertRaises(IncoError) as caught:
      await self.inco.get_string("Missing")
    self.assertEqual(caught.exception.code, 0x50201)
    self.assertEqual(caught.exception.path, "Missing")
    self.assertEqual(await self.inco.get_string("Firmware"), "FW")
    self.io.stop.assert_not_awaited()

  async def test_malformed_and_async_replies_close_socket(self) -> None:
    """Uncertain responses invalidate the socket rather than repeating an operation."""
    for packet in (b"garbage", reply(1, b"", 0x4FE), reply(1, b"unterminated")):
      await self.inco.setup()
      self.inco._sequence = 0
      self.io.read.return_value = packet
      with self.assertRaises(IncoProtocolError):
        await self.inco.get_string("Firmware")
      with self.assertRaises(RuntimeError):
        await self.inco.get_string("Firmware")
    self.assertEqual(self.io.write.await_count, 3)
    self.assertEqual(self.io.stop.await_count, 3)

  async def test_timeout_and_cancellation_never_repeat_pulse(self) -> None:
    """A possibly delivered pulse must not be sent again after response loss."""
    started = asyncio.Event()

    async def slow_read() -> bytes:
      """Wait for cancellation without returning a packet."""
      started.set()
      await asyncio.Event().wait()
      return b""

    self.io.read.side_effect = slow_read
    with self.assertRaises(asyncio.TimeoutError):
      await self.inco.call("Valve.Cmd.Dispens", 1000)
    await self.inco.setup()
    started.clear()
    task = asyncio.create_task(self.inco.call("Valve.Cmd.Dispens", 1000))
    await started.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertEqual(self.io.write.await_count, 2)
    self.assertEqual(self.io.stop.await_count, 2)

  async def test_invalid_arguments_do_not_send(self) -> None:
    """Validate strings, numeric bounds, argument count, and frame size first."""
    for value in (float("nan"), float("inf"), True, 2**31):
      with self.assertRaises(ValueError):
        await self.inco.put_number("Pressure", value)
    with self.assertRaises(ValueError):
      await self.inco.call("Pulse", *([1] * 17))
    with self.assertRaises(ValueError):
      await self.inco.get_string("bad\0path")
    with self.assertRaises(ValueError):
      await self.inco.get_string("x" * 494)
    self.io.write.assert_not_awaited()
