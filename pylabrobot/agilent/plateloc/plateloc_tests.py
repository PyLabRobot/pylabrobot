import asyncio
import contextlib
import unittest
from collections import deque
from typing import Deque, cast
from unittest.mock import patch

import pylabrobot.agilent.plateloc.plateloc as plateloc_module
from pylabrobot.agilent.plateloc import (
  PlateLoc,
  PlateLocError,
  PlateLocSerialProfile,
  PlateLocStatus,
)


class FakeSerial:
  def __init__(self, **kwargs):
    self.kwargs = kwargs
    self._port = kwargs["port"]
    self.writes = []
    self.responses: Deque[bytes] = deque()
    self.setup_called = False
    self.stop_called = False
    self.timeout = kwargs["timeout"]
    self.reset_input_buffer_called = False

  @property
  def port(self):
    return self._port

  @contextlib.contextmanager
  def temporary_timeout(self, timeout: float):
    previous_timeout = self.timeout
    self.timeout = timeout
    try:
      yield
    finally:
      self.timeout = previous_timeout

  async def setup(self):
    self.setup_called = True

  async def stop(self):
    self.stop_called = True

  async def write(self, data: bytes):
    self.writes.append(data)

  async def read(self, num_bytes: int = 1) -> bytes:
    if not self.responses:
      await asyncio.sleep(0)
      return b""
    response = self.responses[0]
    chunk = response[:num_bytes]
    response = response[num_bytes:]
    if response:
      self.responses[0] = response
    else:
      self.responses.popleft()
    return chunk

  def queue_response(self, response: bytes):
    self.responses.append(response)

  async def reset_input_buffer(self):
    self.reset_input_buffer_called = True


