"""Offline protocol and recovery tests; no serial ports are opened."""

import asyncio
import unittest
from collections import deque
from typing import Optional
from unittest.mock import AsyncMock, patch

from pylabrobot.io.serial import Serial
from pylabrobot.liconic import StoreX, StoreXCassette, StoreXError, StoreXProtocolError
from pylabrobot.resources import Coordinate, Lid, Plate
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.corning.plates import cor_96_wellplate_360uL_Fb


async def yield_once(_delay: float) -> None:
  """Keep cancellation and concurrent tasks live without hardware settling delays."""
  await asyncio.sleep(0)


class PLC(Serial):
  """In-memory PLC, with independent physical tray/shovel occupancy and reply faults."""

  def __init__(self) -> None:
    super().__init__("offline StoreX", port="offline")
    self.commands: list[str] = []
    self.bits = {1915: True, 1814: False, 1812: False, 1813: False, 2: True, 3: False}
    self.words = {202: 4, 200: 0, 39: 25, 890: 370, 982: 370}
    self.timers = {16: "0,00000,00005", 17: "1,00005,00005"}
    self.replies: deque[bytes] = deque()
    self.override: dict[str, list[bytes]] = {}
    self.fail_after: Optional[str] = None
    self.pause_after: Optional[str] = None
    self.paused = asyncio.Event()
    self.resume = asyncio.Event()
    self.transport_closed = False
    self.opens = 0

  async def setup(self) -> None:
    self.transport_closed = False
    self.opens += 1

  async def stop(self) -> None:
    self.transport_closed = True

  async def send_break(self, duration: float) -> None:
    self.replies.clear()

  async def reset_input_buffer(self) -> None:
    self.replies.clear()

  async def reset_output_buffer(self) -> None:
    pass

  async def write(self, data: bytes) -> None:
    assert data.endswith(b"\r") and not data.endswith(b"\r\n")
    command = data[:-1].decode("ascii")
    self.commands.append(command)
    reply = "OK"
    if command == "CR":
      reply = "CC"
    elif command.startswith("RD DM"):
      reply = f"{self.words.get(int(command[5:]), 0):05d}"
    elif command.startswith("RD T"):
      reply = self.timers[int(command[4:])]
    elif command.startswith("RD "):
      reply = str(int(self.bits.get(int(command[3:]), False)))
    elif command.startswith("WR DM"):
      register, value = command[5:].split()
      self.words[int(register)] = int(value)
    elif command == "ST 1801":
      self.words[202] |= 4
      self.bits[1915] = True
    elif command == "ST 1900":
      self.bits[1814] = False
    elif command == "ST 1908":
      self.bits[1812] = True
    elif command == "ST 1907":
      self.bits[1812] = True
      self.bits[1813] = False
    elif command == "ST 1906":
      self.bits[1812] = False
      self.bits[1813] = True
    elif command == "ST 1909":
      self.bits[1812] = False
    elif command == "ST 1901":
      self.bits[3] = True
      self.bits[2] = False
    elif command == "ST 1902":
      self.bits[3] = False
      self.bits[2] = True
    elif command in ("ST 1912", "RS 1912"):
      home = command == "RS 1912"
      self.bits[1912] = not home
      self.timers = {17: f"{int(home)},00005,00005", 16: f"{int(not home)},00005,00005"}
    elif command.startswith(("ST ", "RS ")):
      self.bits[int(command[3:])] = command.startswith("ST")
    else:
      raise AssertionError(f"Unexpected command: {command}")
    if command == self.pause_after:
      self.paused.set()
      await self.resume.wait()
    if command == self.fail_after:
      raise OSError("Lost reply after physical execution")
    self.replies.extend(self.override.get(command, [(reply + "\r\n").encode("ascii")]))

  async def readline(self) -> bytes:  # type: ignore[override]
    await asyncio.sleep(0)
    return self.replies.popleft() if self.replies else b""


class StoreXTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self) -> None:
    self.rack = StoreXCassette("rack", num_sites=3, site_height=17, pitch=23)
    self.store = StoreX(
      "store",
      "offline",
      "STX44_IC",
      {2: self.rack},
      read_timeout=0.03,
      has_shaker=True,
      has_humidity_control=True,
      has_co2_control=True,
      has_n2_control=True,
      swap_home_sensor=100,
    )
    self.plc = PLC()
    self.store.io = self.plc
    self.sleep_patch = patch("pylabrobot.liconic.storex.sleep", new=yield_once)
    self.sleep_patch.start()
    self.addCleanup(self.sleep_patch.stop)
    await self.store.setup()
    self.plate = cor_96_wellplate_360uL_Fb("plate")
    self.rack.sites[0].assign_child_resource(self.plate)
    self.plc.commands.clear()

  async def test_framing_accepts_fragmented_reply_without_reading_128_bytes(self) -> None:
    self.plc.override["RD DM982"] = [b"00", b"370\r", b"\n"]
    self.assertEqual(await self.store.get_temperature(), 37)
    self.assertEqual(self.plc.commands, ["RD DM982"])

  async def test_malformed_and_unknown_replies_invalidate_transport(self) -> None:
    for reply in (b"1\n", b"\r\n", b"OK\r\n0\r\n", b"E9\r\n", b"\xff\r\n"):
      with self.subTest(reply=reply):
        await self.store.setup()
        self.plc.override["RD 1915"] = [reply]
        with self.assertRaises((StoreXProtocolError, UnicodeDecodeError)):
          await self.store.request_ready()
        self.assertTrue(self.plc.transport_closed)
        self.assertIsNotNone(self.plate.parent)
        self.plc.override.clear()

  async def test_partial_reply_times_out_and_never_retries(self) -> None:
    self.plc.override["RD 1915"] = [b"1\r"]
    with self.assertRaises(TimeoutError):
      await self.store.request_ready()
    self.assertEqual(self.plc.commands, ["RD 1915"])
    self.assertTrue(self.plc.transport_closed)

  async def test_reconnect_waits_for_failed_exchange_to_finish_closing(self) -> None:
    closing = asyncio.Event()
    release_close = asyncio.Event()

    async def slow_close() -> None:
      closing.set()
      await release_close.wait()
      self.plc.transport_closed = True

    self.plc.fail_after = "RD 1915"
    with patch.object(self.plc, "stop", new=slow_close):
      query = asyncio.create_task(self.store.request_ready())
      await closing.wait()
      reconnect = asyncio.create_task(self.store.setup())
      await asyncio.sleep(0)
      self.assertEqual(self.plc.opens, 1)
      release_close.set()
      with self.assertRaises(OSError):
        await query
      await reconnect
    self.assertEqual(self.plc.opens, 2)
    self.assertFalse(self.plc.transport_closed)
    self.assertEqual(self.plc.commands, ["RD 1915", "CR"])

  async def test_controller_fault_preserves_code_command_and_description(self) -> None:
    self.plc.override["RD 1915"] = [b"E1\r\n"]
    with self.assertRaises(StoreXError) as caught:
      await self.store.request_ready()
    self.assertEqual(caught.exception.code, "E1")
    self.assertEqual(caught.exception.command, "RD 1915")
    self.assertIn("Invalid command", caught.exception.message)
    self.assertFalse(self.plc.transport_closed)

  async def test_busy_fault_is_reported_before_ready_timeout_without_reset(self) -> None:
    self.plc.bits[1915] = False
    self.plc.bits[1814] = True
    self.plc.words[200] = 13
    with self.assertRaises(StoreXError) as caught:
      await self.store._wait_ready(0.01)
    self.assertEqual(caught.exception.code, "00013")
    self.assertEqual(self.plc.commands, ["RD 1814", "RD DM200"])

  async def test_plate_ready_uses_1815(self) -> None:
    self.plc.bits[1815] = True
    self.assertTrue(await self.store.request_plate_ready())
    self.assertEqual(self.plc.commands, ["RD 1815"])

  async def test_setup_is_repeatable_and_does_not_home(self) -> None:
    await self.store.setup()
    self.assertEqual(self.plc.opens, 1)
    self.assertEqual(self.plc.commands, [])
    await self.store.stop()
    await self.store.stop()
    await self.store.setup()
    self.assertEqual(self.plc.opens, 2)
    self.assertEqual(self.plc.commands, ["CR"])
    self.assertIs(self.plate.parent, self.rack.sites[0])

  async def test_failed_and_cancelled_setup_close_connection(self) -> None:
    await self.store.stop()
    self.plc.override["CR"] = [b"OK\r\n"]
    with self.assertRaises(StoreXProtocolError):
      await self.store.setup()
    self.assertTrue(self.plc.transport_closed)
    self.plc.override.clear()
    self.plc.pause_after = "CR"
    task = asyncio.create_task(self.store.setup())
    await self.plc.paused.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertTrue(self.plc.transport_closed)

  async def test_initialize_confirms_state_and_is_repeatable(self) -> None:
    self.plc.words[202] = 0
    await self.store.initialize()
    await self.store.initialize()
    self.assertEqual(self.plc.commands.count("ST 1801"), 1)
    self.assertNotIn("ST 1900", self.plc.commands)

  async def test_initialization_refuses_occupied_shovel(self) -> None:
    self.plc.words[202] = 0
    self.plc.bits[1812] = True
    with self.assertRaisesRegex(RuntimeError, "plate on the shovel"):
      await self.store.initialize()
    self.assertNotIn("ST 1801", self.plc.commands)

  async def test_motion_waits_before_first_ready_poll(self) -> None:
    calls: list[object] = []

    async def record_sleep(delay: float) -> None:
      calls.append(delay)

    async def record_command(command: str) -> str:
      calls.append(command)
      return "OK"

    with (
      patch("pylabrobot.liconic.storex.sleep", new=record_sleep),
      patch.object(self.store, "_send_command", new=AsyncMock(side_effect=record_command)),
      patch.object(
        self.store, "_wait_ready", new=AsyncMock(side_effect=lambda timeout: calls.append("poll"))
      ),
    ):
      await self.store._send_command_and_wait_until_ready("ST 1908", 60)
    self.assertEqual(calls, ["ST 1908", 0.25, "poll"])

  async def test_fetch_uses_staged_commands_and_commits_inventory(self) -> None:
    self.assertIs(await self.store.fetch_plate_to_loading_tray("plate"), self.plate)
    self.assertIs(self.plate.parent, self.store.loading_tray)
    self.assertIsNone(self.store.shovel.resource)
    movements = [
      cmd for cmd in self.plc.commands if cmd.startswith("WR ") or cmd.startswith("ST 190")
    ]
    self.assertEqual(
      movements, ["WR DM23 788", "WR DM25 3", "WR DM0 2", "WR DM5 1", "ST 1908", "ST 1906"]
    )
    self.assertNotIn("ST 1903", self.plc.commands)
    count = len(self.plc.commands)
    await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertFalse(any(cmd.startswith(("ST 190", "WR ")) for cmd in self.plc.commands[count:]))

  async def test_store_then_repeat_does_not_move(self) -> None:
    self.plate.unassign()
    self.store.loading_tray.assign_child_resource(self.plate)
    self.plc.bits[1813] = True
    await self.store.store_plate(self.plate, self.rack.sites[1])
    self.assertIs(self.plate.parent, self.rack.sites[1])
    self.assertIn("WR DM5 2", self.plc.commands)
    self.assertIn("ST 1907", self.plc.commands)
    self.assertIn("ST 1909", self.plc.commands)
    count = len(self.plc.commands)
    await self.store.store_plate(self.plate, self.rack.sites[1])
    self.assertFalse(any(cmd.startswith("ST 190") for cmd in self.plc.commands[count:]))

  async def test_between_cassettes_programs_each_pitch(self) -> None:
    other = StoreXCassette("deepwell", num_sites=2, site_height=44, pitch=50, pitch_steps=1700)
    store = StoreX("other_store", "offline", "STX44_IC", {2: self.rack, 5: other})
    store.io = self.plc
    await store.setup()
    self.plc.commands.clear()
    await store.store_plate(self.plate, other.sites[1])
    writes = [cmd for cmd in self.plc.commands if cmd.startswith("WR ")]
    self.assertEqual(
      writes,
      [
        "WR DM23 788",
        "WR DM25 3",
        "WR DM0 2",
        "WR DM5 1",
        "WR DM23 1700",
        "WR DM25 2",
        "WR DM0 5",
        "WR DM5 2",
      ],
    )
    self.assertIs(self.plate.parent, other.sites[1])

  async def test_destination_and_sensor_preflight_happen_before_motion(self) -> None:
    self.plc.bits[1813] = True
    with self.assertRaisesRegex(RuntimeError, "sensor reports"):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertFalse(any(cmd.startswith(("WR ", "ST 190")) for cmd in self.plc.commands))
    self.assertIsNone(self.store.unresolved_transfer)
    self.assertIs(self.plate.parent, self.rack.sites[0])

  async def test_lost_pick_reply_records_uncertainty_and_blocks_motion(self) -> None:
    self.plc.fail_after = "ST 1908"
    with self.assertRaises(OSError):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    transfer = self.store.unresolved_transfer
    self.assertIsNotNone(transfer)
    assert transfer is not None
    self.assertIs(transfer.source, self.rack.sites[0])
    self.assertIs(transfer.destination, self.store.shovel)
    self.assertIs(self.plate.parent, self.rack.sites[0])
    self.plc.fail_after = None
    await self.store.setup()
    count = len(self.plc.commands)
    for action in (
      self.store.initialize,
      self.store.open_door,
      self.store.recover,
      lambda: self.store.fetch_plate_to_loading_tray(self.plate),
      lambda: self.store.scan_barcode(self.rack.sites[0], AsyncMock()),
    ):
      with self.assertRaisesRegex(RuntimeError, "unresolved"):
        await action()
    self.assertEqual(len(self.plc.commands), count)
    await self.store.resolve_unresolved_transfer("destination")
    self.assertIs(self.plate.parent, self.store.shovel)
    await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIs(self.plate.parent, self.store.loading_tray)

  async def test_lost_put_reply_retains_confirmed_shovel_assignment(self) -> None:
    self.plc.fail_after = "ST 1906"
    with self.assertRaises(OSError):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIs(self.plate.parent, self.store.shovel)
    self.plc.fail_after = None
    await self.store.setup()
    with self.assertRaisesRegex(RuntimeError, "sensor reports"):
      await self.store.resolve_unresolved_transfer("source")
    count = len(self.plc.commands)
    await self.store.resolve_unresolved_transfer("destination")
    self.assertIs(self.plate.parent, self.store.loading_tray)
    self.assertFalse(any(cmd.startswith(("ST 190", "WR ")) for cmd in self.plc.commands[count:]))

  async def test_cancellation_preserves_uncertainty_and_closes_serial(self) -> None:
    self.plc.pause_after = "ST 1908"
    task = asyncio.create_task(self.store.fetch_plate_to_loading_tray(self.plate))
    await self.plc.paused.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertTrue(self.plc.transport_closed)
    self.assertIsNotNone(self.store.unresolved_transfer)
    self.assertNotIn("ST 1906", self.plc.commands)

  async def test_resource_commit_failure_remains_reconcilable(self) -> None:
    with patch.object(
      self.store.loading_tray, "assign_child_resource", side_effect=RuntimeError("callback")
    ):
      with self.assertRaisesRegex(RuntimeError, "callback"):
        await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIsNotNone(self.store.unresolved_transfer)
    await self.store.resolve_unresolved_transfer("destination")
    self.assertIs(self.plate.parent, self.store.loading_tray)

  async def test_concurrent_fetch_validates_under_operation_lock(self) -> None:
    other = cor_96_wellplate_360uL_Fb("other")
    self.rack.sites[1].assign_child_resource(other)
    self.plc.pause_after = "ST 1908"
    first = asyncio.create_task(self.store.fetch_plate_to_loading_tray(self.plate))
    await self.plc.paused.wait()
    second = asyncio.create_task(self.store.fetch_plate_to_loading_tray(other))
    await asyncio.sleep(0)
    self.plc.resume.set()
    await first
    with self.assertRaises((RuntimeError, ValueError)):
      await second
    self.assertEqual(self.plc.commands.count("ST 1908"), 1)
    self.assertIs(other.parent, self.rack.sites[1])

  async def test_external_handshake_and_reversed_transfer_route_are_rejected(self) -> None:
    for relay in (1601, 1610, 1613, 1714, 1910, 1607):
      with self.subTest(relay=relay):
        self.plc.bits[relay] = True
        self.plc.commands.clear()
        with self.assertRaises(RuntimeError):
          await self.store.fetch_plate_to_loading_tray(self.plate)
        self.assertFalse(any(cmd.startswith(("ST 190", "WR ")) for cmd in self.plc.commands))
        self.plc.bits[relay] = False

  async def test_swap_home_is_idempotent_and_uses_debounced_sensor(self) -> None:
    await self.store.turn_swap_station(home=True)
    self.assertNotIn("ST 1912", self.plc.commands)
    self.assertNotIn("RS 1912", self.plc.commands)
    await self.store.turn_swap_station(home=False)
    self.assertEqual(await self.store.request_swap_station_position(), "swapped")
    await self.store.turn_swap_station(home=True)
    self.assertIn("RS 1912", self.plc.commands)
    self.assertEqual(await self.store.request_swap_station_position(), "home")

  async def test_swap_requires_calibration_and_rejects_contradictory_sensors(self) -> None:
    self.store.swap_home_sensor = None
    with self.assertRaisesRegex(RuntimeError, "measured"):
      await self.store.turn_swap_station(home=True)
    self.store.swap_home_sensor = 100
    self.plc.timers[16] = "1,00005,00005"
    with self.assertRaises(StoreXProtocolError):
      await self.store.request_swap_station_position()

  async def test_swap_home_overrides_pending_departure_from_home_switch(self) -> None:
    self.plc.bits[1912] = True
    await self.store.turn_swap_station(home=True)
    self.assertIn("RS 1912", self.plc.commands)
    self.assertNotIn("ST 1912", self.plc.commands)

  async def test_gate_operations_confirm_end_switch_and_repeat(self) -> None:
    await self.store.open_door()
    await self.store.open_door()
    await self.store.close_door()
    await self.store.close_door()
    self.assertEqual(self.plc.commands.count("ST 1901"), 1)
    self.assertEqual(self.plc.commands.count("ST 1902"), 1)
    self.assertIn("RD 3", self.plc.commands)
    self.assertIn("RD 2", self.plc.commands)

  async def test_barcode_success_and_failure_always_reset_positioning_mode(self) -> None:
    barcode = Barcode("ABC", "Code 128", "front")
    self.assertEqual(
      await self.store.scan_barcode(self.rack.sites[0], AsyncMock(return_value=barcode)), barcode
    )
    self.assertEqual(self.plate.barcode, barcode)
    with self.assertRaisesRegex(ValueError, "reader"):
      await self.store.scan_barcode(self.rack.sites[0], AsyncMock(side_effect=ValueError("reader")))
    self.assertEqual(self.plc.commands.count("ST 1910"), 2)
    self.assertEqual(self.plc.commands.count("RS 1910"), 2)
    self.assertFalse(self.plc.bits[1910])

  async def test_barcode_cancellation_cleans_up_and_preserves_cancellation(self) -> None:
    reading = asyncio.Event()

    async def read() -> Barcode:
      reading.set()
      await asyncio.Event().wait()
      return Barcode("unreachable", "Code 128", "front")

    task = asyncio.create_task(self.store.scan_barcode(self.rack.sites[0], read))
    await reading.wait()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):
      await task
    self.assertFalse(self.plc.bits[1910])
    self.assertIsNone(self.store.unresolved_transfer)

  async def test_recover_clears_barcode_mode_after_lost_positioning_reply(self) -> None:
    self.plc.fail_after = "ST 1910"
    reader = AsyncMock()
    with self.assertRaises(OSError):
      await self.store.scan_barcode(self.rack.sites[0], reader)
    reader.assert_not_called()
    self.assertTrue(self.store.needs_recovery)
    self.assertTrue(self.plc.bits[1910])
    self.plc.fail_after = None
    await self.store.setup()
    await self.store.recover()
    self.assertFalse(self.plc.bits[1910])
    self.assertFalse(self.store.needs_recovery)

  async def test_completed_command_with_missing_plate_sensor_stays_unresolved(self) -> None:
    self.plc.override["RD 1812"] = [b"0\r\n"]
    with self.assertRaisesRegex(RuntimeError, "sensor reports"):
      await self.store.fetch_plate_to_loading_tray(self.plate)
    self.assertIs(self.plate.parent, self.rack.sites[0])
    self.assertIsNotNone(self.store.unresolved_transfer)
    self.assertNotIn("ST 1906", self.plc.commands)

  async def test_shaker_scale_is_vendor_integer_not_tenths(self) -> None:
    await self.store.start_shaking(25)
    await self.store.start_shaking(25)
    self.assertIn("WR DM39 00025", self.plc.commands)
    self.assertNotIn("WR DM39 00250", self.plc.commands)
    self.assertEqual(self.plc.commands.count("ST 1913"), 1)
    self.assertEqual(await self.store.get_shaker_speed(), 25)
    await self.store.stop_shaking()
    await self.store.stop_shaking()
    self.assertEqual(self.plc.commands.count("RS 1913"), 1)

  async def test_climate_units_negative_temperature_and_readback(self) -> None:
    self.plc.words.update({982: 65436, 983: 850, 984: 400, 985: 8000})
    await self.store.set_target_temperature(-20)
    await self.store.set_target_humidity(0.9)
    await self.store.set_target_co2_level(0.05)
    await self.store.set_target_n2_level(0.9)
    self.assertEqual(await self.store.get_target_temperature(), -20)
    self.assertEqual(await self.store.get_target_humidity(), 0.9)
    self.assertEqual(await self.store.get_target_co2_level(), 0.05)
    self.assertEqual(await self.store.get_target_n2_level(), 0.9)
    self.assertEqual(await self.store.get_temperature(), -10)
    self.assertEqual(await self.store.get_humidity(), 0.85)
    self.assertEqual(await self.store.get_co2_level(), 0.04)
    self.assertEqual(await self.store.get_n2_level(), 0.8)
    self.assertIn("WR DM890 65336", self.plc.commands)
    self.assertIn("WR DM893 00900", self.plc.commands)
    self.assertIn("WR DM894 00500", self.plc.commands)
    self.assertIn("WR DM895 09000", self.plc.commands)

  async def test_invalid_setpoints_timeouts_and_absent_options_do_not_write(self) -> None:
    for action in (
      lambda: self.store.start_shaking(0),
      lambda: self.store.start_shaking(51),
      lambda: self.store.set_target_humidity(2),
      lambda: self.store.set_target_temperature(float("nan")),
      lambda: self.store.fetch_plate_to_loading_tray(self.plate, timeout=float("inf")),
    ):
      with self.assertRaises(ValueError):
        await action()
    self.store.has_co2_control = False
    with self.assertRaises(NotImplementedError):
      await self.store.set_target_co2_level(0.05)
    self.assertEqual(self.plc.commands, [])

  async def test_transfer_station_wait_is_bounded(self) -> None:
    with self.assertRaises(TimeoutError):
      await self.store.wait_for_transfer_station(True, timeout=0.001)

  async def test_recovery_does_not_home_a_plate_on_shovel(self) -> None:
    self.plc.bits[1814] = True
    self.plc.bits[1812] = True
    with self.assertRaisesRegex(RuntimeError, "shovel"):
      await self.store.recover()
    self.assertNotIn("ST 1900", self.plc.commands)
    self.plc.bits[1812] = False
    await self.store.recover()
    self.assertIn("ST 1900", self.plc.commands)
    self.assertIn("ST 1801", self.plc.commands)
    self.assertFalse(self.store.needs_recovery)


