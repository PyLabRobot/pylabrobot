import asyncio
import unittest
import concurrent.futures
from unittest.mock import AsyncMock, MagicMock, patch

# Import the module under test
from pylabrobot.plate_reading.tecan.spark20m.enums import SparkDevice, SparkEndpoint
from pylabrobot.plate_reading.tecan.spark20m.spark_reader_async import SparkReaderAsync


class TestSparkReaderAsync(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    # Patch USB class
    self.usb_patcher = patch("pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.USB")
    self.mock_usb_class = self.usb_patcher.start()

    self.reader = SparkReaderAsync()

  async def asyncTearDown(self) -> None:
    self.usb_patcher.stop()

  async def test_connect_success(self) -> None:
    # Create a mock USB instance
    mock_usb_instance = AsyncMock()
    self.mock_usb_class.return_value = mock_usb_instance

    await self.reader.connect()

    # Verify USB initialized for known devices (we iterate all SparkDevices)
    # Just check for one of them
    self.assertTrue(self.mock_usb_class.call_count >= 1)

    # Check that it's in devices
    # Note: connect iterates all enum members. If all succeed, all are in devices.
    # We mock success for all.
    self.assertIn(SparkDevice.PLATE_TRANSPORT, self.reader.devices)
    self.assertEqual(self.reader.devices[SparkDevice.PLATE_TRANSPORT], mock_usb_instance)

    mock_usb_instance.setup.assert_awaited()

  async def test_connect_no_devices(self) -> None:
    # USB raising RuntimeError means device not found
    self.mock_usb_class.side_effect = RuntimeError("Device not found")

    with self.assertRaisesRegex(ValueError, "Failed to connect to any known Spark devices"):
      await self.reader.connect()

  async def test_connect_usb_error(self) -> None:
    # Device 1: Fails with Exception (not RuntimeError)
    # Device 2: Succeeds

    # We need side_effect for the constructor to return different mocks or raise exceptions
    # based on input arguments (id_product).

    mock_usb_success = AsyncMock()

    def side_effect(
      id_vendor: int, id_product: int, configuration_callback: object = None, max_workers: int = 1
    ) -> AsyncMock:
      if id_product == SparkDevice.PLATE_TRANSPORT.value:
        raise Exception("Some USB Error")
      if id_product == SparkDevice.ABSORPTION.value:
        return mock_usb_success
      raise RuntimeError("Not found")  # Others not found

    self.mock_usb_class.side_effect = side_effect

    await self.reader.connect()

    # Device 1 should not be in devices
    self.assertNotIn(SparkDevice.PLATE_TRANSPORT, self.reader.devices)
    # Device 2 should be in devices
    self.assertIn(SparkDevice.ABSORPTION, self.reader.devices)

  async def test_send_command(self) -> None:
    # Setup connected device
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    # Configure mock executor and device
    mock_dev._executor = MagicMock()
    mock_dev.dev = MagicMock()
    mock_dev.write_timeout = 30  # default

    def execute_sync(func, *args):
      f: concurrent.futures.Future = concurrent.futures.Future()
      try:
        result = func(*args)
        f.set_result(result)
      except Exception as e:
        f.set_exception(e)
      return f

    mock_dev._executor.submit.side_effect = execute_sync

    # Mock calculate_checksum to return a predictable value
    with patch.object(self.reader, "_get_response") as mock_get_response:
      mock_get_response.return_value = {"payload": {"message": "OK"}}
      with patch.object(self.reader, "_calculate_checksum", return_value=0x99):
        success = await self.reader.send_command("CMD", timeout=1.0)

    self.assertTrue(success)
    # Expected message: header + payload + checksum
    # Header: 0x01, seq=0, 0x00, len=3
    # Payload: b"CMD"
    # Checksum: 0x99
    expected_msg = bytes([0x01, 0x00, 0x00, 0x03]) + b"CMD" + bytes([0x99])

    mock_dev.dev.write.assert_called_with(SparkEndpoint.BULK_OUT.value, expected_msg, timeout=30000)
    self.assertEqual(self.reader.seq_num, 1)

  async def test_send_command_device_not_connected(self) -> None:
    with self.assertRaisesRegex(RuntimeError, "Device type .* not connected"):
      await self.reader.send_command("CMD", device_type=SparkDevice.ABSORPTION, timeout=1.0)

  async def test_get_response_success(self) -> None:
    # Mock parse_single_spark_packet
    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      mock_parse.return_value = {"type": "RespReady", "payload": {"status": "OK"}}

      async def return_bytes() -> bytes:
        return b"response_bytes"

      read_task = asyncio.create_task(return_bytes())

      parsed = await self.reader._get_response(read_task)

      self.assertEqual(parsed, {"type": "RespReady", "payload": {"status": "OK"}})

  async def test_get_response_busy_then_ready(self) -> None:
    # This tests the retry loop
    mock_reader = AsyncMock()
    self.reader.cur_reader = mock_reader
    self.reader.cur_endpoint_addr = 0x81

    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      # Sequence of parse results:
      # 1. First read (passed as task): RespMessage (busy/intermediate)
      # 2. Retry read 1: RespReady
      # Sequence of parse results:
      # 1. First read (passed as task): RespMessage (busy/intermediate)
      # 2. Retry read 1: RespReady
      mock_parse.side_effect = [
        {"type": "RespMessage", "payload": "msg1"},
        {"type": "RespReady", "payload": "done"},
      ]

      # Configure mock executor and device for read retry
      mock_reader._executor = MagicMock()
      mock_reader.dev = MagicMock()
      mock_reader.read_timeout = 30

      def execute_sync(func, *args):
        f: concurrent.futures.Future = concurrent.futures.Future()
        result = func(*args)
        f.set_result(result)
        return f

      mock_reader._executor.submit.side_effect = execute_sync

      # Mock dev.read
      # First call inside _read_from_endpoint
      mock_reader.dev.read.return_value = b"retry_data"

      async def return_initial_data() -> bytes:
        return b"initial_data"

      read_task = asyncio.create_task(return_initial_data())

      parsed = await self.reader._get_response(read_task, timeout=1.0)

      self.assertEqual(parsed, {"type": "RespReady", "payload": "done"})
      self.assertIn("msg1", self.reader.msgs)

      # Should have called dev.read once for the retry
      mock_reader.dev.read.assert_called()

  async def test_start_background_read(self) -> None:
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    # Mock read_from_endpoint via dev.read
    # We need to simulate:
    # 1. Read successful data
    # 2. Read successful data
    # 3. Raise CancelledError (simulating task cancellation) - wait, _read_from_endpoint catches exceptions?
    # Actually _read_from_endpoint catches USBError.

    mock_dev._executor = MagicMock()
    mock_dev.dev = MagicMock()
    mock_dev.read_timeout = 30

    def execute_sync(func, *args):
      f: concurrent.futures.Future = concurrent.futures.Future()
      try:
        result = func(*args)
        f.set_result(result)
      except Exception as e:
        f.set_exception(e)
      return f

    mock_dev._executor.submit.side_effect = execute_sync

    async def side_effect(*args: object, **kwargs: object) -> bytes:
      if mock_dev.dev.read.call_count == 1:
        return b"data1"
      elif mock_dev.dev.read.call_count == 2:
        return b"data2"
      else:
        # We need to block here to simulate waiting until cancellation
        # But dev.read is called in executor (sync). If we sleep here, we block the test thread if not careful.
        # But here side_effect is for dev.read (sync).
        import time

        time.sleep(10)  # This might block the loop if run_in_executor runs in same thread?
        # No, run_in_executor runs in thread pool.
        # But we are using a Mock executor that runs synchronously in the main thread!
        # So we cannot sleep.
        # Instead, we should return empty bytes or raise an exception that is handled?
        # If we return None (or empty), the loop continues.
        # We want to keep the loop running until we cancel it.
        return b""

    # Better approach for the mock executor:
    # We want to control when to return.

    # Actually, simpler: just return data a few times then return valid data that indicates 'no data' or let it loop.
    # The original test relied on `await asyncio.sleep(10)` inside the async side effect.
    # Since we are now mocking the sync `dev.read`, we can't easily await.

    # We can assume `dev.read` returns immediately.
    mock_dev.dev.read.side_effect = [b"data1", b"data2", b"", b"", b""]  # Return empty after

    # But `SparkReaderAsync` background reader loop:
    # if data: results.append...
    # else: nothing.
    # It loops `while not stop_event.is_set()`.

    # So if we return empty, it just loops.

    # The original test used `mock_dev.read_from_endpoint` which was async.
    # Now `_read_from_endpoint` calls `run_in_executor`.

    # Let's trust that returning data works.

    mock_dev.dev.read.side_effect = [b"data1", b"data2", b"", b"", b"", b"", b"", b""]

    # We also need to configure find_descriptor for `_read_from_endpoint` if size is None.
    # But start_background_read passes size=1024.

    task, stop_event, results = await self.reader.start_background_read(SparkDevice.PLATE_TRANSPORT)

    assert task is not None
    assert stop_event is not None
    assert results is not None

    # Let it run to collect data
    await asyncio.sleep(0.5)  # Wait for 2 reads (0.2 sleep in loop)

    stop_event.set()
    task.cancel()
    try:
      await task
    except asyncio.CancelledError:
      pass

    self.assertIn(b"data1", results)
    self.assertIn(b"data2", results)

  async def test_close(self) -> None:
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    await self.reader.close()

    self.assertEqual(self.reader.devices, {})
    # Ensure stop called on the mocked USB device
    mock_dev.stop.assert_awaited()

  async def test_get_response_error(self) -> None:
    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      mock_parse.return_value = {"type": "RespError", "payload": {"error": "BadCommand"}}

      async def return_error_bytes() -> bytes:
        return b"error_bytes"

      read_task = asyncio.create_task(return_error_bytes())

      # get_response catches exceptions and logs them, returning None
      result = await self.reader._get_response(read_task)
      self.assertIsNone(result)

  async def test_get_response_empty_packet_retry(self) -> None:
    # Test that empty packet (ZLP) triggers retry
    mock_reader = AsyncMock()
    self.reader.cur_reader = mock_reader
    self.reader.cur_endpoint_addr = 0x81

    # Configure mock executor and device for read retry
    mock_reader._executor = MagicMock()
    mock_reader.dev = MagicMock()
    mock_reader.read_timeout = 30

    def execute_sync(func, *args):
      f: concurrent.futures.Future = concurrent.futures.Future()
      result = func(*args)
      f.set_result(result)
      return f

    mock_reader._executor.submit.side_effect = execute_sync

    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      # Sequence:
      # 1. First read task returns empty bytes -> Triggers ValueError in parser (mocked below) -> retry
      # 2. Retry read returns valid data -> Success

      # Mock the retry read
      mock_reader.dev.read.return_value = b"retry_data"

      # Logic:
      # _get_response awaits read_task -> returns b""
      # calls parse_single_spark_packet(b"") -> raises ValueError
      # catches, loops.
      # loop calls _read_from_endpoint -> returns b"retry_data"
      # calls parse_single_spark_packet(b"retry_data") -> returns valid

      mock_parse.side_effect = [
        ValueError("Packet too short"),  # First call with empty bytes
        {"type": "RespReady", "payload": "done"},  # Second call with retry_data
      ]

      async def return_empty_bytes() -> bytes:
        return b""

      read_task = asyncio.create_task(return_empty_bytes())

      parsed = await self.reader._get_response(read_task, timeout=1.0)

      self.assertEqual(parsed, {"type": "RespReady", "payload": "done"})
      # Verify retry happened
      mock_reader.dev.read.assert_called()


if __name__ == "__main__":
  unittest.main()
