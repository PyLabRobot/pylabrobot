"""Native request fixtures and simulated INCO replies; never connects to hardware."""

import inspect
import json
import struct
from pathlib import Path
from typing import Any, Callable, Optional

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io import Socket
from pylabrobot.testing.concurrency import AnyioTestBase
from pylabrobot.testing.mock_io import MockIO

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


class _MockIncoSocket(MockIO):
  """Loop-agnostic INCO TCP double providing ``read_exact`` and disconnect tracking."""

  def __init__(self) -> None:
    """Initialize open/close counters and chunked response queue."""
    super().__init__()
    self.open_count = 0
    self.close_count = 0
    self._stream_open = False
    self.chunks: list[bytes] = []
    self.read_exact_handler: Optional[Callable[[int], Any]] = None

  async def _disconnect(self) -> None:
    """Record stream closure on lifespan exit."""
    await anyio.lowlevel.checkpoint()
    if self._stream_open:
      self._stream_open = False
      self.close_count += 1

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding) -> None:
    """Open the simulated stream and register shielded teardown."""
    await super()._enter_lifespan(stack)
    self.open_count += 1
    self._stream_open = True
    stack.push_shielded_async_callback(self._disconnect)

  async def read_exact(self, num_bytes: int, timeout: Optional[float] = None) -> bytes:
    if self.read_exact_handler is not None:
      result = self.read_exact_handler(num_bytes)
      if inspect.isawaitable(result):
        return bytes(await result)
      return bytes(result)
    if not self.chunks:
      raise ConnectionError("EOF")
    nxt = self.chunks.pop(0)
    if isinstance(nxt, BaseException):
      raise nxt
    return nxt


