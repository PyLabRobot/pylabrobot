import unittest
from typing import Dict, List

from pylabrobot.highres.sample_storage.driver import HighResSampleStorageDriver
from pylabrobot.highres.sample_storage.errors import HighResSampleStorageError

# Real responses captured from a TundraStore (firmware 3.0.0.119, serial
# HRB-2209-35148) over the port-1000 remote-control server.
CAPTURES: Dict[str, List[str]] = {
  "version": [
    "ACK! version 1",
    "Product Name: SteriStore",
    "Serial Number: HRB-2209-35148",
    "libcommon Version:    1.1.0.119",
    "libts7600 Version:    1.0.0.119",
    "Firmware Version:     3.0.0.119",
    "Firmware Build: D9BE232A",
    "OK! version 1",
  ],
  "homedstatus": ["ACK! homedstatus 5", "not homed", "OK! homedstatus 5"],
  "doorstatus": [
    "ACK! doorstatus 17",
    "User Door: CLOSED",
    "RI: CLOSED",
    "SEAL: CLOSING",
    "RO1: CLOSING",
    "RO2: CLOSED",
    "RO3: CLOSED",
    "RO4: CLOSED",
    "OK! doorstatus 17",
  ],
  "platestatus": ["ACK! platestatus 9", "NO_PLATE", "OK! platestatus 9"],
  "neststatus": ["ACK! neststatus 11", "1: CLEAR", "2: CLEAR", "OK! neststatus 11"],
  "environmentstatus": [
    "ACK! environmentstatus 31",
    "TEMP:21.9/22.0/100.0",
    "RH:54.7/0.0/-100.0",
    "CO2:0.0/5.0/100.0",
    "O2:20.5/5.0/100.0",
    "TANK1:135.0:",
    "TANK2:135.0:",
    "OK! environmentstatus 31",
  ],
  "getstackerdimensions": [
    "ACK! getstackerdimensions 35",
    "1: 0.000 28.940 0",
    "2: 0.000 22.867 24",
    "13: 0.000 22.867 0",
    "OK! getstackerdimensions 35",
  ],
  # The home command failed because the pneumatic doors could not close (no air).
  "home": [
    "ACK! home 13",
    "Error 1: (00:32:44) 13: Unable to close all doors",
    "ERROR! home 13",
  ],
}


class FakeSocket:
  """Replays scripted line responses keyed by the command written to it."""

  def __init__(self, captures: Dict[str, List[str]]):
    self.captures = captures
    self.written: List[str] = []
    self._queue: List[str] = []

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, timeout=None):
    command = data.decode("ascii").rstrip("\r\n")
    self.written.append(command)
    self._queue = list(self.captures[command])

  async def readuntil(self, separator: bytes = b"\n", timeout=None) -> bytes:
    return self._queue.pop(0).encode("ascii") + b"\r\n"


class HighResSampleStorageBackendTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.driver = HighResSampleStorageDriver(host="10.253.253.253")
    self.socket = FakeSocket(CAPTURES)
    self.driver.io = self.socket  # type: ignore[assignment]
    self.retrieval = self.driver.automated_retrieval

  async def test_send_command_strips_ack_and_completion(self):
    data = await self.driver.send_command("neststatus")
    self.assertEqual(data, ["1: CLEAR", "2: CLEAR"])
    self.assertEqual(self.socket.written, ["neststatus"])

  async def test_version(self):
    v = await self.driver.request_version()
    self.assertEqual(v.product_name, "SteriStore")
    self.assertEqual(v.serial_number, "HRB-2209-35148")
    self.assertEqual(v.firmware_version, "3.0.0.119")
    self.assertEqual(v.firmware_build, "D9BE232A")

  async def test_request_is_homed(self):
    self.assertFalse(await self.retrieval.request_is_homed())

  async def test_door_status(self):
    doors = await self.retrieval.request_door_status()
    self.assertEqual(doors["User Door"], "closed")
    self.assertEqual(doors["SEAL"], "closing")
    self.assertEqual(doors["RO1"], "closing")
    self.assertFalse(all(state == "closed" for state in doors.values()))

  async def test_nest_status(self):
    nests = await self.retrieval.request_nest_status()
    self.assertEqual(nests, {1: "clear", 2: "clear"})

  async def test_plate_on_spatula(self):
    self.assertFalse(await self.retrieval.request_spatula_is_holding())

  async def test_environment_parsing(self):
    env = await self.driver.request_environment()
    self.assertAlmostEqual(env["TEMP"].current, 21.9)
    self.assertIsNotNone(env["TEMP"].setpoint)
    assert env["TEMP"].setpoint is not None  # narrow for type checker
    self.assertAlmostEqual(env["TEMP"].setpoint, 22.0)
    self.assertAlmostEqual(env["O2"].current, 20.5)
    # Sensor-only channel: current value, no setpoint.
    self.assertAlmostEqual(env["TANK1"].current, 135.0)
    self.assertIsNone(env["TANK1"].setpoint)

  async def test_temperature_capability_reads_temp_channel(self):
    self.assertAlmostEqual(await self.driver.temperature.request_current_temperature(), 21.9)
    self.assertTrue(self.driver.temperature.supports_active_cooling)

  async def test_humidity_capability_reads_rh_as_fraction(self):
    self.assertAlmostEqual(await self.driver.humidity.request_current_humidity(), 0.547)
    self.assertFalse(self.driver.humidity.supports_humidity_control)

  async def test_stacker_dimensions(self):
    dims = await self.retrieval.request_stacker_dimensions()
    self.assertEqual(dims[0].stacker, 1)
    self.assertEqual(dims[0].slot_count, 0)
    self.assertEqual(dims[1].stacker, 2)
    self.assertAlmostEqual(dims[1].slot_height, 22.867)
    self.assertEqual(dims[1].slot_count, 24)

  async def test_home_error_raises_with_stack_detail(self):
    with self.assertRaises(HighResSampleStorageError) as ctx:
      await self.retrieval.home()
    self.assertIn("Unable to close all doors", str(ctx.exception))
    self.assertEqual(ctx.exception.command, "home")

  async def test_pick_formats_command(self):
    self.socket.captures["pick 3 12 1"] = ["ACK! pick 3 12 1 99", "OK! pick 3 12 1 99"]
    await self.retrieval.pick(3, 12, 1)
    self.assertEqual(self.socket.written, ["pick 3 12 1"])

  def test_tray_maps_to_nest(self):
    # 0-based capability tray -> 1-based device nest; None uses the default.
    self.assertEqual(self.retrieval._nest_for_tray(None), 1)
    self.assertEqual(self.retrieval._nest_for_tray(0), 1)
    self.assertEqual(self.retrieval._nest_for_tray(1), 2)
    with self.assertRaises(ValueError):
      self.retrieval._nest_for_tray(2)

  def test_default_tray_follows_loading_tray_nest(self):
    driver = HighResSampleStorageDriver(host="10.253.253.253", loading_tray_nest=2)
    self.assertEqual(driver.automated_retrieval._nest_for_tray(None), 2)

  async def test_set_humidity_unsupported(self):
    with self.assertRaises(NotImplementedError):
      await self.driver.humidity.set_humidity(0.5)


if __name__ == "__main__":
  unittest.main()
