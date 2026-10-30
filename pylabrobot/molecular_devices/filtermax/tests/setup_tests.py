"""Baud detection and cleanup through the real FilterMax protocol transport."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.molecular_devices.filtermax import FilterMaxF5
from pylabrobot.molecular_devices.filtermax.errors import (
  FilterMaxChecksumError,
  FilterMaxDeviceError,
  FilterMaxIdentityError,
  FilterMaxProtocolError,
  FilterMaxTimeoutError,
)
from pylabrobot.molecular_devices.filtermax.protocol import (
  ACK,
  ENQ,
  EOT,
  FilterMaxTransport,
  build_frame,
)

IDENTITY = (
  "+\rAnthos Fluoro\rV1.2 b13 11.12.2014\r141\r1191\r"
  "Excitation Filter Slider Nr: 2\rEmission Filter Slider Nr: 1\r"
  "Barcode: not installed\rTemperatureControl: installed\rDevice Code: 57855\r"
  "Plate Control: TRUE\rPIC Firmware: + V1.0 b5 30.01.2006\rCPLD Version: 6"
)


def exchange_response(payload: str) -> bytes:
  """Build a complete acknowledged request and device response."""
  return bytes((ACK, ACK, ENQ)) + build_frame(payload) + bytes((EOT,))


class StartupSerial:
  """Expose responses by baud rate and require close before reopening."""

  def __init__(self, responses: dict[int, bytes | BaseException]):
    self.responses = responses
    self.baudrate = 38400
    self.is_open = False
    self.events: list[tuple[str, int]] = []
    self.writes: list[tuple[int, bytes]] = []
    self.incoming = bytearray()
    self.failure: BaseException | None = None

  async def setup(self) -> None:
    """Open at the configured rate with a fresh input stream."""
    assert not self.is_open, "Previous serial session was not closed"
    self.events.append(("open", self.baudrate))
    self.is_open = True
    response = self.responses[self.baudrate]
    self.failure = response if isinstance(response, BaseException) else None
    self.incoming = bytearray(response) if isinstance(response, bytes) else bytearray()

  async def stop(self) -> None:
    """Record which session was closed."""
    if self.is_open:
      self.events.append(("close", self.baudrate))
    self.is_open = False

  async def write(self, data: bytes) -> None:
    """Record transmissions with the active baud rate."""
    assert self.is_open
    self.writes.append((self.baudrate, data))

  async def read(self, num_bytes: int = 1) -> bytes:
    """Return input or simulate expiration of the transport read deadline."""
    assert self.is_open
    if self.failure is not None:
      raise self.failure
    if not self.incoming:
      raise FilterMaxTimeoutError("Simulated protocol read timeout")
    data = bytes(self.incoming[:num_bytes])
    del self.incoming[:num_bytes]
    return data


class TestFilterMaxStartup(unittest.IsolatedAsyncioTestCase):
  """Exercise startup without opening a physical serial port."""

  def make_driver(
    self, responses: dict[int, bytes | BaseException]
  ) -> tuple[FilterMaxF5, StartupSerial]:
    """Attach a baud-aware serial double to the production transport."""
    driver = FilterMaxF5("test", "COM1")
    io = StartupSerial(responses)
    driver.io = io  # type: ignore[assignment]
    driver._transport = FilterMaxTransport(io)  # type: ignore[arg-type]
    return driver, io

  async def test_connects_at_38400_without_fallback(self) -> None:
    """The preferred rate is retained when the F5 answers immediately."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    self.assertEqual(io.events, [("open", 38400)])
    self.assertEqual(io.baudrate, 38400)
    self.assertTrue(driver._setup_complete)
    assert driver._instrument_info is not None
    self.assertEqual(driver._instrument_info.serial_number, "1191")
    await driver.stop()
    self.assertFalse(io.is_open)

  async def test_falls_back_to_9600_and_retains_rate_for_status(self) -> None:
    """Only the silent handshake is retried; subsequent queries stay at 9600."""
    driver, io = self.make_driver(
      {
        38400: b"",
        9600: exchange_response(IDENTITY)
        + exchange_response("+ READY")
        + exchange_response("+ 0 0 0 0 0 0 0 0 0 0 0"),
      }
    )
    with self.assertLogs(
      "pylabrobot.molecular_devices.filtermax.filtermax_f5", level="INFO"
    ) as log:
      await driver.setup()
    self.assertEqual(io.events, [("open", 38400), ("close", 38400), ("open", 9600)])
    self.assertEqual(io.baudrate, 9600)
    self.assertTrue(driver._setup_complete)
    self.assertTrue((await driver.get_status()).ready)
    self.assertEqual([data for baud, data in io.writes if baud == 38400], [bytes((ENQ,))])
    self.assertEqual(
      [data[2:-5].decode("ascii") for _, data in io.writes if data.startswith(b"\x02")],
      ["?", "STAT", "SGS"],
    )
    self.assertIn("38400", "\n".join(log.output))
    self.assertIn("9600", "\n".join(log.output))
    await driver.stop()
    self.assertEqual(io.events[-1], ("close", 9600))
    self.assertFalse(driver._setup_complete)

  async def test_both_rates_silent_report_port_and_rates_and_close(self) -> None:
    """Exhausted detection reports actionable context without sending a command."""
    driver, io = self.make_driver({38400: b"", 9600: b""})
    with self.assertRaises(FilterMaxTimeoutError) as caught:
      await driver.setup()
    for expected in ("COM1", "38400", "9600"):
      self.assertIn(expected, str(caught.exception))
    self.assertEqual(
      io.events, [("open", 38400), ("close", 38400), ("open", 9600), ("close", 9600)]
    )
    self.assertEqual(io.writes, [(38400, bytes((ENQ,))), (9600, bytes((ENQ,)))])
    self.assertFalse(io.is_open)
    self.assertFalse(driver._setup_complete)
    self.assertIsNone(driver._instrument_info)

  async def test_wrong_identity_at_either_rate_is_not_retried(self) -> None:
    """Receiving an identity establishes a rate even when the device is wrong."""
    for baudrate in (38400, 9600):
      with self.subTest(baudrate=baudrate):
        responses: dict[int, bytes | BaseException] = {
          38400: b"",
          baudrate: exchange_response(IDENTITY.replace("57855", "1")),
        }
        driver, io = self.make_driver(responses)
        with self.assertRaises(FilterMaxIdentityError):
          await driver.setup()
        self.assertEqual(io.events[-1], ("close", baudrate))
        self.assertEqual(len(io.events), 2 if baudrate == 38400 else 4)
        self.assertFalse(io.is_open)
        self.assertFalse(driver._setup_complete)
        self.assertIsNone(driver._instrument_info)

  async def test_errors_after_a_response_do_not_trigger_fallback(self) -> None:
    """Protocol, device, and later timeout errors retain their original meaning."""
    corrupt = bytearray(exchange_response(IDENTITY))
    corrupt[-5:-3] = b"00"
    cases = (
      ("unexpected handshake byte", b"\x15", FilterMaxProtocolError),
      ("frame ACK timeout", bytes((ACK,)), FilterMaxTimeoutError),
      ("response ENQ timeout", bytes((ACK, ACK)), FilterMaxTimeoutError),
      ("partial response", bytes((ACK, ACK, ENQ, 2)), FilterMaxTimeoutError),
      ("checksum", bytes(corrupt), FilterMaxChecksumError),
      ("malformed identity", exchange_response("+ invalid"), FilterMaxProtocolError),
      ("device error", exchange_response("- E62: test fault"), FilterMaxDeviceError),
      ("serial read error", OSError("Disconnected"), OSError),
    )
    for label, incoming, error_type in cases:
      with self.subTest(case=label):
        driver, io = self.make_driver({38400: incoming})
        with self.assertRaises(error_type):
          await driver.setup()
        self.assertEqual(io.events, [("open", 38400), ("close", 38400)])
        self.assertFalse(io.is_open)
        self.assertFalse(driver._setup_complete)

  async def test_cancellation_at_either_rate_closes_without_retry(self) -> None:
    """Cancellation never becomes a baud detection attempt or a timeout."""
    for baudrate in (38400, 9600):
      with self.subTest(baudrate=baudrate):
        driver, io = self.make_driver({38400: b"", baudrate: asyncio.CancelledError()})
        with self.assertRaises(asyncio.CancelledError):
          await driver.setup()
        self.assertEqual(io.events[-1], ("close", baudrate))
        self.assertEqual(len(io.events), 2 if baudrate == 38400 else 4)
        self.assertFalse(io.is_open)
        self.assertFalse(driver._setup_complete)

  async def test_open_error_is_not_retried(self) -> None:
    """An unavailable port is not treated as an unsupported baud rate."""
    driver, io = self.make_driver({})
    with patch.object(
      io, "setup", AsyncMock(side_effect=PermissionError("Port unavailable"))
    ) as setup:
      with self.assertRaises(PermissionError):
        await driver.setup()
    setup.assert_awaited_once()
    self.assertFalse(io.is_open)

  async def test_write_timeout_is_not_retried(self) -> None:
    """A failure to transmit ENQ is not evidence of a silent reader."""
    driver, io = self.make_driver({38400: b""})
    with patch.object(io, "write", AsyncMock(side_effect=FilterMaxTimeoutError("Write failed"))):
      with self.assertRaisesRegex(FilterMaxTimeoutError, "Write failed"):
        await driver.setup()
    self.assertEqual(io.events, [("open", 38400), ("close", 38400)])
    self.assertFalse(io.is_open)

  async def test_repeated_setup_keeps_connected_session(self) -> None:
    """Calling setup twice cannot leak or reconfigure the connected serial session."""
    driver, io = self.make_driver({38400: b"", 9600: exchange_response(IDENTITY)})
    await driver.setup()
    writes = list(io.writes)
    await driver.setup()
    self.assertEqual(io.events, [("open", 38400), ("close", 38400), ("open", 9600)])
    self.assertEqual(io.writes, writes)
    self.assertEqual(io.baudrate, 9600)
    await driver.stop()

  async def test_failed_reconnect_clears_previous_identity(self) -> None:
    """A disconnected or failed session cannot retain a successful setup state."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    await driver.stop()
    io.responses = {38400: b"", 9600: b""}
    with self.assertRaises(FilterMaxTimeoutError):
      await driver.setup()
    self.assertFalse(driver._setup_complete)
    self.assertIsNone(driver._instrument_info)
    self.assertFalse(io.is_open)

  async def test_command_timeout_after_setup_does_not_redetect_or_retry(self) -> None:
    """Baud detection is confined to setup, including for measurement payloads."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    with self.assertRaises(FilterMaxTimeoutError):
      await driver._command("ABS 0 1 450 1 8 1 1 0 0 0 0 0 4 1 1 0 O e INFO")
    self.assertEqual(io.events, [("open", 38400), ("close", 38400)])
    self.assertEqual(io.writes[-1], (38400, bytes((ENQ,))))
    self.assertEqual(sum(data == bytes((ENQ,)) for _, data in io.writes), 2)
    await driver.stop()

  async def test_transport_fault_invalidates_connection_and_blocks_followup(self) -> None:
    """Incomplete transactions must close and block writes, including leftover EOT."""
    corrupt = bytearray(build_frame("+ 25"))
    corrupt[-4:-2] = b"00"
    cases = (
      ("checksum", bytes((ACK, ACK, ENQ)) + corrupt + bytes((EOT,)), FilterMaxChecksumError),
      (
        "frame number",
        bytes((ACK, ACK, ENQ)) + build_frame("+ 25", 2) + bytes((EOT,)),
        FilterMaxProtocolError,
      ),
      ("early EOT", bytes((ACK, ACK, ENQ, EOT)), FilterMaxProtocolError),
      ("missing EOT", bytes((ACK, ACK, ENQ)) + build_frame("+ 25"), FilterMaxTimeoutError),
      ("partial frame", bytes((ACK, ACK, ENQ, 2, 49)), FilterMaxTimeoutError),
      ("handshake byte", b"\x15", FilterMaxProtocolError),
      ("read error", OSError("Disconnected"), OSError),
    )
    for label, reply, error_type in cases:
      with self.subTest(case=label):
        driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
        await driver.setup()
        if isinstance(reply, BaseException):
          io.failure = reply
        else:
          io.incoming = bytearray(reply)
        with self.assertRaises(error_type):
          await driver.get_temperature()
        self.assertFalse(driver._setup_complete)
        self.assertIsNone(driver._instrument_info)
        self.assertFalse(io.is_open)
        if label == "checksum":
          self.assertEqual(io.incoming, bytes((EOT,)))
        writes = list(io.writes)
        with self.assertRaisesRegex(FilterMaxProtocolError, "setup"):
          await driver.get_temperature()
        self.assertEqual(io.writes, writes)
        self.assertFalse(driver._operation_lock.locked())

  async def test_explicit_setup_reconnects_after_fault(self) -> None:
    """Only explicit setup opens a fresh stream and verifies identity after failure."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    io.incoming = bytearray((ACK, ACK, ENQ, EOT))
    with self.assertRaises(FilterMaxProtocolError):
      await driver.get_temperature()
    io.responses = {38400: exchange_response(IDENTITY) + exchange_response("+ 26")}
    await driver.setup()
    self.assertTrue(driver._setup_complete)
    self.assertEqual(await driver.get_temperature(), 26)
    self.assertEqual(io.events, [("open", 38400), ("close", 38400), ("open", 38400)])

  async def test_failed_reconnect_after_fault_does_not_restore_connection(self) -> None:
    """A bad identity on reopening cannot make stale state usable."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    io.incoming = bytearray((ACK, ACK, ENQ, EOT))
    with self.assertRaises(FilterMaxProtocolError):
      await driver.get_temperature()
    io.responses = {38400: exchange_response("+ invalid")}
    with self.assertRaises(FilterMaxProtocolError):
      await driver.setup()
    self.assertFalse(driver._setup_complete)
    self.assertIsNone(driver._instrument_info)
    self.assertFalse(io.is_open)
    writes = list(io.writes)
    with self.assertRaisesRegex(FilterMaxProtocolError, "setup"):
      await driver.get_temperature()
    self.assertEqual(io.writes, writes)

  async def test_close_failure_preserves_original_protocol_error(self) -> None:
    """Failure to close cannot replace the cause or permit further transmissions."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    io.incoming = bytearray((ACK, ACK, ENQ, EOT))
    with patch.object(io, "stop", AsyncMock(side_effect=OSError("Close failed"))):
      with self.assertRaisesRegex(FilterMaxProtocolError, "before a final ETX"):
        await driver.get_temperature()
    self.assertFalse(driver._setup_complete)
    writes = list(io.writes)
    with self.assertRaisesRegex(FilterMaxProtocolError, "setup"):
      await driver.get_temperature()
    self.assertEqual(io.writes, writes)

    io.responses = {38400: exchange_response(IDENTITY)}
    await driver.setup()
    self.assertTrue(driver._setup_complete)
    self.assertTrue(io.is_open)

  async def test_write_error_after_setup_invalidates_connection(self) -> None:
    """An interrupted write cannot leave the established session usable."""
    driver, io = self.make_driver({38400: exchange_response(IDENTITY)})
    await driver.setup()
    with patch.object(io, "write", AsyncMock(side_effect=OSError("Write failed"))):
      with self.assertRaisesRegex(OSError, "Write failed"):
        await driver.get_temperature()
    self.assertFalse(driver._setup_complete)
    self.assertFalse(io.is_open)

  async def test_fully_received_errors_keep_connection_usable(self) -> None:
    """Firmware errors and malformed values do not imply lost frame boundaries."""
    for reply, error_type in (
      ("- E62: fault", FilterMaxDeviceError),
      ("- E140: ", FilterMaxDeviceError),
      ("+ invalid", FilterMaxProtocolError),
    ):
      with self.subTest(reply=reply):
        driver, io = self.make_driver(
          {
            38400: exchange_response(IDENTITY)
            + exchange_response(reply)
            + exchange_response("+ 26")
          }
        )
        await driver.setup()
        with self.assertRaises(error_type):
          await driver.get_temperature()
        self.assertTrue(driver._setup_complete)
        self.assertTrue(io.is_open)
        self.assertEqual(await driver.get_temperature(), 26)
