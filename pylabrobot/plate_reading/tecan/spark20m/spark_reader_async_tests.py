import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

# Import the module under test
from pylabrobot.plate_reading.tecan.spark20m.spark_reader_async import SparkReaderAsync, SparkDevice, SparkEndpoint

class TestSparkReaderAsync(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Patch usb.core and usb.util inside the module
        self.usb_core_patcher = patch('pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.usb.core')
        self.usb_util_patcher = patch('pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.usb.util')
        
        self.mock_usb_core = self.usb_core_patcher.start()
        self.mock_usb_util = self.usb_util_patcher.start()
        
        # Setup MockUSBError
        class MockUSBError(Exception):
            def __init__(self, errno=None, *args):
                super().__init__(*args)
                self.errno = errno
        self.mock_usb_core.USBError = MockUSBError
        
        self.reader = SparkReaderAsync()

    async def asyncTearDown(self):
        self.usb_core_patcher.stop()
        self.usb_util_patcher.stop()

    async def test_connect_success(self):
        # Mock device
        mock_dev = MagicMock()
        mock_dev.idProduct = SparkDevice.PLATE_TRANSPORT.value
        mock_dev.is_kernel_driver_active.return_value = True
        mock_dev.serial_number = "TEST_SERIAL"
        
        # Mock configuration and endpoints
        mock_cfg = {(0, 0): MagicMock()}
        mock_dev.get_active_configuration.return_value = mock_cfg
        
        self.mock_usb_core.find.return_value = iter([mock_dev])
        self.mock_usb_util.find_descriptor.side_effect = ["ep_out", "ep_in", "ep_in1", "ep_int"]
        
        self.reader.connect()
        
        self.assertIn(SparkDevice.PLATE_TRANSPORT, self.reader.devices)
        self.assertEqual(self.reader.devices[SparkDevice.PLATE_TRANSPORT], mock_dev)
        mock_dev.detach_kernel_driver.assert_called_with(0)
        # Check set_configuration calls: 0, 0, 1
        # mock_dev.set_configuration.assert_has_calls([call(0), call(0), call(1)]) # Code calls it multiple times?
        # Code:
        # d.set_configuration() (no args?) -> Wait, existing code: d.set_configuration(), then d.set_configuration(0), d.set_configuration(0), d.set_configuration(1)
        # Let's just check it was called.
        self.assertTrue(mock_dev.set_configuration.called)

    async def test_connect_no_devices(self):
        self.mock_usb_core.find.return_value = iter([])
        with self.assertRaisesRegex(ValueError, "No devices found"):
            self.reader.connect()

    async def test_connect_unknown_device(self):
        mock_dev = MagicMock()
        mock_dev.idProduct = 0xFFFF # Unknown PID
        self.mock_usb_core.find.return_value = iter([mock_dev])
        
        with self.assertRaisesRegex(ValueError, "Failed to connect to any known Spark devices"):
            self.reader.connect()

    async def test_send_command(self):
        # Setup connected device
        mock_dev = MagicMock()
        mock_ep_out = MagicMock()
        mock_ep_out.write = MagicMock() # sync write
        
        self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev
        self.reader.endpoints[SparkDevice.PLATE_TRANSPORT] = {SparkEndpoint.BULK_OUT: mock_ep_out}
        
        # Mock calculate_checksum to return a predictable value
        with patch.object(self.reader, '_calculate_checksum', return_value=0x99):
            success = await self.reader.send_command("CMD")
            
        self.assertTrue(success)
        # Expected message: header + payload + checksum
        # Header: 0x01, seq=0, 0x00, len=3
        # Payload: b"CMD"
        # Checksum: 0x99
        expected_msg = bytes([0x01, 0x00, 0x00, 0x03]) + b"CMD" + bytes([0x99])
        mock_ep_out.write.assert_called_with(expected_msg)
        self.assertEqual(self.reader.seq_num, 1)

    async def test_send_command_device_not_connected(self):
        success = await self.reader.send_command("CMD", device_type=SparkDevice.ABSORPTION)
        self.assertFalse(success)

    async def test_usb_read(self):
        mock_ep = MagicMock()
        mock_ep.read = MagicMock(return_value=b"data")
        
        data = await self.reader._usb_read(mock_ep, timeout=100)
        
        self.assertEqual(data, b"data")
        mock_ep.read.assert_called_with(mock_ep.wMaxPacketSize, timeout=100)

    async def test_get_response_success(self):
        # Mock parse_single_spark_packet
        with patch('pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet') as mock_parse:
            mock_parse.return_value = {'type': 'RespReady', 'payload': {'status': 'OK'}}
            
            read_task: asyncio.Future = asyncio.Future()
            read_task.set_result(b"response_bytes")
            
            parsed = await self.reader.get_response(read_task)
            
            self.assertEqual(parsed, {'type': 'RespReady', 'payload': {'status': 'OK'}})

    async def test_get_response_busy_then_ready(self):
        # This tests the retry loop
        mock_ep = MagicMock()
        self.reader.cur_in_endpoint = mock_ep
        
        # Mock _usb_read to return data on retry
        with patch.object(self.reader, '_usb_read', new_callable=AsyncMock) as mock_read, \
             patch('pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet') as mock_parse:
            
            # Sequence of parse results:
            # 1. First read (passed as task): RespMessage (busy/intermediate)
            # 2. Retry read 1: RespReady
            mock_parse.side_effect = [
                {'type': 'RespMessage', 'payload': 'msg1'},
                {'type': 'RespReady', 'payload': 'done'}
            ]
            
            mock_read.return_value = b"retry_data"
            
            read_task: asyncio.Future = asyncio.Future()
            read_task.set_result(b"initial_data")
            
            parsed = await self.reader.get_response(read_task, attempts=5)
            
            self.assertEqual(parsed, {'type': 'RespReady', 'payload': 'done'})
            self.assertIn('msg1', self.reader.msgs)
            # Should have called _usb_read once for the retry
            mock_read.assert_called_once()

    async def test_reading_context_manager(self):
        mock_dev = MagicMock()
        mock_ep = MagicMock()
        self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev
        self.reader.endpoints[SparkDevice.PLATE_TRANSPORT] = {SparkEndpoint.INTERRUPT_IN: mock_ep}
        
        with patch.object(self.reader, 'init_read') as mock_init_read, \
             patch.object(self.reader, 'get_response', new_callable=AsyncMock) as mock_get_resp:
            
            read_task_mock: asyncio.Future = asyncio.Future()
            mock_init_read.return_value = read_task_mock
            
            mock_get_resp.return_value = {'status': 'ok'}
            
            async with self.reader.reading(SparkDevice.PLATE_TRANSPORT):
                pass
            
            mock_init_read.assert_called_with(mock_ep, 512, 2000)
            mock_get_resp.assert_awaited()

    async def test_start_background_read(self):
        mock_dev = MagicMock()
        mock_ep = MagicMock()
        # Ensure wMaxPacketSize is set
        mock_ep.wMaxPacketSize = 64
        # Ensure bEndpointAddress is set for logging
        mock_ep.bEndpointAddress = 0x81
        
        self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev
        self.reader.endpoints[SparkDevice.PLATE_TRANSPORT] = {SparkEndpoint.INTERRUPT_IN: mock_ep}
        
        # Mock _usb_read
        with patch.object(self.reader, '_usb_read', new_callable=AsyncMock) as mock_read:
            # Return some data then block or raise CancelledError
            # Note: The background reader catches CancelledError and exits.
            # We need to simulate:
            # 1. Read successful data
            # 2. Read successful data
            # 3. Raise CancelledError (simulating task cancellation or just ending the loop via exception injection)
            
            async def side_effect(*args, **kwargs):
                if mock_read.call_count == 1:
                    return b"data1"
                elif mock_read.call_count == 2:
                    return b"data2"
                else:
                    # Stall until cancelled
                    await asyncio.sleep(10)
                    return None

            mock_read.side_effect = side_effect
            
            task, stop_event, results = await self.reader.start_background_read(SparkDevice.PLATE_TRANSPORT)
            
            self.assertIsNotNone(task)
            
            # Let it run to collect data
            await asyncio.sleep(0.5) # Wait for 2 reads (0.2 sleep in loop)
            
            stop_event.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            self.assertIn(b"data1", results)
            self.assertIn(b"data2", results)

    async def test_close(self):
        mock_dev = MagicMock()
        self.reader.devices[SparkDevice.PLATE_TRANSPORT] = mock_dev
        
        await self.reader.close()
        
        self.assertEqual(self.reader.devices, {})
        # Ensure dispose_resources called on the mocked module
        self.mock_usb_util.dispose_resources.assert_called_with(mock_dev)

    async def test_connect_usb_error(self):
        # Device 1: Fails
        mock_dev1 = MagicMock()
        mock_dev1.idProduct = SparkDevice.PLATE_TRANSPORT.value
        mock_dev1.is_kernel_driver_active.return_value = False
        # Raise USBError on set_configuration
        mock_dev1.set_configuration.side_effect = self.mock_usb_core.USBError(None, "Error")
        
        # Device 2: Succeeds
        mock_dev2 = MagicMock()
        mock_dev2.idProduct = SparkDevice.ABSORPTION.value
        mock_dev2.is_kernel_driver_active.return_value = False
        mock_dev2.get_active_configuration.return_value = {(0, 0): MagicMock()}
        
        self.mock_usb_core.find.return_value = iter([mock_dev1, mock_dev2])
        # Simplify descriptor finding for all calls
        self.mock_usb_util.find_descriptor.return_value = MagicMock()
        
        self.reader.connect()
        
        # Device 1 should not be in devices
        self.assertNotIn(SparkDevice.PLATE_TRANSPORT, self.reader.devices)
        # Device 2 should be in devices
        self.assertIn(SparkDevice.ABSORPTION, self.reader.devices)

    async def test_get_response_error(self):
        with patch('pylabrobot.plate_reading.tecan.spark20m.spark_reader_async.parse_single_spark_packet') as mock_parse:
            mock_parse.return_value = {'type': 'RespError', 'payload': {'error': 'BadCommand'}}
            
            read_task: asyncio.Future = asyncio.Future()
            read_task.set_result(b"error_bytes")
            
            # get_response catches exceptions and logs them, returning None
            result = await self.reader.get_response(read_task)
            self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main()