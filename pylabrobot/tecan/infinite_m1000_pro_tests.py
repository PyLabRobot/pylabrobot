import unittest
from unittest.mock import AsyncMock, patch

from pylabrobot.tecan import InfiniteM1000Pro


def reader_frame(text):
  body = b"\x02" + text.encode("ascii") + b"\x03"
  checksum = 0
  for byte in body:
    checksum ^= byte
  return body + b"\x00" + (len(text) + 1).to_bytes(2, "big") + bytes([checksum, 13])


class InfiniteM1000ProTests(unittest.IsolatedAsyncioTestCase):
  async def test_initialize_then_open_with_mocked_transport(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY#T85000"),
          reader_frame("MSG9:test"),
          reader_frame("ST"),
          reader_frame("IN"),
          reader_frame("BY") + reader_frame("ST"),
          reader_frame("OUT"),
        ]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      await device.initialize()
      await device.open()
      self.assertEqual(usb.write.await_count, 5)

  async def test_tray_refuses_motion_before_initialization(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("OUT"))
      device = InfiniteM1000Pro()
      await device.setup()
      with self.assertRaisesRegex(RuntimeError, "initialize"):
        await device.open()
      usb.write.assert_not_awaited()

  async def test_initialize_refuses_unexpected_buffered_response_before_reset(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("ST") + reader_frame("ST"))
      device = InfiniteM1000Pro()
      await device.setup()
      with self.assertRaisesRegex(RuntimeError, "pending"):
        await device.initialize()
      self.assertEqual(usb.write.await_count, 1)

  async def test_initialize_refuses_extra_response_after_completion(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY"),
          reader_frame("ST") + reader_frame("OUT"),
        ]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      with self.assertRaisesRegex(RuntimeError, "pending"):
        await device.initialize()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()

  async def test_initialize_refuses_busy_reader_without_reset(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("BY"))
      device = InfiniteM1000Pro()
      await device.setup()
      with self.assertRaisesRegex(RuntimeError, "not in standby"):
        await device.initialize()
      self.assertEqual(usb.write.await_count, 1)
      with self.assertRaisesRegex(RuntimeError, "uncertain"):
        await device.setup()

  async def test_initialize_timeout_faults_session_without_retry(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[reader_frame("ST"), reader_frame("BY#T85000"), TimeoutError()]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      with self.assertRaises(TimeoutError):
        await device.initialize()
      self.assertEqual(usb.write.await_count, 2)
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()
      with self.assertRaisesRegex(RuntimeError, "uncertain"):
        await device.setup()

  async def test_initialize_waits_for_completion_once_per_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.drain = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("ST"),
          reader_frame("BY#T85000"),
          reader_frame("MSG9:initializing") + reader_frame("ST"),
        ]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      await device.initialize()
      await device.initialize()
      self.assertEqual(len(usb.write.await_args_list), 2)
      self.assertIn(b"QQ", usb.write.await_args_list[0].args[0])
      self.assertIn(b"INIT FORCE", usb.write.await_args_list[1].args[0])

  async def test_setup_opens_standard_usb_transport(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      device = InfiniteM1000Pro()
      await device.setup()
      usb.setup.assert_awaited_once_with(empty_buffer=False)
      self.assertEqual(usb_class.call_args.kwargs["id_vendor"], 0x0C47)
      self.assertEqual(usb_class.call_args.kwargs["id_product"], 0x8009)
      self.assertEqual(usb_class.call_args.kwargs["read_endpoint_address"], 0x81)
      self.assertEqual(usb_class.call_args.kwargs["write_endpoint_address"], 0x02)

  async def test_repeated_setup_does_not_reconnect_or_discard_pending_input(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      device = InfiniteM1000Pro()
      await device.setup()
      device._buffer.extend(reader_frame("ST"))
      await device.setup()
      usb.setup.assert_awaited_once_with(empty_buffer=False)
      self.assertEqual(device._buffer, reader_frame("ST"))

  async def test_failed_reconnect_disables_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock(side_effect=[None, RuntimeError("usb failure"), None])
      usb.stop = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("OUT"))
      device = InfiniteM1000Pro()
      await device.setup()
      await device.stop()
      with self.assertRaisesRegex(RuntimeError, "usb failure"):
        await device.setup()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()
      usb.write.assert_not_awaited()
      with self.assertRaisesRegex(RuntimeError, "uncertain"):
        await device.setup()
      self.assertEqual(usb.setup.await_count, 2)

  async def test_open_skips_informational_frames_before_command_response(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("MSG4:initializing") + reader_frame("IN"),
          reader_frame("BY#T85000"),
          reader_frame("LOG:working") + reader_frame("ST"),
          reader_frame("OUT"),
        ]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True  # Simulate prior successful initialization in tray-only tests.
      await device.open()
      self.assertEqual(usb.write.await_count, 3)

  async def test_receive_does_not_truncate_remaining_timeout(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.read = AsyncMock(return_value=reader_frame("ST"))
      device = InfiniteM1000Pro()
      with patch("pylabrobot.tecan.infinite_m1000_pro.time") as clock:
        clock.monotonic.side_effect = [100.0, 100.1, 100.1, 100.2]
        self.assertEqual(await device._receive(), "ST")
      self.assertGreaterEqual(usb.read.await_args_list[0].kwargs["timeout"], 29.9)

  async def test_oversized_complete_response_faults_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("MSG" + "x" * 4100) + reader_frame("OUT"))
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True
      with self.assertRaisesRegex(ValueError, "too long"):
        await device.open()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()
      self.assertEqual(usb.write.await_count, 1)

  async def test_open_already_out_is_idempotent(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("OUT"))
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True  # Simulate prior successful initialization in tray-only tests.
      await device.open()
      self.assertEqual(usb.write.await_count, 1)

  async def test_close_handles_fragmented_and_coalesced_responses(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      position = reader_frame("OUT")
      usb.read = AsyncMock(
        side_effect=[
          position[:3],
          position[3:],
          reader_frame("BY") + reader_frame("ST"),
          reader_frame("IN"),
        ]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True  # Isolate tray behavior from initialization.
      await device.close()
      self.assertEqual(usb.write.await_count, 3)
      self.assertIn(b"ABSOLUTE MTP,IN", usb.write.await_args_list[1].args[0])

  async def test_unexpected_position_refuses_motion_and_faults_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("UNKNOWN"))
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True
      with self.assertRaisesRegex(RuntimeError, "Unexpected carrier position"):
        await device.open()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()
      self.assertEqual(usb.write.await_count, 1)

  async def test_faulted_session_cannot_be_reenabled_by_reconnecting(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.stop = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(return_value=reader_frame("UNKNOWN"))
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True
      with self.assertRaisesRegex(RuntimeError, "Unexpected carrier position"):
        await device.open()
      await device.stop()
      with self.assertRaisesRegex(RuntimeError, "uncertain"):
        await device.setup()
      self.assertEqual(usb.setup.await_count, 1)

  async def test_unexpected_movement_status_fails_without_retry(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), reader_frame("ER")])
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True
      with self.assertRaisesRegex(RuntimeError, "Unexpected movement status"):
        await device.open()
      self.assertEqual(usb.write.await_count, 2)

  async def test_checksum_failure_faults_session(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      corrupt = bytearray(reader_frame("IN"))
      corrupt[-2] ^= 1
      usb.read = AsyncMock(return_value=bytes(corrupt))
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True
      with self.assertRaisesRegex(ValueError, "checksum"):
        await device.open()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()
      self.assertEqual(usb.write.await_count, 1)

  async def test_position_mismatch_raises(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(side_effect=[reader_frame("IN"), reader_frame("ST"), reader_frame("IN")])
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True
      with self.assertRaisesRegex(RuntimeError, "not 'OUT'"):
        await device.open()

  async def test_stop_closes_transport(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.stop = AsyncMock()
      device = InfiniteM1000Pro()
      await device.setup()
      await device.stop()
      usb.stop.assert_awaited_once()
      with self.assertRaisesRegex(RuntimeError, "not ready"):
        await device.open()

  async def test_open_checks_position_moves_and_verifies(self):
    with patch("pylabrobot.tecan.infinite_m1000_pro.USB") as usb_class:
      usb = usb_class.return_value
      usb.setup = AsyncMock()
      usb.write = AsyncMock()
      usb.read = AsyncMock(
        side_effect=[
          reader_frame("IN"),
          reader_frame("BY"),
          reader_frame("ST"),
          reader_frame("OUT"),
        ]
      )
      device = InfiniteM1000Pro()
      await device.setup()
      device._initialized = True  # Simulate prior successful initialization in tray-only tests.
      await device.open()
      self.assertEqual(
        [call.args[0] for call in usb.write.await_args_list],
        [
          bytes.fromhex("02 3f 41 42 53 4f 4c 55 54 45 20 4d 54 50 2c 50 4f 53 03 00 00 11 20 0d"),
          bytes.fromhex("02 41 42 53 4f 4c 55 54 45 20 4d 54 50 2c 4f 55 54 03 00 00 10 1d 0d"),
          bytes.fromhex("02 3f 41 42 53 4f 4c 55 54 45 20 4d 54 50 2c 50 4f 53 03 00 00 11 20 0d"),
        ],
      )
