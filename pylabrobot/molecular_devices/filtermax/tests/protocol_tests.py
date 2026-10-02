import asyncio
import unittest

from pylabrobot.molecular_devices.filtermax.errors import (
  FilterMaxChecksumError,
  FilterMaxDeviceError,
  FilterMaxProtocolError,
  FilterMaxReadCancelled,
  FilterMaxTimeoutError,
)
from pylabrobot.molecular_devices.filtermax.protocol import (
  ACK,
  ENQ,
  EOT,
  FilterMaxTransport,
  _HandshakeTimeout,
  build_frame,
)


class FakeSerial:
  def __init__(self, incoming: bytes):
    self.incoming = bytearray(incoming)
    self.writes: list[bytes] = []

  async def write(self, data: bytes) -> None:
    self.writes.append(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self.incoming:
      return b""
    count = min(num_bytes, len(self.incoming))
    data = bytes(self.incoming[:count])
    del self.incoming[:count]
    return data


class YieldingSerial(FakeSerial):
  """Expose interleaving that a serial executor permits between every byte."""

  async def write(self, data: bytes) -> None:
    """Yield before writing to the simulated port."""
    await asyncio.sleep(0)
    await super().write(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    """Yield before returning the next bytes."""
    await asyncio.sleep(0)
    return await super().read(num_bytes)


def device_message(payload: str) -> bytes:
  return bytes((ENQ,)) + build_frame(payload) + bytes((EOT,))


class TestFilterMaxProtocol(unittest.IsolatedAsyncioTestCase):
  async def test_concurrent_exchanges_keep_their_own_responses(self) -> None:
    """Another request cannot consume an outstanding response's control bytes."""
    incoming = b"".join(
      bytes((ACK, ACK)) + device_message(payload) for payload in ("+ 24.6", "+ 24.7")
    )
    transport = FilterMaxTransport(YieldingSerial(incoming))  # type: ignore[arg-type]
    first, second = await asyncio.wait_for(
      asyncio.gather(transport.exchange("TG"), transport.exchange("TG")), timeout=1
    )
    self.assertEqual((first.payload, second.payload), ("+ 24.6", "+ 24.7"))

  async def test_eot_requires_a_final_frame(self) -> None:
    """Empty transactions and truncated continuation chains cannot return data."""
    for frames in (b"", build_frame("+ 24.6", final=False)):
      with self.subTest(frames=frames):
        incoming = bytes((ACK, ACK, ENQ)) + frames + bytes((EOT,))
        transport = FilterMaxTransport(FakeSerial(incoming))  # type: ignore[arg-type]
        with self.assertRaises(FilterMaxProtocolError):
          await transport.exchange("TG")

  async def test_frame_numbers_wrap_through_zero(self) -> None:
    """A valid continuation sequence can span more than seven frames."""
    frames = b"".join(
      build_frame(str(index), frame_number=(index + 1) % 8, final=index == 8) for index in range(9)
    )
    incoming = bytes((ACK, ACK, ENQ)) + frames + bytes((EOT,))
    transport = FilterMaxTransport(FakeSerial(incoming))  # type: ignore[arg-type]
    message = await transport.exchange("?")
    self.assertEqual((message.payload, message.frame_count), ("012345678", 9))

  def test_captured_checksum(self) -> None:
    self.assertEqual(build_frame("TG"), b"\x021TG\x03CF\r\n")
    self.assertEqual(build_frame("CS"), b"\x021CS\x03CA\r\n")
    self.assertEqual(build_frame("TS 25"), b"\x021TS 25\x0362\r\n")

  async def test_exchange_multiframe_identity(self) -> None:
    part_1 = "+\rAnthos Fluoro\rV1.2 b13 11.12.2014\r141\r1191\rDevice Code: 57855\rCPLD Ver"
    part_2 = "sion: 6"
    incoming = (
      bytes((ACK, ACK, ENQ))
      + build_frame(part_1, frame_number=1, final=False)
      + build_frame(part_2, frame_number=2)
      + bytes((EOT,))
    )
    io = FakeSerial(incoming)
    transport = FilterMaxTransport(io)  # type: ignore[arg-type]
    message = await transport.exchange("?")
    self.assertEqual(message.payload, part_1 + part_2)
    self.assertEqual(message.frame_count, 2)
    self.assertEqual(
      io.writes,
      [bytes((ENQ,)), build_frame("?"), bytes((EOT,)), bytes((ACK,)), bytes((ACK,)), bytes((ACK,))],
    )

  async def test_bad_checksum(self) -> None:
    frame = bytearray(build_frame("+ READY"))
    frame[-4:-2] = b"00"
    incoming = bytes((ACK, ACK, ENQ)) + bytes(frame) + bytes((EOT,))
    transport = FilterMaxTransport(FakeSerial(incoming))  # type: ignore[arg-type]
    with self.assertRaises(FilterMaxChecksumError):
      await transport.exchange("STAT")

  async def test_bad_continuation_frame_number(self) -> None:
    incoming = (
      bytes((ACK, ACK, ENQ))
      + build_frame("+ first", frame_number=1, final=False)
      + build_frame("second", frame_number=3)
      + bytes((EOT,))
    )
    transport = FilterMaxTransport(FakeSerial(incoming))  # type: ignore[arg-type]
    with self.assertRaisesRegex(FilterMaxProtocolError, "expected 2"):
      await transport.exchange("?")

  async def test_timeout_with_silent_device(self) -> None:
    transport = FilterMaxTransport(FakeSerial(b""))  # type: ignore[arg-type]
    with self.assertRaises(_HandshakeTimeout):
      await transport.send_request("STAT", timeout=0.001)

  async def test_timeout_after_initial_ack_is_not_handshake_timeout(self) -> None:
    """An acknowledged command must not be eligible for baud detection retries."""
    io = FakeSerial(bytes((ACK,)))
    transport = FilterMaxTransport(io)  # type: ignore[arg-type]
    with self.assertRaises(FilterMaxTimeoutError) as caught:
      await transport.send_request("STAT", timeout=0.001)
    self.assertNotIsInstance(caught.exception, _HandshakeTimeout)
    self.assertEqual(io.writes, [bytes((ENQ,)), build_frame("STAT")])

  async def test_device_error_and_cancel(self) -> None:
    e62 = "- E62: ABS Led 7 ADCValue: 16277 Gain 255 LedHighPoti 44 LedLowPoti 255"
    transport = FilterMaxTransport(
      FakeSerial(bytes((ACK, ACK)) + device_message(e62))  # type: ignore[arg-type]
    )
    with self.assertRaisesRegex(FilterMaxDeviceError, "E62"):
      await transport.exchange("ABS 1 2 450 595")

    transport = FilterMaxTransport(
      FakeSerial(bytes((ACK, ACK)) + device_message("- E140: "))  # type: ignore[arg-type]
    )
    with self.assertRaises(FilterMaxReadCancelled):
      await transport.exchange("STOP")
