import asyncio
import contextlib
import unittest
from collections import deque
from typing import Deque
from unittest.mock import patch

import pylabrobot.agilent.plateloc.plateloc as plateloc_module
from pylabrobot.agilent.plateloc import (
  PlateLoc,
  PlateLocDriver,
  PlateLocError,
  PlateLocSealer,
  PlateLocSealerBackend,
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

  def make_driver(self, ack_timeout=0.01, timeout=30):
    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=ack_timeout,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
    )
    with self.patch_serial():
      return PlateLocDriver(port="COM6", profile=profile, timeout=timeout)

  def make_device(self, timeout=30):
    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=0.01,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
    )
    with self.patch_serial():
      return PlateLoc(name="plateloc", port="COM6", profile=profile, timeout=timeout)

  def backend(self, device: PlateLoc) -> PlateLocSealerBackend:
    return device.sealer.backend

  async def test_setup_uses_plr_serial_wrapper_settings(self):
    driver = self.make_driver()

    await driver.setup()

    self.assertTrue(driver.io.setup_called)
    self.assertEqual(driver.io.kwargs["human_readable_device_name"], "Agilent PlateLoc Sealer")
    self.assertEqual(driver.io.kwargs["port"], "COM6")
    self.assertEqual(driver.io.kwargs["baudrate"], 19200)
    self.assertEqual(driver.io.kwargs["bytesize"], 8)
    self.assertEqual(driver.io.kwargs["parity"], "N")
    self.assertEqual(driver.io.kwargs["stopbits"], 1)

    await driver.stop()
    self.assertTrue(driver.io.stop_called)

  async def test_driver_sends_literal_serial_frame(self):
    driver = self.make_driver()
    await driver.setup()
    driver.io.queue_response(b"STAK\r")

    response = await driver.send_command("ST 0.030", timeout=0.01)

    self.assertEqual(response, "STAK")
    self.assertEqual(driver.io.writes, [b"ST 0.030\r"])
    self.assertTrue(driver.io.reset_input_buffer_called)
    self.assertEqual(driver.last_command, "ST 0.030")
    self.assertEqual(driver.last_response, "STAK")

  async def test_temperature_and_time_writes_are_scaled_and_validated(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"STAK\r")
    driver.io.queue_response(b"SSAK\r")

    await backend.set_sealing_temperature(30)
    await backend.set_sealing_time(0.5)

    self.assertEqual(driver.io.writes, [b"ST 0.030\r", b"SS 0.05\r"])

    with self.assertRaises(ValueError):
      await backend.set_sealing_temperature(19)
    with self.assertRaises(ValueError):
      await backend.set_sealing_time(0.4)

  async def test_negative_acknowledgement_raises_protocol_error(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"STNK(Desired Temperature is Out of Range)\r\r")

    with self.assertRaisesRegex(PlateLocError, "Desired Temperature is Out of Range"):
      await backend.set_sealing_temperature(30)

    self.assertEqual(driver.io.writes, [b"ST 0.030\r"])

  async def test_missing_acknowledgement_raises_timeout(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()

    with self.assertRaisesRegex(TimeoutError, "Timeout"):
      await backend.set_sealing_temperature(30)

    self.assertEqual(driver.io.writes, [b"ST 0.030\r"])

  async def test_malformed_acknowledgement_raises_protocol_error(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"unexpected\r")

    with self.assertRaisesRegex(PlateLocError, "invalid response"):
      await backend.set_sealing_temperature(30)

    self.assertEqual(driver.io.writes, [b"ST 0.030\r"])

  async def test_required_response_reads_until_plate_loc_ack(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"CCAK\r")

    self.assertTrue(await backend.check_cycle_complete())
    self.assertEqual(driver.io.writes, [b"CC 00\r"])

  async def test_cycle_not_complete_returns_false(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"CCNK\r")

    self.assertFalse(await backend.check_cycle_complete())
    self.assertEqual(driver.io.writes, [b"CC 00\r"])

  async def test_invalid_cycle_complete_response_raises_protocol_error(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"unexpected\r")

    with self.assertRaisesRegex(PlateLocError, "invalid response"):
      await backend.check_cycle_complete()

    self.assertEqual(driver.io.writes, [b"CC 00\r"])

  async def test_status_snapshot_tracks_setpoints_and_live_cycle_complete(self):
    driver = self.make_driver()
    backend = PlateLocSealerBackend(driver)
    await driver.setup()
    driver.io.queue_response(b"STAK\r")
    driver.io.queue_response(b"SSAK\r")
    driver.io.queue_response(b"SOAK\r")

    await backend.set_sealing_temperature(30)
    await backend.set_sealing_time(0.5)
    await backend.move_stage_out()
    driver.io.queue_response(b"CCAK\r")

    status = await backend.request_status()

    self.assertIsInstance(status, PlateLocStatus)
    self.assertEqual(status.port, "COM6")
    self.assertTrue(status.connected)
    self.assertEqual(status.target_temperature, 30)
    self.assertEqual(status.sealing_time, 0.5)
    self.assertEqual(status.stage_position, "open")
    self.assertTrue(status.cycle_complete)
    self.assertEqual(status.last_command, "CC 00")
    self.assertEqual(status.last_response, "CCAK")
    self.assertEqual(driver.io.writes, [b"ST 0.030\r", b"SS 0.05\r", b"SO 00\r", b"CC 00\r"])

  async def test_seal_waits_for_cycle_completion(self):
    device = self.make_device(timeout=1)
    backend = self.backend(device)

    await device.setup()
    device.driver.io.queue_response(b"STAK\r")
    device.driver.io.queue_response(b"SSAK\r")
    device.driver.io.queue_response(b"GOAK\r")
    device.driver.io.queue_response(b"CCNK\r")
    device.driver.io.queue_response(b"CCAK\r")

    await device.sealer.seal(120, 1.2)

    self.assertEqual(
      device.driver.io.writes,
      [
        b"ST 0.120\r",
        b"SS 0.12\r",
        b"GO 00\r",
        b"CC 00\r",
        b"CC 00\r",
      ],
    )
    self.assertEqual(backend.status_snapshot().target_temperature, 120)
    self.assertEqual(backend.status_snapshot().sealing_time, 1.2)

  async def test_device_exposes_plate_loc_sealer_capability(self):
    device = self.make_device()

    await device.setup()
    device.driver.io.queue_response(b"STAK\r")
    await device.sealer.set_sealing_temperature(100)
    device.driver.io.queue_response(b"SSAK\r")
    await device.sealer.set_sealing_time(0.5)
    device.driver.io.queue_response(b"STAK\r")
    device.driver.io.queue_response(b"SSAK\r")
    device.driver.io.queue_response(b"GOAK\r")
    device.driver.io.queue_response(b"CCAK\r")
    await device.sealer.seal(120, 1.2)
    device.driver.io.queue_response(b"SOAK\r")
    await device.sealer.open()
    device.driver.io.queue_response(b"SIAK\r")
    await device.sealer.close()
    device.driver.io.queue_response(b"CCAK\r")
    status = await device.sealer.request_status()
    await device.stop()

    self.assertIsInstance(device.sealer, PlateLocSealer)
    self.assertFalse(hasattr(device, "set_sealing_temperature"))
    self.assertFalse(hasattr(device, "set_sealing_time"))
    self.assertEqual(
      device.driver.io.writes,
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
    self.assertTrue(device.driver.io.stop_called)


if __name__ == "__main__":
  unittest.main()
