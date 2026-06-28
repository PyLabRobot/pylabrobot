import unittest
from typing import Dict, List

from pylabrobot.highres.sample_storage.driver import HighResSampleStorageDriver
from pylabrobot.highres.sample_storage.errors import (
  PlateNotFoundError,
  HighResSampleStorageError,
  HighResSampleStorageFault,
)

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

  async def test_is_homed(self):
    self.assertFalse(await self.retrieval.is_homed())

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
    self.assertFalse(await self.retrieval.spatula_request_is_holding())

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
    dims = await self.retrieval.get_stacker_dimensions()
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


def _ok(command: str, cid: int) -> List[str]:
  return [f"ACK! {command} {cid}", f"OK! {command} {cid}"]


class ScriptedSocket:
  """Replays an ordered (expected_command, response_lines) script, asserting the
  exact command sequence — used to verify multi-step recovery flows."""

  def __init__(self, script):
    self.script = list(script)
    self.i = 0
    self.commands: List[str] = []
    self._queue: List[str] = []

  async def setup(self):
    pass

  async def stop(self):
    pass

  async def write(self, data: bytes, timeout=None):
    command = data.decode("ascii").rstrip("\r\n")
    self.commands.append(command)
    expected, lines = self.script[self.i]
    self.i += 1
    assert command == expected, f"expected {expected!r}, got {command!r}"
    self._queue = list(lines)

  async def readuntil(self, separator: bytes = b"\n", timeout=None) -> bytes:
    return self._queue.pop(0).encode("ascii") + b"\r\n"


class HighResSampleStorageRecoveryTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.driver = HighResSampleStorageDriver(host="10.253.253.253")
    self.retrieval = self.driver.automated_retrieval

  async def test_empty_slot_pick_raises_plate_not_found_and_stays_homed(self):
    # The store reports "No plate detected" and stays homed (graceful empty).
    empty = [
      "ACK! pick 5 12 1 50",
      "Error 1: (00:00:01) 50: No plate detected",
      "ERROR! pick 5 12 1 50",
    ]
    sock = ScriptedSocket(
      [
        ("pick 5 12 1", empty),
        ("homedstatus", ["ACK! homedstatus 51", "homed", "OK! homedstatus 51"]),
      ]
    )
    self.driver.io = sock  # type: ignore[assignment]
    with self.assertRaises(PlateNotFoundError):
      await self.retrieval.pick(5, 12, 1)
    # classified by state, no recovery motion issued
    self.assertEqual(sock.commands, ["pick 5 12 1", "homedstatus"])

  async def test_top_slot_stuck_raises_fault_despite_homed_lie(self):
    # Empty TOP slot: "No plate detected" but the spatula is left extended and
    # the firmware reports "unsafe for rotation" while homedstatus still says
    # homed. The "unsafe" signal must win -> HighResSampleStorageFault (no homedstatus
    # query needed, the signature short-circuits).
    stuck = [
      "ACK! pick 5 24 1 60",
      "Error 1: 60: No plate detected",
      "Error 2: 60: Z height is unsafe for rotation, check machine",
      "ERROR! pick 5 24 1 60",
    ]
    sock = ScriptedSocket([("pick 5 24 1", stuck)])
    self.driver.io = sock  # type: ignore[assignment]
    with self.assertRaises(HighResSampleStorageFault):
      await self.retrieval.pick(5, 24, 1)
    self.assertEqual(sock.commands, ["pick 5 24 1"])

  async def test_dehomed_pick_raises_fault(self):
    # An error with no "unsafe" signature but the machine reports unhomed.
    fault = [
      "ACK! pick 5 24 1 70",
      "Error 1: 70: motor fault",
      "ERROR! pick 5 24 1 70",
    ]
    sock = ScriptedSocket(
      [
        ("pick 5 24 1", fault),
        ("homedstatus", ["ACK! homedstatus 71", "not homed", "OK! homedstatus 71"]),
      ]
    )
    self.driver.io = sock  # type: ignore[assignment]
    with self.assertRaises(HighResSampleStorageFault):
      await self.retrieval.pick(5, 24, 1)
    self.assertEqual(sock.commands, ["pick 5 24 1", "homedstatus"])

  def _homed(self, cid: int) -> List[str]:
    return [f"ACK! homedstatus {cid}", "homed", f"OK! homedstatus {cid}"]

  def _status(self, cid: int, y: float) -> List[str]:
    return [
      f"ACK! status {cid}",
      "Carousel: 0.0",
      f"Y axis: {y}",
      "Z axis: 0.0",
      f"OK! status {cid}",
    ]

  async def test_is_parked_catches_the_homed_lie(self):
    # homedstatus says homed, but the spatula is stuck extended (Y=256) -> NOT parked.
    sock = ScriptedSocket([("homedstatus", self._homed(1)), ("status", self._status(2, 255.9999))])
    self.driver.io = sock  # type: ignore[assignment]
    self.assertFalse(await self.retrieval.is_parked())

  async def test_recover_always_retracts_and_rehomes(self):
    # recover() must issue retract+home even when homedstatus already says homed
    # (the lie), then confirm parked via the slide position.
    sock = ScriptedSocket(
      [
        ("enable", _ok("enable", 1)),
        ("spatulaout", _ok("spatulaout", 2)),
        ("home", _ok("home", 3)),
        ("homedstatus", self._homed(4)),
        ("status", self._status(5, 0.0)),
      ]
    )
    self.driver.io = sock  # type: ignore[assignment]
    self.assertTrue(await self.retrieval.recover())
    self.assertEqual(sock.commands, ["enable", "spatulaout", "home", "homedstatus", "status"])

  async def test_recover_retries_until_parked(self):
    # First round still reads extended (homed-lie); recover retries and succeeds.
    sock = ScriptedSocket(
      [
        ("enable", _ok("enable", 1)),
        ("spatulaout", _ok("spatulaout", 2)),
        ("home", _ok("home", 3)),
        ("homedstatus", self._homed(4)),
        ("status", self._status(5, 255.9999)),  # still extended -> retry
        ("enable", _ok("enable", 6)),
        ("spatulaout", _ok("spatulaout", 7)),
        ("home", _ok("home", 8)),
        ("homedstatus", self._homed(9)),
        ("status", self._status(10, 0.0)),  # now retracted
      ]
    )
    self.driver.io = sock  # type: ignore[assignment]
    self.assertTrue(await self.retrieval.recover())

  async def test_place_default_leaves_doors_sealed(self):
    sock = ScriptedSocket([("place 2 5 1", _ok("place 2 5 1", 1))])
    self.driver.io = sock  # type: ignore[assignment]
    await self.retrieval.place(2, 5, 1)  # close_door=True default
    self.assertEqual(sock.commands, ["place 2 5 1"])

  async def test_place_close_door_false_reopens(self):
    sock = ScriptedSocket(
      [
        ("place 2 5 1", _ok("place 2 5 1", 1)),
        ("openalldoors", _ok("openalldoors", 2)),
      ]
    )
    self.driver.io = sock  # type: ignore[assignment]
    await self.retrieval.place(2, 5, 1, close_door=False)
    self.assertEqual(sock.commands, ["place 2 5 1", "openalldoors"])

  async def test_pick_close_door_false_reopens(self):
    sock = ScriptedSocket(
      [
        ("pick 2 5 1", _ok("pick 2 5 1", 1)),
        ("openalldoors", _ok("openalldoors", 2)),
      ]
    )
    self.driver.io = sock  # type: ignore[assignment]
    await self.retrieval.pick(2, 5, 1, close_door=False)
    self.assertEqual(sock.commands, ["pick 2 5 1", "openalldoors"])


if __name__ == "__main__":
  unittest.main()
