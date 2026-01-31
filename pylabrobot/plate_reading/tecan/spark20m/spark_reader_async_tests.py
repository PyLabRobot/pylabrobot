import asyncio
import unittest
from unittest.mock import AsyncMock, patch

# Import the module under test
from pylabrobot.plate_reading.tecan.spark20m.spark_reader_async import (
  SparkDevice,
  SparkEndpoint,
  SparkReaderAsync,
)


class TestSparkReaderAsync(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    # Patch USB class
    self.usb_patcher = patch("pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.USB")
    self.mock_usb_class = self.usb_patcher.start()

    self.reader = SparkReaderAsync()

  async def asyncTearDown(self):
    self.usb_patcher.stop()

  async def test_connect_success(self):
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

  async def test_connect_no_devices(self):
    # USB raising RuntimeError means device not found
    self.mock_usb_class.side_effect = RuntimeError("Device not found")

    with self.assertRaisesRegex(ValueError, "Failed to connect to any known Spark devices"):
      await self.reader.connect()

  async def test_connect_usb_error(self):
    # Device 1: Fails with Exception (not RuntimeError)
    # Device 2: Succeeds

    # We need side_effect for the constructor to return different mocks or raise exceptions
    # based on input arguments (id_product).

    mock_usb_success = AsyncMock()

    def side_effect(id_vendor, id_product, configuration_callback=None):
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

  async def test_send_command(self):
    # Setup connected device
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    # Mock calculate_checksum to return a predictable value
    with patch.object(self.reader, "_calculate_checksum", return_value=0x99):
      success = await self.reader.send_command("CMD")

    self.assertTrue(success)
    # Expected message: header + payload + checksum
    # Header: 0x01, seq=0, 0x00, len=3
    # Payload: b"CMD"
    # Checksum: 0x99
    expected_msg = bytes([0x01, 0x00, 0x00, 0x03]) + b"CMD" + bytes([0x99])

    mock_dev.write_to_endpoint.assert_awaited_with(SparkEndpoint.BULK_OUT.value, expected_msg)
    self.assertEqual(self.reader.seq_num, 1)

  async def test_send_command_device_not_connected(self):
    success = await self.reader.send_command("CMD", device_type=SparkDevice.ABSORPTION)
    self.assertFalse(success)

  async def test_get_response_success(self):
    # Mock parse_single_spark_packet
    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      mock_parse.return_value = {"type": "RespReady", "payload": {"status": "OK"}}

      read_task: asyncio.Future = asyncio.Future()
      read_task.set_result(b"response_bytes")

      parsed = await self.reader._get_response(read_task)

      self.assertEqual(parsed, {"type": "RespReady", "payload": {"status": "OK"}})

  async def test_get_response_busy_then_ready(self):
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
      mock_parse.side_effect = [
        {"type": "RespMessage", "payload": "msg1"},
        {"type": "RespReady", "payload": "done"},
      ]

      mock_reader.read_from_endpoint.return_value = b"retry_data"

      read_task: asyncio.Future = asyncio.Future()
      read_task.set_result(b"initial_data")

      parsed = await self.reader._get_response(read_task, attempts=5)

      self.assertEqual(parsed, {"type": "RespReady", "payload": "done"})
      self.assertIn("msg1", self.reader.msgs)

      # Should have called read_from_endpoint once for the retry
      mock_reader.read_from_endpoint.assert_awaited()

  async def test_start_background_read(self):
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    # Mock read_from_endpoint
    # We need to simulate:
    # 1. Read successful data
    # 2. Read successful data
    # 3. Raise CancelledError (simulating task cancellation)

    async def side_effect(*args, **kwargs):
      if mock_dev.read_from_endpoint.call_count == 1:
        return b"data1"
      elif mock_dev.read_from_endpoint.call_count == 2:
        return b"data2"
      else:
        # Stall until cancelled
        await asyncio.sleep(10)
        return None

    mock_dev.read_from_endpoint.side_effect = side_effect

    task, stop_event, results = await self.reader.start_background_read(SparkDevice.PLATE_TRANSPORT)

    self.assertIsNotNone(task)

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

  async def test_close(self):
    mock_dev = AsyncMock()
    self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev

    await self.reader.close()

    self.assertEqual(self.reader.devices, {})
    # Ensure stop called on the mocked USB device
    mock_dev.stop.assert_awaited()

  async def test_get_response_error(self):
    with patch(
      "pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet"
    ) as mock_parse:
      mock_parse.return_value = {"type": "RespError", "payload": {"error": "BadCommand"}}

      read_task: asyncio.Future = asyncio.Future()
      read_task.set_result(b"error_bytes")

      # get_response catches exceptions and logs them, returning None
      result = await self.reader._get_response(read_task)
      self.assertIsNone(result)


if __name__ == "__main__":
  unittest.main()
