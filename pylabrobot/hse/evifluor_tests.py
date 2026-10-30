"""Protocol and measurement tests using a scripted serial transport, without hardware."""

import asyncio
import unittest
from collections import deque
from typing import Awaitable, Callable, Optional, cast

from pylabrobot.hse import (
  EviFluor,
  EviFluorAirCalibration,
  EviFluorError,
  EviFluorMeasurement,
  EviFluorProtocolError,
)
from pylabrobot.io.serial import Serial


class ScriptedSerial:
  """Release response bytes only after the corresponding expected request is written."""

  def __init__(self) -> None:
    """Initialize an empty dialogue."""
    self.script: deque[tuple[bytes, bytes]] = deque()
    self.pending = bytearray()
    self.writes: list[bytes] = []
    self.opened = False
    self.failure: Optional[Exception] = None

  async def setup(self) -> None:
    """Open the simulated port."""
    self.opened = True

  async def stop(self) -> None:
    """Close the simulated port."""
    self.opened = False

  async def reset_input_buffer(self) -> None:
    """Discard bytes left from a prior connection."""
    self.pending.clear()

  async def write(self, data: bytes) -> None:
    """Require an exact request match before releasing its reply."""
    if self.failure:
      raise self.failure
    expected, response = self.script.popleft()
    if data != expected:
      raise AssertionError(f"Expected {expected!r}, received {data!r}")
    self.writes.append(data)
    self.pending.extend(response)

  async def read(self, num_bytes: int = 1) -> bytes:
    """Return fragments and yield so concurrency and cancellation are exercised."""
    await asyncio.sleep(0)
    result = bytes(self.pending[:num_bytes])
    del self.pending[:num_bytes]
    return result


