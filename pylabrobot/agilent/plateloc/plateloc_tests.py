import asyncio
import contextlib
import unittest
from collections import deque
from typing import Deque

from pylabrobot.agilent.plateloc import (
  DEFAULT_PLATELOC_COMMANDS,
  PlateLoc,
  PlateLocDriver,
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
  def make_driver(self, commands=None, ack_timeout=0, timeout=30):
    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=ack_timeout,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
      commands=commands or DEFAULT_PLATELOC_COMMANDS,
    )
    return PlateLocDriver(port="COM6", profile=profile, timeout=timeout, serial_cls=FakeSerial)

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

  async def test_temperature_and_time_writes_are_scaled_and_validated(self):
    driver = self.make_driver()
    await driver.setup()

    await driver.set_sealing_temperature(30)
    await driver.set_sealing_time(0.5)

    self.assertEqual(driver.io.writes, [b"ST 0.030\r", b"SS 0.05\r"])

    with self.assertRaises(ValueError):
      await driver.set_sealing_temperature(19)
    with self.assertRaises(ValueError):
      await driver.set_sealing_time(0.4)

  async def test_negative_acknowledgement_raises_protocol_error(self):
    driver = self.make_driver(ack_timeout=0.01)
    await driver.setup()
    driver.io.queue_response(b"STNK(Desired Temperature is Out of Range)\r\r")

    with self.assertRaisesRegex(PlateLocError, "Desired Temperature is Out of Range"):
      await driver.set_sealing_temperature(30)

    self.assertEqual(driver.io.writes, [b"ST 0.030\r"])

  async def test_required_response_reads_until_plate_loc_ack(self):
    driver = self.make_driver()
    await driver.setup()
    driver.io.queue_response(b"CCAK\r")

    self.assertTrue(await driver.check_cycle_complete())
    self.assertEqual(driver.io.writes, [b"CC 00\r"])

  async def test_cycle_not_complete_returns_false(self):
    driver = self.make_driver()
    await driver.setup()
    driver.io.queue_response(b"CCNK\r")

    self.assertFalse(await driver.check_cycle_complete())
    self.assertEqual(driver.io.writes, [b"CC 00\r"])

  async def test_status_snapshot_tracks_setpoints_and_live_cycle_complete(self):
    driver = self.make_driver()
    await driver.setup()

    await driver.set_sealing_temperature(30)
    await driver.set_sealing_time(0.5)
    await driver.move_stage_out()
    driver.io.queue_response(b"CCAK\r")

    status = await driver.request_status()

    self.assertIsInstance(status, PlateLocStatus)
    self.assertEqual(status.port, "COM6")
    self.assertTrue(status.connected)
    self.assertEqual(status.target_temperature, 30)
    self.assertEqual(status.sealing_time, 0.5)
    self.assertEqual(status.stage_position, "open")
    self.assertTrue(status.cycle_complete)
    self.assertEqual(status.last_command, "check_cycle_complete")
    self.assertEqual(status.last_response, "CCAK")
    self.assertEqual(driver.io.writes, [b"ST 0.030\r", b"SS 0.05\r", b"SO 00\r", b"CC 00\r"])

  async def test_custom_command_profile(self):
    driver = self.make_driver(
      commands={
        "set_sealing_temperature": "TP",
        "set_sealing_time": "TM",
      }
    )
    await driver.setup()

    await driver.set_sealing_temperature(120)
    await driver.set_sealing_time(1.25)

    self.assertEqual(driver.io.writes, [b"TP 0.120\r", b"TM 0.12\r"])

  async def test_custom_command_acknowledgement_codes_are_parsed(self):
    commands = {
      **DEFAULT_PLATELOC_COMMANDS,
      "set_sealing_temperature": "TP",
      "check_cycle_complete": "CP",
    }
    driver = self.make_driver(commands=commands, ack_timeout=0.01)
    await driver.setup()
    driver.io.queue_response(b"TPNK(Desired Temperature is Out of Range)\r")

    with self.assertRaisesRegex(PlateLocError, "Desired Temperature is Out of Range"):
      await driver.set_sealing_temperature(120)

    driver.io.queue_response(b"CPAK\r")
    self.assertTrue(await driver.check_cycle_complete())
    self.assertEqual(driver.io.writes, [b"TP 0.120\r", b"CP 00\r"])

  async def test_seal_waits_for_cycle_completion(self):
    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=0,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
    )
    device = PlateLoc(name="plateloc", port="COM6", profile=profile, timeout=1, serial_cls=FakeSerial)

    await device.setup()
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

  async def test_device_exposes_sealer_capability(self):
    profile = PlateLocSerialProfile(
      response_timeout=0.01,
      ack_timeout=0,
      read_delay=0,
      stage_move_delay=0,
      cycle_poll_interval=0,
    )
    device = PlateLoc(name="plateloc", port="COM6", profile=profile, serial_cls=FakeSerial)

    await device.setup()
    await device.set_sealing_temperature(100)
    await device.set_sealing_time(0.5)
    device.driver.io.queue_response(b"CCAK\r")
    await device.sealer.seal(120, 1.2)
    await device.sealer.open()
    await device.sealer.close()
    status = device.status_snapshot()
    await device.stop()

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
      ],
    )
    self.assertEqual(status.target_temperature, 120)
    self.assertEqual(status.sealing_time, 1.2)
    self.assertEqual(status.stage_position, "closed")
    self.assertTrue(device.driver.io.stop_called)


if __name__ == "__main__":
  unittest.main()