class TestInco(AnyioTestBase):
  """Check packets, stream failures and serialization without a real socket."""

  def _make_inco(self) -> tuple[Inco, _MockIncoSocket]:
    inco = Inco("example.invalid", "CERTUS", timeout=0.1)
    io = _MockIncoSocket()
    inco.io = io  # type: ignore[assignment]
    return inco, io

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.inco, self.io = self._make_inco()
    await stack.enter_async_context(self.inco.lifespan())

  async def test_native_request_fixtures(self) -> None:
    fixtures = json.loads((Path(__file__).parent / "fixtures/native_requests.json").read_text())
    for fixture in fixtures:
      self.inco._sequence = 0
      expected = bytes.fromhex(fixture["frame"])
      op, path, args = fixture["operation"], fixture["path"], fixture["arguments"]
      value = bytes.fromhex("0202000000040000") + b"FW1\0" if op == "gets" else numeric(4.0)
      self.io.chunks = list(reply(value, kind=expected[18], subtype=expected[19]))
      if op == "get":
        assert await self.inco.get_number(path) == 4.0
      elif op == "gets":
        assert await self.inco.get_string(path) == "FW1"
      elif op == "put":
        await self.inco.put_number(path, args[0])
      else:
        await self.inco.call(path, *args)
      assert self.io.write.calls[-1][0][0] == expected

  async def test_sequence_increments_and_little_endian(self) -> None:
    self.io.chunks = [
      *reply(numeric(-12.25, "<"), order="<"),
      *reply(numeric(3.0), sequence=2),
    ]
    assert await self.inco.get_number("Position") == -12.25
    assert await self.inco.get_number("State") == 3.0
    assert struct.unpack_from(">I", self.io.write.calls[-1][0][0], 20)[0] == 2

  async def test_controller_error_preserves_code_and_connection(self) -> None:
    self.io.chunks = [*reply(b"", error=0x50201), *reply(numeric(4.0), sequence=2)]
    with pytest.raises(IncoError) as caught:
      await self.inco.get_number("Missing")
    assert caught.value.code == 0x50201
    assert caught.value.path == "Missing"
    assert await self.inco.get_number("State") == 4.0
    assert self.io.close_count == 0

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
      inco, io = self._make_inco()
      async with inco:
        header = bytearray(good)
        header[offset : offset + len(replacement)] = replacement
        io.chunks = [bytes(header)]
        with pytest.raises(IncoProtocolError):
          await inco.get_number("State")
        assert io.close_count == 1
        with pytest.raises(RuntimeError):
          await inco.get_number("State")
      assert io.close_count == 1

  async def test_wrong_response_type_closes_stream(self) -> None:
    self.io.chunks = list(reply(numeric(4.0), subtype=5))
    with pytest.raises(IncoProtocolError):
      await self.inco.get_number("State")
    assert self.io.close_count == 1

  async def test_disconnect_is_not_retried(self) -> None:
    self.io.chunks = []
    with pytest.raises(ConnectionError):
      await self.inco.call("Valve.Cmd.Dispens", 1000.0)
    assert len(self.io.write.calls) == 1
    assert self.io.close_count == 1

  async def test_socket_reads_a_reply_split_across_tcp_chunks(self) -> None:
    header, body = reply(numeric(4.0))
    wire = header + body
    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
    async with listener:
      port = int(listener.extra(anyio.abc.SocketAttribute.local_port))

      async def serve_split(client: anyio.abc.ByteStream) -> None:
        async with client:
          await client.receive(4096)
          for start in range(0, len(wire), 7):
            await client.send(wire[start : start + 7])
            await anyio.sleep(0)

      async with anyio.create_task_group() as tg:
        tg.start_soon(listener.serve, serve_split)
        inco = Inco("127.0.0.1", "CERTUS", port=port, timeout=1.0)
        assert isinstance(inco.io, Socket)
        async with inco:
          assert await inco.get_number("State") == 4.0
        tg.cancel_scope.cancel()

  async def test_timeout_and_cancel_close_stream(self) -> None:
    waiting = anyio.Event()

    async def slow_read(size: int) -> bytes:
      waiting.set()
      await anyio.Event().wait()
      return b""

    inco1, io1 = self._make_inco()
    io1.read_exact_handler = slow_read
    async with inco1:
      with pytest.raises(TimeoutError):
        await inco1.get_number("State")
      assert io1.close_count == 1

    waiting = anyio.Event()
    inco2, io2 = self._make_inco()
    io2.read_exact_handler = slow_read
    async with inco2:
      scope = anyio.CancelScope()

      async def run_pulse() -> None:
        with scope:
          await inco2.call("Valve.Cmd.Dispens", 1000.0)

      async with anyio.create_task_group() as tg:
        tg.start_soon(run_pulse)
        await waiting.wait()
        scope.cancel()
      assert scope.cancelled_caught
      assert io2.close_count == 1
      assert len(io2.write.calls) == 1

      io2.read_exact_handler = None
      io2.chunks = list(reply(numeric(9.0), sequence=1))
      await inco2.reconnect()
      assert inco2.is_connected
      assert io2.open_count == 2
      assert await inco2.get_number("State") == 9.0
    assert io2.close_count == 2

  async def test_concurrent_requests_do_not_interleave(self) -> None:
    waiting, release = anyio.Event(), anyio.Event()
    parts = iter((*reply(numeric(1.0)), *reply(numeric(2.0), sequence=2)))

    async def read(size: int) -> bytes:
      waiting.set()
      await release.wait()
      return next(parts)

    self.io.read_exact_handler = read
    results: dict[str, float] = {}

    async def call_one(key: str, path: str) -> None:
      results[key] = await self.inco.get_number(path)

    async with anyio.create_task_group() as tg:
      tg.start_soon(call_one, "first", "One")
      await waiting.wait()
      tg.start_soon(call_one, "second", "Two")
      await anyio.sleep(0.005)
      assert len(self.io.write.calls) == 1
      release.set()
    assert [results["first"], results["second"]] == [1.0, 2.0]

  async def test_bad_arguments_do_not_write(self) -> None:
    for value in (float("nan"), float("inf"), 2**31, True):
      with pytest.raises(ValueError):
        await self.inco.put_number("Pressure", value)
    with pytest.raises(ValueError):
      await self.inco.call("Procedure", "bad\0string")
    with pytest.raises(ValueError):
      await self.inco.get_number("x" * 1024)
    assert self.io.write.calls == []


class TestIncoValues:
  """Decode known layouts including negative fixed point and length prefixes."""

  def test_numeric_types(self) -> None:
    for order in (">", "<"):
      for fixed in (False, True):
        assert _decode_number(numeric(-1.25, order, fixed), order) == -1.25
    for bad in (b"", b"\0" * 12, numeric(float("nan"))):
      with pytest.raises(IncoProtocolError):
        _decode_number(bad, ">")

  def test_string_lengths(self) -> None:
    for order in (">", "<"):
      assert _decode_string(struct.pack(order + "H2xHH", 0x202, 4, 0) + b"FW1\0", order) == "FW1"
    for bad in (
      b"",
      bytes.fromhex("0202000000080000") + b"FW1\0",
      bytes.fromhex("0202000000040000") + b"FW1X",
    ):
      with pytest.raises(IncoProtocolError):
        _decode_string(bad, ">")

  def test_target_validation(self) -> None:
    for target in ("", "x" * 32, "server/device", "...", "bad\0name"):
      with pytest.raises(ValueError):
        Inco("example.invalid", target)
