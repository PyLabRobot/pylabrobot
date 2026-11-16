"""Recorded classic INCO reads/park commands and simulated failure tests."""

import json
import struct
from pathlib import Path
from typing import Any

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

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


class _MockIncoUDP(MockIO):
  """Loop-agnostic UDP transport double tracking lifespan opens, closes, and reconnects."""

  def __init__(self) -> None:
    """Initialize open, close, and reconnect counters."""
    super().__init__()
    self.open_count = 0
    self.close_count = 0
    self._socket_open = False
    self.reconnect_count = 0

  async def _disconnect(self) -> None:
    """Record UDP socket closure on lifespan exit."""
    await anyio.lowlevel.checkpoint()
    if self._socket_open:
      self._socket_open = False
      self.close_count += 1

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the simulated UDP socket and register shielded teardown."""
    await super()._enter_lifespan(stack)
    self.open_count += 1
    self._socket_open = True
    stack.push_shielded_async_callback(self._disconnect)

  async def reconnect(self) -> None:
    """Record explicit ephemeral-port reconnection."""
    await anyio.lowlevel.checkpoint()
    self.reconnect_count += 1


class TestClassicFrames:
  """Check escaping, exact packet lengths, checksums, and legacy numbers."""

  def test_captured_firmware_request_and_response(self) -> None:
    """Match the first successful hardware exchange byte for byte."""
    expected = bytes.fromhex(
      "100101020501ff0100f5536f6674776172652e5265766973696f6e2e44697370656e73657200100359"
    )
    assert _request(1, 2, 5, b"Software.Revision.Dispenser\0") == expected
    assert _response(bytes.fromhex("10060100000100f7312e302e372e3337382d7472756e6b001003d7")) == (
      1,
      0,
      b"1.0.7.378-trunk\0",
    )

  def test_escaped_data_and_checksum(self) -> None:
    """Literal control pairs and a DLE-valued checksum survive escaping."""
    data = b"\x10\x03\x10\x06\x10"
    assert _response(reply(16, data)) == (16, 0, data)
    assert reply(1, b"\xec").endswith(b"\x10\x10")
    assert _response(reply(1, b"\xec"))[2] == b"\xec"

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
      with pytest.raises(IncoProtocolError):
        _response(packet)

  def test_legacy_signed_fixed_and_double(self) -> None:
    """Mask the legacy sign flag rather than introducing a fractional bit."""
    assert _decode_numeric(bytes.fromhex("ffffffc1ffc000010d")) == -62.0009765625
    assert _decode_numeric(bytes.fromhex("ffffffff000000010d")) == -1
    assert _decode_numeric(bytes.fromhex("ffffffff00000000")) == 4294967295
    assert _decode_numeric(bytes.fromhex("ffffffff00000001")) == -1
    assert _decode_numeric(bytes.fromhex("ffff8001")) == -0.5
    assert _decode_numeric(struct.pack(">dB", -12.25, 9)) == -12.25
    for data in (b"", b"123", b"\0" * 8 + b"\x0f", struct.pack(">dB", float("nan"), 9)):
      with pytest.raises(IncoProtocolError):
        _decode_numeric(data)


class TestIncoUDP(AnyioTestBase):
  """Exercise the driver through a mock transport; never connect to hardware."""

  def _make_inco(self) -> tuple[IncoUDP, _MockIncoUDP]:
    inco = IncoUDP("example.invalid", timeout=0.03)
    io = _MockIncoUDP()
    inco.io = io  # type: ignore[assignment]
    return inco, io

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    """Use an asynchronous mock transport for each test."""
    self.inco, self.io = self._make_inco()
    await stack.enter_async_context(self.inco.lifespan())

  async def test_recorded_hardware_reads(self) -> None:
    """Replay real serial, firmware, state, head, negative position and pressure."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_reads.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.io.read.side_effect = bytes.fromhex(fixture["response"])
      if fixture["operation"] == "get_string":
        value: str | float = await self.inco.get_string(fixture["path"])
      else:
        value = await self.inco.get_number(fixture["path"])
      assert value == fixture["value"]
      assert self.io.write.calls[-1][0][0] == bytes.fromhex(fixture["request"])

  async def test_recorded_initialize_and_park_commands(self) -> None:
    """Replay accepted start and park requests captured from the dry protocol."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_procedures.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.side_effect = bytes.fromhex(fixture["response"])
      await self.inco.call(fixture["path"], *fixture["arguments"])
      assert self.io.write.calls[-1][0][0] == bytes.fromhex(fixture["request"])

  async def test_recorded_plate_moves(self) -> None:
    """Replay numeric motion arguments from verified A1, A2, and A3 dry moves."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_plate_moves.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.side_effect = bytes.fromhex(fixture["response"])
      await self.inco.call(fixture["path"], *fixture["arguments"])
      assert self.io.write.calls[-1][0][0] == bytes.fromhex(fixture["request"])

  async def test_recorded_water_dispensing(self) -> None:
    """Replay verified pressure writes, timed valve calls, barriers, and discharge."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_water.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.side_effect = bytes.fromhex(fixture["response"])
      if fixture["operation"] == "put_number":
        await self.inco.put_number(fixture["path"], fixture["arguments"][0])
      else:
        await self.inco.call(fixture["path"], *fixture["arguments"])
      assert self.io.write.calls[-1][0][0] == bytes.fromhex(fixture["request"])

  async def test_recorded_water_clean_cycle(self) -> None:
    """Replay prime limits, waste positioning, purge/prime calls, barriers, and park."""
    fixtures = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_maintenance.json").read_text()
    )["exchanges"]
    for fixture in fixtures:
      self.inco._sequence = fixture["sequence"] - 1
      self.io.read.side_effect = bytes.fromhex(fixture["response"])
      if fixture["operation"] == "get_number":
        value = await self.inco.get_number(fixture["path"])
        assert value == fixture["value"]
      else:
        await self.inco.call(fixture["path"], *fixture["arguments"])
      assert self.io.write.calls[-1][0][0] == bytes.fromhex(fixture["request"])

  async def test_recorded_plate_groups_run_controls_and_maintenance(self) -> None:
    """Replay accepted geometry reads, grouped doses, closure, flush, and idle purge."""
    fixture = json.loads(
      Path(__file__).with_name("fixtures").joinpath("hardware_udp_features.json").read_text()
    )
    for exchange in fixture["exchanges"]:
      self.inco._sequence = exchange["sequence"] - 1
      self.io.read.side_effect = bytes.fromhex(exchange["response"])
      if exchange["operation"] == "get_number":
        assert await self.inco.get_number(exchange["path"]) == exchange["value"]
      else:
        await self.inco.call(exchange["path"], *exchange["arguments"])
      assert self.io.write.calls[-1][0][0] == bytes.fromhex(exchange["request"])

  async def test_fixed_point_put_and_typed_procedures(self) -> None:
    """Verify payload layout recovered from the vendor's classic conversion routines."""
    self.io.read.side_effect = reply(1, b"")
    await self.inco.put_number("Pressure", 0.3)
    assert self.io.write.calls[-1][0][0] == _request(
      1, 2, 0, bytes.fromhex("000000004ccccccc") + b"Pressure\0\x0d"
    )
    self.io.read.side_effect = reply(2, b"\0" * 8 + b"\x0d")
    await self.inco.call("MoveXYZ", -12.25, 0.3, 0, "Slow")
    assert self.io.write.calls[-1][0][0] == _request(
      2,
      4,
      1,
      bytes.fromhex("00000004000f0007")
      + b"MoveXYZ\0"
      + bytes.fromhex("fffffff3c0000000000000004ccccccc0000000000000000")
      + b"Slow\0\0\0\0",
    )
    self.io.read.side_effect = reply(3, b"\0" * 4)
    await self.inco.call("SyncValves")
    assert self.io.write.calls[-1][0][0] == _request(3, 4, 0, b"\0" * 4 + b"SyncValves\0\0")
    self.io.read.side_effect = reply(4, b"\0" * 8 + b"\x0d")
    await self.inco.call("Init", "")
    assert self.io.write.calls[-1][0][0] == _request(
      4, 4, 1, bytes.fromhex("0000000100010000") + b"Init\0\0\0\0" + b"\0" * 4
    )

  async def test_stale_packets_are_ignored_without_resending(self) -> None:
    """A duplicate older reply cannot satisfy the next request."""
    self.inco._sequence = 7
    self.io.read.side_effect = [reply(7, b"old\0"), reply(8, b"current\0")]
    assert await self.inco.get_string("Firmware") == "current"
    assert len(self.io.write.calls) == 1

  async def test_sequence_rollover_opens_new_socket(self) -> None:
    """Use a fresh source port before reusing the controller's eight-bit sequence."""
    self.inco._sequence = 255
    self.io.read.side_effect = reply(1, b"FW\0")
    assert await self.inco.get_string("Firmware") == "FW"
    assert self.io.reconnect_count == 1
    assert self.inco._sequence == 1

  async def test_controller_error_keeps_connection(self) -> None:
    """Preserve a complete error code and allow a subsequent explicit read."""
    self.io.read.side_effect = [reply(1, b"", 0x201), reply(2, b"FW\0")]
    with pytest.raises(IncoError) as caught:
      await self.inco.get_string("Missing")
    assert caught.value.code == 0x50201
    assert caught.value.path == "Missing"
    assert await self.inco.get_string("Firmware") == "FW"
    assert self.io.close_count == 0

  async def test_malformed_and_async_replies_close_socket(self) -> None:
    """Uncertain responses invalidate the socket rather than repeating an operation."""
    for packet in (b"garbage", reply(1, b"", 0x4FE), reply(1, b"unterminated")):
      inco, io = self._make_inco()
      async with inco:
        io.read.side_effect = packet
        with pytest.raises(IncoProtocolError):
          await inco.get_string("Firmware")
        assert io.close_count == 1
        assert len(io.write.calls) == 1
        with pytest.raises(RuntimeError):
          await inco.get_string("Firmware")

  async def test_timeout_and_cancellation_never_repeat_pulse(self) -> None:
    """A possibly delivered pulse must not be sent again after response loss."""
    started = anyio.Event()

    async def slow_read() -> bytes:
      started.set()
      await anyio.Event().wait()
      return b""

    inco1, io1 = self._make_inco()
    io1.read.side_effect = slow_read
    async with inco1:
      with pytest.raises(TimeoutError):
        await inco1.call("Valve.Cmd.Dispens", 1000)
      assert len(io1.write.calls) == 1
      assert io1.close_count == 1

    started = anyio.Event()
    inco2, io2 = self._make_inco()
    io2.read.side_effect = slow_read
    async with inco2:
      scope = anyio.CancelScope()

      async def run_dispense() -> None:
        with scope:
          await inco2.call("Valve.Cmd.Dispens", 1000)

      async with anyio.create_task_group() as tg:
        tg.start_soon(run_dispense)
        await started.wait()
        scope.cancel()
      assert scope.cancelled_caught
      assert len(io2.write.calls) == 1
      assert io2.close_count == 1

      io2.read.side_effect = reply(1, b"FW2\0")
      await inco2.reconnect()
      assert inco2.is_connected
      assert io2.open_count == 2
      assert await inco2.get_string("Firmware") == "FW2"
    assert io2.close_count == 2

  async def test_invalid_arguments_do_not_send(self) -> None:
    """Validate strings, numeric bounds, argument count, and frame size first."""
    for value in (float("nan"), float("inf"), True, 2**31):
      with pytest.raises(ValueError):
        await self.inco.put_number("Pressure", value)
    with pytest.raises(ValueError):
      await self.inco.call("Pulse", *([1] * 17))
    with pytest.raises(ValueError):
      await self.inco.get_string("bad\0path")
    with pytest.raises(ValueError):
      await self.inco.get_string("x" * 494)
    assert self.io.write.calls == []
