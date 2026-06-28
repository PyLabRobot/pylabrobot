import unittest
from typing import List

from pylabrobot.highres.sample_storage.driver import HighResSampleStorageDriver
from pylabrobot.highres.sample_storage.errors import HighResSampleStorageFault, PlateNotFoundError


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

  async def test_request_is_parked_catches_the_homed_lie(self):
    # homedstatus says homed, but the spatula is stuck extended (Y=256) -> NOT parked.
    sock = ScriptedSocket([("homedstatus", self._homed(1)), ("status", self._status(2, 255.9999))])
    self.driver.io = sock  # type: ignore[assignment]
    self.assertFalse(await self.retrieval.request_is_parked())

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