class PlateLocTests(unittest.IsolatedAsyncioTestCase):
  @contextlib.contextmanager
  def patch_serial(self):
    with (
      patch.object(plateloc_module, "HAS_SERIAL", True),
      patch.object(plateloc_module, "Serial", FakeSerial),
    ):
      yield

  def make_device(self, ack_timeout: float = 0.01, timeout: float = 30) -> PlateLoc:
    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=ack_timeout,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
    )
    with self.patch_serial():
      return PlateLoc(port="COM6", profile=profile, timeout=timeout)

  def fake_io(self, device: PlateLoc) -> FakeSerial:
    return cast(FakeSerial, device.io)

  async def test_setup_uses_plr_serial_wrapper_settings(self):
    device = self.make_device()
    io = self.fake_io(device)

    await device.setup()

    self.assertTrue(io.setup_called)
    self.assertEqual(io.kwargs["human_readable_device_name"], "Agilent PlateLoc Sealer")
    self.assertEqual(io.kwargs["port"], "COM6")
    self.assertEqual(io.kwargs["baudrate"], 19200)
    self.assertEqual(io.kwargs["bytesize"], 8)
    self.assertEqual(io.kwargs["parity"], "N")
    self.assertEqual(io.kwargs["stopbits"], 1)

    await device.stop()
    self.assertTrue(io.stop_called)

  async def test_sends_literal_serial_frame(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"STAK\r")

    response = await device.send_command("ST 0.030", timeout=0.01)

    self.assertEqual(response, "STAK")
    self.assertEqual(io.writes, [b"ST 0.030\r"])
    self.assertTrue(io.reset_input_buffer_called)
    self.assertEqual(device.last_command, "ST 0.030")
    self.assertEqual(device.last_response, "STAK")

  async def test_temperature_and_time_writes_are_scaled_and_validated(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"STAK\r")
    io.queue_response(b"SSAK\r")

    await device.set_sealing_temperature(30)
    await device.set_sealing_time(0.5)

    self.assertEqual(io.writes, [b"ST 0.030\r", b"SS 0.05\r"])

    with self.assertRaises(ValueError):
      await device.set_sealing_temperature(19)
    with self.assertRaises(ValueError):
      await device.set_sealing_time(0.4)

  async def test_negative_acknowledgement_raises_protocol_error(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"STNK(Desired Temperature is Out of Range)\r\r")

    with self.assertRaisesRegex(PlateLocError, "Desired Temperature is Out of Range"):
      await device.set_sealing_temperature(30)

    self.assertEqual(io.writes, [b"ST 0.030\r"])

  async def test_missing_acknowledgement_raises_timeout(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()

    with self.assertRaisesRegex(TimeoutError, "Timeout"):
      await device.set_sealing_temperature(30)

    self.assertEqual(io.writes, [b"ST 0.030\r"])

  async def test_malformed_acknowledgement_raises_protocol_error(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"unexpected\r")

    with self.assertRaisesRegex(PlateLocError, "invalid response"):
      await device.set_sealing_temperature(30)

    self.assertEqual(io.writes, [b"ST 0.030\r"])

  async def test_required_response_reads_until_plate_loc_ack(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"CCAK\r")

    self.assertTrue(await device.check_cycle_complete())
    self.assertEqual(io.writes, [b"CC 00\r"])

  async def test_cycle_not_complete_returns_false(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"CCNK\r")

    self.assertFalse(await device.check_cycle_complete())
    self.assertEqual(io.writes, [b"CC 00\r"])

  async def test_invalid_cycle_complete_response_raises_protocol_error(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"unexpected\r")

    with self.assertRaisesRegex(PlateLocError, "invalid response"):
      await device.check_cycle_complete()

    self.assertEqual(io.writes, [b"CC 00\r"])

  async def test_status_snapshot_tracks_setpoints_and_live_cycle_complete(self):
    device = self.make_device()
    io = self.fake_io(device)
    await device.setup()
    io.queue_response(b"STAK\r")
    io.queue_response(b"SSAK\r")
    io.queue_response(b"SOAK\r")

    await device.set_sealing_temperature(30)
    await device.set_sealing_time(0.5)
    await device.move_stage_out()
    io.queue_response(b"CCAK\r")

    status = await device.request_status()

    self.assertIsInstance(status, PlateLocStatus)
    self.assertEqual(status.port, "COM6")
    self.assertTrue(status.connected)
    self.assertEqual(status.target_temperature, 30)
    self.assertEqual(status.sealing_time, 0.5)
    self.assertEqual(status.stage_position, "open")
    self.assertTrue(status.cycle_complete)
    self.assertEqual(status.last_command, "CC 00")
    self.assertEqual(status.last_response, "CCAK")
    self.assertEqual(io.writes, [b"ST 0.030\r", b"SS 0.05\r", b"SO 00\r", b"CC 00\r"])

  async def test_seal_waits_for_cycle_completion(self):
    device = self.make_device(timeout=1)
    io = self.fake_io(device)

    await device.setup()
    io.queue_response(b"STAK\r")
    io.queue_response(b"SSAK\r")
    io.queue_response(b"GOAK\r")
    io.queue_response(b"CCNK\r")
    io.queue_response(b"CCAK\r")

    await device.seal(120, 1.2)

    self.assertEqual(
      io.writes,
      [
        b"ST 0.120\r",
        b"SS 0.12\r",
        b"GO 00\r",
        b"CC 00\r",
        b"CC 00\r",
      ],
    )
    self.assertEqual(device.status_snapshot().target_temperature, 120)
    self.assertEqual(device.status_snapshot().sealing_time, 1.2)

  async def test_device_exposes_plain_sealer_api(self):
    device = self.make_device()
    io = self.fake_io(device)

    await device.setup()
    io.queue_response(b"STAK\r")
    await device.set_sealing_temperature(100)
    io.queue_response(b"SSAK\r")
    await device.set_sealing_time(0.5)
    io.queue_response(b"STAK\r")
    io.queue_response(b"SSAK\r")
    io.queue_response(b"GOAK\r")
    io.queue_response(b"CCAK\r")
    await device.seal(120, 1.2)
    io.queue_response(b"SOAK\r")
    await device.open()
    io.queue_response(b"SIAK\r")
    await device.close()
    io.queue_response(b"CCAK\r")
    status = await device.request_status()
    await device.stop()

    self.assertEqual(
      io.writes,
      [
        b"ST 0.100\r",
        b"SS 0.05\r",
        b"ST 0.120\r",
        b"SS 0.12\r",
        b"GO 00\r",
        b"CC 00\r",
        b"SO 00\r",
        b"SI 00\r",
        b"CC 00\r",
      ],
    )
    self.assertEqual(status.target_temperature, 120)
    self.assertEqual(status.sealing_time, 1.2)
    self.assertEqual(status.stage_position, "closed")
    self.assertTrue(status.cycle_complete)
    self.assertTrue(io.stop_called)


if __name__ == "__main__":
  unittest.main()
