import asyncio
import concurrent.futures
import unittest
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
    # Check that endpoint addresses were passed
    _, kwargs = self.mock_usb_class.call_args
    self.assertEqual(kwargs["read_endpoint_address"], SparkEndpoint.INTERRUPT_IN.value)
    self.assertEqual(kwargs["write_endpoint_address"], SparkEndpoint.BULK_OUT.value)
    # Check that endpoint addresses were passed
    _, kwargs = self.mock_usb_class.call_args
    self.assertEqual(kwargs["read_endpoint_address"], SparkEndpoint.INTERRUPT_IN.value)
    self.assertEqual(kwargs["write_endpoint_address"], SparkEndpoint.BULK_OUT.value)

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
      id_vendor: int,
      id_product: int,
      configuration_callback: object = None,
      max_workers: int = 1,
      **kwargs,
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

    # Mock _read_packet to avoid TypeError in background task (must be MagicMock, not AsyncMock)
    mock_dev._read_packet = MagicMock()
    mock_dev._read_packet.return_value = b"\x81\x00\x00\x00\x00"

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

    expected_msg = bytes([0x01, 0x00, 0x00, 0x03]) + b"CMD" + bytes([0x99])

    mock_dev.write.assert_awaited_with(expected_msg)
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
        return b"\x81\x00\x00\x00\x00"

      read_task = asyncio.create_task(return_bytes())

      mock_reader = AsyncMock()
      parsed = await self.reader._get_response(read_task, reader=mock_reader)

      self.assertEqual(parsed, {"type": "RespReady", "payload": {"status": "OK"}})

  async def test_get_response_busy_then_ready(self) -> None:
    # This tests the retry loop
    mock_reader = AsyncMock()
    mock_reader._read_packet = MagicMock()

    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
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

      # Mock _read_packet
      # First call inside _get_response (via executor)
      mock_reader._read_packet.return_value = b"\x81\x00\x00\x00\x00"

      async def return_initial_data() -> bytes:
        return b"\x81\x00\x00\x00\x00"

      read_task = asyncio.create_task(return_initial_data())

      parsed = await self.reader._get_response(read_task, reader=mock_reader, timeout=1.0)

      self.assertEqual(parsed, {"type": "RespReady", "payload": "done"})
      self.assertIn("msg1", self.reader.msgs)

      # Should have called _read_packet once for the retry
      mock_reader._read_packet.assert_called()

  async def test_start_background_read(self) -> None:
    mock_dev = AsyncMock()
    mock_dev._read_packet = MagicMock()
    self.reader.devices[SparkDevice.ABSORPTION] = mock_dev

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

    DATA1 = b"\x81\x01\x00\x00\x00"
    DATA2 = b"\x81\x02\x00\x00\x00"

    # _read_packet_in_executor calls reader._read_packet.
    # We must mock that directly because reader is a Mock.
    # We provide DATA1, DATA2, then None/Empty to simulate no more data.
    # Note: _read_packet_in_executor loops on invalid/empty data.
    # If we return valid data, it returns it.
    # If we want to simulate "no data" (timeout), we should return None or have side_effect raise something?
    # But current logic: if len(data) < 5 check fails, it continues loop.
    # We want it to timeout.
    # To simulate timeout in our synchronous mock executor, we can't easily wait.
    # But _read_packet_in_executor checks time.monotonic().
    # If we return "too short" data repeatedly, it will eventually time out.
    # Let's return DATA1, DATA2, then b"" (too short) repeatedly.
    mock_dev._read_packet.side_effect = [DATA1, DATA2] + [b""] * 100

    # We also need to configure find_descriptor for `_read_from_endpoint` if size is None.
    # But start_background_read passes size=1024.

    task, stop_event, results = await self.reader.start_background_read(SparkDevice.ABSORPTION)

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

    self.assertIn(DATA1, results)
    self.assertIn(DATA2, results)

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
        return b"\x86\x00\x00\x00\x00"

      read_task = asyncio.create_task(return_error_bytes())

      # get_response catches exceptions and logs them, returning None
      mock_reader = AsyncMock()
      result = await self.reader._get_response(read_task, reader=mock_reader)
      self.assertIsNone(result)

  async def test_get_response_empty_packet_retry(self) -> None:
    # Test that empty packet (ZLP) triggers retry
    mock_reader = AsyncMock()
    mock_reader._read_packet = MagicMock()

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
      mock_reader._read_packet.return_value = b"\x81\x00\x00\x00\x00"

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

      parsed = await self.reader._get_response(read_task, reader=mock_reader, timeout=1.0)

      self.assertEqual(parsed, {"type": "RespReady", "payload": "done"})
      # Verify retry happened
      mock_reader._read_packet.assert_called()

  async def test_read_packet_in_executor_retries(self) -> None:
    # Test that _read_packet_in_executor retries on invalid packets using new validation logic
    mock_reader = AsyncMock()
    mock_reader._executor = MagicMock()

    def execute_sync(func, *args):
      # We need to execute the lambda passed to run_in_executor
      # The lambda calls reader._read_packet(...)
      return func()

    # We can't mock submit to execute lambda easily without modifying the test structure
    # confusing?
    # run_in_executor(executor, func, *args) -> func(*args)
    # The code calls loop.run_in_executor(executor, lambda: reader._read_packet(...))
    # So run_in_executor calls the lambda.

    # We can assume run_in_executor works (it's part of loop).
    # But checking internal logic of _read_packet_in_executor requires mocking reader._read_packet.

    # We need to setup reader._read_packet to return [Invalid, Invalid, Valid] sequence.

    # Case 1: Too short (<5 bytes)
    # Case 2: Invalid Indicator (0x00)
    # Case 3: Truncated (len < expected)
    # Case 4: Valid

    INVALID_SHORT = b"\x01\x02"
    INVALID_INDICATOR = b"\x00\x00\x00\x00\x00"
    INVALID_TRUNCATED = b"\x81\x00\x00\x05\x00"  # Payload len 5, but total len 5 (expect 4+5+1=10)
    VALID = b"\x81\x00\x00\x00\x00"

    mock_reader._read_packet = MagicMock(
      side_effect=[INVALID_SHORT, INVALID_INDICATOR, INVALID_TRUNCATED, VALID]
    )

    # We need to mock loop.run_in_executor to execute the lambda?
    # Or just use the real loop since we are in async test.
    # The real loop uses the executor. Since we mocked reader._executor, we just need to confirm it's passed.
    # But wait, helper checks reader._executor is not None.

    # In the code: loop.run_in_executor(reader._executor, lambda...)
    # We want to use the REAL run_in_executor with a Mock executor?
    # Standard ThreadPoolExecutor works fine.
    # Let's use a real ThreadPoolExecutor for simplicity and just mock _read_packet on the reader object.

    mock_reader._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    try:
      data = await self.reader._read_packet_in_executor(mock_reader, timeout=1.0)
      self.assertEqual(data, VALID)
      self.assertEqual(mock_reader._read_packet.call_count, 4)
    finally:
      mock_reader._executor.shutdown()


if __name__ == "__main__":
  unittest.main()
