"""Exercise operation ownership and cooperative cancellation over a yielding serial stream."""

import asyncio
import unittest
from pathlib import Path
from typing import Optional

from pylabrobot.molecular_devices.filtermax import (
  FilterMaxF5,
  FilterMaxReadCancelled,
  FilterMaxTimeoutError,
  FilterSlideCatalog,
  KineticTiming,
)
from pylabrobot.molecular_devices.filtermax.protocol import (
  ACK,
  ENQ,
  EOT,
  FilterMaxTransport,
  build_frame,
)
from pylabrobot.molecular_devices.filtermax.tests.filtermax_f5_tests import (
  FakeSerial,
  device_message,
  exchange_response,
  host_payloads,
  make_plate,
  measurement_response,
)

ROW = "+ " + " ".join("100" for _ in range(12))
PREPARATION = b"".join(exchange_response(reply) for reply in ("+ 2 1", "+", "+", "+ -67"))
RECOVERY = exchange_response("- E140: ") + exchange_response("+ 6512")


class PausingSerial(FakeSerial):
  """Yield at every byte and optionally pause immediately before a specified read."""

  def __init__(self, incoming: bytes, pause_at: Optional[int] = None, empty_on_resume=False):
    """Configure a deterministic boundary at which another task may request cancellation."""
    super().__init__(incoming)
    self.pause_at = pause_at
    self.empty_on_resume = empty_on_resume
    self.bytes_read = 0
    self.paused = asyncio.Event()
    self.resume = asyncio.Event()
    self.stop_read_positions: list[int] = []

  async def write(self, data: bytes) -> None:
    """Record exactly how much of a device response preceded STOP."""
    await asyncio.sleep(0)
    if data == build_frame("STOP"):
      self.stop_read_positions.append(self.bytes_read)
    await super().write(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    """Pause once, then deliver bytes while retaining real serial scheduling points."""
    await asyncio.sleep(0)
    if self.bytes_read == self.pause_at:
      self.pause_at = None
      self.paused.set()
      await self.resume.wait()
      if self.empty_on_resume:
        return b""
    if not self.incoming:
      raise FilterMaxTimeoutError("Scripted serial response exhausted")
    data = await super().read(num_bytes)
    self.bytes_read += len(data)
    return data


class TestFilterMaxOperations(unittest.IsolatedAsyncioTestCase):
  """Test public APIs without creating a hardware connection."""

  def setUp(self) -> None:
    """Use the captured plate as a PLR resource."""
    self.plate = make_plate()

  def make_driver(self, io: PausingSerial) -> FilterMaxF5:
    """Attach the byte-level serial double to the production transport."""
    catalog = FilterSlideCatalog.from_softmax_xml(Path(__file__).with_name("filtermax_slides.xml"))
    reader = FilterMaxF5("test", "COM1", catalog)
    reader.io = io  # type: ignore[assignment]
    reader._transport = FilterMaxTransport(io)  # type: ignore[arg-type]
    return reader

  async def test_query_waits_for_measurement_and_tray_recovery(self) -> None:
    """Temperature polling cannot consume measurement rows or run before ejection."""
    first = PREPARATION + measurement_response(ROW, "+ INFO")
    io = PausingSerial(
      first + exchange_response("+ 25") + exchange_response("+ 6512") + exchange_response("+ 26"),
      pause_at=len(PREPARATION) + 2,
    )
    reader = self.make_driver(io)
    read = asyncio.create_task(
      reader.read_absorbance(self.plate, 450, wells=[self.plate.get_well("A1")], read_height=10.27)
    )
    await asyncio.wait_for(io.paused.wait(), 1)
    query = asyncio.create_task(reader.get_temperature())
    await asyncio.sleep(0)
    self.assertFalse(query.done())
    io.resume.set()
    result, temperature = await asyncio.wait_for(asyncio.gather(read, query), 2)
    self.assertEqual(result[0].data[0][0], 0.1)
    self.assertEqual(temperature, 26)
    self.assertEqual(host_payloads(io)[-3:], ["TG", "E P", "TG"])

  async def test_cancel_at_result_boundaries(self) -> None:
    """Cancellation before results, after a row, or within a frame drains safely."""
    continuation = (
      bytes((ENQ,))
      + build_frame(ROW[:15], final=False)
      + build_frame(ROW[15:], frame_number=2)
      + bytes((EOT,))
    )
    cases = (
      ("before_first", b"", 0, True),
      ("after_row", device_message(ROW), len(device_message(ROW)) - 1, False),
      ("within_continuation", continuation, 12, False),
    )
    for mode in ("absorbance", "luminescence"):
      for label, message, offset, empty in cases:
        with self.subTest(mode=mode, boundary=label):
          prefix = PREPARATION + bytes((ACK, ACK))
          io = PausingSerial(
            prefix + message + RECOVERY, pause_at=len(prefix) + offset, empty_on_resume=empty
          )
          reader = self.make_driver(io)
          plate = self.plate
          read = asyncio.create_task(
            reader.read_absorbance(
              plate,
              450,
              wells=[self.plate.get_well("A1"), self.plate.get_well("B1")],
              read_height=10.27,
            )
            if mode == "absorbance"
            else reader.read_luminescence(
              plate, wells=[self.plate.get_well("A1"), self.plate.get_well("B1")]
            )
          )
          await asyncio.wait_for(io.paused.wait(), 1)
          cancellations = [asyncio.create_task(reader.cancel_read()) for _ in range(2)]
          await asyncio.sleep(0)
          io.resume.set()
          outcomes = await asyncio.wait_for(
            asyncio.gather(read, *cancellations, return_exceptions=True), 2
          )
          self.assertIsInstance(outcomes[0], FilterMaxReadCancelled)
          self.assertEqual(outcomes[1:], [None, None])
          self.assertEqual(host_payloads(io)[-2:], ["STOP", "E P"])
          self.assertEqual(host_payloads(io).count("STOP"), 1)
          self.assertGreaterEqual(io.stop_read_positions[0], len(prefix) + len(message))
          self.assertFalse(reader._operation_lock.locked())

  async def test_cancel_during_preparation_sends_no_measurement_or_stop(self) -> None:
    """A preparation response is drained before cancellation prevents the next step."""
    slides = exchange_response("+ 2 1")
    io = PausingSerial(slides + exchange_response("+ 6512"), pause_at=len(slides) - 1)
    reader = self.make_driver(io)
    read = asyncio.create_task(
      reader.read_absorbance(self.plate, 450, wells=[self.plate.get_well("A1")], read_height=10.27)
    )
    await asyncio.wait_for(io.paused.wait(), 1)
    cancel = asyncio.create_task(reader.cancel_read())
    await asyncio.sleep(0)
    io.resume.set()
    results = await asyncio.wait_for(asyncio.gather(read, cancel, return_exceptions=True), 2)
    self.assertIsInstance(results[0], FilterMaxReadCancelled)
    self.assertEqual(host_payloads(io), ["CS", "E P"])

  async def test_kinetic_cancellation_never_starts_another_cycle(self) -> None:
    """Cancellation works during an interval and before an overdue cycle."""
    from unittest.mock import patch

    for interval in (60, 1e-9):
      for mode in ("absorbance", "luminescence"):
        with self.subTest(interval=interval, mode=mode):
          io = PausingSerial(
            PREPARATION + measurement_response(ROW) + exchange_response("+ 25") + RECOVERY
          )
          reader = self.make_driver(io)
          waiting = asyncio.Event()
          release = asyncio.Event()
          original_wait = reader._wait_until

          async def wait_at_cycle(target: float) -> None:
            """Expose the interval boundary without relying on a wall-clock sleep."""
            waiting.set()
            if interval < 1:
              await release.wait()
            await original_wait(target)

          with patch.object(reader, "_wait_until", side_effect=wait_at_cycle):
            plate = self.plate
            timing = KineticTiming(interval=interval, reads=3)
            read = asyncio.create_task(
              reader.read_absorbance(
                plate, 450, wells=[self.plate.get_well("A1")], kinetic=timing, read_height=10.27
              )
              if mode == "absorbance"
              else reader.read_luminescence(
                plate, wells=[self.plate.get_well("A1")], kinetic=timing
              )
            )
            await asyncio.wait_for(waiting.wait(), 1)
            cancel = asyncio.create_task(reader.cancel_read())
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.wait_for(
              asyncio.gather(read, cancel, return_exceptions=True), 2
            )
          self.assertIsInstance(results[0], FilterMaxReadCancelled)
          self.assertEqual(sum(p.startswith(("ABS ", "LUM ")) for p in host_payloads(io)), 1)
          self.assertEqual(host_payloads(io)[-2:], ["STOP", "E P"])

  async def test_repeated_task_cancellation_keeps_ownership_through_cleanup(self) -> None:
    """Interrupting the caller cannot leave a half-read frame for a queued query."""
    message = device_message(ROW)
    prefix = PREPARATION + bytes((ACK, ACK))
    io = PausingSerial(
      prefix + message + RECOVERY + exchange_response("+ 26"), pause_at=len(prefix) + 12
    )
    reader = self.make_driver(io)
    read = asyncio.create_task(
      reader.read_absorbance(
        self.plate,
        450,
        wells=[self.plate.get_well("A1"), self.plate.get_well("B1")],
        read_height=10.27,
      )
    )
    await asyncio.wait_for(io.paused.wait(), 1)
    query = asyncio.create_task(reader.get_temperature())
    for _ in range(3):
      read.cancel()
      await asyncio.sleep(0)
    self.assertTrue(reader._operation_lock.locked())
    self.assertFalse(query.done())
    io.resume.set()
    results = await asyncio.wait_for(asyncio.gather(read, query, return_exceptions=True), 2)
    self.assertIsInstance(results[0], asyncio.CancelledError)
    self.assertEqual(results[1], 26)
    self.assertEqual(host_payloads(io)[-3:], ["STOP", "E P", "TG"])
    self.assertFalse(reader._operation_lock.locked())

  async def test_shutdown_cancels_read_before_closing_serial(self) -> None:
    """Shutdown waits for the active owner to stop and eject before releasing the port."""
    message = device_message(ROW)
    prefix = PREPARATION + bytes((ACK, ACK))
    io = PausingSerial(prefix + message + RECOVERY, pause_at=len(prefix) + 10)
    reader = self.make_driver(io)
    read = asyncio.create_task(
      reader.read_luminescence(
        self.plate, wells=[self.plate.get_well("A1"), self.plate.get_well("B1")]
      )
    )
    await asyncio.wait_for(io.paused.wait(), 1)
    stop = asyncio.create_task(reader.stop())
    await asyncio.sleep(0)
    self.assertFalse(io.stop_called)
    io.resume.set()
    results = await asyncio.wait_for(asyncio.gather(read, stop, return_exceptions=True), 2)
    self.assertIsInstance(results[0], FilterMaxReadCancelled)
    self.assertIsNone(results[1])
    self.assertEqual(host_payloads(io)[-2:], ["STOP", "E P"])
    self.assertTrue(io.stop_called)

  async def test_shaking_is_cancelled_by_its_owner(self) -> None:
    """Stopping a standalone shake uses its owner instead of a competing exchange."""
    io = PausingSerial(
      bytes((ACK, ACK)) + exchange_response("- E140: "), pause_at=2, empty_on_resume=True
    )
    reader = self.make_driver(io)
    shake = asyncio.create_task(reader.start_shaking("linear", "medium", 30))
    await asyncio.wait_for(io.paused.wait(), 1)
    stop = asyncio.create_task(reader.stop_shaking())
    await asyncio.sleep(0)
    io.resume.set()
    results = await asyncio.wait_for(asyncio.gather(shake, stop, return_exceptions=True), 2)
    self.assertIsInstance(results[0], FilterMaxReadCancelled)
    self.assertEqual(host_payloads(io), ["SHAKE X 0 3 40 30", "STOP"])

  async def test_framing_failure_skips_recovery_and_releases_waiters(self) -> None:
    """A malformed exchange cannot be followed by STOP, tray recovery, or queued queries."""
    from pylabrobot.molecular_devices.filtermax import FilterMaxProtocolError

    malformed = bytes((ACK, ACK, ENQ, EOT))
    io = PausingSerial(
      PREPARATION
      + malformed
      + exchange_response("- E62: stop failed")
      + exchange_response("- E21: tray failed")
      + exchange_response("+ 26"),
      pause_at=len(PREPARATION) + 3,
    )
    reader = self.make_driver(io)
    read = asyncio.create_task(
      reader.read_absorbance(self.plate, 450, wells=[self.plate.get_well("A1")], read_height=10.27)
    )
    await asyncio.wait_for(io.paused.wait(), 1)
    cancel = asyncio.create_task(reader.cancel_read())
    query = asyncio.create_task(reader.get_temperature())
    await asyncio.sleep(0)
    with self.assertLogs("pylabrobot.molecular_devices.filtermax.protocol", level="WARNING") as log:
      io.resume.set()
      results = await asyncio.wait_for(
        asyncio.gather(read, cancel, query, return_exceptions=True), 2
      )
    self.assertIsInstance(results[0], FilterMaxProtocolError)
    self.assertIn("before a final ETX", str(results[0]))
    self.assertIsNone(results[1])
    self.assertIsInstance(results[2], FilterMaxProtocolError)
    self.assertIn("setup", str(results[2]))
    self.assertIn("reconnection", "\n".join(log.output))
    self.assertNotIn("STOP", host_payloads(io))
    self.assertNotIn("E P", host_payloads(io))
    self.assertNotIn("TG", host_payloads(io))
    self.assertTrue(io.stop_called)
    self.assertFalse(reader._operation_lock.locked())
    self.assertFalse(reader._pending_read)

  async def test_checksum_failure_in_reads_and_shaking_invalidates_session(self) -> None:
    """Streamed reads and standalone shakes use the same fault boundary as queries."""
    from pylabrobot.molecular_devices.filtermax import FilterMaxChecksumError
    from pylabrobot.molecular_devices.filtermax.tests.setup_tests import IDENTITY

    corrupt = bytearray(build_frame(ROW))
    corrupt[-4:-2] = b"00"
    for mode in ("absorbance", "luminescence", "shake"):
      with self.subTest(mode=mode):
        prefix = b"" if mode == "shake" else PREPARATION
        io = PausingSerial(
          exchange_response(IDENTITY) + prefix + bytes((ACK, ACK, ENQ)) + corrupt + bytes((EOT,))
        )
        reader = self.make_driver(io)
        await reader.setup()
        with self.assertRaises(FilterMaxChecksumError):
          if mode == "absorbance":
            await reader.read_absorbance(self.plate, 450, read_height=10.27)
          elif mode == "luminescence":
            await reader.read_luminescence(self.plate)
          else:
            await reader.start_shaking("linear", "medium", 5)
        self.assertTrue(io.stop_called)
        self.assertFalse(reader._setup_complete)
        self.assertIsNone(reader._instrument_info)
        self.assertFalse(reader._pending_read)
        self.assertFalse(reader._pending_shake)
        self.assertNotIn("STOP", host_payloads(io))
        self.assertNotIn("E P", host_payloads(io))
        self.assertEqual(io.incoming, bytes((EOT,)))

  async def test_fault_during_cancellation_stop_prevents_tray_recovery(self) -> None:
    """Even a suppressed STOP failure invalidates the connection before read cleanup."""
    prefix = PREPARATION + bytes((ACK, ACK))
    io = PausingSerial(
      prefix + bytes((ACK, ACK, ENQ, EOT)), pause_at=len(prefix), empty_on_resume=True
    )
    reader = self.make_driver(io)
    read = asyncio.create_task(reader.read_luminescence(self.plate))
    await asyncio.wait_for(io.paused.wait(), 1)
    cancel = asyncio.create_task(reader.cancel_read())
    await asyncio.sleep(0)
    io.resume.set()
    results = await asyncio.wait_for(asyncio.gather(read, cancel, return_exceptions=True), 2)
    self.assertIsInstance(results[0], FilterMaxReadCancelled)
    self.assertIsNone(results[1])
    self.assertEqual(host_payloads(io)[-1], "STOP")
    self.assertNotIn("E P", host_payloads(io))
    self.assertTrue(io.stop_called)
    self.assertFalse(reader._setup_complete)
    self.assertFalse(reader._pending_read)

  async def test_idle_cancellation_does_not_transmit(self) -> None:
    """Repeated cancellation is idempotent when no operation is active."""
    io = PausingSerial(b"")
    reader = self.make_driver(io)
    await reader.cancel_read()
    await reader.cancel_read()
    await reader.stop_shaking()
    self.assertEqual(io.writes, [])

  async def test_cancelled_setup_drains_identity_and_closes_port(self) -> None:
    """Cancelling the caller cannot leave an opened port or partial identity response."""
    from pylabrobot.molecular_devices.filtermax.tests.setup_tests import IDENTITY

    io = PausingSerial(exchange_response(IDENTITY), pause_at=12)
    reader = self.make_driver(io)
    setup = asyncio.create_task(reader.setup())
    await asyncio.wait_for(io.paused.wait(), 1)
    setup.cancel()
    await asyncio.sleep(0)
    self.assertFalse(io.stop_called)
    io.resume.set()
    with self.assertRaises(asyncio.CancelledError):
      await asyncio.wait_for(setup, 2)
    self.assertEqual(io.incoming, b"")
    self.assertTrue(io.stop_called)
    self.assertFalse(reader._setup_complete)
    self.assertFalse(reader._operation_lock.locked())

  async def test_cancelled_queued_call_never_sends_a_command(self) -> None:
    """Cancelling a waiting operation cannot leak a coroutine or change the wire."""
    io = PausingSerial(exchange_response("+ 25"), pause_at=12)
    reader = self.make_driver(io)
    first = asyncio.create_task(reader.get_temperature())
    await asyncio.wait_for(io.paused.wait(), 1)
    queued = asyncio.create_task(reader.move_plate_tray_out())
    await asyncio.sleep(0)
    queued.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await queued
    io.resume.set()
    self.assertEqual(await asyncio.wait_for(first, 2), 25)
    self.assertEqual(host_payloads(io), ["TG"])

  async def test_cancellation_after_final_info_needs_no_stop(self) -> None:
    """A completed firmware operation is not stopped again during cancellation cleanup."""
    read_response = PREPARATION + measurement_response(ROW, "+ INFO")
    io = PausingSerial(read_response + exchange_response("+ 6512"), pause_at=len(read_response) - 1)
    reader = self.make_driver(io)
    read = asyncio.create_task(
      reader.read_absorbance(self.plate, 450, wells=[self.plate.get_well("A1")], read_height=10.27)
    )
    await asyncio.wait_for(io.paused.wait(), 1)
    cancel = asyncio.create_task(reader.cancel_read())
    await asyncio.sleep(0)
    io.resume.set()
    results = await asyncio.wait_for(asyncio.gather(read, cancel, return_exceptions=True), 2)
    self.assertIsInstance(results[0], FilterMaxReadCancelled)
    self.assertNotIn("STOP", host_payloads(io))
    self.assertEqual(host_payloads(io)[-1], "E P")
