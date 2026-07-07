"""Tests for the BD FACSMelody backend.

All tests are device-free. The armed-transmission tests use an in-memory fake
connection so the safety guards can be exercised without an instrument.
"""

import unittest
from typing import List

from pylabrobot.becton_dickinson.facsmelody import (
  FACSMelody,
  ProtocolMapIncompleteError,
  SortActuationError,
)
from pylabrobot.becton_dickinson.facsmelody.backend import (
  FACSMelodyCellSorterBackend,
  FACSMelodyDriver,
  _Connection,
)
from pylabrobot.becton_dickinson.facsmelody.constants import REQUIRED_COMMANDS, Transport
from pylabrobot.becton_dickinson.facsmelody.protocol_map import Command, ProtocolMap


def _complete_map() -> ProtocolMap:
  """A fully decoded map: every required command has a frame template."""
  pm = ProtocolMap(transport=Transport.TCP, endpoint="10.0.0.5:9100")
  for name, note in REQUIRED_COMMANDS:
    template = {
      "start_sort": "aa{wells}55",
      "set_deposition": "bb{cells}{plate}",
      "load_template": "cc{name}",
    }.get(name, "00")
    pm.commands[name] = Command(
      name=name,
      transport=Transport.TCP,
      frame_template=template,
      decoded=True,
      notes=note,
    )
  return pm


class _FakeConnection(_Connection):
  """Records written frames; returns no response."""

  def __init__(self) -> None:
    self.written: List[bytes] = []

  def write(self, data: bytes) -> None:
    self.written.append(data)

  def read(self, size: int = 512) -> bytes:
    return b""

  def close(self) -> None:
    pass


class TestFACSMelodyDryRun(unittest.IsolatedAsyncioTestCase):
  async def test_dry_run_sort_runs_without_hardware(self):
    dev = FACSMelody()  # armed=False
    await dev.setup()
    await dev.sorter.sort_to_plate(cells_per_well=1, wells=96, template="singlet_deposit")
    await dev.stop()

  async def test_armed_with_incomplete_map_refuses(self):
    dev = FACSMelody(armed=True)  # no protocol_path -> seeded, all undecoded
    with self.assertRaises(ProtocolMapIncompleteError) as ctx:
      await dev.setup()
    self.assertEqual(set(ctx.exception.missing), {name for name, _ in REQUIRED_COMMANDS})


class TestFACSMelodyActuationGuard(unittest.IsolatedAsyncioTestCase):
  def _armed_backend(self, allow_actuation: bool):
    driver = FACSMelodyDriver(armed=True, allow_actuation=allow_actuation)
    driver.pm = _complete_map()
    conn = _FakeConnection()
    driver._conn = conn  # bypass a real link
    return FACSMelodyCellSorterBackend(driver), conn

  async def test_actuating_command_blocked_without_opt_in(self):
    backend, conn = self._armed_backend(allow_actuation=False)
    with self.assertRaises(SortActuationError):
      await backend.start_sort(wells=96)
    self.assertEqual(conn.written, [])

  async def test_actuating_command_transmits_when_allowed(self):
    backend, conn = self._armed_backend(allow_actuation=True)
    await backend.start_sort(wells=96)
    # 0xaa + (96 & 0xff = 0x60) + 0x55
    self.assertEqual(conn.written, [bytes.fromhex("aa6055")])

  async def test_readonly_command_transmits_without_actuation_opt_in(self):
    backend, conn = self._armed_backend(allow_actuation=False)
    await backend.get_status()  # not an actuating command
    self.assertEqual(conn.written, [bytes.fromhex("00")])


class TestFACSMelodyFraming(unittest.IsolatedAsyncioTestCase):
  async def test_frame_substitutes_parameters(self):
    driver = FACSMelodyDriver()
    driver.pm = _complete_map()
    backend = FACSMelodyCellSorterBackend(driver)
    # load_template template is "cc{name}"; name encodes as utf-8 hex of "gate"
    self.assertEqual(backend._frame("load_template", name="gate"), "cc" + "gate".encode().hex())
    # set_deposition template "bb{cells}{plate}" with cells=50 (0x32), plate="96"
    self.assertEqual(
      backend._frame("set_deposition", cells=50, plate="96"),
      "bb" + "32" + "96".encode().hex(),
    )

  async def test_undecoded_command_yields_empty_frame(self):
    driver = FACSMelodyDriver()  # seeded lazily on setup; set directly here
    from pylabrobot.becton_dickinson.facsmelody.protocol_map import seed_required

    driver.pm = seed_required()
    backend = FACSMelodyCellSorterBackend(driver)
    self.assertEqual(backend._frame("start_sort", wells=1), "")


class TestFACSMelodySerialize(unittest.IsolatedAsyncioTestCase):
  async def test_driver_serialize_roundtrips_config(self):
    driver = FACSMelodyDriver(protocol_path="p.json", armed=True, allow_actuation=True)
    data = driver.serialize()
    self.assertEqual(data["protocol_path"], "p.json")
    self.assertTrue(data["armed"])
    self.assertTrue(data["allow_actuation"])


if __name__ == "__main__":
  unittest.main()