class EviFluorTests(unittest.IsolatedAsyncioTestCase):
  """Check request bytes, parsed results, lifecycle, and concurrent operations."""

  async def asyncSetUp(self) -> None:
    """Create a driver with a simulated firmware handshake."""
    self.device = EviFluor(port="test", timeout=0.05)
    self.io = ScriptedSerial()
    self.device.io = cast(Serial, self.io)
    self.io.script.append((b":V 0\n", b':V "1.2.3"\r\n'))
    with self.assertLogs("pylabrobot.hse.evifluor", level="WARNING") as logs:
      await self.device.setup()
    self.assertIn("not been tested on hardware", logs.output[0])
    self.io.writes.clear()

  async def asyncTearDown(self) -> None:
    """Close the simulated connection."""
    await self.device.stop()

  async def test_identity_and_quoted_strings(self) -> None:
    """Decode both quote styles, spaces, hashes, and literal backslashes."""
    self.io.script.extend(
      [
        (b":V 0\n", b':V "FW 1.2"\n'),
        (b":V 1\n", b":V 'SN #123'\n"),
        (b":V 3\n", b':V "HSE\\2025"\n'),
      ]
    )
    self.assertEqual(await self.device.get_firmware_version(), "FW 1.2")
    self.assertEqual(await self.device.get_serial_number(), "SN #123")
    self.assertEqual(await self.device.get_production_number(), "HSE\\2025")

  async def test_plain_and_reserved_measurements(self) -> None:
    """Read the C# triplet and the C six-field response without inventing channel semantics."""
    self.io.script.extend(
      [
        (b":M\n", b"boot noise\r\n:M 10.5 123.75 120\r"),
        (b":M 0\n", b":M 10 2010 120 20 30 40\n"),
      ]
    )
    result = await self.device.measure()
    self.assertEqual(result.signal, 113.25)
    self.assertEqual(result.led_power, 120)
    stored = await self.device.get_stored_measurement()
    self.assertEqual(stored.reserved_values, ("20", "30", "40"))
    self.assertEqual(stored.signal, 2000)

  async def test_crc_matches_manufacturer_c_vectors(self) -> None:
    """Use fixed vectors calculated with the reference crc-16-ccitt.c, seed 0x1D0F."""
    self.device.use_checksum = True
    self.io.script.extend(
      [
        (b";V 0@52695\n", b';V "1.2.3"@21069\n'),
        (b";M@22005\n", b";M 10 2010 120 0 0 0@45344\r\n"),
      ]
    )
    self.assertEqual(await self.device.get_firmware_version(), "1.2.3")
    self.assertEqual((await self.device.measure()).signal, 2000)

  async def test_bad_frames_require_reconnection(self) -> None:
    """Reject corrupt checksums, malformed errors, wrong commands and broken framing."""
    for response in [
      b";V hello@1\n",
      b";V hello\n",
      b";V hello@x\n",
      b":\n",
      b":E\n",
      b":E abc\n",
      b":E 2 3\n",
      b":X 1\n",
      b":V a b\n",
      b':V "unterminated\n',
      b":V \xff\n",
      b":" + b"x" * 255,
    ]:
      with self.subTest(response=response):
        self.io.script.append((b":V 1\n", response))
        with self.assertRaises(EviFluorProtocolError):
          await self.device.get_serial_number()
        with self.assertRaisesRegex(RuntimeError, "synchronization"):
          await self.device.get_serial_number()
        await self.device.stop()
        self.io.script.append((b":V 0\n", b":V 1.2\n"))
        await self.device.setup()

  async def test_device_error_preserves_code_and_allows_next_request(self) -> None:
    """A complete firmware error is not a transport desynchronization."""
    self.io.script.extend(
      [
        (b":M\n", b":E 100\n"),
        (b":M\n", b":E 123\n"),
        (b":V 1\n", b":V 123\n"),
      ]
    )
    for code in [100, 123]:
      with self.assertRaises(EviFluorError) as caught:
        await self.device.measure()
      self.assertEqual(caught.exception.code, code)
      self.assertEqual(caught.exception.command, "M")
    self.assertEqual(await self.device.get_serial_number(), "123")

  async def test_timeout_rejects_late_response_until_reconnected(self) -> None:
    """A partial response cannot be reused for a later request."""
    self.io.script.append((b":M\n", b":M 1 2"))
    with self.assertRaises(TimeoutError):
      await self.device.measure()
    self.io.pending.extend(b" 32\n")
    with self.assertRaisesRegex(RuntimeError, "synchronization"):
      await self.device.measure()
    with self.assertRaisesRegex(RuntimeError, "stop"):
      await self.device.setup()

  async def test_cancellation_requires_reconnection(self) -> None:
    """Cancel a command in flight and reject subsequent writes on that connection."""
    self.io.script.append((b":M\n", b""))
    task = asyncio.create_task(self.device.measure())
    while not self.io.writes:
      await asyncio.sleep(0)
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    with self.assertRaisesRegex(RuntimeError, "synchronization"):
      await self.device.measure()

  async def test_transport_failure(self) -> None:
    """Propagate an I/O failure without accepting another command."""
    self.io.failure = OSError("port disconnected")
    with self.assertRaises(OSError):
      await self.device.measure()
    with self.assertRaisesRegex(RuntimeError, "synchronization"):
      await self.device.measure()

  async def test_setup_is_idempotent_and_failure_closes_port(self) -> None:
    """Repeated setup sends nothing, and a failed handshake leaves no open port."""
    await self.device.setup()
    self.assertEqual(self.io.writes, [])
    await self.device.stop()
    await self.device.stop()
    with self.assertRaisesRegex(RuntimeError, "setup"):
      await self.device.measure()
    self.io.script.append((b":V 0\n", b":E 1\n"))
    with self.assertRaises(EviFluorError):
      await self.device.setup()
    self.assertFalse(self.io.opened)

  async def test_status_operations(self) -> None:
    """Check self-test flags, holder diagnostics, history reset/count, and LED colors."""
    self.io.script.extend(
      [
        (b":Y\n", b":Y 1\n"),
        (b":X\n", b":X 1 12.5\n"),
        (b":X\n", b":X 0\n"),
        (b":G\n", b":G\n"),
        (b":V 10\n", b":V 0\n"),
        (b":Z 2\n", b":Z\n"),
      ]
    )
    self.assertEqual(await self.device.run_self_test(), 1)
    self.assertTrue(await self.device.is_cuvette_holder_empty())
    self.assertFalse(await self.device.is_cuvette_holder_empty())
    await self.device.clear_measurements()
    self.assertEqual(await self.device.get_measurement_count(), 0)
    await self.device.set_status_led("green")

  async def test_first_air_and_sample_workflow(self) -> None:
    """Lock the reference min/max sweep and autogain/sample sequences together."""
    self.io.script.extend(
      [
        (b":V 16\n", b":V 32\n"),
        (b":V 17\n", b":V 222\n"),
        (b":V 15 32\n", b":V\n"),
        (b":M\n", b":M 10 110 32 0 0 0\n"),
        (b":V 15 222\n", b":V\n"),
        (b":M\n", b":M 20 220 222 0 0 0\n"),
        (b":C 2000\n", b":C 1 127\n"),
        (b":M\n", b":M 15 2015 127 0 0 0\n"),
      ]
    )
    air = await self.device.measure_first_air()
    sample = await self.device.measure_first_sample()
    adjusted = air.interpolate(sample.led_power)
    self.assertEqual(adjusted, EviFluorMeasurement(15, 165, 127))
    self.assertEqual(sample.signal - adjusted.signal, 1850)
    self.assertFalse(self.io.script)

  async def test_failed_autogain_does_not_measure(self) -> None:
    """An unsuccessful target search must not silently create a standard reading."""
    self.io.script.extend(
      [
        (b":C 2000\n", b":C 0 222\n"),
        (b":C 1000\n", b":C 0 222\n"),
      ]
    )
    with self.assertRaisesRegex(RuntimeError, "autogain failed"):
      await self.device.measure_first_sample()
    result = await self.device.autogain(1000)
    self.assertFalse(result.found)
    self.assertEqual(result.led_power, 222)

  async def test_measure_at_power_is_atomic(self) -> None:
    """A concurrent reader cannot interleave between setting power and measuring."""
    self.io.script.extend(
      [
        (b":V 15 120\n", b":V\n"),
        (b":M\n", b":M 10 100 120\n"),
        (b":V 1\n", b":V 123\n"),
      ]
    )
    measurement, serial = await asyncio.gather(
      self.device.measure(led_power=120), self.device.get_serial_number()
    )
    self.assertEqual(measurement.led_power, 120)
    self.assertEqual(serial, "123")

  async def test_power_and_limits(self) -> None:
    """Use only the confirmed 470 nm registers."""
    self.io.script.extend(
      [
        (b":V 15\n", b":V 100\n"),
        (b":V 16\n", b":V 32\n"),
        (b":V 17\n", b":V 222\n"),
        (b":V 15 120\n", b":V\n"),
      ]
    )
    self.assertEqual(await self.device.get_led_power(), 100)
    self.assertEqual(await self.device.get_led_power_limits(), (32, 222))
    await self.device.set_led_power(120)

  async def test_bad_measurements(self) -> None:
    """Reject nonnumeric/nonfinite signals and invalid integer powers."""
    for values in [b"nan 10 120", b"1 inf 120", b"a 10 120", b"1 10 256", b"1 10 1.5"]:
      with self.subTest(values=values):
        self.io.script.append((b":M\n", b":M " + values + b"\n"))
        with self.assertRaises(EviFluorProtocolError):
          await self.device.measure()

  async def test_invalid_status_values(self) -> None:
    """Invalid booleans/counts must not become plausible results."""
    self.io.script.extend(
      [
        (b":X\n", b":X 2\n"),
        (b":V 10\n", b":V 11\n"),
        (b":C 2000\n", b":C 2 120\n"),
      ]
    )
    operations: list[Callable[[], Awaitable[object]]] = [
      self.device.is_cuvette_holder_empty,
      self.device.get_measurement_count,
      self.device.autogain,
    ]
    for operation in operations:
      with self.assertRaises(EviFluorProtocolError):
        await operation()

  async def test_invalid_inputs_do_not_write(self) -> None:
    """Validate input bounds and types before any I/O."""
    for value in [-1, 256, True, 1.5]:
      with self.subTest(value=value):
        with self.assertRaises(ValueError):
          await self.device.set_led_power(value)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
          await self.device.measure(value)  # type: ignore[arg-type]
    for value in [-1, 2501, True, 1.5]:
      with self.assertRaises(ValueError):
        await self.device.autogain(value)  # type: ignore[arg-type]
    for value in [-1, 10, True, 1.5]:
      with self.assertRaises(ValueError):
        await self.device.get_stored_measurement(value)  # type: ignore[arg-type]
    with self.assertRaises(ValueError):
      await self.device.set_status_led("purple")  # type: ignore[arg-type]
    self.assertEqual(self.io.writes, [])


