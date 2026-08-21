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
  SortNotReadyError,
  SortTimeoutError,
)
from pylabrobot.becton_dickinson.facsmelody.backend import (
  FACSMelodyCellSorterBackend,
  FACSMelodyDriver,
  _Connection,
)
from pylabrobot.becton_dickinson.facsmelody.constants import (
  ACTUATING_COMMANDS,
  REQUIRED_COMMANDS,
  Transport,
)
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

  async def test_device_serialize_roundtrips_config(self):
    dev = FACSMelody(protocol_path="p.json", armed=True, allow_actuation=True)
    restored = FACSMelody.deserialize(dev.serialize())
    self.assertEqual(restored.driver.protocol_path, "p.json")
    self.assertTrue(restored.driver.armed)
    self.assertTrue(restored.driver.allow_actuation)


class TestFACSMelodyCoverageGate(unittest.IsolatedAsyncioTestCase):
  async def test_coverage_flags_required_commands_absent_from_map(self):
    # A map that decodes only start_sort and OMITS every other required command must
    # still report those as missing, so it cannot pass the live-run gate.
    pm = ProtocolMap(transport=Transport.TCP, endpoint="10.0.0.5:9100")
    pm.commands["start_sort"] = Command(
      name="start_sort", transport=Transport.TCP, frame_template="aa{wells}55", decoded=True
    )
    cov = pm.coverage()
    required = {name for name, _ in REQUIRED_COMMANDS}
    self.assertEqual(set(cov["missing"]), required - {"start_sort"})
    self.assertEqual(cov["total"], len(required))
    self.assertEqual(cov["decoded"], 1)

  async def test_armed_setup_refuses_map_missing_required_commands(self):
    # Write a real map JSON that decodes only start_sort, then drive the actual
    # setup() gate (via from_json) and confirm it refuses to open a live link.
    import tempfile

    pm = ProtocolMap(transport=Transport.TCP, endpoint="10.0.0.5:9100")
    pm.commands["start_sort"] = Command(
      name="start_sort", transport=Transport.TCP, frame_template="aa{wells}55", decoded=True
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
      path = fh.name
    pm.to_json(path)

    dev = FACSMelody(protocol_path=path, armed=True)
    with self.assertRaises(ProtocolMapIncompleteError) as ctx:
      await dev.setup()
    required = {name for name, _ in REQUIRED_COMMANDS}
    self.assertEqual(set(ctx.exception.missing), required - {"start_sort"})


class TestFACSMelodyEncodeParam(unittest.IsolatedAsyncioTestCase):
  async def test_encode_param_refuses_out_of_range_int(self):
    from pylabrobot.becton_dickinson.facsmelody.backend import _encode_param

    self.assertEqual(_encode_param(96), "60")
    self.assertEqual(_encode_param(255), "ff")
    for overflow in (256, 384, 1000):
      with self.assertRaises(ValueError):
        _encode_param(overflow)


class TestFACSMelodyActuationGuardAllCommands(unittest.IsolatedAsyncioTestCase):
  def _armed_backend(self, allow_actuation: bool):
    driver = FACSMelodyDriver(armed=True, allow_actuation=allow_actuation)
    driver.pm = _complete_map()
    conn = _FakeConnection()
    driver._conn = conn
    return FACSMelodyCellSorterBackend(driver), conn

  async def _actuate(self, backend, name):
    if name == "prime":
      await backend.prime()
    elif name == "clean":
      await backend.clean()
    elif name == "set_deposition":
      await backend.set_deposition(cells_per_well=1, plate_format="96")
    elif name == "start_sort":
      await backend.start_sort(wells=4)
    else:
      raise AssertionError(f"unhandled actuating command {name}")

  async def test_every_actuating_command_blocked_without_opt_in(self):
    backend, conn = self._armed_backend(allow_actuation=False)
    # Exercise the exact set the backend declares actuating, so a newly added
    # actuating command without a guard test makes this fail.
    for name in sorted(ACTUATING_COMMANDS):
      with self.subTest(command=name), self.assertRaises(SortActuationError):
        await self._actuate(backend, name)
    self.assertEqual(conn.written, [])

  async def test_every_actuating_command_transmits_with_opt_in(self):
    backend, conn = self._armed_backend(allow_actuation=True)
    for name in sorted(ACTUATING_COMMANDS):
      await self._actuate(backend, name)
    self.assertEqual(len(conn.written), len(ACTUATING_COMMANDS))


class TestFACSMelodyAbort(unittest.IsolatedAsyncioTestCase):
  async def test_abort_transmits_and_is_not_actuation_gated(self):
    # abort is an emergency stop: it must transmit even with allow_actuation=False.
    driver = FACSMelodyDriver(armed=True, allow_actuation=False)
    driver.pm = _complete_map()  # abort decoded as "00"
    conn = _FakeConnection()
    driver._conn = conn
    backend = FACSMelodyCellSorterBackend(driver)
    await backend.abort()
    self.assertEqual(conn.written, [bytes.fromhex("00")])

  async def test_abort_refuses_empty_frame_instead_of_silent_noop(self):
    # A "decoded" abort with no frame template must fail loud on a live run, not
    # silently transmit nothing (a no-op emergency stop is dangerous).
    driver = FACSMelodyDriver(armed=True, allow_actuation=True)
    pm = _complete_map()
    pm.commands["abort"].frame_template = None
    driver.pm = pm
    driver._conn = _FakeConnection()
    backend = FACSMelodyCellSorterBackend(driver)
    with self.assertRaises(SortNotReadyError):
      await backend.abort()


class TestFACSMelodyFrameGuards(unittest.IsolatedAsyncioTestCase):
  async def test_send_refuses_empty_frame_on_live_run(self):
    driver = FACSMelodyDriver(armed=True, allow_actuation=True)
    driver.pm = _complete_map()
    driver._conn = _FakeConnection()
    with self.assertRaises(SortNotReadyError):
      await driver.send("get_status", "", live=True)

  async def test_frame_raises_on_unsubstituted_token(self):
    driver = FACSMelodyDriver()
    pm = _complete_map()
    pm.commands["start_sort"].frame_template = "aa{wells}{missing}55"
    driver.pm = pm
    backend = FACSMelodyCellSorterBackend(driver)
    with self.assertRaises(ValueError):
      backend._frame("start_sort", wells=1)


class TestFACSMelodyWaitTimeout(unittest.IsolatedAsyncioTestCase):
  async def test_wait_for_completion_times_out_when_never_idle(self):
    # Armed against an instrument that never reports idle: wait must raise, not hang.
    driver = FACSMelodyDriver(armed=True, allow_actuation=False)
    driver.pm = _complete_map()
    driver._conn = _FakeConnection()  # read() -> b"" -> get_status returns "unknown"
    backend = FACSMelodyCellSorterBackend(driver)
    with self.assertRaises(SortTimeoutError):
      await backend.wait_for_completion(poll_interval=0.001, timeout=0.005)


class TestFACSMelodyFrameSequence(unittest.IsolatedAsyncioTestCase):
  async def test_backend_primitives_write_expected_frames(self):
    driver = FACSMelodyDriver(armed=True, allow_actuation=True)
    driver.pm = _complete_map()
    conn = _FakeConnection()
    driver._conn = conn
    backend = FACSMelodyCellSorterBackend(driver)
    await backend.load_template(name="gate")
    await backend.set_deposition(cells_per_well=50, plate_format="96")
    await backend.prime()
    await backend.start_sort(wells=4)
    await backend.clean()
    self.assertEqual(
      conn.written,
      [
        bytes.fromhex("cc" + "gate".encode().hex()),
        bytes.fromhex("bb" + "32" + "96".encode().hex()),
        bytes.fromhex("00"),
        bytes.fromhex("aa" + "04" + "55"),
        bytes.fromhex("00"),
      ],
    )


class TestProtocolMapRoundTrip(unittest.IsolatedAsyncioTestCase):
  async def test_to_json_from_json_roundtrip(self):
    import os
    import tempfile

    pm = _complete_map()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
      path = fh.name
    try:
      pm.to_json(path)
      loaded = ProtocolMap.from_json(path)
      self.assertEqual(loaded.transport, pm.transport)
      self.assertEqual(loaded.endpoint, pm.endpoint)
      self.assertEqual(set(loaded.commands), set(pm.commands))
      for name, cmd in pm.commands.items():
        self.assertEqual(loaded.commands[name].frame_template, cmd.frame_template)
        self.assertEqual(loaded.commands[name].decoded, cmd.decoded)
        self.assertEqual(loaded.commands[name].transport, cmd.transport)
      self.assertEqual(loaded.coverage()["missing"], [])
    finally:
      os.unlink(path)

  async def test_from_json_tolerates_unknown_keys(self):
    import json
    import os
    import tempfile

    payload = {
      "device": "BD FACSMelody",
      "transport": "tcp",
      "endpoint": "x:1",
      "commands": {
        "start_sort": {
          "name": "start_sort",
          "transport": "tcp",
          "frame_template": "aa",
          "decoded": True,
          "future_field": "ignored",
          "another": 123,
        },
      },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
      json.dump(payload, fh)
      path = fh.name
    try:
      loaded = ProtocolMap.from_json(path)  # must not raise on unknown keys
      self.assertEqual(loaded.commands["start_sort"].frame_template, "aa")
      self.assertTrue(loaded.commands["start_sort"].decoded)
    finally:
      os.unlink(path)


if __name__ == "__main__":
  unittest.main()