class CassetteTests(unittest.TestCase):
  def test_documented_tall_pitch_and_calibrated_override(self) -> None:
    rack = StoreXCassette("tall", num_sites=4, site_height=104, pitch=110)
    self.assertEqual(rack.pitch_steps, 3769)
    self.assertEqual(rack.sites[3].location, Coordinate(z=330))
    self.assertEqual(rack.sites[3].get_size_z(), 104)
    calibrated = StoreXCassette(
      "calibrated", num_sites=4, site_height=104, pitch=110, pitch_steps=942
    )
    self.assertEqual(calibrated.pitch_steps, 942)

  def test_plate_height_includes_lid_and_last_slot_has_real_clearance(self) -> None:
    rack = StoreXCassette("rack", num_sites=2, site_height=17, pitch=23)
    store = StoreX("store", "offline", "STX44_IC", {1: rack})
    plate = Plate(
      "plate",
      size_x=127.76,
      size_y=85.48,
      size_z=15,
      ordered_items={},
      lid=Lid("lid", size_x=127.76, size_y=85.48, size_z=5, nesting_z_height=1),
    )
    with self.assertRaisesRegex(RuntimeError, "No free slot"):
      store.find_smallest_site_for_plate(plate)

  def test_invalid_geometry_and_physical_mapping(self) -> None:
    for kwargs in (
      {"num_sites": 0},
      {"pitch": float("nan")},
      {"pitch_steps": 0},
      {"site_height": 24},
    ):
      parameters: dict = dict(num_sites=3, pitch=23, site_height=17)
      parameters.update(kwargs)
      with self.assertRaises(ValueError):
        StoreXCassette("invalid", **parameters)
    rack = StoreXCassette("rack", num_sites=2, site_height=17, pitch=23)
    with self.assertRaises(ValueError):
      StoreX("store", "offline", "STX44_IC", {1: rack, 2: rack})