class CalibrationTests(unittest.TestCase):
  """Check the scientific calculation independently from serial parsing."""

  def setUp(self) -> None:
    """Create standards with distinct dark and empty-cuvette corrections."""
    self.device = EviFluor("test")
    self.air = EviFluorMeasurement(10, 110, 120)
    self.low = EviFluorMeasurement(20, 220, 120)  # corrected signal 100
    self.high = EviFluorMeasurement(30, 1130, 120)  # corrected signal 1000

  def test_calibration_and_interpolation(self) -> None:
    """Correct both dark and cuvette signals, and preserve the standards' units."""
    self.device.calibrate(self.air, self.low, self.air, self.high, 2.0, 20.0)
    self.assertAlmostEqual(self.device.calculate_concentration(self.air, self.low), 2.0)
    self.assertAlmostEqual(self.device.calculate_concentration(self.air, self.high), 20.0)
    sample = EviFluorMeasurement(40, 690, 120)  # corrected 550, halfway between standards
    self.assertAlmostEqual(self.device.calculate_concentration(self.air, sample), 11.0)

  def test_invalid_calibration(self) -> None:
    """Reject equal/reversed signals and nonfinite or unordered standard concentrations."""
    for low_concentration, high_concentration in [(-1, 20), (20, 2), (2, 2), (0, float("nan"))]:
      with self.assertRaises(ValueError):
        self.device.calibrate(
          self.air, self.low, self.air, self.high, low_concentration, high_concentration
        )
    with self.assertRaisesRegex(ValueError, "exceed"):
      self.device.calibrate(self.air, self.low, self.air, self.low, 0, 20)
    with self.assertRaisesRegex(ValueError, "same LED"):
      self.device.calibrate(self.air, self.low, self.air, EviFluorMeasurement(0, 1000, 121), 0, 20)

  def test_saturation_power_mismatch_and_missing_calibration(self) -> None:
    """Do not report a concentration from incompatible measurements."""
    with self.assertRaisesRegex(RuntimeError, "calibrate"):
      self.device.calculate_concentration(self.air, self.low)
    self.device.calibrate(self.air, self.low, self.air, self.high, 0, 20)
    with self.assertRaisesRegex(ValueError, "Saturated"):
      self.device.calculate_concentration(self.air, EviFluorMeasurement(0, 2499, 120))
    with self.assertRaisesRegex(ValueError, "differs"):
      self.device.calculate_concentration(
        EviFluorMeasurement(0, 100, 121), EviFluorMeasurement(0, 500, 121)
      )

  def test_air_interpolation_range_and_equal_endpoints(self) -> None:
    """Avoid extrapolating empty-cuvette measurements beyond the measured powers."""
    air = EviFluorAirCalibration(
      EviFluorMeasurement(10, 110, 32), EviFluorMeasurement(20, 220, 222)
    )
    self.assertEqual(air.interpolate(32), air.minimum)
    self.assertEqual(air.interpolate(222), air.maximum)
    with self.assertRaises(ValueError):
      air.interpolate(223)
    same = EviFluorAirCalibration(self.air, self.air)
    self.assertEqual(same.interpolate(120), self.air)

  def test_invalid_timeout(self) -> None:
    """Timeouts must allow a bounded wait."""
    for timeout in [0, -1, float("inf"), float("nan")]:
      with self.assertRaises(ValueError):
        EviFluor("test", timeout=timeout)

  def test_saturated_air_cannot_produce_an_apparently_valid_interpolated_reading(self) -> None:
    """A saturated endpoint would otherwise interpolate to a plausible value below 2499 mV."""
    air = EviFluorAirCalibration(
      EviFluorMeasurement(10, 110, 32), EviFluorMeasurement(20, 2500, 222)
    )
    with self.assertRaisesRegex(ValueError, "saturated"):
      air.interpolate(127)
